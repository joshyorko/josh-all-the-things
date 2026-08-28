# JAT Hauler Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the official pinned Hauler binary inside the JAT RCC Holotree before freeze and publish a freshly verified Linux amd64 environment artifact.

**Architecture:** A JAT-owned manifest and installer run as RCC's post-install layer. `robot.yaml` selects a generated Linux freeze file carrying that hook; ordinary JAT task execution consumes only environment-owned Hauler/tar/zstd. The artifact proof invokes Hauler through the materialized environment's Python because RCC v18.19.2 resolves bare command names before applying the child PATH, and the launcher requires the resolved binary below `CONDA_PREFIX`. The existing optional Homebrew recovery payload remains data-only.

**Tech Stack:** Bash, Python 3.13, RCC v18.19.2, Hauler v2.0.3, GNU tar/zstd, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-27-hauler-runtime-design.md`

## Global Constraints

- Hauler source is the official `hauler-dev/hauler` GitHub Release only.
- Linux amd64 asset is `hauler_2.0.3_linux_amd64.tar.gz` with SHA256 `6685eb1ba86291566f3694d69a8b7e80c928e5a589853691cccf51b26bc61617`.
- RCC is exactly `v18.19.2` from `43aa8c3f834fc84606fd1e442443fbb224324c40`.
- No Homebrew, `sudo`, root, floating URL, or post-acquire Holotree mutation may be required.
- The selected `environment_linux_amd64_freeze.yaml` must include `rccPostInstall`.
- Optional `--brew` capture/restore behavior remains available as a payload feature.

### Task 1: Add the canonical Hauler installer contract

**Files:**
- Create: `runtime/hauler.json`
- Create: `scripts/install_hauler.sh`
- Test: `tests/test_hauler_installer.py`
- Modify: `tests/test_runtime_dependencies.py` if needed by the existing suite

**Interfaces:**
- `runtime/hauler.json` provides `version`, `platform`, `asset`, `url`, and `sha256`.
- `scripts/install_hauler.sh` accepts no required positional arguments, uses `$CONDA_PREFIX`, and exits nonzero on unsupported platform, checksum mismatch, unsafe archive, failed promotion, or failed `hauler version`.

- [ ] **Step 1: Write tests for manifest fields, checksum failure, and atomic promotion.**
- [ ] **Step 2: Run `python -m pytest tests/test_hauler_installer.py -q` and observe the expected missing-file failure.**
- [ ] **Step 3: Implement the manifest reader and installer with private staging, pre-extraction SHA verification, regular-file checks, and atomic rename.**
- [ ] **Step 4: Run the focused test and confirm the installer contract passes.**

### Task 2: Move Hauler into RCC construction and remove host Homebrew lookup

**Files:**
- Modify: `conda.yaml`
- Modify: `robot.yaml`
- Create: `environment_linux_amd64_freeze.yaml`
- Modify: `archive.py`
- Modify: `tests/test_adapters.py`
- Modify: `tests/test_tasks.py`

**Interfaces:**
- `conda.yaml` declares `rccPostInstall: - bash scripts/install_hauler.sh` and retains environment-owned `curl`, `coreutils`, `tar`, `zstd`, and `bash`.
- `robot.yaml` lists the Linux freeze file before fallback `conda.yaml` and has no Homebrew pre-run installer.

- [ ] **Step 1: Add failing assertions that the Linux freeze carries `rccPostInstall` and `ArchiveAdapter` never invokes `brew`.**
- [ ] **Step 2: Run focused adapter/task tests and observe the retained Homebrew behavior.**
- [ ] **Step 3: Remove the Homebrew branch from `ArchiveAdapter` and the `preRunScripts` installer from `robot.yaml`; add the post-install command.**
- [ ] **Step 4: Build the Linux environment with RCC v18.19.2, generate the freeze file through RCC's artifact directory, and verify its hook and exact dependencies.**
- [ ] **Step 5: Run focused tests and `git diff --check`.**

### Task 3: Extend the canonical artifact proof with Hauler execution

**Files:**
- Modify: `scripts/build_environment_artifact.py`
- Modify: `docs/environment-artifact-receipt.schema.json`
- Modify: `tests/test_environment_artifact_builder.py`
- Modify: `README.md`

**Interfaces:**
- The builder's fresh verifier sequence is `env acquire`, `--no-build ht vars`, then a no-build `env exec --artifact <digest>` Python launcher that locates and runs `hauler version` inside the acquired environment.
- The receipt adds bounded logical-command, launcher, and resolved-under-`CONDA_PREFIX` fields under `verified_hauler` without exposing temporary paths.

- [ ] **Step 1: Add a fake-RCC test that rejects a host-resolved bare command and requires the artifact-Python Hauler launcher and receipt assertion.**
- [ ] **Step 2: Run the focused builder test and observe the missing proof.**
- [ ] **Step 3: Add the minimal command and schema/README updates.**
- [ ] **Step 4: Run builder, model, service, and shell syntax checks through the declared RCC environment.**

### Task 4: Build the immutable artifact and execute the real JAT vertical

**Files:**
- Produce: `dist/jat-runtime.rcca`
- Produce: `dist/jat-runtime.json`
- Evidence: external evidence directory supplied by the run

- [ ] **Step 1: Run the create-only builder with isolated producer/verifier homes.**
- [ ] **Step 2: In a second fresh `ROBOCORP_HOME`, acquire the archive and run the no-build RCC artifact-Python Hauler launcher with no build or post-acquire mutation.**
- [ ] **Step 3: Run real JAT Doctor, Build, Restore, and Serve through the acquired artifact.**
- [ ] **Step 4: Publish the immutable artifact through the existing carrier and a public GitHub Release asset for extension bootstrap, recording both identities and checksums.**
