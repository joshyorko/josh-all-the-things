# Josh's All the Things

Josh's All the Things (JAT) captures a portable **software capability capsule**:
one Hauler-powered haul that can carry a workspace folder, an optional Homebrew
recovery export, an optional RCC Environment Artifact (`.rcca`), and arbitrary
extra content — container images, Helm charts, and files. The haul is useful on
any machine; RCC is optional. The embedded `.rcca` is one possible compute
payload that gives RCC-aware consumers exact environment reconstruction — it is
not the definition of a JAT.

JAT runs as an RCC robot, from a self-contained RCC bundle, or through the
standalone Python `jat` command. Hauler (pinned: v2.0.3) owns the OCI content,
store, transfer, and serving mechanics; JAT gives the capsule a small, safe,
human-friendly contract.

## What It Does

```text
Capture -> Inspect -> Project / Move

Project:
  Restore the workspace
  Extract one artifact
  Serve files, serve OCI, or serve both
  Export images for containerd

Move:
  Copy / seed a registry or directory
```

- `build`: capture a folder plus optional Homebrew recovery, local Docker
  images, native `images.txt` lists, and advanced Hauler manifests into one
  capsule (optionally chunked, with a bounded retry policy for retry-capable
  transfers).
- `inspect`: list everything inside a capsule — normalized inventory plus
  identified JAT anchors — without restoring anything.
- `extract`: pull one selected reference out of a capsule safely.
- `restore`: opinionated workspace reconstruction: rebuilds only the JAT-owned
  workspace / Brew / RCC anchors and ignores unrelated extra content.
- `serve`: expose capsule content as `auto` (compatible default), `files`,
  `registry`, or `both` concurrently.
- `export`: materialize the capsule's images as a containerd-compatible archive.
- `copy`: seed an external Hauler target (`registry://...`, `dir://...`).
- Interactive mode: run with no arguments and choose build, restore, or serve.

Every command accepts `--json` and emits a stable machine receipt
(`format_version` 1 for the classic build/restore/serve/doctor receipts; 2 when
structured multi-output details such as inventory, chunk sets, endpoints, or
transfer policy are present). Receipts never contain credentials.

The generated haul is the portable payload. The RCC bundle contains the tool and
its RCC-managed runtime; it is not the haul itself.

## Build a verified RCC Environment Artifact

Build the canonical JAT runtime artifact with RCC v18.19.3. The builder uses
isolated producer and verifier homes, publishes and exports through RCC's
official Environment Artifact commands, acquires the archive into the fresh
verifier, and proves ordinary `--no-build` resolution before promotion. RCC
v18.19.3 resolves a relative `env exec` command before applying the child
environment, so the Hauler check uses the artifact's Python to locate and run
the `hauler` binary from the acquired Holotree. The launcher also requires the
resolved executable to be below the acquired `CONDA_PREFIX`, preventing a
contaminated host PATH from satisfying the proof:

```bash
scripts/build_environment_artifact.sh \
  --output dist/jat-runtime.rcca \
  --receipt dist/jat-runtime.json
```

Both outputs are create-only. The receipt records RCC's artifact,
specification, and legacy blueprint identities together with the exact JAT
commit, RCC version, platform, archive SHA-256 and size, and fresh-home proofs.
`verified_hauler.command` remains the logical `hauler version` operation;
`verified_hauler.launcher` records the Python boundary used to prove it without
a host Hauler.
RCC owns environment inventory and materialization; JAT does not export or
import raw Holotree state.

Publish the verified outputs with the token exported by your shell:

```bash
scripts/publish_environment_artifact.sh
```

The publisher validates the receipt and `.rcca`, derives a content-specific tag,
logs into GHCR through stdin, publishes both layers, reads the manifest back,
and prints the immutable `repository@sha256:...` reference. Override paths or
the target with `--archive`, `--receipt`, `--repository`, and `--username`.

## Build the RCC Bundle

Run this from the `josh-all-the-things` robot directory:

```bash
rcc robot bundle \
  --robot robot.yaml \
  --output ../josh-all-the-things-bundle.py
```

