---
name: upstream-contribution
description: "Diagnose reproducible bugs in JAT dependencies, file evidence-backed upstream reports, submit focused upstream fixes when authorized, and track verification back into JAT. Use for Hauler, RCC, and any other external dependency problem."
---

# Upstream defect reporting and contribution

## Trigger and ownership

**Load this skill** when a JAT test, runtime, release qualification, or dependency upgrade reveals a suspected external bug; before opening or commenting on an upstream issue; before proposing an upstream PR; or when an existing upstream report affecting JAT changes state.

This is an **agent operating workflow**, not a new `jat` command, automation service, crawler, or new grant of GitHub access. Use existing authenticated tools and approvals. Never bypass a connector restriction, request broad repository credentials, or push/merge on behalf of an upstream maintainer.

First decide which project owns the broken behavior. A failure appearing while JAT runs is not proof its dependency is broken.

| Finding | Correct owner / first action |
| --- | --- |
| JAT constructs invalid Hauler argv, exposes unsafe environment, mishandles receipts, filters data, chooses a wrong version, or violates a JAT contract | Fix or file in `joshyorko/josh-all-the-things` |
| Pinned Hauler CLI/store/manifest behavior reproduces **directly in Hauler**, independently of JAT | `hauler-dev/hauler` |
| Josh RCC fork's Environment Artifact, acquisition, build, `env exec`, or pinned fork contract breaks | `joshyorko/rcc` |
| Behavior also reproduced with official RCC, and upstream contribution is appropriate under its licensing/policy | Consider `robocorp/rcc` **after** establishing the separate official reproducer; never assume a fork-only feature exists upstream |
| Other third-party library, action, tool, or registry | Resolve the owner from the actual dependency manifest, source, and current repository policy; do not guess |
| Both upstream deficiency and a JAT integration defect exist | Split evidence and ownership into separately scoped, cross-linked issues |

JAT currently pins **Hauler v2.1.1** in `runtime/hauler.json` and Josh RCC versions/checksums in `runtime/rcc.json`; read the **current files** for every new investigation, not the version written in this skill.

## Evidence gate (before external writes)

1. **Reproduce and isolate.** Capture the smallest failing JAT command and try an equivalent direct invocation of the pinned dependency without JAT. Keep a failing fixture or script that someone else can run; record whether a minimal external reproduction was possible. Use an actual installed/pinned binary; don't confuse a mock/test-double mismatch with upstream failure. When useful, try a clean independent environment or existing test harness to distinguish host-specific behavior from a general defect. A second environment strengthens evidence; it is not required to report an otherwise verified upstream bug.
2. **Identify the exact implementation and conditions.** Record the observed platform/architecture, JAT commit, dependency release, actual binary/source commit and checksum or digest when available, and the runtime configuration needed to understand the failure. Separate **where it was discovered** from **what reproduction has shown matters**: describe concrete conditions such as selected container endpoint, privilege mode, or filesystem behavior rather than workstation names or distro variants unless those are material. Preserve accurate environment details when requested. Quote exact upstream source locations using **commit-SHA permalinks with line anchors**, not mutable branch links.
3. **Capture observations.** Show expected behavior, actual behavior, sanitized output, exit status, and the **UTC date/time** of live probes. Make any claims about current upstream behavior from current source/evidence, not assumptions about another release.
4. **Prove attribution.** Contrast the standalone dependency reproducer with the JAT adapter or RCC task. If direct reproduction fails or ownership is still ambiguous, classify as **unconfirmed** and investigate further or open a local JAT investigation issue—**do not present a suspected upstream failure as confirmed**.
5. **Search and deduplicate.** Search the exact upstream repository's open and closed issues, PRs, releases, and current documentation for matching symptoms and version. If a suitable report exists, add *new* evidence there when appropriate and link it; do not open a duplicate.
6. **Check disclosure route first.** Inspect the target repository's current `SECURITY.md`, `CONTRIBUTING.md`, templates, branch policy, DCO, and agent/AI contribution policies. **Never publish a vulnerability, credential leak, PHI, private customer data, exploit detail, or unredacted token.** Security issues use that project's private reporting channel; follow its instructions before disclosing any details.

## Write authorization

Every GitHub write in this workflow requires authorization for the current task, scoped to the target repository and operation. This includes creating or commenting on upstream issues, opening or updating upstream PRs, commenting on existing reports, and creating or updating the local JAT tracking issue. Credential or tool capability alone is not authorization.

Do not infer authorization for one write from authorization for another. Do not request repeated authorization for calls already covered by current-task authorization. If authorization is absent, prepare the complete draft or patch without writing. If tool approval is required, honor denied or pending approval and do not retry through another tool or route.

## Upstream issue: evidence, not a demand

If a report is warranted and the assignment explicitly authorizes the specific external issue write, file in the confirmed owner repository using its current template. If a tool requests approval, seek that approval; do not rerun or circumvent a denied/pending action. Otherwise prepare a complete draft for an authorized maintainer.

