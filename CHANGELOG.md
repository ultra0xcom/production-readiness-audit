# Changelog

## 4.0 - 2026-10-14 - English translation

- Translate the complete prompt package, skill, tools, checker, evaluation plan, and documentation to plain English; audit rules and tool behavior are unchanged.
- Rename the prompts to `MASTER_PROMPT.md`, `START_PROMPT.md`, `RESUME_PROMPT.md`, and `IMPLEMENTATION_PROMPT.md`; rename the maintenance directory to `maintenance/` and the evaluation plan to `EVAL_PLAN.md`.
- Replace skill command words with `start`, `resume`, `reaudit`, `implement`, and `status`; translate report headings and implementation artifact paths to `_implementation/IMPLEMENTATION.md`.
- Bump the prompt package to 4.0 and the helper tool to 2.0.0. The English file names, command words, and report headings are breaking changes. Old 3.x runs remain listable; `validate` checks 4.0 reports, warns on other prompt versions, and German 3.x reports fail the English heading checks.
- Add public installation instructions and MIT license reference; remove machine-specific examples and archive dependency from the manifest builder; retain the German 3.2 master prompt hash as a constant.

## 3.2 - 2026-10-11

- Add the explicitly invoked Claude Code skill with start, resume, re-audit, implementation, and status mode selection (`SKILL.md`).
- Correct self-audit 3.2 issues in tool 1.3.0: strip credentials by value (URL credentials, known key formats, private keys) and `NPM_CONFIG__AUTH`, `DOCKER_AUTH_CONFIG`, `GIT_CONFIG_*`; redact known UTF-16 values, reset Git credential helpers and profile pointers under `--isolated-home`, resolve programs only from copy/PATH and record them, stop surviving POSIX groups after identity checks, scan UTF-16 and files over 5 MB, require an authorization list and nonempty `RESUME.md`; reconcile waiting, original-output, verbatim-authorization, superseded-run and implementation-mode rules (tool, master prompt §2.1, §13.1, §16, §17, skill, prompts, mutation probe).
- Use the stable, versionless package directory name `production-readiness-audit` (directory, prompts, README).
- Add `audit_tools.py runs` with open, completed, and inferred-legacy classifications (§16, resume prompt, tool).
- Add an implementation prompt with a branch per package, regression tests, diff review, and progress file (`IMPLEMENTATION_PROMPT.md`).
- Make project instructions restrictive only (§2).
- Inspect agent-environment automations before the first write (§2, skill).
- Check paths leaving the copy and output directories created by agent tools (§2.1, `workspace`, `original_path_refs`).
- Define working-copy placement and marker-protected, non-following, repeatable cleanup, refusing execution from inside the copy and preserving interrupted-run copies (§2.1, §13.1, tool).
- Detect secrets in code quotations and result files: known key formats and private-key material as errors; JWTs, URL credentials, and placeholders as warnings (§10.1, `validate`).
- Extend `run` redaction to sensitive copy-file values, output assignments including symbol passwords and nested values, `Authorization` headers, sensitive `--env`/`--redact` values; keep evidence names, paths, status words, expressions, and credential-free connection targets readable; specify exceptions and leaked-log handling (§2.1, §6, tool).
- Reject GO with a violated T1 journey or open unconfirmed CRITICAL/P0 suspicion; reject current logs older than the baseline and empty final required artifacts (Appendix A, `validate`).
- Correct multiline `envkeys`, parent-repository ignore warnings, inherited `init` exclusions, aborted-run `stop`, grep-style quotation line counting, Unicode and sibling-boundary original-path references, and manifest run-marker exclusion (tool, maintenance).
- Clarify baseline plausibility, copies without Git history, `--isolated-home` criteria, redaction limits, shared-log exit codes, PARTIAL/BLOCKED, violated gates as blockers, and authorization format (§2.1, §3, §6, §10.1, §15, §16, Appendix A).
- Extend structure checks across the whole package: title versions, parsed skill frontmatter, resolved references, tool commands, skill size, and master final line (maintenance).
- Detect hard-aborted `run` and remove its markers with `stop` (§13.1, tool).
- Add evaluation scenarios E30–E39 (`maintenance/EVAL_PLAN.md`).

## 3.1

- Add a quick reference with phases, core rules, section references, and artifacts (master prompt opening).
- Require an isolated working copy, lockfile fidelity, no real secrets in copy/test environment, and protection of shared local resources (§2.1).
- Require decision value for new audit tests (§6.3).
- Assess existing tests as supporting, weak, misleading, etc., with a coverage map (§7.1).
- Check documentation, UI promises and completion claims, including invented packages, APIs and options (§7.2).
- Treat agent-written plans and status files as low-authority sources (§4.1).
- Add a cleanup inventory (§7.3).
- Require verifiable code quotations, verbatim source excerpts, and logs from real command output (§6, §10.1, Appendix A).
- Add `tools/audit_tools.py`: baseline/side-effect comparison, working copies, sanitized environments, evidence logs, and validator with selftest (§13.1).
- Make the start instruction the authorization source; add a vision checkpoint with prework and authorization requests (§0, §4.2, start prompt).
- Extend vision sources to UI text and agent instruction files, with Git history as leads (§3.1, §4.1).
- Add journey tiers T1–T3, S/M/L size profiles with guidelines, and rolling planning (§3.2, §5).
- Give proportionality precedence over "all/every"; make Appendix A the only mandatory field list; permit compact LOW/INFO/CLEANUP findings (§0, §11, §13).
- Define severity, priority and confidence scales, one status vocabulary, and the core data contract (§13.2, Appendix A).
- Add context economy, compaction rules, completion capacity reserve, and continuous package creation (§12, §16).
- Make progress updates continue the working turn (§17, start prompt).
- Start reports with an Executive summary and organize implementation waves (§12, §13).
- Consolidate repeated prohibitions, explain rules, and use a calmer tone (whole prompt).
- Merge `human_tasks` into `tasks` with type HUMAN (§12, Appendix A).
- Keep agent-facing files at the top level, maintenance in its own directory, and archive version 3.0 (package; the archive is not part of this repository).

## 3.0 - 2026-09-29

- Establish the provider-independent audit framework with 18 numbered main sections and 19 domains A–S, from evidenced vision through real checks and repair packages to integrated recheck (master prompt).
