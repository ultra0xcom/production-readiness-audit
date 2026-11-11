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

`runs` reports each run as `OPEN`, `CLOSED`, `PROBABLY_CLOSED`, `CORRUPT` or `LEGACY_*`. A run listed as `CLOSED`, `PROBABLY_CLOSED` or `LEGACY_CLOSED_INFERRED` is never resumed or restored (see CORRUPT below). `PROBABLY_CLOSED` means RUN-STATE.json is lost while `audit.json` holds a finished security review and a final verdict: the run is not resumable by default, its interim checkpoint is never restored over it, and it is no closed base for a re-audit until the user confirms it. `resume_guidance` names the user's two branches: if the run was closed, restore RUN-STATE.json from a backup or start a linked run; if it continues, rebuild RUN-STATE.json, then validate and `close`. A run closed with `close --superseded-by` is marked `closure_superseded` with `superseded_by_run` and carries a note; it is never reported as `latest_closed`, because its verdict was never validated and its successor is the closed base.

Tool commands, each with `--help` for its options (`python -B "<PACKAGE>/tools/audit_tools.py" <command>`):

| Command | Purpose |
|---|---|
| `init` | Create the run directory with a unique run ID, capture the start baseline and write a valid `audit.json` skeleton. |
| `runs` | List runs with compatibility, phase, closure, security review, live validation, checkpoint state and `resume_guidance`. |
| `baseline` | Capture project state or compare it with an earlier state, including side effects. |
| `workspace` | Create and check the isolated working copy. |
| `run` | Execute a command in the working copy and save the complete log with metadata as evidence. |
| `stop` | Stop an active `run`, such as a service started for end-to-end tests. |
| `cleanup` | Remove a working copy, only with its marker, without overlap with the original and without following links. |
| `envkeys` | Show keys and value shapes of `.env` files without printing values. |
| `validate` | Check `audit.json` against the data contract; `--final` also checks artifacts and report contents. |
| `checkpoint` | `create`, `verify`, `restore` or `reanchor` the immutable interim checkpoint; only `restore` and `reanchor` take `--dry-run`. |
| `close` | Record closure after repeating the final validation, or supersession by a linked run with `--superseded-by`; prints the number of `closure_review` entries. |
| `selftest` | Run the tools' controls; prints a summary, and `--out <new-file>` writes the full report. |
| `example` | Print a fictional `audit.json`; `--variant` selects a GO, interim or no-go template. |

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

`init` gives the run directory a valid `audit.json` skeleton, and the registers grow from it; `validate` runs after every work package. In P2 the agent pre-registers the check program as `NOT_RUN` checks, each stating a result that can change a claim, finding or gate. Every T1 journey gets a check of its own beyond a static or unit check. That check is executed with qualifying evidence, is BLOCKED with evidence of the failed attempt, or is deferred by a recorded user decision; a check shared with other journeys does not count, and the final validation before the interim checkpoint refuses the run otherwise. Context pressure, time and convenience never justify closing: the agent yields and continues in a new session. `close` records the closure after repeating the final validation. A thorough audit takes correspondingly longer and can span several sessions.

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
| `tools/audit_tools.py` | Baseline, working copy, evidence execution/masking, services, cleanup, run inspection, validation, interim checkpoint, closure and selftest. |
| `MANIFEST.json` | Package metadata and byte hashes; excludes itself. |
| `maintenance/check_prompt_structure.py` | Text, vocabulary and package checks with controls. |
| `maintenance/build_manifest.py` | Generate and check hashes and metadata. |
| `maintenance/mutation_probe.py` | Check that selected safeguards' controls reject mutations. |
| `maintenance/EVAL_PLAN.md` | Empirical evaluation plan; not executed. |
| `README.md`, `LICENSE` | User overview and MIT license. |

## Requirements

An agent with file access and authorized shell execution is needed for active checks. Python 3.10 or newer with the standard library runs the optional helper tools; the agent tries `python`, then `python3`, then `py -3.10` or newer. On an older interpreter the tool prints `audit_tools.py requires Python 3.10 or newer, found <version>; run it with python3 or py -3.10.`, exits with code 2, and the agent tries the next one. Git is used for Git projects. Without Python, the master permits an equivalent manual method, with the loss of mechanical checks. Browser automation, containers, subagents and additional checking tools are optional and depend on project needs, availability and authorization. No additional Python dependencies are required.

