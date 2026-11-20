# Foreground Serve startup contract

Standalone `jat serve` and RCC `Serve` use the same `ServeRequest` and service.
`fileserver_port` / `registry_port` default to `0`, meaning automatic selection.
Explicit ports `1..65535` are exact requests: conflicts fail without fallback.
`mode=auto` still selects files for file-only content, registry otherwise.
Every requested public endpoint must bind IPv4 loopback (`127.0.0.1`). Hauler
remains the only HTTP server; JAT supervises processes and publishes a file.

## Readiness receipt

CLI: `--startup-file PATH --startup-timeout 30`.
RCC JSON: `startup_file`, `startup_timeout` (seconds, >0 and <=120).

The parent directory must exist. The receipt path must be new, including no
preexisting symlink. A complete UTF-8 JSON record smaller than 1 KiB is linked
atomically into place without replacing an existing file. Version 1 contains:

- `schema_version`: `1`
- `state`: `ready`
- `operation`: `serve`
- `pid`: the JAT Python process PID (may be a descendant of an RCC wrapper)
- `session_id`: a random per-launch identity
- `serve.mode`: resolved `files`, `registry`, or `both`
- `serve.fileserver_url`, `serve.registry_url`: actual `http://127.0.0.1:PORT`
  URLs, or null for inactive endpoints
- `serve.fileserver_bind`, `serve.registry_bind`: `loopback`, or null when inactive

Receipt publication requires every endpoint's exact Hauler child PID to own
its loopback listener and return HTTP 2xx (`/` for files, `/v2/` for registry).
A foreign server responding on the candidate port cannot qualify. HTTP probes
ignore proxy environment variables. Hauler child environments exclude
`HAULER_IGNORE_ERRORS` and `REGISTRY_*` overrides; the registry YAML owns the
loopback address and read-only policy.

Automatic selection launches Hauler on random nonzero candidate ports without
probing and closing a free socket. A reported bind collision on an automatic
endpoint stops the attempt's children and retries, at most eight attempts and
within the startup deadline. Explicit-port failures never change the port.
Non-bind failures do not retry. The startup deadline covers server startup,
not preceding haul loading or the bounded fileserver capability check.

Controllers must use a unique receipt path for each launch, check process exit
while waiting, read at most 4096 bytes, validate the schema and loopback URLs,
and derive readiness, links, catalog and image authorities from those URLs.
Do not parse Hauler's informational port log: registry config can override the
port printed there. Do not wait for the final operation receipt, which is only
available after the foreground command exits.

Handled shutdown/failure stops all owned children and removes the unchanged
owned receipt. A child exiting even with status zero is unexpected; the sibling
is stopped and the operation fails. SIGKILL, a machine crash or filesystem
failure can leave a stale receipt, so the file alone is never proof of liveness.
Ctrl-C/SIGTERM retain the existing foreground cancellation behavior.

## Hauler runtime gate

