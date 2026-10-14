# production-readiness-audit

A prompt package and agent skill for auditing your own software project against its evidenced vision and target operation. It uses existing tests and authorized environments in an isolated working copy, records findings with evidence, and produces repair, diagnosis and verification packages. The audit leaves product code unchanged.

This is an audit-and-plan workflow, not deployment authorization, a guarantee of defect-free software, or a benchmark of an agent's detection rate. Implementation requires a separate assignment. The tools check structure and consistency; the agent must also check whether the evidence supports each conclusion.

## Install

```bash
npx skills add ultra0xcom/production-readiness-audit
```

The installer asks which agents to install to. Add `-g` to install globally (user-level) instead of into the current project, or `-a <agent>` (repeatable, e.g. `-a claude-code -a codex`) to choose agents without the prompt; the flags are listed in the table below. The whole package is installed, including the prompts, `SECURITY_REVIEW.md` and `tools/`. The CLI normally links agent destinations to a canonical copy; `--copy` requests copies. A manually placed package must include the whole folder. Tools exclude a project-local package from the baseline and working copy, but project linters and Git still see it; consider a local `.git/info/exclude` entry.

The workflow is portable across capable agents; harness-specific loading and invocation controls are documented as verified, documented-only or unverified. Installer discovery (`skills add --list`) establishes that the installer recognizes the package, not that a harness loads it, invokes it correctly or honors its controls.

## Using with different agents

- **verified**: checked directly in a package artifact or an executed command, including the disclosed smoke tests; applies only to the stated observation, not to untested harness behavior.
- **documented-only**: supported by the cited installer or agent documentation, without an end-to-end invocation test of this package in that harness.
- **unverified**: not established by the available documentation or a direct test; no behavior is promised.

All paths are portable conventions, relative to the project or to the symbolic home directory `~`; append `production-readiness-audit/` to a skill root. **Installer destinations** are where the skills CLI places the package. **Native discovery** is where the harness says it searches; these are separate claims, and a destination need not match every native path.

| Agent | Status | Install flag | Installer project / global root | Native project / global discovery | Explicit invocation | Invocation controls | Subagent support |
|---|---|---|---|---|---|---|---|
| Claude Code | verified — CLI 2.1.299, 2026-10; personal-scope copy loaded; project install not isolated | `-a claude-code` | `.claude/skills/` / `~/.claude/skills/` | Same roots; also plugins, nested directories and added directories | `/production-readiness-audit status` loaded the skill and ran the tool | `disable-model-invocation: true` is documented | Supported in documentation; audit orchestration stays in the main session |
| Codex CLI | verified — CLI 0.159.2, 2026-10 | `-a codex` | `.agents/skills/` / `~/.agents/skills/` | Project `.agents/skills/` discovery verified; cwd-to-repository and home/system roots documented | `$production-readiness-audit status` loaded the listed skill and ran the tool | Implicit invocation is possible; SKILL.md asks before starting a run when loaded implicitly | Supported in documentation when requested or required by instructions |
| Antigravity CLI (agy) | verified — CLI 1.3.5, 2026-10 | `-a antigravity-cli` | `.agents/skills/` / `~/.gemini/antigravity-cli/skills/` | Project `.agents/skills/` verified; legacy `.agent/skills/` and global root documented | `/production-readiness-audit status` loaded the project skill and ran the tool | No opt-out documented; description requests explicit use | Unverified |
| Antigravity app/IDE | documented-only | `-a antigravity` | `.agents/skills/` / `~/.gemini/antigravity/skills/` | `.agents/skills/`, legacy `.agent/skills/` / `~/.gemini/config/skills/` | `/production-readiness-audit` documented | No opt-out documented; description requests explicit use | Unverified |
| Cursor | documented-only | `-a cursor` | `.agents/skills/` / `~/.cursor/skills/` | `.agents/skills/`, `.cursor/skills/`, `.claude/skills/`, `.codex/skills/` and home equivalents | `/production-readiness-audit` in Agent chat documented | `disable-model-invocation: true` documented | Supported in documentation |
| GitHub Copilot (VS Code / CLI) | documented-only | `-a github-copilot` | `.agents/skills/` / `~/.copilot/skills/` | `.github/skills/`, `.claude/skills/`, `.agents/skills/` / `~/.copilot/skills/`, `~/.claude/skills/`, `~/.agents/skills/` | VS Code: `/production-readiness-audit`; CLI: `/skills` documented | VS Code honors `disable-model-invocation: true` in documentation; CLI opt-out unverified | VS Code `context: fork` experimental; CLI unverified |
| OpenCode | verified — CLI 2.0.26, 2026-10 | `-a opencode` | `.agents/skills/` / `~/.config/opencode/skills/` | Project `.agents/skills/` offered through the skill tool; other documented roots: `.opencode/skills/`, `.claude/skills/` and home equivalents | Loading by name through the skill tool ran the tool; slash syntax with `metadata.opencode/slash: "true"` documented | `metadata.opencode/autoinvoke: "false"` documented | Supported in documentation |
| Windsurf | documented-only | `-a windsurf` | `.windsurf/skills/` / `~/.codeium/windsurf/skills/` | `.devin/skills/` preferred, legacy `.windsurf/skills/`, `.agents/skills/` / `~/.codeium/windsurf/skills/` | `@production-readiness-audit` documented | No opt-out documented; description requests explicit use | Unverified |

