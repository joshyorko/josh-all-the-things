# ClawPatch discovery recipe

Load for an explicit repository discovery request without a supplied defect, or
to resume a previous discovery run. Use the repository's actual tools and
constraints; there is no required workstation, provider/model, review quota or
finding count. ClawPatch remains external tooling.

## Establish the execution boundary

Read the target's instructions, manifests and available domain skills. Record
its requested revision and working-tree state. Find existing native ClawPatch
state, reports, patch attempts and external submissions before initializing
anything. Keep analysis state/evidence outside the product checkout when
practical, and keep confidential material out of public Git/CI artifacts.

Discover the selected executable with `clawpatch --version` and `--help`; then
read the relevant operation help and current provider/configuration docs. Use
`doctor` to check the chosen provider when available. Prefer an existing usable
installation. Tool installation, provider configuration, network access and
budget changes follow task scope; this recipe does not grant permission for
them. If ClawPatch or its provider is unavailable, report discovery as NOT RUN.
Any authorized alternative investigation is supplemental, not a ClawPatch scan
or evidence that the repository has no defects.

Use native mapping, review, status, finding inspection, triage, patch and
revalidation records. Initialize only when needed; do not force-reset existing
state. A representative command surface, to verify against the installed help:

| Need | Native operation |
| --- | --- |
| Map or inspect coverage | `map`, `status --json` |
| Review | `review`; inspect supported modes and feature/limit/job selectors |
| Export findings and patch information | unfiltered `report --json`, `report`, `show --finding <id> --json` |
| Investigate or patch a lead | `next`, `show --finding <id>`, explicit `fix --finding <id>` |
| Record decisions or recheck | `triage --finding <id> --status ... --note ...`, `revalidate --finding <id>` |

Use the selected root/state/config consistently for every operation. Scope
work by coherent slices and actual budgets. Complete all requested modes and
slices, or account for what remains; do not repeat batches until a desired
number of findings appears. Tool `open-pr` or Git automation, when available,
still follows the contribution contract and current write authorization.

## Verify what workers receive

Loading a skill in the controlling agent is not proof that a ClawPatch worker
received it. Inspect the installed provider's arguments, working directory,
prompt construction and supported context mechanisms. Record which project
instructions/domain guidance were supplied, what was observed at the boundary,
and what remains unverified. Do not claim inspection of a real worker session
from configuration alone.

For versions advertising `review --prompt-file`, an external guidance file can
carry the relevant repository constraints and domain execution instructions.
Inspect the resulting prompt/context manifest or supported request capture to
check inclusion. This supplies explicit text; it does not prove automatic
inheritance of the controller's skill catalog. Use only the context mechanisms
the chosen provider and operation actually support.

Inspect effective validation commands, including feature-level test commands
as well as global configuration. A generic detected `pytest` command can
survive a global override. Align these with the project's authorized execution
route before native fixing. For JATT, load the RCC router and project context;
host-Python checks do not qualify the project. Confirm test-tool prerequisites.
If native fixing cannot safely use the project contract, make the focused
manual patch instead and link it to the original native finding IDs.

A read-only review/revalidation worker may be unable to create RCC/Holotree
locks. Preserve that failed/unrun check and native uncertainty. Independently
validate only through an execution boundary already authorized for those
checks, binding evidence to its actual source. Do not turn a worker restriction
into a product/upstream defect, bypass a denied approval, or weaken provider
permissions, trust or integrity checks to force a green status.

## Account for coverage

Mapping a feature is not reviewing it. Track requested versus completed modes
and slices, failed/unfinished work, rejected/dropped findings and omitted
context. Inspect prompt manifests and file/character budgets. A reviewed slice
may include only file prefixes; increasing file counts does not restore an
omitted tail. Review relevant omissions separately and label that work as
supplemental instead of attributing it to ClawPatch.

Retain source SHA, tool version, provider configuration identity, native run and
finding IDs, requested scope, included/omitted ranges and evidence locations.
A small completed batch does not establish comprehensive coverage. A complete
run with no new verified defects is a valid outcome.

## Investigate and contribute

For actionable leads, read the [contribution contract](contribution-workflow.md).
Reproduce, test attribution, search existing reports and inspect disclosure
policy before external writes. Challenge wrong assumptions about CLI flags,
mock behavior and deliberate safety/test contracts. Group reports only when
reproduction establishes the same root cause; retain every original finding ID.
Keep optional cleanup separate from confirmed behavior defects.

When authorized, use a small regression-tested local fix or the feasible
upstream issue/PR route. Do not stop at a report if the assignment already
includes fixes and contributions. Preserve confidential raw evidence privately
and sanitize public handoffs. A private finding is not permission to publish
its logs, an advisory or a CVE request.

Record reproduction, owner, proposed/applied patch, commands/results and
issue/PR references in native history/notes or linked evidence. Native patch
status means only what the tool actually did; model revalidation is not a test
pass. Review the diff and run the relevant project/runtime checks independently.
Recheck current remote merge/release and consumer-pin state before claiming
resolution. An upstream fix is not consumed until the pinned corrected binary
passes downstream qualification.

## Complete inventory and resumption

Export the complete native findings/patch inventory for the requested scope,
not just one `next` result or a filtered severity report. Include all original
IDs, source/tool/run provenance and coverage gaps. For each actionable group,
link observation, reproduction, owning repository, patch, validation and remote
work. Explain deferred, disproven, already-addressed and still-unverified leads.
Count raw reports, unique confirmed root causes, false positives, unresolved
leads and optional improvements separately.

Use native state as canonical. A derived report may join its IDs with Git and
remote publication evidence; it must not become a second findings database or
rewrite old analysis as if it reviewed a newer revision. Preserve analysis,
validation, patch and release source identities separately. Distinguish
proposed, patched, merged, released and consumed states even when the native
status vocabulary is narrower.

On continuation, inspect those links and current source/pins first. Resume
missing review or targeted current-source validation, reuse matching upstream
reports, and avoid duplicate submissions. Update a tracker only for meaningful
new evidence, not timer-driven reminders. Report skill/tool gaps and what was
independently verified; one successful run does not certify unattended use.

Operational sources: installed CLI help first, then the current ClawPatch
[review](https://github.com/openclaw/clawpatch/blob/main/docs/code-review.md),
[configuration](https://github.com/openclaw/clawpatch/blob/main/docs/configuration.md)
and [provider](https://github.com/openclaw/clawpatch/blob/main/docs/providers.md)
documentation. Check those documents against the executable actually selected;
matching a package version string alone does not establish identical source.