Pinned v2.1.1 (`f2511b1d6a809832c128b6bb8ab33fd34b1dd927`) cannot select a
fileserver host. Its [`NewFile`](https://github.com/hauler-dev/hauler/blob/f2511b1d6a809832c128b6bb8ab33fd34b1dd927/internal/server/file.go#L17-L54)
constructs `:PORT` and rewrites port 0 to 8080. Its
[fileserver options](https://github.com/hauler-dev/hauler/blob/f2511b1d6a809832c128b6bb8ab33fd34b1dd927/internal/flags/serve.go#L38-L65)
have no host/config option or host environment path. Explicit port selection
already works. This is a missing optional feature, not broken documented behavior.

Official current [fileserver documentation](https://docs.hauler.dev/docs/hauler-usage/store/serve/fileserver)
documents explicit ports and serving files to clients. The
[registry documentation](https://docs.hauler.dev/docs/hauler-usage/store/serve/registry)
supports `--config` with `http.addr`, which JAT already uses for loopback.
Current main `1af4205802a7c51f2fd5b5a88b27163f33a26aa4` has the same fileserver
host limitation. No documented native ephemeral-port discovery is assumed.

JAT checks `fileserver --help` for `--host` before starting files serving.
`files`, `both`, and file-only `auto` fail clearly on the current pin rather than
silently exposing a wildcard listener. Registry automatic selection works with
the unchanged pin. The optional-host patch preserves upstream wildcard defaults
and existing port semantics; JAT's retry allocation is separate.

Upstream feature request: [hauler-dev/hauler#962](https://github.com/hauler-dev/hauler/issues/962).
[Local patch against v2.1.1](upstream/hauler-fileserver-host.patch) is a reviewable
feature proposal, not a shipped runtime. After upstream acceptance/release, JAT
must checksum-pin the supporting release, rebuild its RCC artifacts, and pass
Linux and Windows qualification before Josh Room can pin and verify that exact
published JAT runtime. Source integration and a local patched binary do not
satisfy that gate. No merge, release or deployment is authorized here.

The earlier Josh Room `portable-export-failed` precedes serving. These port
changes neither diagnose that failure nor prove that its cause was a collision.

## Verification recorded on 2026-11-20 (Linux amd64)

- Declared RCC Linux environment: Python 3.13.11, RCC 18.19.11. Development
  pytest/ruff/jsonschema were installed only into task scratch, not the frozen
  runtime. `psutil` is now explicit in source environment manifests; the Linux
  freeze already contained its exact 7.2.2 pin.
- Broad Python suite excluding the older real-vertical module: **432 passed,
  26 skipped, 5 subtests passed**. Final CLI/RCC/startup subset after adding
  request-forwarding coverage: **51 passed**. Skips are not passes.
- Opt-in synthetic real-serving suite with locally patched v2.1.1:
  **9 passed**, including occupied 8080/5000, concurrent sessions across all
  four modes, exact explicit files/registry conflicts, sibling exit/cleanup,
  content download and rejection of the unsupported actual pin.
- Actual unchanged v2.1.1 registry concurrency: **1 passed**, six deselected
  in that invocation. Files serving on the unchanged pin is intentionally
  unavailable. Windows/native packaged runtime and released Room acceptance
  were not verified.
- New supervisor and its tests pass Ruff. A broad Ruff pass over existing
  service code also reported preexisting unused imports/unpacked variables;
  those unrelated findings are not claimed fixed.
- The local Hauler patch passed server/flags tests on v2.1.1. The upstream
  proposal is based on main `1af4205802a7c51f2fd5b5a88b27163f33a26aa4`,
  head `75a1db5d8a9d316483a30269f0a7af7a579eb975`:
  [draft PR #963](https://github.com/hauler-dev/hauler/pull/963).
  Main-based targeted race tests and vet passed. A broader Go run hit a missing
  Helm repository-cache fixture and did not complete (exit 143); full upstream
  acceptance remains open.
- Downstream qualification tracker:
  [JAT #38](https://github.com/joshyorko/josh-all-the-things/issues/38).

Local patched binary SHA256:
`aa9bcacde3f1a736bfff29c82f84725cc37317e1f49f6d39530a05685a1af3d8`.
Actual pinned binary SHA256:
`68deab80633307d3834c52d40d5b39d8f06e9636f94896beff03f69217b907b4`.
The committed v2.1.1 patch is the minimal tested host change; the upstream PR
additionally includes README and flag-parser coverage against current main.

The current Josh Room source `vscode-extension/registry.js` also consumed a real
JAT `both` receipt through `waitForServeStartup` and successfully ran its
fileserver and registry HTTP probes. Shutdown removed the receipt. This check
used the local patched Hauler build and source JAT CLI; it did not exercise a
published/pinned Room runtime or the complete controller/export flow.

Upstream CI currently requires a maintainer to approve the fork's workflow;
local passes are not hosted CI acceptance.