## Maintainer verification commands

Review portability labels against actual harness observations separately from installer discovery.

```bash
python -B tools/audit_tools.py selftest --out <new-temp-file>
python -B tools/audit_tools.py selftest --group <NAME> --out <new-temp-file>
python -B maintenance/build_manifest.py
python -B maintenance/check_prompt_structure.py --output <temp-file>
python -B maintenance/build_manifest.py --check
python -B maintenance/mutation_probe.py tools/audit_tools.py <empty-temp-directory> <mutant-name> ...
npx -y skills add <repo-path> --list
```

Also run selftest with a Python 3.10 interpreter (for example, `py -3.10` on Windows or `python3.10` elsewhere). Use scratch destinations outside the package. `selftest` prints a summary with the control counts and the failed and skipped controls; `--out` writes the full report to a new file. `--group NAME` (repeatable) runs only the named control groups; a partial run says so and is no selftest result for the tool. The mutation probe checks all target occurrences before running selected mutants; omit names for the full probe. It first runs the complete selftest on the unmutated tool, which must pass, then runs each mutant only against the selftest group of its control; a mutant that survives there runs the complete selftest before it counts as survived. `MUTATION_PROBE_FULL=1` runs the complete selftest for every mutant, and `MUTATION_PROBE_WORKERS` (default 4) sets the number of parallel selftests and stays within the global resource budget. `-B` avoids package bytecode caches. Local maintenance reports are regenerated, not shipped. Hashes establish byte identity, not factual truth or model quality.

## Known limits

### Scope and verdicts

- The result directory is inside the project. Whole-tree formatters and linters may include its Markdown and JSON files; the validator prevents only executable code and configuration files there.
- Result files contain absolute project and working-copy paths, often including account names on Windows. Review them before sharing.
- Neutral wording cannot guarantee that project content never triggers model behavior.
- The empirical evaluation plan, including warning/model-switch scenario E40, has not been executed. Text checks and tool controls do not measure defect detection, false positives or harness compliance.
- Validator checks cannot authenticate screenshots, prove causal chains, prove inventory completeness or prevent deliberate metadata forgery. Final semantic review remains necessary.
- `COMPLETE_FOR_DEFINED_SCOPE` is completion of the defined program, not freedom from defects; `GO_FOR_DEFINED_SCOPE` is neither a global security guarantee nor deployment authorization.
- Prompt instructions require real file, target, network and permission boundaries; text alone does not establish them.

### Integrity and closure

- Checkpoint verification and the closure receipt detect accidents and careless repairs, not a party that rewrites all files consistently. Hand-forged logs and metadata cannot be detected. The `closure_review` list exists so that a reader judges what the validator cannot.
- The closure review lists every failed run record it finds below the run's `evidence/` directory that no other entry shows (runs cited by a check or finding first; the first 50 listed, the rest counted). A failed record written outside `evidence/`, or deleted or moved out of the run directory before the interim checkpoint, leaves no trace: there is no ledger of runs. `run` never overwrites an existing record (a repeated ID gets a numbered name). The review checks the register against the records; it does not guard against an agent that removes its own evidence.
- A re-anchored checkpoint shows as the warning `W_CHECKPOINT_REANCHORED` and in the output of `close` and `runs`, not as a `closure_review` entry. REPORT.md must say so by hand.
- A closed run whose RUN-STATE.json and audit.json are both unreadable is listed as `CORRUPT` and can be restored to its interim state (the damaged files are kept in `recovery-*`), so the user decides whether it had been closed.
- A run of an earlier prompt (`prompt_version` in audit.json below 5.1 or not readable, no checkpoint of a tool from 3.1 on) whose RUN-STATE.json has no `tool_version` is not held to the closure receipt and anchor rules; its checkpoint is verified against the manifest alone, with a note. For any other run, a RUN-STATE.json without `tool_version` is an error (`E_RUN_STATE_TOOL_VERSION`): add the field and keep every other field; run `checkpoint reanchor` only if the anchor is missing.
- `checkpoint create` prints the verification result only; before P8, `create` moves a checkpoint that does not verify to `checkpoints/invalid-<UTC>/` and writes a new one. The next step is P8.

