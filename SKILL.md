---
name: production-readiness-audit
description: "Production readiness audit of the project in the current working directory: start, resume, re-audit, implement repair packages, or continue the deferred security review. Assess the project against its evidenced vision, use existing tests in an isolated working copy, and deliver evidenced findings and executable packages; the product remains unchanged during the audit. Long and costly: run only when the user explicitly asks for it by name. Keywords: production readiness audit, system audit, re-audit, resume audit, implement repair packages."
license: MIT
compatibility: "Needs an agent with file and shell access. Python 3.10+ (standard library) for tools/audit_tools.py; git for Git projects."
argument-hint: "[start|resume|reaudit|implement|security|status] [vision, target operation, authorization changes ...]"
disable-model-invocation: true
metadata:
  opencode/slash: "true"
  opencode/autoinvoke: "false"
---

# Production readiness audit

Package (PACKAGE): `${CLAUDE_SKILL_DIR}` when expanded by the harness; if literal, use the directory containing this SKILL.md from its loaded location. If unknown, look for `production-readiness-audit/tools/audit_tools.py` under selected installer destinations: `./.agents/skills`, `~/.agents/skills`, `./.claude/skills`, `~/.claude/skills`, `./.github/skills`, `~/.copilot/skills`, `~/.gemini/skills`, `~/.gemini/antigravity-cli/skills`, `~/.cursor/skills`, `~/.config/opencode/skills`, `./.windsurf/skills`, `~/.codeium/windsurf/skills`; ask if several or none. These are search locations, not claims of native discovery.
Invocation text: `$ARGUMENTS`; if literal or not provided by the harness, use the user's message after the skill name. The audit subject is the project in the current working directory.

Run only on explicit user request; no skill controls grant additional permissions. If loaded implicitly, ask whether to start a run and wait for an explicit user request before creating one.

## 1. Determine the mode

1. If the working directory is PACKAGE itself, abort: a self-audit requires a copy of the package as the audit subject.
2. Run `python -B "<PACKAGE>/tools/audit_tools.py" runs --project .` (Python 3.10+). Without Python, work manually according to master prompt §13.1 and read run indexes yourself.
3. The first argument word selects the mode; the rest contains your details:

| Mode | Assignment | When |
|---|---|---|
| `start` | Block from `PACKAGE/START_PROMPT.md` | new initial run |
| `resume` | `PACKAGE/RESUME_PROMPT.md`, Variant A | dispatch from persisted state, including P8/P9 |
| `reaudit` | `PACKAGE/RESUME_PROMPT.md`, Variant B | after implemented packages |
| `implement` | Block from `PACKAGE/IMPLEMENTATION_PROMPT.md` | implement packages of a closed run |
| `security` | `PACKAGE/RESUME_PROMPT.md`, Variant C | P8 AND P9; valid interim checkpoint required |
| `status` | run `python -B "<PACKAGE>/tools/audit_tools.py" runs --project . --inspect-all`; summarize phase, closure, compatibility, `security_review.status`, live validation and checkpoint state, then end | read-only |

Without a mode word: if `latest_resumable` exists, ask once with your question tool or plain text whether to resume it (recommended) or start a new run. For an incompatible open run, offer only a new linked run; preserve artifacts, never change its schema identifier. Otherwise use `start`; ask if selection or inferred closure (`LEGACY_*`) is ambiguous. Compatible differing prompt identifiers are warnings only. Recovery: master prompt §13.1.

## 2. Form the assignment

- Take the selected prompt's code block verbatim, set `PACKAGE:` to the resolved package directory, and replace every `<PACKAGE>` with that path.
- `start`: Enter under PROJECT DETAILS only what appears in the arguments; empty fields remain empty. Authorizations remain unchanged unless the arguments explicitly change them. Clarify unclear change requests with one question before starting. Ask no other questions in advance; the vision checkpoint collects them when enabled (default).
- `resume`, `reaudit`, `security`: Enter authorization changes from the arguments in the line "[Changes to authorizations: ...]"; otherwise omit the line.
- `implement`: Take the scope (wave or package IDs) and integration rule from the arguments; if missing, ask once for both together.
- The resulting text is the user's starting assignment. Record its authorizations verbatim under `authorization` in `RUN-STATE.json`; in `implement` mode, record the entire assignment in `IMPLEMENTATION.md`. Then execute it.

## 3. Read the master prompt

