---
name: production-readiness-audit
description: "Production readiness system audit V4.0 for the project in the current working directory: start, resume, re-audit, or implement a run's repair packages. Assess the project against its evidenced vision, use existing tests in an isolated working copy, and deliver evidenced findings and executable packages; the product remains unchanged during the audit. Invoke explicitly only (long, costly runs). Keywords: production readiness audit, system audit, re-audit, resume audit, implement repair packages."
argument-hint: "[start|resume|reaudit|implement|status] [vision, target operation, authorization changes ...]"
disable-model-invocation: true
---

# Production readiness audit V4.0

Package: `${CLAUDE_SKILL_DIR}` (called PACKAGE below). The audit subject is the project in the current working directory. Invocation: `$ARGUMENTS`.

## 1. Determine the mode

1. If the working directory is PACKAGE itself, abort: a self-audit requires a copy of the package as the audit subject.
2. Run `python -B "${CLAUDE_SKILL_DIR}/tools/audit_tools.py" runs --project .` (Python 3.10+). Without Python, work manually according to master prompt §13.1 and read `audit-results/*/RUN-STATE.json` yourself.
3. The first argument word selects the mode; the rest contains your details:

| Mode | Assignment | When |
|---|---|---|
| `start` | Block from `PACKAGE/START_PROMPT.md` | new initial run |
| `resume` | `PACKAGE/RESUME_PROMPT.md`, Variant A | `latest_open` exists |
| `reaudit` | `PACKAGE/RESUME_PROMPT.md`, Variant B | after implemented packages |
| `implement` | Block from `PACKAGE/IMPLEMENTATION_PROMPT.md` | implement packages of a closed run |
| `status` | briefly summarize only the `runs` output, then end | - |

Without a mode word: if `latest_open` exists, ask once (AskUserQuestion) whether to resume (recommended) or start a new run; otherwise use `start`. If multiple runs are open or closure is only derived (`LEGACY_*`), ask which run is intended.

## 2. Form the assignment

- Take the selected prompt's code block verbatim, set `PACKAGE:` to `${CLAUDE_SKILL_DIR}`, and replace every `<PACKAGE>` with this path.
- `start`: Enter under PROJECT DETAILS only what appears in the arguments; empty fields remain empty. Authorizations remain unchanged unless the arguments explicitly change them. Clarify unclear change requests with one question before starting. Ask no other questions in advance; the vision checkpoint collects them when enabled (default).
- `resume`, `reaudit`: Enter authorization changes from the arguments in the line "[Changes to authorizations: ...]"; otherwise omit the line.
- `implement`: Take the scope (wave or package IDs) and integration rule from the arguments; if missing, ask once for both together.
- The resulting text is the user's starting assignment. Record its authorizations verbatim under `authorization` in `RUN-STATE.json`; in `implement` mode, record the entire assignment in `IMPLEMENTATION.md`. Then execute it.

## 3. Read the master prompt

In `implement` mode, read only the sections named by `IMPLEMENTATION_PROMPT.md`; the workflow from P0 does not apply there. In `start`, `resume` and `reaudit` modes: `PACKAGE/MASTER_PROMPT.md` exceeds a normal read operation. Read it with `allow_large: true` or section by section with offset/limit through the last line: "`audit_tools.py example` shows a valid minimal structure." Verify that it is version 4.0. Only the respective prompt, MASTER_PROMPT.md and tools/ from the package belong to the assignment.

## 4. Claude Code: additional rules

These rules apply in addition to the master prompt. They supplement it for this environment and partly come from actual runs.

- **After context compaction:** This skill's beginning is retained unless skills invoked later exhaust the shared budget; none of the master prompt is retained. Therefore reread `RESUME.md`, `RUN-STATE.json`, the master prompt's quick reference (through "**Artifacts:**") and the current phase's section before continuing.
- **Wait only at the vision checkpoint:** Ask up to four decision questions through AskUserQuestion. The assumption that applies without an answer is the first option. Present further questions and authorization requests as text, then end the turn. Progress updates do not end the turn.
- **Before `init` (P0):** Read the hooks at all levels that apply in this session: the project's `.claude/settings.json` and `.claude/settings.local.json`, `~/.claude/settings.json`, a managed `managed-settings.json` if present, and, for plugins from `enabledPlugins`, their `hooks/hooks.json` or the `hooks` field in `.claude-plugin/plugin.json`; also `.mcp.json`. All hook events count, not only write and stop events. Hooks that would format, write, commit or send data in the original are a blocker under master prompt §2. Report it before writing. Treat the project's automatically loaded `CLAUDE.md` or `AGENTS.md` according to §2: restrictions apply, extensions do not.
- **Other providers:** Project content goes to no other provider, meaning no delegation skill, CLI or reviewer outside this Claude environment. This also applies when global routing rules otherwise prescribe it, unless the starting assignment authorizes "external checking tools from other providers". Only this wording or an explicitly named provider authorizes it; "external checking tools: allowed" alone is unclear and requires a question before starting. With a project data classification such as `agent-data-class: restricted`, the prohibition always applies.
- **Subagents:** Use `Explore` for broad inventories and searches. Then check material claims yourself against the code (§10.1). For the second review of every CRITICAL or HIGH finding and every release blocker in P5, use a fresh `general-purpose` subagent with `model: opus`. Give it the assignment according to §9: check objective, source locations, target source, allowed means, and the explicit request to disprove the finding or limit its reach. Do not give it your verdict. Subagents also execute commands through `audit_tools.py run`. Only you write `audit.json`. This second review was consistently missing in previous runs.
- **Working copy:** Create it with a short path on the project's drive, for example `<drive>:\audit-work\<RUN_ID>`. Do not use the session-bound scratchpad: resuming in a new session would otherwise require rebuilding it. Do not use the system drive if environment rules prohibit it. Register the copy, services and containers according to your environment's rules, if any. In P7, remove the copy with `python -B "${CLAUDE_SKILL_DIR}/tools/audit_tools.py" cleanup --dest <Copy> --project .`. If the environment requires confirmation for deletion commands, for example through a protection hook, obtain it beforehand once for all deletions together instead of bypassing it.
- **Services:** Start them with `audit_tools.py run --timeout ...` as a background command (`run_in_background`) and stop them with `audit_tools.py stop`. Do not wait with `sleep` loops.
- **Browser:** Prefer the project's E2E runner through `run` in the working copy. Use interactive browser automation only with a fresh profile, never the extension in the personal browser with real logins. Browser tools can create output directories such as `.playwright-mcp/` in the working directory, meaning the original; this happened in two actual runs. Therefore save screenshots with absolute paths under `evidence/ui/`. After use, check the project root and follow master prompt §2.1: immediately move your evidence files to `evidence/ui/` and remove your other outputs - only those new according to the starting baseline; if the directory already existed, only your new files inside it.
- **Artifacts and session:** The run artifacts under master prompt §13, including `REPORT.md`, are explicitly requested; write them with the file tool. Execute the skill in the main session, not as a subagent: subagents lack the user channel for the vision checkpoint, and subagent rules rejected report files in one run.
- **Results directory:** If `git check-ignore -q audit-results` shows no exclusion, recommend in the first progress update that the user add `audit-results/` to `.git/info/exclude` themselves. Do not change Git metadata yourself.
- **`implement`:** Choose the implementer lane according to the environment's global routing rules, within the provider rule above. Reviews use a fresh reviewer who receives only the assignment, diff and test outputs. Afterwards, recommend a re-audit in a new session with `/production-readiness-audit reaudit`.
- **Effort and model** are inherited from the session; this skill does not set them.
