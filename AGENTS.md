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
  (`runtime/hauler.json`, currently v2.0.3) is the implementation authority for
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