The output path must be outside this robot directory so a previous bundle is not
included in the next bundle.

## Run the RCC Bundle

Run the interactive wizard:

```bash
rcc robot run-from-bundle ../josh-all-the-things-bundle.py \
  --task JAT \
  --interactive \
  --silent
```

Run a non-interactive operation by passing arguments after the task command is
not supported by `run-from-bundle`. Use the interactive wizard, run the local
robot, or unpack the bundle and invoke the standalone script directly.

To inject environment variables from an RCC environment file:

```bash
rcc robot run-from-bundle ../josh-all-the-things-bundle.py \
  --task JAT \
  --environment "$PWD/env.json" \
  --interactive \
  --silent
```

## Unpack the RCC Bundle

Extract the robot source from the bundle:

```bash
rcc robot unpack \
  --bundle ./josh-all-the-things-bundle.py \
  --output ./josh-all-the-things-unpacked
```

Use `--force` only when intentionally overwriting an existing output directory:

```bash
rcc robot unpack \
  --bundle ./josh-all-the-things-bundle.py \
  --output ./josh-all-the-things-unpacked \
  --force
```

`unpack` extracts the robot code. Run the bundle with `run-from-bundle` when you
want RCC to recreate and manage the bundled environment.

## Run the Robot from Source

From the robot directory, first verify that RCC can resolve its environment:

```bash
ROBOCORP_HOME="$PWD/.rcc_home" rcc ht vars -r robot.yaml
```

Run the interactive task:

```bash
ROBOCORP_HOME="$PWD/.rcc_home" rcc run \
  -r robot.yaml \
  -t JAT \
  --interactive
```

With an environment file:

```bash
ROBOCORP_HOME="$PWD/.rcc_home" rcc run \
  -r robot.yaml \
  -t JAT \
  -e env.json \
  --interactive
```

## Use It Standalone

From the source tree or an unpacked bundle:

```bash
rcc task script -r robot.yaml -- ./jat
```

RCC provides the pinned Python runtime and runs the dependency bootstrap before
the command. The legacy Bash filename is retained only as a thin compatibility
shim that delegates to `jat`; it owns no orchestration.

Show the command summary:

```bash
rcc task script -r robot.yaml -- ./jat --help
```

### Build a Folder-Only Haul

```bash
rcc task script -r robot.yaml -- ./jat build \
  --folder /path/to/folder \
  --brew /path/to/homebrew-recovery \
  --output ./folder-haul.tar.zst
```

The output file must not already exist.

### Build with Selected Local Images

Each image must already exist in the local Docker daemon:

```bash
rcc task script -r robot.yaml -- ./jat build \
  --folder /path/to/folder \
  --image ghcr.io/example/api:latest \
  --image docker.io/library/postgres:17 \
  --output ./folder-and-images-haul.tar.zst
```

### Build with All Tagged Local Images

```bash
rcc task script -r robot.yaml -- ./jat build \
  --folder /path/to/folder \
  --all-images \
  --output ./everything-haul.tar.zst
```

`--all-images` cannot be combined with `--image`. If Docker is unavailable,
`--all-images` falls back to a folder-only haul; explicit `--image` requests
fail instead of silently omitting an image.

### Build with a Homebrew Recovery Directory

```bash
rcc task script -r robot.yaml -- ./jat build \
  --folder /path/to/folder \
  --brew /path/to/homebrew-recovery \
  --output ./folder-and-brew-haul.tar.zst
```

`--brew` accepts a Homebrew recovery export directory, not an arbitrary
directory. It must contain a readable regular `Brewfile`, for example:

```bash
mkdir -p ./homebrew-recovery
brew bundle dump --force --file ./homebrew-recovery/Brewfile
```

The haul stores that export as a top-level `homebrew-recovery.tar.zst` artifact.

### Restore the Folder

The destination must either not exist or be an empty directory:

```bash
rcc task script -r robot.yaml -- ./jat restore \
  --haul ./everything-haul.tar.zst \
  --destination ./restored
```

