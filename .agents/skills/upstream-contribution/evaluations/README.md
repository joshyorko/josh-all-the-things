# Offline application scenarios

Use `scenarios.json` as input to a fresh evaluator that loads the skill and its
linked local references. Give it the user requests and raw snapshots only;
withhold this rubric until reviewing its output. Each case is independent.
Ask for entry route, concrete next operations, findings/coverage handoff,
ownership/publication decisions and stopping conditions. The fixture limits
prohibit real scans, tests, network calls and external writes during evaluation.

Run the same inputs against the previous skill and the candidate. Inspect the
actual decisions and artifacts; matching words, headings or frontmatter is not
a behavioral pass. A scenario result is an offline application result, not
evidence that a tool, test, native platform or upstream contribution ran.

| Case | Observable checks |
| --- | --- |
| fresh-bootstrap | With only the short invocation, repository constraints and executable names, select ClawPatch discovery and inspect unknown tool/provider/state facts. Do not stop merely because no defect was supplied or declare unknown coverage a pass. |
| discovery | Resume native state; account for missing modes, failed slices and truncated tails; retain all three IDs while grouping the shared service reproduction and declining the pin-oracle cleanup. Inspect worker context and effective validation commands. No external submission. |
| supplied-caller | Direct investigation, no full scan; caller owns the unsupported flag; verify the focused JATT fix through RCC and pursue the already-authorized JATT PR. No upstream report. |
| fork-owner | Josh RCC fork owns the independently failing fork contract. Reuse the existing report/PR; official RCC is untested. Do not merge for the upstream maintainer or fabricate qualification. |
| confidential | Keep raw evidence/private report/private fix private. A CVE eligibility question authorizes neither a CVE request nor disclosure. Attribute the adapter rather than the independently healthy dependency. |
| resume | Preserve IDs and submissions; validate source B rather than old A. An applied patch and upstream release do not resolve the still-affected consumer pin or tracker. |
| missing-tool | State ClawPatch discovery NOT RUN; no installation/configuration/write permission is inferred. Label any alternative read-only inspection separately, not as a defect-free ClawPatch result. |
| worker-boundary | The read-only lock error is a tool execution boundary, not an RCC defect. Preserve native uncertainty, bind the independent passing check to B, and do not weaken provider security. |
| no-new-defects | Report complete supplied coverage and zero new verified defects without manufacturing patches or issues; do not promise absence of all defects. |

## Evidence levels and limits

Record evaluator context, skill revision, inputs, output and observations in the
PR evidence or a private evaluation artifact. Preserve failures and distinguish
baseline controls that already passed from improvements demonstrated by the
candidate. Supplied tool facts make these scenarios useful for application and
regression checks; they do not independently prove fresh-environment tool
discovery or automatic worker skill inheritance.

Check those boundaries separately against the installed CLI/provider and its
actual prompt/context records. Any JATT Python validation uses the declared RCC
environment. This evaluation creates no scheduler, findings database or tool
integration, and one successful evaluation is not unattended reliability proof.

## Implementation check results

The implementation evaluation used separate fresh-context baseline and
candidate evaluators on the eight inputs. Both retained the existing
contribution controls. The candidate explicitly accounted for native-state
continuation, raw-versus-root-cause counts, incomplete modes/context, effective
feature/global validation commands and separate model/runtime evidence.
This is an application/regression check, not a claim that the baseline agent
could not reason about these facts when the snapshots supplied them.

A separate native CLI transport smoke used an isolated synthetic repository,
heuristic mapping, one reviewed feature and an unfiltered JSON report.
ClawPatch 0.8.4's Codex provider wrapper invoked a capturing stub; the stub
received the `--prompt-file` guidance, correct working directory and read-only
sandbox argument. The prompt's context record exposed a 44,843-byte file capped
at 24,000 included bytes. Effective validation still contained a detected
`pytest` command alongside the configured RCC command, confirming why both
sources need inspection before native fixing.

The smoke executed neither those validation commands nor a real model review.
It does not prove sandbox enforcement or automatic AGENTS/skill inheritance in
Codex. The version and limits above describe the inspected installation, not
requirements for future runs. Native findings/patch state and production code
were not changed outside the new synthetic fixture. Initial smoke-parser and missing-validator-tooling failures
were kept as harness/setup failures, not product findings.

The richer eight-case snapshots did not isolate fresh entry-point discovery,
so a separate minimal-bootstrap comparison withheld findings, tool versions,
provider configuration and native state. The old skill selected "Read-only
intake and ownership investigation" and stopped if no concrete suspected defect
was identified. The candidate selected "ClawPatch repository discovery; no
supplied defect" and planned tool/help/provider/native-state and worker-context
preflight before review. Both retained unknown checks as NOT RUN. The raw
minimal input is included as `fresh-bootstrap` for future evaluations.

This was one baseline/candidate sample per application case, not a statistical
reliability study. Frontmatter validation also passed through declared RCC
Python using external validation-only PyYAML tooling; no production environment
dependency or frozen runtime was changed.