### Process control

- Background-process cleanup by an agent environment depends on its tooling. If `run` itself is killed, for example by an agent timeout, `.meta.json` is missing and the log is not evidence.
- Windows: `stop` and timeouts terminate the command's process tree through the job object at once. No console control event is sent and there is no grace period; without a job object only the command and its current process tree are terminated. If `run` is killed, the job object also ends the command; if no job object could be created and `run` itself was killed, `stop` ends only the command process. A process that something else starts on the command's behalf, such as one created through WMI or a scheduled task, leaves the job and is not ended. A Windows child started before job assignment might also escape the job; this edge case is unverified.
- Linux and macOS: `stop` and timeouts send SIGTERM to the command's process group and SIGKILL if it is still alive five seconds later. A descendant that starts its own session or process group (`setsid`, a daemonizing service) leaves the group and is not ended. If `run` is killed, the process group continues; `stop` ends it after checking the start time (from `/proc`, otherwise from `ps`) and the process group, and removes orphaned markers. If identity cannot be confirmed, the process remains and `stop` reports this.
- Container stacks need their own down command in the working copy: terminating the client does not remove containers. `run` sets its own Compose project name.
- A Windows batch file (`.cmd`, `.bat`) runs through `cmd.exe`, which re-parses its arguments. Arguments with blanks or `& | < > ^ ( ) , ; =` are passed quoted. An argument with `%`, `"`, `!` or a line break is refused and nothing is started, as is a program path with these characters. A batch file that forwards single parameters (`%1` to `%9`) also refuses `& | < > ^ ( )`. A command line over 8191 characters is refused. Call the underlying program directly or use `--shell` deliberately.

### Paths into the original

- `run` resolves programs itself and refuses a program in the original. It refuses a command where the original's path occurs in an argument, an `--env` value or the `--shell` string, or where a part of them (split at quotes, `=`, commas, list separators and, in a `--shell` string, blanks) resolves into the original from the working directory, also through a link. With `--shell`, the string is split on a best-effort basis and variables are not expanded. A string with more than 8192 distinct parts is refused.
- Inherited environment values that point into the original are removed or shortened, and the meta file lists them (`stripped_env`, `shortened_env`). Every path in them is resolved: under WSL, where PATH holds many Windows directories below `/mnt`, this adds seconds to each `run`.
- Not seen by this guard: a path that the command assembles itself (from variables, configuration files or code, such as `path.join(HOME, ...)`) and a string the tool cannot split into parts (other encodings, shell expansions). `original_path_refs` of `workspace` finds only literal original paths, including forward slashes, JSON escapes and drive mount forms using the original drive identifier; scripts that construct paths require reading.
- A link or junction into the original that something other than `workspace` creates inside the working copy lets a command reach the original through a string the guard cannot split, for example parts joined by `@`. `workspace` never creates such a link, and the agent must not create one.
- `cleanup` removes junctions, symlinks and other links first, without following them. Mount points are found on Linux from the mount table and by device, on macOS by device; on Windows a mounted folder is a reparse point and is removed as a link (its target is not touched). Mount points and entries that cannot be listed or classified stop it before anything is removed; unmount them or make them readable, then run it again. The selftest simulates mount tables; runtime behavior on Linux and macOS is not established here.

### Environment and profile

- `--isolated-home` separates profile credentials, resets Git credential helpers, and removes profile-file pointer variables, but forces new package and browser cache downloads. It sets `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA` and the `XDG_*` directories. Runtimes that ask the operating system for the profile directory directly still see the real profile: .NET was confirmed; JVM-based build tools were not verified. It does not separate OS credential stores (Windows Credential Manager, macOS Keychain). `XDG_RUNTIME_DIR`, where Podman stores logins, remains because rootless containers need it. Without this option, working-copy tools may read profile credentials such as `~/.aws`. Temporary directories (`TEMP`, `TMP`, `TMPDIR`) are not redirected; pass all three with `--env` for a separate temporary area.
- `run` strips variables by name and value (URL credentials, known key formats, private keys). Inherited credential variables are recognised by name patterns and value shapes. A credential under a name no pattern knows (for example `CI_JOB_JWT` with a value that is not JWT-shaped, or a `CLOUDINARY_URL` without a credential in its value) or in another format under an inconspicuous name, such as Base64 in `MY_SETTING`, still reaches the test process; use `--redact` and follow §6.

