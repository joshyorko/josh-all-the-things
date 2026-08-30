# Capability-Capsule Workflows over Hauler v2.0.3

Design record for the capability-capsule expansion (issue: "Expose Hauler v2
table stakes as first-class JAT capability-capsule workflows").

## Model

A JAT haul is a portable capability capsule. Hauler (pinned in
`runtime/hauler.json`, currently v2.0.3) owns OCI storage, acquisition,
transfer, serving, and reassembly. JAT owns intent, safety, composition of its
core anchors (workspace, Brew recovery, RCC Environment Artifact + metadata)
with extra Hauler content, and the machine-readable receipts.

```text
Capture (build) -> Inspect -> Project / Move
  Project: restore | extract | serve files/registry/both | export containerd
  Move:    copy to registry:// or dir://
```

## Public surface

| Operation | Summary |
| --- | --- |
| `jat build` | Classic capture plus `--images-file`, `--hauler-manifest`, `--exclude-extras`, `--chunk-size`, `--retries`. |
| `jat inspect` | Normalized inventory + identified JAT anchors; nothing restored. |
| `jat extract` | One selected reference into a create-only destination. |
| `jat serve` | `--mode auto\|files\|registry\|both`, `--fileserver-port`, `--registry-port`. |
| `jat export` | `--format containerd` via Hauler `store save --containerd`. |
| `jat copy` | `registry://`/`reg://`/`oci://` (remote, retry-capable) and `dir://`/`directory://` (local projection). |
| `jat restore` | Unchanged: rebuilds only JAT anchors, tolerates extra content. |

## Receipts

`format_version` 1 receipts are byte-stable for the classic operations. Any
structured multi-output detail (chunk sets, inventory, serve endpoints, transfer
policy, completeness) requires `format_version: 2` and is rejected otherwise.
Receipts never carry credentials.

## Pinned v2.0.3 behaviors verified against the real binary

- `store info --output json` emits one row per reference/platform/digest:
  multi-platform images legitimately repeat a reference per platform variant
  (plus referrer attestations), so inventory uniqueness is per
  `(Reference, Platform, Digest)`.
- Chunked `store save --chunk-size` produces `<base>_<index><ext>` starting at
  zero, with `<ext>` derived by stripping every extension of the requested
  output filename (`capsule.zip` -> `capsule_0.zip`); `store load --filename
  <base>_0<ext>` reassembles tar/tar.zst chunk sets automatically. Hauler can
  split any container but its unarchiver cannot reload non-tar chunk sets
  (zip fails with an `io.ReaderAt`/`io.Seeker` constraint), so JAT restricts
  chunked output names to `.tar`/`.tar.zst` and rejects others up front.
- `parseChunkSize` accepts only a positive byte count with an optional
  `K|KB|M|MB|G|GB|T|TB` suffix (binary multiples, case-insensitive) or a bare
  byte count; forms like `1B`, `1Mi`, or `1KiB` fail in Hauler, so the JAT
  contract rejects them before any capture work.
- `--containerd` removes `oci-layout` from the haul; chunking and `--containerd`
  are mutually exclusive (JAT rejects the combination in the adapter).
- `store serve fileserver` copies file artifacts into its backend directory and
  then serves them; the registry honors the config file's loopback bind.
- Local chart manifests resolve `repoURL` relative to Hauler's working
  directory, while chart `valuesFiles` resolve relative to the manifest file —
  JAT therefore passes user manifests through without relocating them.
- `--retries` is a persistent store option; default is 3 attempts, and a
  requested `--retries 1` yields exactly one attempt. JAT validates `>= 1`.
- Image acquisition from loopback registries uses plain HTTP
  (go-containerregistry behavior), which the test suite uses to prove remote
  transfer paths hermetically.
- `store save --chunk-size` happily splits a `.zip` container, but
  `store load` cannot re-consume it (`input type must be an io.ReaderAt and
  io.Seeker because of zip format constraints`); only tar/tar.zst chunk sets
  round-trip, which is why JAT restricts chunked output names before capture.
- Absolute `repoURL` paths for local charts fail inside Hauler
  (`could not find protocol handler`); only cwd-relative `repoURL: .` chart
  references work.
- `valuesFiles` are only read when a chart entry enables `add-images`; they are
  not validated otherwise.
- A chunk set smaller than one chunk still emits a single `_0` chunk file (no
  bare base file).
- `store extract` matches references by substring containment, so a requested
  reference that is a substring of another reference (e.g. `hauler/foo.txt:latest`
  inside `myhauler/foo.txt:latest`) would extract both; JAT rejects such
  ambiguous references before delegating.
- Hauler's directory-copy branch does not use its retry loop; JAT therefore
  reports `effective_retries: 1` for local projections and reserves the
  requested value for registry pushes.
- A `.zip` chunk set also fails `store load` (zip streaming constraint), and a
  hidden-base chunk set (`.capsule.tar.zst` -> `_0.capsule.tar.zst`) fails
  reassembly with a truncated blob; JAT rejects both output names before
  capture.

## Supervision and progress

`ProcessRunner.run(on_line=...)` streams truthful, redacted Hauler lines through
a bounded sink while keeping bounded tail diagnostics; `ProcessRunner.supervise`
runs the two `serve --mode both` children, stops all siblings on failure or
cancellation (including the SIGTERM/SystemExit path), and leaves no orphans.

## Out of scope (unchanged)

No daemon, no JAT-owned REST API, no web UI, no Action Server, no provider
framework, no global persistent store, no second registry/fileserver
implementation, no best-effort/ignore-errors mode.