Keep upstream reports focused on the upstream defect. Mention JAT or another downstream consumer only when it adds concrete reproduction evidence or impact context. When it does, give a brief explanation and link to the exact relevant test or implementation using a commit-SHA permalink, rather than describing or promoting the project. A downstream link does not replace an independent upstream reproduction.

Use these sections, adapting to upstream's issue template:

```markdown
## Observed behavior
<Precise failure and observable impact>

## Expected behavior
<Contract/documentation and expected result>

## Reproduction
<Small standalone commands/fixture; state exactly which run is confirmed>

## Environment
- Dependency version, commit and executable digest:
- Observed platform/architecture and relevant runtime conditions:
- Independent environment reproduction (if performed, with result):
- UTC reproduction time:
- Downstream impact (optional, only when useful): <Brief impact and commit-SHA permalink to the relevant test or implementation>

## Evidence
<Sanitized stdout/stderr, exit status, measurements,
 and immutable source permalinks with line anchors>

## Attribution
<Why this is an upstream defect rather than a JAT caller error;
 what has and has not been verified>

## Possible approaches (optional)
<Options/tradeoffs, not an imposed design>
```

Leave upstream labels, assignments, milestones, and prioritization to upstream maintainers. Do not require a change to their roadmap or workaround choice. Never impersonate a maintainer, claim a probe ran when it did not, or fabricate timestamps, versions, permissions, or links.

## When to contribute a PR upstream

An issue is not the end of the workflow **when an upstream patch is feasible**. After checking existing work and target contribution policy:

1. Start from the upstream project's **currently correct base branch** (not an assumed `main`). Prefer the smallest change that solves one independently reproduced issue. Do not wholesale transplant JAT implementation into a dependency or invent a new dependency API solely for JAT.
2. Add an upstream-owned regression test that fails before the fix and passes afterwards. Run the target project's required tests, formatters and checks; report any not run, with reasons.
3. Observe upstream license, contribution rules, DCO `Signed-off-by:` if required, AI/agent disclosure if required, and issue/PR templates. Use issue-closing keywords only if correct for that project and intended by its maintainer.
4. Open an upstream PR only with current-task authorization for that repository and operation, plus any required tool approval; otherwise prepare a clean patch or branch and draft PR text for the maintainer. **Do not force-push others' branches, merge a PR, change upstream triage labels, or self-approve.**
5. Respond to requested changes and new evidence; if upstream says this is expected behavior or rejects the approach, update the JAT issue truthfully. Do not silently relabel it fixed.

## Close the loop in JAT

If the defect blocks or changes JAT behavior, create or update **one** local tracking issue (search first), only with current-task authorization for that JAT repository operation and any required tool approval. It should reference:

- Original JAT reproduction, ownership determination, and sanitized evidence.
- Upstream issue and PR URLs, with state: `observed`, `reported`, `patch-proposed`, `merged`, `released`, `consumed-and-verified`, or `not-upstream`.
- The exact upstream fix commit/release and the JAT dependency pin change, if any.
- JAT unit/integration/real-vertical verification on the **new pinned binary** when consuming an upstream fix; link CI runs and record anything not tested.

**Merged upstream does not mean fixed downstream.** When JAT depends on an upstream fix, keep the local JAT blocker open until JAT consumes the fix and passes its own relevant qualification. The local JAT tracking issue may instead close after a verified JAT-owned fix or a demonstrable no-defect reclassification. Rejection of an upstream report alone does not establish that the JAT failure is solved; investigate and track any remaining JAT defect. Do not presume authority to close an upstream maintainer’s issue. If a report was previously public and its attribution is incorrect, correct or withdraw that attribution by an authorized comment, with current-task authorization for that repository and operation and any required tool approval. Avoid a permanent local reimplementation or silent compatibility shim; any unavoidable short-term mitigation requires an explicitly tracked owner decision, a removal condition, and safety-preserving tests. Never lower signature, integrity, secret-handling, or fail-closed guarantees to hide a dependency bug.

Follow up when there is meaningful new evidence, a maintainer request, an upstream release, or a downstream verification result. Do not post nagging timer-driven comments.

## Final agent handoff

Report the diagnosis and owner; links to the existing/new upstream issue and PR (or why neither was filed); local JAT tracking issue; exact tests and outcomes; and the next dependency version/verification gate. Explicitly distinguish **filed**, **patched**, **merged**, **released**, and **consumed**. If the needed current-task write authorization or tool approval is missing or pending, say so and provide the ready-to-send report or patch without claiming it was submitted.

Reference: [Bluefin Contribute's original upstream-hive contract](https://github.com/projectbluefin/contribute/blob/main/docs/skills/upstream-hive.md). Borrow its evidence-first, respectful follow-up practice; **do not copy Hive's branches, protocols, labels, or DCO requirements** without verifying the current target repository.