### Masking by value

- A recorded value (sensitive environment values, `--env`, `--inherit` and `--redact` values, and values of credential files in the copy) is masked wherever it occurs when it has twelve or more characters and is not letters only. A shorter or letters-only value is masked only as a whole word, not inside longer words. `run` prints a note naming such values and records it in the meta file; the notes are built only for `--env`, `--inherit` and copy-file (env-file) values. A value given with `--env` is masked from six characters on under any name, from four characters under a credential name or with `--redact`; sensitive `--env` values under four characters are not masked in the log.
- A credential that must appear in an argument is also given with `--env`: it is then masked in the recorded command, the log and the output. An option value under a credential name (`--password X`, `--api-key=X`, `pass:X`) is masked from four characters on without it; a credential in another argument, such as `curl -u user:secret`, is not.
- Values of withheld or recorded credential files are masked as the copy holds them at run time, from eight characters on (placeholders such as `changeme` are skipped, URLs and paths only with a generated part); shorter or simple values are masked only in assignment form. An agent that rewrites such a file in the copy with synthetic values should do so before the first run.
- A value is also masked in these spellings: JSON (with its escapes), Python repr and URL encoding; for values up to 4096 characters also JSON `\uXXXX` escapes, lower-case URL encoding, HTML and XML entities, and base64, base64url or hexadecimal of the whole value; known values also in UTF-16 and in the console code pages. Shell, PowerShell, cmd and YAML re-quoted or escaped renderings of a value that contains quotes or special characters are not matched.
- A secret embedded in a larger encoded string (the Base64 of `user:password` outside an `Authorization` header, a value inside a Base64 blob) is not recognised.
- Known token formats of common providers (source hosting, package registries, payment, cloud, chat and AI services), JWTs and age secret keys are masked, as are URL credentials, `Authorization` headers and private-key blocks. Other token formats are not.
- A private-key block (PEM, PGP, ssh.com armor, in any case) is masked from its BEGIN line through its END line, and PuTTY private lines after `Private-Lines`. A block ends after five consecutive ordinary lines, so a key body separated from its BEGIN line by ordinary lines stays readable. Key material without any marker (bare Base64 or hexadecimal dumps) is not recognised. JWK private members are recognised in JSON only, not in Python repr or YAML output.
- An output line over 1 MiB is cut with a `[LINE CUT: <n> bytes omitted]` marker; the meta file counts the cuts.

### Masking of assignments in output

- `run` redacts sensitive assignments in output, including inside other values and code expressions, singly escaped double-quoted JSON, after `=>` and after YAML tags. Patterns work on ASCII-compatible text and on UTF-16 lines of mostly Latin text; other encodings are not decoded for them. The first line of a YAML block value (`key: |`) is masked in output only when its value is also a recorded value of a file in the copy; each output line is judged alone.
- A colon without following space counts only at line start (`password:...`, `spring.datasource.password:...`, at most 64 indentation characters), in Go/map output (`{Host:db Password:...}`, `map[password:...]`), and in quoted headers with hyphenated names (`-H "X-Api-Key:..."`, not `-H "apikey:..."`). Space before a colon counts only at line start or before a quoted value. Log lines such as `WARNING:app.token:...` and Spring's `c.a.TokenService : ...` therefore do not count as assignments.
- Closing brackets at an unquoted value's end remain when closing brackets opened before the name (`{Password:...}`); a password itself ending with that bracket leaves the character visible. A closing bracket in a previous value (`{msg:"}" password:...}`) disables recognition of subsequent lowercase fields.
- Doubly escaped JSON (`\\\"password\\\"`) and escaped single-quoted JSON are not recognized. Encoded (Base64, URL-encoded), split, or unknown-format values remain; follow §6.
- Also unrecognized: internally spaced/quoted values not quoted as a whole (`password: my secret phrase`), whitespace-only assignments (`key value` in `.properties`), mid-line colons without following space or with preceding space (`INFO loaded db.password : ...`), digit-initial values after a tight colon, name/value pairs in separate fields (Kubernetes `name:`/`value:`, ECS/SSM JSON, XML `key=... value=...`), tables, values under four characters, and names not classified as sensitive (such as `auth`).
- To preserve readable evidence, status words (`done`, `expired`), placeholders (`${API_KEY}`, `<token>`), code expressions (`get_token(user)`, `settings.API_KEY`), descriptive-name values (`password_policy=strict`, `token_type=Bearer`, `routing_key=orders.created`), numbers, URLs under connection names (`DATABASE_URL`, `*_URI`), and file paths remain visible if no part looks like a generated token and the value is at most 200 characters. Numbers and paths after password names are redacted. A password looking exactly like an exception would remain visible. Conversely, harmless values such as pytest IDs and Go `got`/`want` lines are sometimes hidden.