Restore uses separate reserved directories: workspace contents go below
`./restored/workspace/`, while a Homebrew recovery export goes below
`./restored/homebrew-recovery/`. This prevents a workspace directory named
`homebrew-recovery` from colliding with the recovery export.

### Serve Included Images

```bash
rcc task script -r robot.yaml -- ./jat serve \
  --haul ./everything-haul.tar.zst
```

This foreground command serves a temporary, read-only OCI registry on port
`5000`. In another terminal:

```bash
curl http://127.0.0.1:5000/v2/_catalog
curl http://127.0.0.1:5000/v2/REPOSITORY/tags/list
docker pull 127.0.0.1:5000/REPOSITORY:TAG
```

The temporary store is deleted when the command exits.

### Build with an images.txt List

```bash
rcc task script -r robot.yaml -- ./jat build \
  --folder /path/to/folder \
  --images-file ./images.txt \
  --output ./images-list-haul.tar.zst
```

`--images-file` is repeatable and accepts local paths or HTTP(S) URLs. The list
is consumed by Hauler's native `store sync --image-txt`; each listed image is
acquired through Hauler's normal remote pull path (with its retry and
signature/referrer behavior). Lines starting with `#` and blank lines are
Hauler's business, not JAT's: JAT never parses the list itself.

### Build with Hauler Manifests (Advanced Composition)

```bash
rcc task script -r robot.yaml -- ./jat build \
  --folder /path/to/folder \
  --hauler-manifest ./airgap.yaml \
  --hauler-manifest https://example.test/product.yaml \
  --output ./composed-haul.tar.zst
```

Hauler manifests are the **advanced declarative composition boundary**. Simple
JAT flags cover common use; users and agents who need rich Hauler behavior
express it in Hauler's own manifest model instead of a parallel JAT DSL.

For the pinned v2.0.3, ordinary manifests support exactly three content kinds:
`Files`, `Images`, and `Charts`. (Hauler also has a separate product/collection
acquisition path via `store sync --products`; that is a different mechanism and
is not part of `--hauler-manifest`.)

JAT syncs your manifests into the same store as its core anchors. Your manifest
paths are passed to Hauler exactly as you provide them — Hauler resolves chart
`valuesFiles` relative to the manifest file, so JAT never relocates or rewrites
your manifests. JAT's anchors are reserved: if user-provided content replaces or
duplicates a reserved anchor reference (`joshs-all-the-things-workspace.tar.zst`,
`homebrew-recovery.tar.zst`, `rcc-environment.rcca`,
`rcc-environment-metadata.json`), the build fails instead of producing a capsule
that cannot restore the intended workspace.

### Slim Acquisition and Transfer Retries

```bash
rcc task script -r robot.yaml -- ./jat build \
  --folder /path/to/folder \
  --images-file ./images.txt \
  --exclude-extras \
  --retries 2 \
  --output ./slim-haul.tar.zst
```

- By default remote image/chart acquisition keeps Hauler's associated extras:
  cosign signatures, attestations, SBOMs, and OCI referrers.
- `--exclude-extras` is an explicit opt-out for smaller/slimmer acquisition.
- `--retries` is a bounded integer `>= 1` and describes **transfer reliability
  policy for retry-capable Hauler operations** (remote image pulls, images.txt
  acquisition, Helm image closure, and registry pushes). Hauler's own default is
  3 attempts with a 5-second sleep. It is not a magic wrapper around every JAT
  step: local file ingestion and local Docker `--local` capture are not
  retry-wrapped remote transfers. JAT has no best-effort/ignore-errors mode; a
  failed transfer fails the operation.

### Build a Chunked Haul

```bash
rcc task script -r robot.yaml -- ./jat build \
  --folder /path/to/folder \
  --chunk-size 500MB \
  --output ./chunked-haul.tar.zst
```

