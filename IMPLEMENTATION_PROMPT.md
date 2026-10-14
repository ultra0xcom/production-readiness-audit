# Implementation prompt - Implement an audit's repair packages (V4.0)

For work between audit and re-audit: an orchestrating agent implements the packages from `tasks/` of a closed run, wave by wave, then hands over to the re-audit (`RESUME_PROMPT.md`, Variant B). The audit itself never changes the product; this assignment provides the separate authorization for that work.

First adjust: the package path in the first line, the scope (waves or package IDs) and the integration line. The rules in the block come from actual implementations following audits with this package; each includes its reason.

```text
PACKAGE: ./production-readiness-audit

Implement repair packages from the latest closed audit run under ./audit-results/ (python <PACKAGE>/tools/audit_tools.py runs lists the runs; if the choice is ambiguous, ask).

BASIS
Read REPORT.md (Executive summary, implementation waves, HUMAN packages), audit.json (findings, tasks), the task files for the selected scope, and §2, §11, §12 and §6.3 of <PACKAGE>/MASTER_PROMPT.md. Each task file contains the binding implementation assignment; it applies verbatim.

SCOPE AND AUTHORIZATIONS
- Packages: wave 1 [or: all waves in order | package IDs ...]. Do not implement HUMAN packages; ask me about open decisions together.
- Product changes are authorized only for these packages, each in its own branch and worktree outside my worktree.
- Integration into the main branch: only after my confirmation for each wave [or: after passed checks without asking]. No push, no deployment, no changes to remote targets.
- Other authorizations (network, environments, credentials) as in the audit run's starting assignment (RUN-STATE.json), unless I change them here.
- Protection rules as in the audit (master prompt §2), with one exception: branches, worktrees, commits on package branches and integration according to the rule above are authorized for these packages. The audit's workflow rules (§13 through §17, phases from P0) do not apply here. Restrictive project rules such as data classification or excluded providers apply; where rules conflict, the narrower rule applies. Project content goes to no additional provider without authorization, including implementers or reviewers. Secret values and personal data appear neither in commits, tests and outputs nor in IMPLEMENTATION.md; tests use synthetic values.

RULES
1. Establish the starting state. Compare the project state with the audit run's starting baseline (audit_tools.py baseline --compare). Do not commit, move or discard my uncommitted or new changes; ask instead. Reason: in practice, other people's working states otherwise ended up in repair commits.
2. One package, one branch. Packages in a wave run in parallel only if their task files show that they share no files, contracts or data; otherwise run them sequentially.
3. Confirm first, then change. The implementer confirms the defect or hypothesis against the current state before making changes and reports deviations from the audited state. Diagnosis packages (INVESTIGATE) change no product code.
4. Regression tests. Carry over existing audit tests from evidence/audit-tests/. If a test must be adapted to changed code, its assertion remains equally strong; justify the adaptation. Each new or carried-over test fails against the old state at the intended assertion. A countercheck against the state before the package establishes this.
5. Run checks yourself. As orchestrator, run the build, typecheck, tests and affected journeys yourself on the package branch, even if the implementer reports that they passed. Reason: implementer environments often could not start tests, child processes or containers. Test suites do not run in parallel with other resource-intensive runs and use limited parallelism. Label an unstable test as such; do not repeat it until it is green.
6. Review the diff before integration. Changes stay within the authorized scope. There are no weakened tests, gates or error checks and no secrets. Line endings and character encoding remain unchanged; changed line endings and corrupted umlauts occurred repeatedly in practice. For packages concerning security, data integrity, payments or migrations, an independent reviewer also reviews the changes. The reviewer receives only the assignment, diff and test outputs, not the author's rationale.
7. Keep status honest. An implemented package is candidate_complete, not fixed. Corrections run as another round of the same package with a reason. A finding is fixed only after the re-audit.
8. Record decisions. Immediately record my answers to HUMAN packages and open questions, and later authorizations, with date, exact wording, target and limits. The re-audit carries them over as dated user sources. Reason: decisions given in chat were previously lost between implementation and recheck.
9. No bypasses. Without my consent, no --no-verify and no disabling hooks, gates or checking tools. Commits on my main branch occur only according to the integration rule above.
10. Progress. Maintain audit-results/<AuditRun>_implementation/IMPLEMENTATION.md: begin with this assignment verbatim, then record for each package its branch, rounds, checks run with results, review, integration state and open points, plus the "Decisions" section. Update it after each package so a new session can take over. Place no executable files there.

COMPLETION
After the selected scope: overall integration checks against the integrated state, meaning build, full suite and critical journeys; explicitly check interactions between packages. Then report to me the packages with status, open decisions and the re-audit command (RESUME_PROMPT.md, Variant B), preferably in a new session so the recheck does not build on implementation knowledge.
```
