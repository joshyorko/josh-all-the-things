---
name: upstream-contribution
description: Use when asked to discover repository defects with ClawPatch, run this contribution workflow against a repository, investigate a supplied failure or finding, or follow a dependency fix through downstream verification.
---

# Defect discovery and upstream contribution

Discover real defects, establish ownership, and carry authorized fixes and
reports through verification. Findings are leads, not proof of a dependency bug.

## Choose the entry point

- **Supplied failure or finding:** Read the [contribution contract](references/contribution-workflow.md)
  and investigate directly. A targeted investigation does not require a
  repository-wide ClawPatch run.
- **Repository discovery request without a supplied defect:** Read the
  [ClawPatch discovery recipe](references/clawpatch-discovery.md). This includes
  an invocation such as “Run the upstream-contribution skill against JATT.”
  Feed actionable findings into the same contribution contract.
- **Continuation:** Reuse native state, finding IDs and linked evidence. Check
  the source revision now requested; resume missing work or targeted validation
  instead of restarting the campaign or duplicating submissions.

For JATT, also read [project context](references/jatt-context.md). For another
repository, derive its owners, execution environment and product boundaries from
its own instructions and manifests. Do not carry JATT assumptions into it.

## Shared boundaries

Use the current task's authorization and the target's contribution/security
policies. This skill grants no new external permissions and introduces no
second approval policy. Continue already-authorized fixes and contributions;
do not ask again for operations already covered. Honor denied or pending tool
approvals. Never merge on behalf of an upstream maintainer or weaken integrity,
trust, secret handling or fail-closed behavior to make a check pass.

Keep confidential evidence private and produce a sanitized public handoff.
Fixing code, publishing an advisory and requesting a CVE ID are distinct actions.
A question about CVE eligibility does not authorize a request or disclosure.

## Handoff

Report scope, source/tool identities, complete findings and patch inventory,
reproduction, owner, validation and contribution links, including gaps and
remaining downstream gates. Preserve original IDs when grouping demonstrated
root causes. Distinguish raw reports, confirmed defects, false positives,
unresolved leads and optional improvements. Separate proposed, patched, merged,
released and consumed-and-verified states; neither a tool's `fixed` status nor
an upstream merge proves downstream resolution. No new verified defect is a
valid result. Record skill/tool gaps without claiming unattended reliability.