Verified with status-mode invocation and a tool run in 2026-10 after `npx skills add <package> -a <agent> -y --copy` into throwaway projects. Verification covers only the observed paths/actions; other cells describe documentation or state their limits. Claude Code loaded a personal skill with the same name, so project-copy precedence was not isolated.

For the Antigravity app, global installation uses `~/.gemini/antigravity/skills/`, which differs from documented global discovery at `~/.gemini/config/skills/`; that destination's discovery is unverified, so use a project install or place/link the package in the documented path.

In the Windows Codex smoke test, both read-only and workspace-write sandboxes refused process creation (error 5); Python ran with the sandbox bypassed in a throwaway project.

Installer destinations follow the [skills CLI supported-agents table](https://github.com/vercel-labs/skills#supported-agents). Harness claims follow [Claude Code](https://code.claude.com/docs/en/skills), [Codex](https://developers.openai.com/codex/skills/), [Antigravity](https://antigravity.google/docs/skills), [Cursor](https://cursor.com/docs/context/skills), [Copilot in VS Code](https://code.visualstudio.com/docs/copilot/customization/agent-skills) and [Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills), [OpenCode](https://opencode.ai/docs/skills/), and [Windsurf](https://docs.devin.ai/desktop/cascade/skills). The labels deliberately distinguish documentation from exercised behavior.

Unknown-frontmatter handling and package-directory disclosure remain unverified for several harnesses. Strict skill validators may reject the extra `argument-hint` and `disable-model-invocation` keys. Where a harness cannot load the package, use the skill-free route below. The description's explicit-use instruction is not a technical opt-out in harnesses without a documented control.

## Usage

Use your harness's explicit invocation syntax followed by a mode and any details or authorization changes:

| Mode | Purpose |
|---|---|
| `start` | Create a new audit; supply vision, target operation and authorizations. |
| `resume` | Continue from persisted state, including interrupted P8 or unfinished P9. |
| `reaudit` | Create a linked run to check repairs against the current baseline. |
| `implement` | Separately authorize selected packages or waves and an integration rule. |
| `security` | Resume P8 **and** P9; requires a valid P7 interim checkpoint. |
| `status` | Read-only overview including phase, closure, compatibility, security, live validation and checkpoint state. |

For example, in a slash-command harness: `/production-readiness-audit start Vision: ... Target operation: ...`. Replace the prefix for other harnesses using the table. A run is long and potentially costly; invoke it deliberately. When earlier runs exist, the skill asks before doing anything: resume the open run or start a new one; with only closed runs, re-audit the latest one or start a new independent audit. `start` also asks when an open run could be resumed instead.

For a complete state inspection:

```bash
python -B "<PACKAGE>/tools/audit_tools.py" runs --project . --inspect-all
```

A `LEGACY_*` status means closure inferred from stored artifacts rather than an explicit closure marker; clarify it when uncertain.

Default `runs` inspects the selected run; other runs can report `NOT_CHECKED` or `PRESENT_UNVERIFIED`. Follow `resume_guidance`. Schema-incompatible runs stay discoverable but cannot resume with this package: preserve them and create a new linked audit/re-audit; never convert them by changing the schema identifier. A differing prompt identifier on a compatible run is a warning.

**Skill-free route:** Fill in the package path, project details and authorizations, then copy the block from [START_PROMPT.md](START_PROMPT.md) into an agent session in the project directory. Copy [RESUME_PROMPT.md](RESUME_PROMPT.md) Variant A for resume, B for re-audit, or C for security and closure. Copy [IMPLEMENTATION_PROMPT.md](IMPLEMENTATION_PROMPT.md) for repair packages. Keep [MASTER_PROMPT.md](MASTER_PROMPT.md), [SECURITY_REVIEW.md](SECURITY_REVIEW.md) and `tools/` accessible; security instructions are read only at the designated final phase. Every fresh session reads the master completely in bounded chunks.

## Workflow

| Phase | Work and persisted result |
|---|---|
| P0 Setup | Establish authorizations, capabilities, automation, run selection, baseline and session/resource plan. |
| P1 Vision | Reconstruct the evidenced vision, objectives, invariants and open questions; vision checkpoint when enabled. |
| P2 Inventory and plan | Inventory components, journeys, environments, tests and requirements; create the working copy; register neutral security scope and deferred mandatory checks. |
| P3 Baseline runs | Execute existing builds, checks and functional tests in the working copy; assess discovery and test value. |
| P4 Investigation | Check T1 journeys, non-security risk hypotheses, data, release artifact, implementation and documentation truth. |
| P5 Cross-check | Confirm/disprove non-security findings, verify locations and obtain second reviews; security findings are reviewed in P8. |
| P6 Repair planning | Create packages and implementation waves, cleanup assignments and integration rechecks. |
| P7 Interim closure | Persist a complete interim report, pass final validation, create and verify the immutable checkpoint, stop own services and retain the copy; leave the run open with security PENDING and no GO/COMPLETE. |
| P8 Security review | Verify checkpoint and baseline before reading SECURITY_REVIEW.md; review the inventoried scope and obtain second reviews. |
| P9 Merge and final closure | Consolidate results, gates and waves; validate structure and semantics, compare baseline, clean up own resources and record closure. |

The security review comes last so the complete core assessment is persisted and recoverable before the deferred work begins. Before P8, security-specific execution, output assessment and claim verdicts are deferred; operational safety of the audit remains active. Inseparable suite output is recorded for later assessment. A secret found in product contents is recorded without its value; confirmed immediate harm is reported without further probing.

Read `audit-results/<RUN_ID>/REPORT.md` first, then selected files in `findings/` and `tasks/`. Implementation reports `candidate_complete`; only a re-audit confirms a repair. A refused or interrupted review leaves an open resumable run. Evidenced mandatory BLOCKED checks can lead to PARTIAL closure with no GO. Omitting the review requires a recorded user decision, NOT_PERFORMED and a HUMAN/VERIFY task.

## Context and resources

The P0 session plan follows the size profile: S normally one session; M boundaries after P4 and P7; L after P2, each P4 area group and P7. Each work package records its objective, bounded scope, completion criterion and output. Results and unresolved checks are checkpointed after each package. At context pressure, or a planned boundary when usage cannot be observed, the agent persists an exact continuation and yields for a fresh session.

Fresh sessions read mandatory bootstrap instructions completely; subsequent reads target the current package through `RESUME.md` and `RUN-STATE.json`, without loading the whole `audit.json`. Observations are reusable only while their baseline and relevant inputs remain unchanged. Large logs stay in evidence files; summaries, indexes and delegated claims must be checked against source evidence.

When supported and authorized, delegates receive self-contained bounded briefs, evidence rules, provider limits, the security deferral and an allocated budget. Only the orchestrator writes central state. Without delegates, the same packages run sequentially.

The default **global concurrency budget is at most 4 concurrent heavy processes**, including delegated agents, nested test workers, browsers, containers and long-running services. Delegates do not multiply it. Resource-intensive verification runs sequentially unless measured capacity permits overlap. Use timeouts, a working-copy drive with space, isolated browser profiles and synthetic data; stop resources started for the run.

## Files

| File | Purpose |
|---|---|
| `SKILL.md` | Agent-neutral entry point, mode dispatch and execution rules. |
| `MASTER_PROMPT.md` | Full audit contract, P0–P9, evidence rules and data contract. |
| `SECURITY_REVIEW.md` | Deferred defensive review instructions and P9 handoff. |
| `START_PROMPT.md` | Copyable start assignment and authorization defaults. |
| `RESUME_PROMPT.md` | Copyable resume, re-audit and security-mode assignments. |
| `IMPLEMENTATION_PROMPT.md` | Separate repair assignment and integration rule. |
| `tools/audit_tools.py` | Baseline, working copy, evidence execution/masking, services, cleanup, run inspection, validation, interim checkpoint and selftest. |
| `MANIFEST.json` | Package metadata and byte hashes; excludes itself. |
| `maintenance/check_prompt_structure.py` | Text, vocabulary and package checks with controls. |
| `maintenance/build_manifest.py` | Generate and check hashes and metadata. |
| `maintenance/mutation_probe.py` | Check that selected safeguards' controls reject mutations. |
| `maintenance/EVAL_PLAN.md` | Empirical evaluation plan; not executed. |
| `README.md`, `LICENSE` | User overview and MIT license. |

## Requirements

An agent with file access and authorized shell execution is needed for active checks. Python 3.10+ with the standard library runs the optional helper tools; Git is used for Git projects. Without Python, the master permits an equivalent manual method, with the loss of mechanical checks. Browser automation, containers, subagents and additional checking tools are optional and depend on project needs, availability and authorization. No additional Python dependencies are required.

## Maintainer verification commands

Review portability labels against actual harness observations separately from installer discovery.

```bash
python -B tools/audit_tools.py selftest --out <new-temp-file>
python -B maintenance/build_manifest.py
python -B maintenance/check_prompt_structure.py --output <temp-file>
python -B maintenance/build_manifest.py --check
python -B maintenance/mutation_probe.py tools/audit_tools.py <empty-temp-directory> <mutant-name> ...
npx -y skills add <repo-path> --list
```

Also run selftest with a Python 3.10 interpreter (for example, `py -3.10` on Windows or `python3.10` elsewhere). Use scratch destinations outside the package. The mutation probe checks all target occurrences before running selected mutants; omit names for the full probe, and keep `MUTATION_PROBE_WORKERS` within the global resource budget. `-B` avoids package bytecode caches. Local maintenance reports are regenerated, not shipped. Hashes establish byte identity, not factual truth or model quality.

## Known limits

- The result directory is inside the project. Whole-tree formatters and linters may include its Markdown and JSON files; the validator prevents only executable code and configuration files there.
- `--isolated-home` separates profile credentials, resets Git credential helpers, and removes profile-file pointer variables, but forces new package and browser cache downloads. It does not separate OS credential stores (Windows Credential Manager, macOS Keychain). `XDG_RUNTIME_DIR`, where Podman stores logins, remains because rootless containers need it. Without this option, working-copy tools may read profile credentials such as `~/.aws`.
- `run` strips variables by name and value (URL credentials, known key formats, private keys). Credentials in another format under an inconspicuous name, such as Base64 in `MY_SETTING`, still reach the test process; use `--redact` and follow §6.
- Result files contain absolute project and working-copy paths, often including account names on Windows. Review them before sharing.
- `run` resolves programs itself and refuses files in the original. With `--shell`, the shell resolves them and this check does not apply.
- Baseline comparison records ignored files only by size and timestamp and summarizes large dependency directories. Changes by the user's concurrent tools (development server, IDE) also appear as side effects and require explanation.
- Background-process cleanup by an agent environment depends on its tooling. If `run` itself is killed, for example by an agent timeout, `.meta.json` is missing and the log is not evidence. The Windows job object also ends the command. On Linux and macOS, the process group continues; `stop` ends it after checking start time and process group, and removes orphaned markers. If identity cannot be confirmed, the process remains and `stop` reports this. On macOS, without `/proc`, only the process group is checked. An unverified edge case is that a Windows child started before job assignment might escape the job.
- Validator secret detection recognizes common key formats, private keys, and URL credentials in UTF-8/UTF-16 files of any size. Encoding is determined from the first 8 KB; UTF-16 appended after UTF-8 in one file is not scanned as UTF-16. JWTs and URL credentials produce only warnings. Free-text passwords, unknown token formats, and encoded values can escape detection; §10.1 still applies.
- `run` redacts sensitive environment values, sensitive assignments discovered by `workspace`, output assignments (including inside other values and code expressions, singly escaped double-quoted JSON, after `=>`, YAML tags, and the first line of YAML block values), `Authorization` headers, URL credentials, and known key formats. Known values are also redacted in UTF-16; other patterns only in UTF-8 and the system encoding. A colon without following space counts only at line start (`password:...`, `spring.datasource.password:...`, at most 64 indentation characters), in Go/map output (`{Host:db Password:...}`, `map[password:...]`), and in quoted headers with hyphenated names (`-H "X-Api-Key:..."`, not `-H "apikey:..."`). Space before a colon counts only at line start or before a quoted value. Closing brackets at an unquoted value's end remain when closing brackets opened before the name (`{Password:...}`); a password itself ending with that bracket leaves the character visible. A closing bracket in a previous value (`{msg:"}" password:...}`) disables recognition of subsequent lowercase fields. Doubly escaped JSON (`\\\"password\\\"`) and escaped single-quoted JSON are not recognized. Encoded (Base64, URL-encoded), split, or unknown-format values remain; follow §6. Also unrecognized: internally spaced/quoted values not quoted as a whole (`password: my secret phrase`), whitespace-only assignments (`key value` in `.properties`), mid-line colons without following space or with preceding space (`INFO loaded db.password : ...`), digit-initial values after a tight colon, name/value pairs in separate fields (Kubernetes `name:`/`value:`, ECS/SSM JSON, XML `key=... value=...`), tables, values under four characters, and names not classified as sensitive (such as `auth`). Log lines such as `WARNING:app.token:...` and Spring's `c.a.TokenService : ...` therefore do not count as assignments. To preserve readable evidence, status words (`done`, `expired`), placeholders (`${API_KEY}`, `<token>`), code expressions (`get_token(user)`, `settings.API_KEY`), descriptive-name values (`password_policy=strict`, `token_type=Bearer`, `routing_key=orders.created`), numbers, URLs under connection names (`DATABASE_URL`, `*_URI`), and file paths remain visible if no part looks like a generated token and the value is at most 200 characters. Numbers and paths after password names are redacted. A password looking exactly like an exception would remain visible. Values from copy files are redacted everywhere only if at least eight characters with two character classes, or a passphrase under a password name of at least twelve characters; shorter/simple values only in assignment form. Sensitive `--env` values under four characters are redacted only in `.meta.json`, not logs. Conversely, harmless values such as pytest IDs and Go `got`/`want` lines are sometimes hidden.
- `original_path_refs` finds only literal original paths, including forward slashes, JSON escapes, and drive mount forms using the original drive identifier. Constructed paths (`path.join(HOME, ...)`) require reading scripts.
- `cleanup` recognizes junctions, symlinks, and mount points and removes them without following them. Runtime behavior on Linux and macOS is not established here.

- Neutral wording cannot guarantee that project content never triggers model behavior.
- The empirical evaluation plan, including warning/model-switch scenario E40, has not been executed. Text checks and tool controls do not measure defect detection, false positives or harness compliance.
- Validator checks cannot authenticate screenshots, prove causal chains, prove inventory completeness or prevent deliberate metadata forgery. Final semantic review remains necessary.
- `COMPLETE_FOR_DEFINED_SCOPE` is completion of the defined program, not freedom from defects; `GO_FOR_DEFINED_SCOPE` is neither a global security guarantee nor deployment authorization.
- Prompt instructions require real file, target, network and permission boundaries; text alone does not establish them.

## License

MIT; see [LICENSE](LICENSE).