Hauler v2.0.3 splits the haul into chunks named `<base>_<index><ext>` starting
at zero (`chunked-haul_0.tar.zst`, `chunked-haul_1.tar.zst`, ...). Chunked
output must therefore be a `.tar` or `.tar.zst` archive name: the pinned binary
can split any container, but its own loader cannot reload other chunk
containers, so JAT rejects other output names before any capture work. Only
Hauler's own size units are accepted — a positive byte count with an optional
`K`, `KB`, `M`, `MB`, `G`, `GB`, `T`, or `TB` suffix (binary multiples,
case-insensitive, e.g. `500M`, `1G`, `500MB`, `1048576`); forms like `1B`,
`1Mi`, or `1KiB` are rejected before any capture work. The build promotes **all** chunks atomically —
every sibling name is reserved create-only and a failed promotion rolls back the
links it created, so a failed build never leaves a partial set — and the receipt
lists every chunk with path, size, and SHA-256. Consumers (`inspect`, `restore`,
`serve`, `export`, `copy`) accept the `_0` entrypoint and Hauler reassembles the
set automatically. Chunking and containerd export are mutually exclusive in
Hauler v2.0.3; JAT rejects the combination instead of guessing.

### Inspect a Capsule

```bash
rcc task script -r robot.yaml -- ./jat inspect \
  --haul ./everything-haul.tar.zst
rcc task script -r robot.yaml -- ./jat inspect --haul ./everything-haul.tar.zst --json
```

Inspect loads the haul into an owned temporary store and reports a normalized
content list (reference, type, platform, digest, size, plus bounded extra
metadata) and which known JAT anchors are present (workspace, Brew recovery,
RCC Environment Artifact + metadata). Arbitrary files, images, charts, and
referrers are visible, never rejected, and nothing is restored to disk.

### Extract One Reference

```bash
rcc task script -r robot.yaml -- ./jat extract \
  --haul ./everything-haul.tar.zst \
  --reference hauler/rcc-environment.rcca:latest \
  --destination ./out
```

Extract is the generic content projection: one selected reference, into a
create-only destination. Missing or ambiguous references are rejected with the
known references listed, and the JSON receipt records the outputs with path,
size, and SHA-256. `restore` remains the opinionated workspace reconstruction.

### Serve Modes

```bash
rcc task script -r robot.yaml -- ./jat serve --haul ./everything-haul.tar.zst --mode auto
rcc task script -r robot.yaml -- ./jat serve --haul ./everything-haul.tar.zst --mode files
rcc task script -r robot.yaml -- ./jat serve --haul ./everything-haul.tar.zst --mode registry
rcc task script -r robot.yaml -- ./jat serve --haul ./everything-haul.tar.zst --mode both
```

- `auto` (default) preserves the historical behavior: files-only capsules are
  served by the fileserver on port `8080`; anything else by the read-only
  registry on `127.0.0.1:5000`.
- `files` explicitly exposes file artifacts even when the capsule is mixed with
  OCI images/charts.
- `registry` explicitly exposes the OCI store.
- `both` runs the fileserver and registry concurrently from the same loaded
  capsule. JAT supervises both children: Ctrl-C/cancellation stops both, a
  failing child stops its sibling and fails the operation, and no Hauler
  processes are left behind.

Port overrides: `--fileserver-port` and `--registry-port`. Exposure is
conservative: the fileserver listens on all interfaces (as Hauler's fileserver
always has — the receipt records `all-interfaces`), while the registry binds to
`127.0.0.1` only (`loopback`).

### Export Images for containerd

```bash
rcc task script -r robot.yaml -- ./jat export \
  --haul ./everything-haul.tar.zst \
  --format containerd \
  --output ./images.tar
```

This loads the capsule into an owned store and delegates to Hauler's
`store save --containerd`. No temporary registry is required. The output is
promoted atomically and the receipt records path, size, and SHA-256.

### Copy / Seed an External Target

```bash
rcc task script -r robot.yaml -- ./jat copy \
  --haul ./everything-haul.tar.zst \
  --to registry://registry.example.test
rcc task script -r robot.yaml -- ./jat copy \
  --haul ./everything-haul.tar.zst \
  --to "dir:///path/to/exported"
```

