# Resume and re-audit prompts - V4.0

Set the package path in the first line as in the start prompt. Add changed authorizations in the last line; otherwise delete the line in square brackets.

## Variant A - Resume an interrupted run

For a new session after an interruption, context limit or pause. Continue in the existing run directory. "Not closed" means that `closed_at_utc` is missing or `null` in `RUN-STATE.json`. Runs before V3.1 do not have this field; `audit_tools.py runs` classifies them from the audit verdict and `REPORT.md` and labels this as derived.

```text
PACKAGE: ./production-readiness-audit

Resume the production readiness system audit of this project.

Use python <PACKAGE>/tools/audit_tools.py runs to find the latest run that is not closed under ./audit-results/; if the choice is ambiguous, ask. First read its RESUME.md and RUN-STATE.json, then the quick reference and the sections of <PACKAGE>/MASTER_PROMPT.md named there for the current phase - when in doubt, the entire master prompt, section by section if output is truncated.

Before any further check, compare the current project state with the run's starting baseline (python <PACKAGE>/tools/audit_tools.py baseline --compare <Run>/evidence/baseline/baseline-start.json). If it is unchanged, continue in the same run directory. If changes affect confirmed findings or passed checks, mark the affected evidence STALE or UNKNOWN and check it again. If files containing code quotations of open findings or source excerpts have changed, or changes are extensive, create a re-audit run instead and close the open run (master prompt §16).

The authorizations of the original starting assignment stored in RUN-STATE.json apply.
[Changes to authorizations: ...]
```

## Variant B - Re-audit after implemented repair packages

After packages from `tasks/` have been implemented. Create a new run and check the implementation against the current state, not completion claims.

```text
PACKAGE: ./production-readiness-audit

Start a re-audit of this project according to the production readiness master prompt V4.0.

Read <PACKAGE>/MASTER_PROMPT.md in full, section by section if output is truncated. The reference run is the latest closed run under ./audit-results/ (latest_closed in the output of python <PACKAGE>/tools/audit_tools.py runs; if closure is derived or unclear, ask). Create a new run with run_type REAUDIT, retain finding and task IDs with their history, and carry over my documented vision checkpoint answers and the decisions from <ReferenceRun>_implementation/IMPLEMENTATION.md, if present, as dated user sources.

For each package reported as implemented, check the original failure condition, regression tests and affected neighboring journeys against the current state; candidate_complete or DONE claims are not evidence. Then perform the overall integration and vision recheck, check newly added code as in the initial run, and update the audit and release verdicts.

The authorizations of the original starting assignment apply, as stored in the reference run's RUN-STATE.json.
[Changes to authorizations: ...]
```