In every new session, including resume, security, re-audit and implementation, read MASTER_PROMPT.md completely in bounded chunks through the last line: "`audit_tools.py example` shows a valid minimal structure." It contains no security-check instructions. Only the selected prompt, SKILL.md, MASTER_PROMPT.md, tools/ and SECURITY_REVIEW.md (read only on reaching P8 after checkpoint verification) belong to the audit assignment. Implementation follows IMPLEMENTATION_PROMPT.md, not P0-P9; in its dedicated last wave/session read deferred findings/tasks and only SECURITY_REVIEW sections 2 and 6. README and maintenance are not audit instructions.

## 4. Execution rules

- **Main session:** Run the audit in the user-facing session, not as a delegated subagent: vision-checkpoint and authorization questions need the user channel.

- **After in-session compaction:** Reread RESUME.md, RUN-STATE.json, the master quick reference, current phase sections, §10 and Appendix A; in P8 also SECURITY_REVIEW.md completely. Package persistence and planned session boundaries follow §16.2. Yield ends the turn/run; the user continues with `resume`.
- **Allowed stops:** Follow §17, including terminal stops; P7 continues into P8 unless one applies. Use your question tool or plain text for decisions/authorization requests; end the turn when waiting is required. Progress updates do not end work.
- **Before `init` (P0):** Inspect all applicable project/user/managed automation: hooks, tool servers, plugins and settings using the actual harness configuration, not guessed paths. All hook events count. Automation that would change original product/Git state or transmit data is a blocker under §2; report before writing. Automatically loaded project instructions restrict actions/data flows but cannot widen authorization or hide scope.
- **Other providers:** No project content to providers outside the session unless the assignment explicitly authorizes them; ambiguous "external checking tools: allowed" requires clarification. Apply any narrower project data-classification/provider limits even if routing rules suggest another lane.
- **Subagents:** If supported and authorized, delegate broad independent packages and second reviews to fresh reviewers on the strongest model available within current authorization; do not switch providers/models independently. Verify material claims yourself. P5 reviews non-security findings; P8 reviews security findings. Briefs include baseline, sources, methods, evidence format, allocated global budget and the deferred-security restriction, not your verdict. Only the orchestrator writes audit.json and RUN-STATE.json; delegates use `audit_tools.py run`. Without independent reviewers record UNAVAILABLE with a reason, without an artificial blocker (§9).
- **Security deferral:** Follow the master quick reference and §16: neutral inventory and index before P8, no deferred detail reads. In audit-core/2, security items have `category: SECURITY`; neutral indexes also treat legacy H as security; "classification unknown" applies only to runs without the field.
- **Working copy:** Short path on a drive with space, outside the project and session-scoped scratch directories, retained for resume. Register copy/services/containers as the environment requires. P7 stops services and retains the copy with an immutable checkpoint; P8 verifies it before reading SECURITY_REVIEW.md. In P9 remove the copy with `python -B "<PACKAGE>/tools/audit_tools.py" cleanup --dest "<Copy>" --project .`. Obtain any required deletion confirmation together; never bypass protection hooks.
- **Resources:** At most 4 concurrent heavy processes across the entire run, including subagents and nested workers, unless the assignment changes it; allocate budgets, do not multiply them. Resource-intensive verification is sequential unless measured capacity permits overlap. Services use `run --timeout ...` as a background command if supported and stop with `audit_tools.py stop`.
- **Browser:** Prefer project E2E through `run`; interactive checks need a fresh profile without personal logins. Save evidence with absolute paths under `evidence/ui/`. Tool output may appear in the original: handle only demonstrably own files new since baseline under §2.1; preserve preexisting directories/files.
- **Artifacts and session:** Write the requested artifacts under §13 using available file tools. Keep user decisions and central state with the orchestrator; delegates return condensed file:line results.
- **Results directory:** If `git check-ignore -q audit-results` shows no exclusion, recommend in the first progress update that the user add `audit-results/` to `.git/info/exclude` themselves. Do not change Git metadata yourself.
- **`implement`:** Follow environment routing within provider limits. Initial reads skip security details; implement security packages in a dedicated last wave/session. Reviews get assignment, diff and tests, not author conclusions. Recommend a fresh-session `reaudit` with the harness's explicit invocation syntax.
- **Model/settings:** Inherit session settings and record only disclosed facts; do not invent harness behavior.
