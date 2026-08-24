# Agent Instructions

Josh's All the Things is an RCC-first Python automation project.

- `src/jat/` owns domain behavior and safety.
- `tasks.py` contains thin `robocorp.tasks` entrypoints.
- `robot.yaml` exposes `Build`, `Restore`, `Serve`, `3tc`, and devTask `Doctor`.
- Hauler remains the capture/restore engine; GNU tar remains the compressed
  archive boundary.
- Keep the standalone `3tc` CLI and RCC tasks on the same Python service layer.
- Do not add Dagger, MCP, Action Server, Work Items, RCC Environment Artifacts,
  a daemon, a web service, or a provider framework.

Run Python tests and lint through the declared RCC environment. Keep the Bash
compatibility program only until its remaining callers have migrated and the
Python CLI, RCC tasks, and real Hauler vertical all pass.
