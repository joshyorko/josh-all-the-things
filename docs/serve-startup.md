# Foreground Serve startup contract

Standalone `jat serve` and RCC `Serve` use the same `ServeRequest` and service.
`fileserver_port` / `registry_port` default to `0`, meaning automatic selection.
Explicit ports `1..65535` are exact requests: conflicts fail without fallback.
`mode=auto` still selects files for file-only content, registry otherwise.
Connection URLs use IPv4 loopback (`127.0.0.1`). The pinned fileserver binds
all interfaces; the registry binds loopback. Hauler remains the only HTTP
server; JAT supervises processes and publishes a file.

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
- `serve.fileserver_bind`: `all-interfaces`, or null when inactive
- `serve.registry_bind`: `loopback`, or null when inactive

Receipt publication requires every endpoint's exact Hauler child PID to own
its expected listener and return HTTP 2xx (`/` for files, `/v2/` for registry).
A foreign server responding on the candidate port cannot qualify. HTTP probes
ignore proxy environment variables. Hauler child environments exclude
`HAULER_IGNORE_ERRORS` and `REGISTRY_*` overrides; the registry YAML owns the
loopback address and read-only policy.

Automatic selection launches Hauler on random nonzero candidate ports without
probing and closing a free socket. A reported bind collision on an automatic
endpoint stops the attempt's children and retries, at most eight attempts and
within the startup deadline. Explicit-port failures never change the port.
Non-bind failures do not retry. The startup deadline covers server startup,
not preceding haul loading.

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

## Hauler binding contract

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

Unpatched v2.1.1 supports every JAT Serve mode, including automatic ports.
JAT supplies nonzero candidates to avoid Hauler's port-0 default behavior,
verifies each child-owned wildcard fileserver listener (`0.0.0.0` or `::`) and
loopback registry listener, and reports those scopes accurately. No `--host`
capability gate or patch is required. Localhost connection URLs do not imply
localhost-only exposure. Consumers explicitly requiring loopback-only files
serving must reject an `all-interfaces` receipt; they must not silently treat
it as loopback. Room View's separate localhost policy is unchanged.

Upstream [feature request #962](https://github.com/hauler-dev/hauler/issues/962)
and [draft PR #963](https://github.com/hauler-dev/hauler/pull/963) remain optional
host-selection work. The [local proposal](upstream/hauler-fileserver-host.patch)
is retained for review, independently of this feature. It preserves upstream
wildcard defaults and existing port semantics. JAT's unchanged pin does not
consume that proposal, and no upstream release is a prerequisite for dynamic
ports. Packaged JAT/runtime qualification and Room's consumption of that exact
published JAT runtime remain separate acceptance gates. No merge, release or
deployment is authorized here.

The earlier Josh Room `portable-export-failed` precedes serving. These port
changes neither diagnose that failure nor prove that its cause was a collision.

## Earlier verification (superseded binding policy, 2026-11-20)

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
  in that invocation. The earlier blanket fileserver loopback gate was an
  agent-added constraint and has been removed. Windows/native packaged runtime and released Room acceptance
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

## Corrected unpatched-pin verification (2026-11-20)

The mandatory loopback fileserver gate was an unnecessary agent-added
constraint. It is removed; dynamic files, registry, both and file-only auto
work with **unmodified pinned Hauler v2.1.1**. Fileserver scope is reported as
`all-interfaces`, registry scope as `loopback`. No `--host` argument, capability
probe, patched binary or new upstream release is required.

Through the declared RCC environment, in an isolated user/network namespace:

- `tests/test_dynamic_serve_real.py`: **9 passed**. All four modes support
  concurrent sessions with 8080/5000 occupied; downloads/catalog use selected
  endpoints. Explicit files/registry conflicts fail promptly without fallback.
  Explicit files/registry/both ports remain exact, child exit stops siblings,
  and shutdown removes owned stages/receipts. Each endpoint's child-owned
  listener is checked against its reported scope.
- `tests/test_serving.py`: **10 passed**. Collision retry never accepts a
  foreign listener, startup timeout/cancellation cleans children, receipt
  ownership is preserved, and a wildcard listener cannot satisfy an explicit
  loopback expectation.
- Model, adapter, service, CLI and RCC task tests: **201 passed, 12 skipped**.
- Ruff checks for the supervisor and its focused unit/real tests passed.

Current Room source `run_serve` invoked the RCC `JAT` task against this checkout
and the unchanged Hauler pin in the isolated namespace. Room's updated
`registry.js` startup consumer and fileserver/registry HTTP probes passed with
an actual `both` receipt (`all-interfaces` fileserver, `loopback` registry).
Stopping the recorded JAT PID removed the receipt and ended the Room wrapper.
This verifies the source adapter/RCC/receipt/probe path, not a released runtime,
installed extension, original export flow or private Harvester data.

The actual pinned binary SHA256 remains
`68deab80633307d3834c52d40d5b39d8f06e9636f94896beff03f69217b907b4`.
[JAT #38](https://github.com/joshyorko/josh-all-the-things/issues/38) now tracks
optional localhost binding independently. PR #963 remains valid optional work;
it is not a blocker for these existing artifact-serving capabilities.
