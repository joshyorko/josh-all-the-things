# RCC-First Python Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move JAT orchestration into one Python service layer shared by a standalone CLI and typed `robocorp.tasks` entrypoints without weakening proven Bash safety behavior.

**Architecture:** `src/jat/` owns typed models, safety primitives, subprocess adapters, and services. `src/jat/cli.py` owns only human argument parsing and the interactive wizard. Root `tasks.py` owns only JSON request loading, service invocation, and `output/result.json`. The Bash program remains a characterization oracle and temporary compatibility shim until the Python verticals pass.

**Tech Stack:** Python 3.13.11, Pydantic v2, robocorp.tasks 3.1.1, RCC robot.yaml, Hauler, GNU tar, pytest, Ruff.

**Spec:** User-supplied RCC-first Python migration contract in this task.

## Global Constraints

- RCC, `robot.yaml`, and `robocorp.tasks` are the only automation runtime.
- No Dagger, MCP, Action Server, Work Items, Environment Artifacts, daemon, web service, or provider framework.
- Hauler remains the capture/restore engine; GNU tar remains the archive boundary.
- Every task writes versioned `output/result.json` through `get_output_dir()`.
- The standalone CLI and RCC tasks invoke the same Python service layer.
- Bash remains executable until Python CLI, RCC tasks, and real Hauler verticals pass.

---

### Task 1: Characterization and typed contracts

**Files:**
- Create: `docs/bash-responsibility-map.md`
- Create: `src/jat/models.py`
- Create: `src/jat/__init__.py`
- Create: `tests/test_models.py`
- Modify: `conda.yaml`

**Interfaces:**
- Produces `BuildRequest`, `RestoreRequest`, `ServeRequest`, and `OperationResult` Pydantic models.
- `OperationResult.write(path)` writes stable JSON with `format_version=1`.

- [ ] Write model tests for strict JSON validation, defaults, stable fields, bounded diagnostics, and result serialization.
- [ ] Run `rcc task script -r robot.yaml -- pytest -q tests/test_models.py` and confirm RED import failures.
- [ ] Add Pydantic/pytest/Ruff pins and minimal models.
- [ ] Run the focused test and existing `bash tests/test_cli.sh` to GREEN.
- [ ] Commit `test: characterize JAT Python contracts`.

### Task 2: RCC JSON task surface

**Files:**
- Create: `tasks.py`
- Create: `src/jat/io.py`
- Create: `tests/test_tasks.py`
- Modify: `robot.yaml`

**Interfaces:**
- `load_request(model, argv)` accepts exactly `--json-input PATH`.
- `write_result(result)` writes `<get_output_dir()>/result.json`.
- Tasks `Build`, `Restore`, `Serve`; devTask `Doctor`.

- [ ] Write failing task/robot contract tests using a temporary output directory.
- [ ] Add thin `@task` entrypoints that call placeholder service interfaces and always write a stable result.
- [ ] Validate `robot.yaml` and run focused tests through RCC.
- [ ] Run synthetic RCC Build/Restore requests and inspect result shape.
- [ ] Commit `feat: add typed RCC task surface`.

### Task 3: Python safety and path layer

**Files:**
- Create: `src/jat/safety.py`
- Create: `src/jat/staging.py`
- Create: `tests/test_safety.py`

**Interfaces:**
- `new_output_path`, `existing_file`, `existing_directory`, `empty_destination` validate fail-closed paths.
- `OwnedStage` cleans only its exact operation-owned root on normal exit, errors, and cancellation.
- `validate_archive_members` rejects absolute, traversal, symlink, hardlink, multi-root, and reserved collisions.

- [ ] Port malicious and collision cases as failing Python tests.
- [ ] Implement minimal validation and owned staging.
- [ ] Prove Ctrl-C cleanup and non-empty destination preservation.
- [ ] Commit `feat: port JAT safety invariants to Python`.

### Task 4: Hauler and GNU tar adapters

**Files:**
- Create: `src/jat/process.py`
- Create: `src/jat/archive.py`
- Create: `src/jat/hauler.py`
- Create: `tests/test_adapters.py`

**Interfaces:**
- `ProcessRunner.run(argv, timeout, foreground=False)` records exact argv, exit status, bounded redacted diagnostics, and cancellation.
- `ArchiveAdapter` resolves `gtar`, capable PATH tar, or Linuxbrew keg tar.
- `HaulerAdapter` owns exact store sync/save/load/info/extract/serve argv.

- [ ] Write argv, timeout, redaction, tar-resolution, and failure tests.
- [ ] Implement adapters without shell execution.
- [ ] Run real Hauler/GNU tar smoke.
- [ ] Commit `feat: add Hauler and archive adapters`.

### Task 5: Build and restore services

**Files:**
- Create: `src/jat/services.py`
- Create: `tests/test_services.py`
- Modify: `tasks.py`

**Interfaces:**
- `JATService.build(BuildRequest) -> OperationResult`
- `JATService.restore(RestoreRequest) -> OperationResult`
- `JATService.serve(ServeRequest) -> OperationResult`
- Services compose safety, staging, archive, and Hauler adapters.

- [ ] Write failing build/restore service tests for create-only output, image behavior, validation-before-publication, reserved restore roots, receipts, and cleanup.
- [ ] Implement folder-only build and restore first, then image/Homebrew behavior.
- [ ] Run RCC Build/Restore JSON verticals and real byte/mode/layout acceptance.
- [ ] Add foreground Serve cancellation acceptance.
- [ ] Commit `feat: migrate JAT services to Python`.

### Task 6: Standalone CLI parity and Bash retirement gate

**Files:**
- Create: `src/jat/cli.py`
- Create: `jat` executable launcher or project script metadata
- Create: `tests/test_cli.py`
- Modify: `README.md`, `AGENTS.md`, `robot.yaml`
- Modify/Delete only after parity: `joshs-all-the-things.sh`

**Interfaces:**
- `python -m jat.cli build|restore|serve` and interactive no-argument mode call `JATService`.
- Bash shim, while present, forwards to Python and preserves old argv/exit behavior.

- [ ] Write failing standalone CLI and interactive characterization tests.
- [ ] Implement parser/wizard around shared models/services.
- [ ] Run Python CLI, RCC tasks, real Hauler vertical, and existing Bash tests.
- [ ] Retain Bash as a forwarding shim unless every retirement gate passes.
- [ ] Commit `feat: complete Python CLI migration`.

## Self-review

- Every requested task/result field is assigned to Tasks 1-2.
- Every listed safety invariant is assigned to Tasks 3-5.
- Exact subprocess metadata, timeout, cancellation, and redaction are assigned to Task 4.
- Interactive behavior is isolated to Task 6.
- Bash deletion is explicitly gated by all three real verticals.
