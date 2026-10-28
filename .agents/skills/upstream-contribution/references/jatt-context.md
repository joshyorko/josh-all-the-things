# JATT project context

Use this reference only for `joshyorko/josh-all-the-things`. The reusable
contribution and discovery procedures live in the sibling references.

## Authority and execution

Read [AGENTS.md](../../../../AGENTS.md), [robot.yaml](../../../../robot.yaml),
the selected environment configuration and current runtime manifests:
[Hauler](../../../../runtime/hauler.json) and [RCC](../../../../runtime/rcc.json).
Record actual executable versions and digests; do not use a version remembered
from an earlier run.

Find the available RCC skill router in the current skill catalog, commonly
`rcc:rcc`. Let it select robot/task expertise for project packaging, core
expertise for Environment Artifact lifecycle/cache/trust, or CI-maintenance
expertise for publication and consumer qualification. Keep command recipes in
those skills rather than copying them here. If a specialist is unavailable,
report that gap and follow the repository's declared execution contract.

Python tests and lint run through the declared RCC environment, not host Python.
Inspect the current task/script or acquired-artifact execution route and test
prerequisites before invoking checks. Missing pytest/lint tooling is an unrun
check until the authorized project development/test setup provides it; do not
silently mutate a frozen production environment or substitute host tests.
Native platform checks and exact pinned-binary qualification remain separate
from fixture tests and model revalidation.

## Ownership

| Demonstrated broken behavior | Owner |
| --- | --- |
| Invalid Hauler argv, request/receipt handling, capture/restore or another JATT caller contract | `joshyorko/josh-all-the-things` |
| Independently failing pinned Hauler CLI/store/manifest behavior | `hauler-dev/hauler` |
| Independently failing Josh RCC fork artifact/acquisition/build/execution contract | `joshyorko/rcc` |
| Also reproduced with official RCC, with compatible contribution/licensing policy | Consider `robocorp/rcc` only after that separate reproduction |
| Another library, action, registry or tool | Resolve its owner from the actual manifest, implementation and policies |

A failure through JATT or an incorrect test double does not establish an
upstream defect. Split separately demonstrated caller and dependency defects.
Keep one authorized local tracker until a corrected released dependency is
checksum-pinned and passes JATT qualification.

## Product boundary

JATT owns `src/jat/`; task and Bash adapters stay thin. RCC stays optional to
ordinary JATT use. Hauler owns capture/restore and its servers. This skill adds
no JATT command, runtime dependency, daemon, provider framework, ClawPatch CI
sweep or scheduler. Keep ClawPatch and confidential analysis state external to
the product. JATT remains the canonical skill home for this iteration; moving
it into `joshyorko/plugins` is separate work, with one canonical copy retained.
