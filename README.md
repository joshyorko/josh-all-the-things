# Josh's All the Things

Josh's All the Things packages a folder, an optional Homebrew recovery
directory, and optional local Docker images into one portable Hauler archive.
It runs as an RCC robot, from a self-contained RCC bundle, or through the
standalone Python `3tc` command in the RCC-managed environment.

## What It Does

- `build`: archive one folder and optionally include a Homebrew recovery
  directory plus selected or all tagged local Docker images.
- `restore`: safely restore the archived folder into an empty destination.
- `serve`: load the haul into a temporary store and serve its images from a
  read-only OCI registry on port `5000`.
- Interactive mode: run with no arguments and choose build, restore, or serve.

The generated haul is the portable payload. The RCC bundle contains the tool and
its RCC-managed runtime; it is not the haul itself.

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
  --task 3tc \
  --interactive \
  --silent
```

Run a non-interactive operation by passing arguments after the task command is
not supported by `run-from-bundle`. Use the interactive wizard, run the local
robot, or unpack the bundle and invoke the standalone script directly.

To inject environment variables from an RCC environment file:

```bash
rcc robot run-from-bundle ../josh-all-the-things-bundle.py \
  --task 3tc \
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
  -t 3tc \
  --interactive
```

With an environment file:

```bash
ROBOCORP_HOME="$PWD/.rcc_home" rcc run \
  -r robot.yaml \
  -t 3tc \
  -e env.json \
  --interactive
```

## Use It Standalone

From the source tree or an unpacked bundle:

```bash
rcc task script -r robot.yaml -- ./3tc
```

RCC provides the pinned Python runtime and runs the dependency bootstrap before
the command. The legacy Bash filename is retained only as a thin compatibility
shim that delegates to `3tc`; it owns no orchestration.

Show the command summary:

```bash
rcc task script -r robot.yaml -- ./3tc --help
```

### Build a Folder-Only Haul

```bash
rcc task script -r robot.yaml -- ./3tc build \
  --folder /path/to/folder \
  --brew /path/to/homebrew-recovery \
  --output ./folder-haul.tar.zst
```

The output file must not already exist.

### Build with Selected Local Images

Each image must already exist in the local Docker daemon:

```bash
rcc task script -r robot.yaml -- ./3tc build \
  --folder /path/to/folder \
  --image ghcr.io/example/api:latest \
  --image docker.io/library/postgres:17 \
  --output ./folder-and-images-haul.tar.zst
```

### Build with All Tagged Local Images

```bash
rcc task script -r robot.yaml -- ./3tc build \
  --folder /path/to/folder \
  --all-images \
  --output ./everything-haul.tar.zst
```

`--all-images` cannot be combined with `--image`. If Docker is unavailable,
`--all-images` falls back to a folder-only haul; explicit `--image` requests
fail instead of silently omitting an image.

### Build with a Homebrew Recovery Directory

```bash
rcc task script -r robot.yaml -- ./3tc build \
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
rcc task script -r robot.yaml -- ./3tc restore \
  --haul ./everything-haul.tar.zst \
  --destination ./restored
```

Restore uses separate reserved directories: workspace contents go below
`./restored/workspace/`, while a Homebrew recovery export goes below
`./restored/homebrew-recovery/`. This prevents a workspace directory named
`homebrew-recovery` from colliding with the recovery export.

### Serve Included Images

```bash
rcc task script -r robot.yaml -- ./3tc serve \
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
- `robot.yaml` exposes `Build`, `Restore`, `Serve`, `3tc`, and devTask `Doctor`.
- `conda.yaml` defines the contained runtime and only bootstraps Homebrew during
  environment creation. The main tool runs as the RCC task, not as a
  `rccPostInstall` hook.
