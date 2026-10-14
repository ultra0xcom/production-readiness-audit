# Resume and re-audit prompts

Set PACKAGE as in START_PROMPT.md. Omit the bracketed authorization-change line when unchanged.

## Variant A - Resume an interrupted run

Continue a supported open run; inferred closure is labelled by the tool.

```text
PACKAGE: ./production-readiness-audit

Resume this project's production readiness audit.

In every new session read <PACKAGE>/MASTER_PROMPT.md completely in bounded chunks before continuing; it contains no security-check instructions. Assignment files: this prompt, MASTER_PROMPT.md, tools/, SECURITY_REVIEW.md (read only on reaching P8 after checkpoint verification), plus SKILL.md when invoked through the skill.

Use python -B "<PACKAGE>/tools/audit_tools.py" runs --project .; inspect the selected run's live validation, compatibility, checkpoint and resume_guidance (--inspect-all when needed). Prefer latest_resumable; ask if ambiguous. Read RESUME.md and RUN-STATE.json, then only the current package's audit.json records into context. Check live state first under master prompt §13.1. Incompatible historical runs cannot resume: preserve their artifacts and create a new linked audit/re-audit run; never change their schema identifier. A differing prompt identifier on a compatible run is a warning. A live schema mismatch against a compatible checkpoint may be interrupted state; use the recovery guidance, not schema conversion, and warn that restoring loses progress since the checkpoint.

Before further checks compare baseline: python -B "<PACKAGE>/tools/audit_tools.py" baseline --compare "<Run>/evidence/baseline/baseline-start.json". Relevant changes invalidate evidence; changed quotations or extensive changes require re-audit (§16).

Dispatch by persisted state under §16. P7 is finished only when checkpoint verify passes; with an ABSENT checkpoint and no P8 results, complete P7 (validate --final, checkpoint create). With an INVALID checkpoint, do not start or continue P8: report it to the user, keep the run open and offer a new linked re-audit run or a recorded user decision (NOT_PERFORMED). ABSENT after P8 results uses the same safe-stop choices. Interrupted P8 verifies checkpoint BEFORE reading SECURITY_REVIEW.md; unfinished P9 consolidates and closes. Never overwrite a checkpoint. Follow §16.1 for evidenced BLOCKED checks/PARTIAL closure and user-directed NOT_PERFORMED; §16.2 for session plans and yields; §17 for allowed stops. Before P8 the neutral index uses category SECURITY for audit-core/2; neutral indexes also treat legacy H as security; classification unknown applies only to runs without the field, never inferred from titles. After in-session compaction reread quick reference, current phase sections, §10 and Appendix A, plus SECURITY_REVIEW.md in P8.

The original authorizations stored in RUN-STATE.json apply.
[Changes to authorizations: ...]
```

## Variant B - Re-audit after implemented repair packages

Create a new linked run and test the current state, not implementation completion claims.

```text
PACKAGE: ./production-readiness-audit

Start a re-audit of this project.

Read <PACKAGE>/MASTER_PROMPT.md completely in bounded chunks in this new session; it contains no security-check instructions. Assignment files: this prompt, MASTER_PROMPT.md, tools/, SECURITY_REVIEW.md (read only on reaching P8 after checkpoint verification), plus SKILL.md when invoked through the skill.

Use python -B "<PACKAGE>/tools/audit_tools.py" runs --project . to select latest_closed; ask if inferred/ambiguous. If replacing an incompatible open run, preserve it unchanged and link it. Create REAUDIT, retain IDs/history and carry documented vision checkpoint answers and implementation decisions as dated user sources (§16).

Before P8 use the neutral index in §16: audit-core/2 security items have category SECURITY; neutral indexes also treat legacy H as security; classification unknown applies only to runs without the field, never inferred from titles. Skip deferred details; stale quotations become recheck candidates. Recheck each implemented package's original condition, regressions and neighboring journeys; candidate_complete/DONE claims are not evidence. Security packages are reassessed in P8. Follow the master workflow through P7 checkpoint, P8 and P9, with its session boundaries, yields and global budget. After in-session compaction reread quick reference, current phase sections, §10 and Appendix A, plus SECURITY_REVIEW.md in P8.

The reference run's original authorizations stored in RUN-STATE.json apply.
[Changes to authorizations: ...]
```

## Variant C - Security mode: deferred review and closure

Continue P8 and P9 without repeating completed core audit work.

```text
PACKAGE: ./production-readiness-audit

Continue the deferred security review AND required P9 consolidation and closure of this project's open run (security mode).

Read Variant A of <PACKAGE>/RESUME_PROMPT.md now for run selection, live-state-first recovery, compatibility, baseline comparison and authorizations. In every new session read <PACKAGE>/MASTER_PROMPT.md completely in bounded chunks before continuing; it contains no security-check instructions. Assignment files: this prompt, MASTER_PROMPT.md, tools/, SECURITY_REVIEW.md (read only on reaching P8 after checkpoint verification), plus SKILL.md when invoked through the skill.

Refuse security mode without a valid interim checkpoint. If the run has not finished P7, use Variant A to finish P7 first; INVALID checkpoints follow Variant A's safe-stop choices. Run python -B "<PACKAGE>/tools/audit_tools.py" checkpoint verify "<Run>", then compare baseline as in Variant A. Only after verification read SECURITY_REVIEW.md in P8 and resume remaining checks. If P8 is complete and P9 unfinished, consolidate and close under §16.1; if P9 cannot finish, leave the run open with an explicit next step. Apply §16.2 and §17 for session boundaries, budget and safe stops. After in-session compaction reread quick reference, current phase sections, §10 and Appendix A, plus SECURITY_REVIEW.md in P8.

The original authorizations stored in RUN-STATE.json apply; no automatic provider/model change.
[Changes to authorizations: ...]
```