Registry targets (`registry://`, `reg://`, `oci://`) are remote artifact
transfers: Hauler retries each artifact push per `--retries`, and Hauler's
normal auth/config/environment contract (`hauler login`, `--plain-http`,
`--insecure`) applies. JAT never stores or prints credentials: targets that
embed credentials in their userinfo or query/fragment components are rejected,
and any credential echo is redacted from diagnostics. Remote capture sources
(`--images-file`, `--hauler-manifest` URLs) are held to the same standard.
Directory targets (`dir://`, `directory://`) are a local projection, not a
network transfer: the receipt reports `effective_retries: 1` for them (Hauler's
directory branch does not retry), and the projection is staged next to the
destination and promoted only after Hauler succeeds, so a failure never leaves
a partial target. Because Hauler replaces same-named artifacts inside the
target, the destination must be a create-only path or an empty directory (never
`/`), so an unrelated file can never be overwritten. Unsupported schemes are
rejected explicitly.

## Use Hauler Directly for Additional Operations

The wrapper covers the safe build, restore, and temporary-registry workflows.
Use Hauler directly when you need a persistent store, a LAN fileserver, custom
ports, or a permanent registry.

### Load a Haul into a Persistent Store

```bash
mkdir -p ./store ./hauler-tmp
hauler store load \
  --store ./store \
  --tempdir ./hauler-tmp \
  --filename ./everything-haul.tar.zst
```

Inspect it:

```bash
hauler store info --store ./store
hauler store info --store ./store --output json
hauler store info --store ./store --type image
hauler store info --store ./store --type file
```

### Serve File Artifacts on the LAN

```bash
hauler store serve fileserver \
  --store ./store \
  --directory ./fileserver \
  --port 8080
```

Hauler listens on all interfaces. From another device on the same network, open:

```text
http://BLUEFIN_IP:8080/
```

If Bluefin's firewall blocks the connection, open the port on the Bluefin host:

```bash
sudo firewall-cmd --add-port=8080/tcp
```

This serves Hauler `Files` artifacts. Use the registry server for container
images.

### Serve Images from a Persistent Store

```bash
hauler store serve registry \
  --store ./store \
  --directory ./registry \
  --port 5000 \
  --readonly
```

The registry is then reachable locally at `127.0.0.1:5000` and from the LAN at
`BLUEFIN_IP:5000`, subject to the host firewall and container networking.

### Extract the Workspace Artifact Manually

```bash
mkdir -p ./extracted
hauler store extract \
  hauler/joshs-all-the-things-workspace.tar.zst:latest \
  --store ./store \
  --output ./extracted
```

The extracted file is
`joshs-all-the-things-workspace.tar.zst`. The wrapper's `restore` command also
validates its paths and top-level layout before extraction, so prefer the wrapper
unless you specifically need the raw artifact.

### Copy Store Contents Elsewhere

Copy artifacts into a directory:

```bash
hauler store copy \
  --store ./store \
  directory://"$PWD/exported"
```

Push images to an OCI registry:

```bash
hauler store copy \
  --store ./store \
  registry://registry.example.com
```

Use Hauler's `--plain-http` or `--insecure` flags only when the destination
requires them and you understand the transport tradeoff.

## Working Directory and Temporary Data

- Temporary work is staged adjacent to the final output or restore destination
  so promotion remains on one filesystem.
- Temporary data is removed after a successful or failed command.
- Set `JAT_RUN_DIR=/path/to/workdir` when an RCC launcher cannot preserve the
  directory from which you invoked the task.
- Build and restore operations do not overwrite existing output files or
  non-empty destinations.

## Development Notes

- `src/jat/` is the shared Python service implementation.
- `tasks.py` contains thin typed `robocorp.tasks` entrypoints.
- `robot.yaml` exposes `Build`, `Restore`, `Serve`, `JAT`, and devTask `Doctor`.
- `conda.yaml` defines the contained runtime and installs the pinned official
  Hauler release through RCC's `rccPostInstall` hook before the environment is
  frozen. Homebrew is not part of normal JAT execution; `--brew` is only a
  data payload option.