### Working copy and baseline

- `workspace` withholds credential files by name unless Git tracks them or `--include` names them exactly (a glob never copies one): `.env` and its variants (`.env.*`, `.env-*`, `.env_*`, `*.env`; not the example, sample, template, dist and defaults variants), `.envrc*`, `.dev.vars`, `*.key`, `*.p8`, `*.p12`, `*.pfx`, `*.jks`, `*.keystore`, `*.ppk`, `*.kdbx`, `*.pem` that holds a private key, `id_rsa*`, `id_dsa*`, `id_ecdsa*`, `id_ed25519*`, `id_xmss*`, `credentials*.json`, `service-account*.json`, `*adminsdk*.json`, `application_default_credentials.json`, `token.json`, `auth.json`, `local.settings.json`, `credentials`, `kubeconfig*`, `.kube/config`, `.docker/config.json`, `.dockercfg`, `.npmrc`, `.pypirc`, `.netrc`, `_netrc`, `.git-credentials`, `.htpasswd`, `htpasswd`, `.pgpass`, `pgpass.conf`, `.my.cnf`, `.s3cfg`, `.boto`, `.vault-token`, `.terraformrc`, `terraform.rc`, `*.tfstate`, `*.tfstate.*`, `*.ovpn`, and `.yarnrc`/`.yarnrc.yml` that hold a registry credential. A public variant (`.example`, `.sample`, `.template`, `.dist`, `.schema`, `.pub`) is withheld only when the secret scan finds a real-looking credential in it. Key files whose name does not mark them are copied and reported by content hits (`secret_content_hits`), not withheld.
- `.git` entries and files named like the working-copy marker are never copied; nested repositories and worktrees are plain directories in the copy. Links and junctions are not followed; only those whose target stays inside the project are recreated, relative to the copy, and the others are listed (`unsafe_symlinks_not_created`) and not created. Links inside the project that the system could not create (for example on Windows without the symlink privilege) or whose target the copy does not hold are listed separately (`links_not_created`): those files or directories are missing from the copy. Non-regular files (FIFOs, sockets, devices) are skipped without opening. A file that cannot be read stops the command with a message naming it; directories that cannot be read are listed as gaps and mark the state `incomplete`.
- Baseline comparison records ignored files only by size and timestamp and summarizes large dependency directories (not hashed). Alternate data streams are not fingerprinted. Windows names with a trailing dot or space are reported as gaps. Git metadata is compared through refs, HEAD, stash, worktrees, local configuration, index and hooks; other files under `.git` (such as `info/exclude`) are not compared. Changes by the user's concurrent tools (development server, IDE) also appear as side effects and require explanation.
- The tool's Git calls only read the original repository: they run with no optional locks (no index refresh), no hooks and no file-system monitor, with trace and redirect variables dropped, trace2 targets off and configured filter drivers off. The check for uncommitted changes does not enter submodules.
- Validator secret detection recognizes common key formats, private keys, and URL credentials in UTF-8/UTF-16 files of any size. Encoding is determined from the first 8 KB; UTF-16 appended after UTF-8 in one file is not scanned as UTF-16. JWTs and URL credentials produce only warnings. Free-text passwords, unknown token formats, and encoded values can escape detection; §10.1 still applies.
- Quoted code excerpts are compared with project files read as UTF-8 (with or without byte-order mark) or UTF-16 with byte-order mark; any other file is read as Windows-1252 and the validator reports the mismatch. Log excerpts are compared without terminal control sequences, trailing blanks and line-ending style.

## License

MIT; see [LICENSE](LICENSE).
