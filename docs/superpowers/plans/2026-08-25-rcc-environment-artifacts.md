# RCC Environment Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JAT's custom Hololib distribution with canonical RCC v18.19.3 Environment Artifacts and optionally carry a workspace's RCC environment inside an interoperable Hauler haul.

**Architecture:** Keep `JATService` and the Hauler CLI adapter intact. Add one narrow RCC CLI JSON adapter beside Hauler, store a typed RCC metadata sidecar plus exactly one `.rcca` file artifact in the haul, and verify acquired environments through ordinary `rcc --no-build ht vars` before workspace promotion.

**Tech Stack:** Python 3.13, Pydantic, RCC v18.19.3 CLI, Hauler CLI, pytest, Ruff.

**Spec:** User-approved migration brief in the coordinating Codex task.

## Global Constraints

- Use released RCC v18.19.3 from commit `4148c2b71705c9d2baf0e88b48d08a79cb7bda0f`.
- Do not use `rcc ht export`, `rcc ht import`, RCC Go internals, or native Hauler packages.
- Preserve all legacy Build/Restore, image, Homebrew, create-only, path-safety, cleanup, cancellation, and bounded-diagnostic behavior.
- `rcc_environment=off` is byte-compatible request behavior; `auto` captures only root `robot.yaml`; `required` fails closed.
- Explicit robot descriptors must be regular non-symlink files resolving below the source root.
- An embedded RCC archive must be exactly one validated file artifact and must acquire to its expected digest.
- Restore must verify the exact saved robot-relative path with ordinary `rcc --no-build ht vars --json`; it must never rebuild silently.
- JAT must never archive an active `ROBOCORP_HOME` or Holotree beneath the selected source; fail before archive creation if the resolved active home is at or below the source root.
- Runtime artifact build uses isolated producer/verifier homes, makes the producer home unavailable for verification, and promotes create-only outputs only after verification.
- No public push or downstream Josh Room mutation until JAT unit and real portable/legacy verticals pass.

---

### Task 1: Repair the JAT Environment Artifact implementation

**Files:**
- Modify: `scripts/build_environment_artifact.py`
- Modify: `docs/environment-artifact-receipt.schema.json`
- Modify: `src/jat/models.py`
- Modify: `src/jat/rcc_artifacts.py`
- Modify: `src/jat/services.py`
- Modify: `tests/test_environment_artifact_builder.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_rcc_artifacts.py`
- Modify: `tests/test_services.py`

**Interfaces:**
- Consumes: official RCC JSON fields `artifactDigest`, `specificationDigest`, `legacyBlueprintKey`, acquire result identity, and ordinary `ht vars --json`.
- Produces: stable typed `EnvironmentArtifactMetadata`, durable `rcc-environment-metadata.json`, and create-only verified `jat-runtime.rcca` plus receipt.

- [ ] **Step 1: Add failing tests for runtime builder safety and receipt identity**

  Assert the production RCC default is `rcc`; receipt includes `format_version=2`, `operation`, `success`, exact RCC/JAT/platform identities, archive hash/size, and fresh-home acquire/no-build flags; export targets owned staging and final output remains absent on verification failure; producer home is unavailable before verifier commands.

- [ ] **Step 2: Run focused builder tests and confirm the new assertions fail for the retained implementation**

  Run: `rcc task script --silent -r robot.yaml -- python -m pytest tests/test_environment_artifact_builder.py -q`

- [ ] **Step 3: Implement minimal runtime-builder repairs**

  Default to `rcc`, retain an explicit executable override, export to an owned temporary stage, verify from a fresh consumer after renaming the producer home out of reach, then atomically create the final archive and receipt. Preserve producer evidence without exposing temporary paths.

- [ ] **Step 4: Add failing tests for official RCC JSON, descriptor safety, typed metadata, and strict inventory**

  Assert camelCase parsing, leading `v` version preservation, `verify --json`, symlink/escape rejection, exact source robot-relative metadata round-trip, digest equality, duplicate/unexpected RCC artifact rejection, and no global `exclude_none` serialization drift.

- [ ] **Step 5: Run focused model/adapter/service tests and confirm expected failures**

  Run: `rcc task script --silent -r robot.yaml -- python -m pytest tests/test_models.py tests/test_rcc_artifacts.py tests/test_services.py tests/test_python_cli.py -q`

- [ ] **Step 6: Implement minimal portable Build/Restore repairs**

  Store typed metadata with official identities and archive digest/size, add one stable metadata sidecar and one stable `.rcca` artifact, validate both strictly, acquire and compare the expected digest, verify the exact restored robot path before atomic promotion, and return only durable result values.

- [ ] **Step 7: Run focused tests, full pytest, Ruff, and shell syntax**

  Run focused commands above, then `python -m pytest -q`, `ruff check .`, and `bash -n scripts/build_environment_artifact.sh scripts/publish_environment_artifact.sh` inside the RCC environment.

- [ ] **Step 8: Commit the repaired candidate and write the implementation report**

  Commit only JAT candidate files; restore accidental executable mode changes such as `Brewfile` to `0644`.

### Task 2: Prove real legacy and portable interoperability

**Files:**
- Evidence only under `/home/kdlocpanda/Documents/Codex/2026-08-25/jat-rcc-v18.19.3-evidence/`

**Interfaces:**
- Consumes: repaired Task 1 CLI/task contract.
- Produces: exact command logs, JSON results, inventories, hashes, timings, and restored-file comparisons.

- [ ] **Step 1: Run an unchanged folder-only Build/Restore with official Hauler CLI**
- [ ] **Step 2: Build a synthetic RCC workspace with `rcc_environment=auto`**
- [ ] **Step 3: Verify official Hauler inventory contains the workspace plus exactly one `.rcca` and its metadata sidecar**
- [ ] **Step 4: Restore into a fresh consumer RCC home and prove ordinary no-build resolution and ordinary task execution**
- [ ] **Step 5: Re-acquire warm with provider/network unavailable and retain cache provenance**
- [ ] **Step 6: Record haul sizes with and without the Environment Artifact and all durations**

### Task 3: Build the immutable JAT runtime archive

**Files:**
- Produce: `dist/jat-runtime.rcca`
- Produce: `dist/jat-runtime.json`

**Interfaces:**
- Consumes: final candidate JAT SHA and released RCC v18.19.3.
- Produces: verified canonical archive and publication receipt for immutable GHCR transport.

- [ ] **Step 1: Build with isolated producer/verifier homes and create-only outputs**
- [ ] **Step 2: Verify archive acquire, producer-unavailable ordinary no-build resolution, and a real JAT Doctor or Build**
- [ ] **Step 3: Publish through the existing GHCR transport and record immutable manifest digest, archive SHA-256, size, RCC artifact digest, specification digest, platform, and RCC version**
