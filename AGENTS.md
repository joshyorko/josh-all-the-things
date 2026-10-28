# Agent Instructions

Josh's All the Things is an RCC-first Python automation project that packages
portable **capability capsules**: a JAT haul can carry the JAT-owned workspace,
Brew recovery, and optional RCC Environment Artifact anchors plus arbitrary
Hauler content (files, images, charts).

- `src/jat/` owns domain behavior and safety.
- `tasks.py` contains thin `robocorp.tasks` entrypoints.
- `robot.yaml` exposes `Build`, `Restore`, `Serve`, `JAT`, and devTask `Doctor`.
- The standalone `jat` CLI is the canonical public operation contract; RCC
  tasks are compatibility adapters over the same service layer.
- Hauler remains the capture/restore engine and the pinned Hauler release
  (`runtime/hauler.json`, currently v2.1.1) is the implementation authority for
  every Hauler flag and manifest behavior. GNU tar remains the compressed
  archive boundary.
- Keep the standalone `jat` CLI and RCC tasks on the same Python service layer.
- RCC Environment Artifacts (`.rcca`) are a first-class, already-shipped
  feature: an optional compute payload that gives RCC-aware consumers exact
  environment reconstruction. RCC must stay optional to ordinary JAT use.
- Do not add Dagger, MCP, Action Server, Work Items, a daemon, a JAT-owned web
  service, or a provider framework. Hauler's registry and fileserver are the
  only servers; JAT must never grow its own.

Run Python tests and lint through the declared RCC environment. The Bash
compatibility filename is a thin RCC-to-`jat` shim and must not regain domain
or orchestration behavior.

## Upstream dependency defects and contributions

When a real run, integration test, pin update, or build exposes a suspected
third-party defect, **read and follow**
[`.agents/skills/upstream-contribution/SKILL.md`](.agents/skills/upstream-contribution/SKILL.md)
before opening an upstream issue, submitting a patch, or adding a JAT
workaround. The skill is the repository's evidence-first reporting and
follow-through contract, adapted from Bluefin Contribute's upstream-Hive
workflow. Do not silently bury an upstream bug in JAT documentation.

For discovery without a supplied failure, invoke the same skill with
"Run the upstream-contribution skill against JATT." Its discovery reference
uses external ClawPatch tooling and the available RCC skills, then feeds
verified findings into the contribution contract. A supplied finding takes
the direct investigation route. Resume prior native state and evidence rather
than restarting a completed campaign. This is an agent workflow, not a JATT
runtime dependency, CLI feature, scheduler, or CI sweep.

Reproduce and distinguish a JAT caller/integration bug from independently
broken upstream behavior. Search for existing reports; route confirmed Hauler
bugs to `hauler-dev/hauler`, Josh RCC fork bugs to `joshyorko/rcc`, and
other defects to their verified owners. When permitted, file an evidence-backed
upstream report and offer a small upstream PR with a regression test. Track
JAT-impacting upstream fixes until the corrected pinned dependency passes
JAT qualification; an upstream merge alone is not downstream resolution.
Use private vulnerability disclosure for security-sensitive findings.

This policy grants **no new external repository permissions**: honor current
authenticated write scope, approval gates, maintainers' contribution rules,
and the existing no-daemon/no-provider JAT product boundary.

