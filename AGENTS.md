# Agent Instructions

Josh's All the Things is an RCC-first Python automation project.

- `src/jat/` owns domain behavior and safety.
- `tasks.py` contains thin `robocorp.tasks` entrypoints.
- `robot.yaml` exposes `Build`, `Restore`, `Serve`, `JAT`, and devTask `Doctor`.
- Hauler remains the capture/restore engine; GNU tar remains the compressed
  archive boundary.
- Keep the standalone `jat` CLI and RCC tasks on the same Python service layer.
- Do not add Dagger, MCP, Action Server, Work Items, RCC Environment Artifacts,
  a daemon, a web service, or a provider framework.

Run Python tests and lint through the declared RCC environment. The Bash
compatibility filename is a thin RCC-to-`jat` shim and must not regain domain
or orchestration behavior.
