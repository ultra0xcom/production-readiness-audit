# production-readiness-audit

Version 4.0 (2026-10-14) is a prompt package and Claude Code skill that audits a software project against its documented vision. It reconstructs the target state from the project's evidence and vision, tests the real system with existing tests and environments in an isolated working copy, documents deviations with evidence, and delivers executable repair and diagnosis packages for follow-up agents. It never changes the product during the audit. The master prompt is independent of model and provider; its operating mode is `AUDIT_AND_PLAN`.

## Install

```bash
npx skills add ultra0xcom/production-readiness-audit -g   # personal: all projects (~/.claude/skills)
npx skills add ultra0xcom/production-readiness-audit      # this project only (./.claude/skills)
```

Without `-g`, the skills CLI installs into the current project. The skill needs its whole folder (`tools/`, the prompts), not only `SKILL.md`.

Manual alternative:

```bash
git clone https://github.com/ultra0xcom/production-readiness-audit.git ~/.claude/skills/production-readiness-audit
```

You can also link a clone into the personal skill directory:

```bash
ln -s ~/projects/production-readiness-audit ~/.claude/skills/production-readiness-audit
```

```powershell
New-Item -ItemType Junction -Path "$HOME\.claude\skills\production-readiness-audit" -Target "<path-to-clone>"
```

A project-local installation in `.claude/skills/production-readiness-audit/` is another option. The tools exclude a project-local package from the baseline and working copy, but project linters and `git status` still see it; add it to `.git/info/exclude`.

Requirements: Python 3.10+ (standard library only), `git` for Git projects, terminal access, and permission to create a working copy outside the project and execute commands there. Without Python, the agent works in an equivalent manual way under master prompt §13.1 and loses the mechanical checks.

## Usage

With Claude Code:

```text
/production-readiness-audit start Vision: ... Target operation: ... Additional checking tools: also established open-source checking tools at pinned versions
/production-readiness-audit resume
/production-readiness-audit reaudit
/production-readiness-audit implement W1
/production-readiness-audit status
```

The skill replaces copying prompts. It reads `SKILL.md` in the package directory and uses the other files there. Without a mode word, it selects a mode from existing runs and asks if the choice is ambiguous. It is invoked only explicitly, never automatically by the model, because a run is long and expensive. Supply authorizations that differ from the start prompt defaults as arguments; the agent copies them verbatim into the start instruction.

Without the skill, edit the project details and authorizations in `START_PROMPT.md` and copy its block into any agent session in the project directory. Use `RESUME_PROMPT.md`, Variant A, to resume and Variant B for a re-audit; use `IMPLEMENTATION_PROMPT.md` to implement packages. Set the package path in each instruction. Read `MASTER_PROMPT.md` as the complete audit contract.

## Workflow

1. **Provide the package.** Store it centrally and enter its absolute path in the first line of the start prompt, or copy it into the project root at `./production-readiness-audit/`. To avoid accidental commits, you can add `production-readiness-audit/` and `audit-results/` locally to `.git/info/exclude`; this changes no tracked file.
2. **Start.** Edit project details and authorizations in `START_PROMPT.md` and copy the block into the agent session in the project directory. A few sentences under "Vision in my words" have the greatest effect because the vision defines the target state for every assessment.
3. **Vision checkpoint.** When enabled (the default), the agent first completes everything that does not need a clarified target state: working copy, installation, build, existing tests. It then pauses once, presenting the few questions that materially change the audit plan or assessment, plus authorization requests for discovered environments. It continues after your answer; the answers also apply to later runs.
4. **Read the result.** `audit-results/<RUN_ID>/REPORT.md` starts with an Executive summary. Repair and diagnosis packages are in `tasks/`, ordered by implementation wave; every task file contains a copyable implementation instruction.
5. **Resume.** After interruption or a context limit, use `RESUME_PROMPT.md`, Variant A. `python <PACKAGE>/tools/audit_tools.py runs` shows open and completed runs.
6. **Implement and recheck.** Implement packages individually or by wave with `IMPLEMENTATION_PROMPT.md`: one branch per package, regression tests, diff review, and progress in `audit-results/<RUN_ID>_implementation/IMPLEMENTATION.md`. Then use `RESUME_PROMPT.md`, Variant B, in a new session: only the re-audit confirms a repair.

## Files

| File | Purpose | Audience |
|---|---|---|
| `MASTER_PROMPT.md` | Complete audit instruction | Executing agent |
| `tools/audit_tools.py` | Baseline and side-effect comparison, working-copy creation and cleanup, evidence logs with secret redaction, service control, `.env` inventory without values, run overview, validator with selftest | Executing agent |
| `SKILL.md` | Claude Code skill: selects the mode, constructs the instruction from the prompts, adds Claude Code rules | Claude Code |
| `START_PROMPT.md` | Start instruction with project details and authorizations | You |
| `RESUME_PROMPT.md` | Resume a run and re-audit after repairs | You |
| `IMPLEMENTATION_PROMPT.md` | Implement a run's repair packages through an orchestrating agent | You |
| `README.md` | This overview | You |
| `CHANGELOG.md` | Version history | You |
| `MANIFEST.json` | SHA-256 hashes of package files | Maintenance |
| `maintenance/` | Prompt and package structure checker, selftest mutation probe, evaluation plan, manifest script; check reports are generated locally and are not part of the repository | Maintenance |

## Notes for Claude Code (observed with Opus 5.5)

These points are rules in the skill; users starting without the skill should know them.

- **Effort:** The skill inherits the session's model and effort. `max` is appropriate when correctness takes precedence over cost; turns become substantially longer and more expensive. To save time, compare `xhigh` on a known project instead of switching across the board.
- **Long files:** The reading tool returns long files in sections. The master prompt does not fit in one read; the start prompt and skill explicitly require reading through the final line.
- **Context compaction:** Long runs summarize the conversation and therefore the rules. The master prompt requires rereading the quick reference, `RESUME.md`, and `RUN-STATE.json`. Only roughly the first 5,000 tokens of the skill survive compaction; all skills share this budget, allocated first to the most recently invoked ones. The structure checker enforces the skill's size limit. Invoking many other skills risks losing this skill after compaction; the master prompt still requires rereading the quick reference and `RESUME.md`.
- **Automations:** Hooks from project, user, managed settings and enabled plugins, plus tool servers in `.mcp.json`, run in the session. The agent reads them before the first write. A hook that writes or commits in the original or sends data is a blocker (§2). `/hooks` shows active hooks and their sources.
- **Subagents:** Useful for broad inventories and searches to preserve main-session context, and for a second review of critical findings without knowing the conclusion (§9). The skill requires a fresh Opus agent for that review. Only the main agent writes `audit.json`.
- **Working copy:** Use a drive with space and a short path, outside the session scratchpad and off a nearly full system disk. Remove it at the end with `audit_tools.py cleanup`. If a protection hook requires confirmation for deletion, the agent asks once for the collected cleanup actions instead of bypassing it.
- **Execution:** Run in the main session. Subagents lack the user channel for the vision checkpoint; in the self-audit, subagent rules rejected writing `REPORT.md`.
- **Services:** The agent starts an E2E server with `audit_tools.py run` and a suitable `--timeout` as a background command, then ends it with `audit_tools.py stop`, producing a complete log and leaving no processes behind.
- **Browser:** Check UI journeys through isolated automation, preferably the project's E2E runner, otherwise a tool such as Playwright with a fresh profile. An extension using the personal browser with real logins is unsuitable. Interactive browser tools can create output directories such as `.playwright-mcp/` in the current directory, which is the original; this happened in real runs. Save screenshots with absolute paths under `evidence/ui/`.
- **Output filters:** Token-saving filters shorten test output. For evidence, `audit_tools.py run` always writes full output to a log; a filtered display is not evidence.
- **Code graphs and indexes:** Navigation indexes can lag behind the working state. Material claims rely on files actually read; the validator checks code quotations against the starting baseline.
- **Environment variables:** `run` removes credentials and cloud, database, and registry targets from the test environment; the test of version 3.2 also removed an agent-session token. If a test needs a variable, the agent authorizes it specifically with `--inherit`, provided the start instruction permits it.

## What "considered" means here

The package contains a cross-domain audit framework and rules requiring the agent to add project-specific paths, domains, contradictions, and missing requirements. The matrix is explicitly not a closed list of every possible defect. A new finding may expand scope; missing tools must not silently reduce it.

Completing the defined audit scope and assessing technical release readiness remain separate results. `COMPLETE_FOR_DEFINED_SCOPE` does not mean defect-free. `GO_FOR_DEFINED_SCOPE` is neither a global security guarantee nor deployment authorization. Known residual defects and missing evidence remain visible.

The tools check form and consistency: required fields, references, hashes, log metadata, quotations tied to the starting baseline, and status logic. They expose mistakes and unsupported shortcuts but do not prevent deliberate forgery, such as fabricated metadata. The validator does not assess whether evidence substantively supports a conclusion; the master prompt requires a semantic final review for that.

The core remains: evidenced project vision → goals and invariants → all identified journeys → real implementation and delivered artifact → actual checks → evidenced defects or explicit evidence gaps → proportionate repair packages → integrated recheck. All 18 numbered main sections and 19 domains A–S remain. The vision defines the target state. Code, effective configuration, the actual artifact, and observed behavior establish the current state. A confirmed defect may have an unknown cause. A successful bug reproducer does not establish a repaired product.

## Verification

Executed for version 4.0 on Windows 11 with Git 2.50.0 under Python 3.10.10, 3.12.9 and 3.13.7, and on Linux (WSL, Python 3.14.4, with Git):

- **Tool selftest:** 4/4 positive controls, 63/63 negative controls, and 54 passing tool controls out of 55 on each platform; the remaining control is POSIX-only on Windows and Windows-only on Linux and is reported as skipped. No failed controls.
- **Prompt/package structure check:** 95/95 checks and 25/25 negative controls passed on all three Windows Python versions. These cover required rule anchors, resolved section references, vocabulary agreement, documented commands, start authorizations, prompt versions and the README version marker, parsed skill frontmatter, the compaction budget, implementation safeguards, and the master's final line.
- **Mutation probe:** All 86 mutants made the expected control fail: 81 on Windows, 5 POSIX mutants on Linux. The probe first checks that every mutation target occurs exactly once in the tool.
- **Skill installation:** `npx skills add <package> -a claude-code -y --copy` into a fresh project installed the whole folder, including `tools/`; the installed tool ran and the manifest check passed.
- **Independent reviews of the translation:** fresh reviewers compared every line of the prompts, skill and evaluation plan with the German 3.2 originals, checked that the tool's syntax tree is unchanged apart from strings, and reviewed README and changelog against the original README. Their wording and terminology fixes are applied.
- **Manifest:** Regenerated with the 4.0 names and checked for matching package-file hashes.

Local verification reports are regenerated, not published: `maintenance/STRUCTURE_CHECK.json` and `maintenance/TOOL_SELFTEST.json`.

The following are recorded results of version 3.2 before translation, not newly executed 4.0 checks:

- **End-to-end run:** On a small Git project, `init` → `workspace` (original-path configuration reported) → `run` (refused in the original, executed in the copy, defect evidenced with exit code 1) → `audit.json` with the real log → `validate --final` without errors or warnings → `runs` showing open then completed → final baseline comparison without changes or side effects → `cleanup` removing the copy and refusing the original → unchanged baseline comparison.
- **Calibration on real runs:** Read-only secret scanning covered four version 3.0 audits and 6,645 files. The sole hard hit was a synthetic test value; URL hits were example domains, templates, and default passwords. This led to placeholder recognition. `runs` classified four legacy runs as completed (inferred), an ongoing 3.1 run as open, and implementation directories as non-runs.
- **Mutation probe during 3.2 development:** The mutant restoring quadratic runtime failed through the control's time limit. Surviving intermediate mutants exposed weak controls: duplicate masking paths, missing counterexamples for values that must remain visible, and an unnecessary masking exception that was removed.
- **Self-audit with 3.2:** A complete headless Claude Code audit using Opus, without a vision checkpoint, audited a copy of this package: NO_GO, 12 findings, two HIGH blockers. F-001–F-005, F-007, F-010, and F-012 were fixed. F-006 (evaluation not executed) and F-009 (profile paths in result files) remain open; F-011 remains unconfirmed, F-008 was refuted. Its audit tests were not rerun against the final state; new selftest controls cover their cases.
- **Self-audit with 3.1:** 9 confirmed findings (one HIGH, including F-004 on redaction), 2 unconfirmed hypotheses (F-010, F-011), 3 refuted suspicions. All 22 audit tests passed against the 3.2 tools; 14 failed against the state before corrections.
- **Independent reviews:** Fresh agents reviewed 3.1 as auditor, red team with reproduced tool attacks, and final consistency reviewer. Two reviews of 3.2 prompt/tool changes and four correction rechecks found and corrected Unicode path and PEM gaps, missing controls, symbol-password leaks, test-name overredaction, quadratic regex runtime, lost exclusions, and non-repeatable cleanup. The first two rechecks found new evidence: exceptions leaking webhook URLs, dotted tokens and passwords such as `hunter2.Backup`; missed `password.txt:` fields, tight-colon lines, Go output and escaped JSON; and distorted log formats such as `WARNING:app.auth.token:...`. The third found only minor bracket, ANSI, escaped-JSON and already-redacted-long-value runtime issues; the fourth confirmed their correction and found no new findings. A fresh Opus reviewer of tool 1.3.0 returned PASS_WITH_NOTES with five medium findings, including orphaned group members after `stop`, identity checks without `/proc`, and validator block-boundary false hits. All were fixed and covered by controls; that reviewer did not recheck them.

**Not executed:** no complete audit of a real project with V3.2 or V4.0, no invocation of the skill in a fresh session, no empirical evaluation under `maintenance/EVAL_PLAN.md` (39 scenarios), no model or effort-level comparison, and no macOS run (POSIX paths ran only on Linux in WSL). Controls establish prompt/tool form and consistency, not an agent's defect-detection rate.

The selftest exercises real processes, Git repositories, symlinks, and junctions. Its positive controls include valid NO_GO and GO runs and legitimate artifact references, pytest IDs, `.txt` evidence copies, a STATIC check with evidenced exit code, and a refuted mandatory investigation under GO. Negative controls reject fabricated logs and quotations, altered sources, inconsistent exit codes, incomplete GO audits, self-accepted risks, downgraded mandatory checks, and executable files in results. Tool controls cover working-copy boundaries, environment stripping and redaction, UTF-16, secret-scanner block boundaries, descendants and services, copy exclusions and changes since the baseline, Unicode original-path references, safe repeatable cleanup, and run classification.

## Known limits

- The result directory is inside the project. Whole-tree formatters and linters may include its Markdown and JSON files; the validator prevents only executable code and configuration files there.
- `--isolated-home` separates profile credentials, resets Git credential helpers, and removes profile-file pointer variables, but forces new package and browser cache downloads. It does not separate OS credential stores (Windows Credential Manager, macOS Keychain). `XDG_RUNTIME_DIR`, where Podman stores logins, remains because rootless containers need it. Without this option, working-copy tools may read profile credentials such as `~/.aws`.
- `run` strips variables by name and value (URL credentials, known key formats, private keys). Credentials in another format under an inconspicuous name, such as Base64 in `MY_SETTING`, still reach the test process; use `--redact` and follow §6.
- **F-009:** Result files contain absolute project and working-copy paths, often including account names on Windows. Review them before sharing.
- `run` resolves programs itself and refuses files in the original. With `--shell`, the shell resolves them and this check does not apply.
- Baseline comparison records ignored files only by size and timestamp and summarizes large dependency directories. Changes by the user's concurrent tools (development server, IDE) also appear as side effects and require explanation.
- **F-010 / F-011:** Background-process cleanup by an agent environment depends on its tooling. If `run` itself is killed, for example by an agent timeout, `.meta.json` is missing and the log is not evidence. The Windows job object also ends the command. On Linux and macOS, the process group continues (3.2 self-audit F-010); `stop` ends it after checking start time and process group, and removes orphaned markers. If identity cannot be confirmed, the process remains and `stop` reports this. On macOS, without `/proc`, only the process group is checked. F-011 remains untested: a Windows child started before job assignment might escape the job.
- **F-006:** The evaluation plan's 39 scenarios have not been executed.
- Validator secret detection recognizes common key formats, private keys, and URL credentials in UTF-8/UTF-16 files of any size. Encoding is determined from the first 8 KB; UTF-16 appended after UTF-8 in one file is not scanned as UTF-16. JWTs and URL credentials produce only warnings. Free-text passwords, unknown token formats, and encoded values can escape detection; §10.1 still applies.
- `run` redacts sensitive environment values, sensitive assignments discovered by `workspace`, output assignments (including inside other values and code expressions, singly escaped double-quoted JSON, after `=>`, YAML tags, and the first line of YAML block values), `Authorization` headers, URL credentials, and known key formats. Known values are also redacted in UTF-16; other patterns only in UTF-8 and the system encoding. A colon without following space counts only at line start (`password:...`, `spring.datasource.password:...`, at most 64 indentation characters), in Go/map output (`{Host:db Password:...}`, `map[password:...]`), and in quoted headers with hyphenated names (`-H "X-Api-Key:..."`, not `-H "apikey:..."`). Space before a colon counts only at line start or before a quoted value. Closing brackets at an unquoted value's end remain when closing brackets opened before the name (`{Password:...}`); a password itself ending with that bracket leaves the character visible. A closing bracket in a previous value (`{msg:"}" password:...}`) disables recognition of subsequent lowercase fields. Doubly escaped JSON (`\\\"password\\\"`) and escaped single-quoted JSON are not recognized. Encoded (Base64, URL-encoded), split, or unknown-format values remain; follow §6. Also unrecognized: internally spaced/quoted values not quoted as a whole (`password: my secret phrase`), whitespace-only assignments (`key value` in `.properties`), mid-line colons without following space or with preceding space (`INFO loaded db.password : ...`), digit-initial values after a tight colon, name/value pairs in separate fields (Kubernetes `name:`/`value:`, ECS/SSM JSON, XML `key=... value=...`), tables, values under four characters, and names not classified as sensitive (such as `auth`). Log lines such as `WARNING:app.token:...` and Spring's `c.a.TokenService : ...` therefore do not count as assignments. To preserve readable evidence, status words (`done`, `expired`), placeholders (`${API_KEY}`, `<token>`), code expressions (`get_token(user)`, `settings.API_KEY`), descriptive-name values (`password_policy=strict`, `token_type=Bearer`, `routing_key=orders.created`), numbers, URLs under connection names (`DATABASE_URL`, `*_URI`), and file paths remain visible if no part looks like a generated token and the value is at most 200 characters. Numbers and paths after password names are redacted. A password looking exactly like an exception would remain visible. Values from copy files are redacted everywhere only if at least eight characters with two character classes, or a passphrase under a password name of at least twelve characters; shorter/simple values only in assignment form. Sensitive `--env` values under four characters are redacted only in `.meta.json`, not logs. Conversely, harmless values such as pytest IDs and Go `got`/`want` lines are sometimes hidden.
- `original_path_refs` finds only literal original paths, including forward slashes, JSON escapes, and drive mount forms such as `/v/...` and `/mnt/v/...`. Constructed paths (`path.join(HOME, ...)`) require reading scripts.
- `cleanup` recognizes junctions, symlinks, and mount points and removes them without following them. On Linux and macOS this was checked only in code, not in a run.
- For runs before 3.1, `runs` infers completion from status and an existing report. Output marks this; the skill and resume prompt ask if uncertain. Version 4.0 still lists old German 3.x run directories. `validate` checks 4.0 reports: a report from another prompt version gets the warning `W_PROMPT_VERSION`, and German 3.x reports fail the English heading checks.

## Technical safeguards outside the prompt

Audit data and tool permissions must match actual permission boundaries. Where possible, the prompt requires read-protected product files, an isolated writable working copy, controlled target/network permissions, and bounded resources. It does not claim that text technically establishes these controls.

OWASP describes restricted permissions, separate handling of untrusted content, validation, and authorizations for risky actions in agents exposed to prompt injection. The prompt alone is therefore not the entire protection architecture. [R3]

## Primary sources

These sources were read when version 3.0 was created on 2026-09-29 to frame the audit. They were not retrieved again for 3.1, 3.2, or this translation. They do not certify this prompt. Determine the applicable version, applicability, and required evidence again for each project.

- **[R1] [OWASP Application Security Verification Standard](https://owasp.org/projects/asvs):** Basis for verifiable technical security requirements for web applications, including relevant environmental controls. Cite concrete requirement IDs with their version.
- **[R2] [NIST Secure Software Development Framework](https://csrc.nist.gov/Projects/ssdf):** Risk-based, outcome-oriented development practices considering business goals, resources, and applicability; explicitly not an exhaustive tick-box checklist.
- **[R3] [OWASP LLM01: Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/):** Controls at role, data, and tool boundaries, and continuing limits of protection.

## Maintenance commands

```bash
python -B maintenance/check_prompt_structure.py --output <file>  # Prompt/package structure check
python -B tools/audit_tools.py selftest --out <file>               # Tool selftest
python -B maintenance/build_manifest.py                          # Regenerate MANIFEST.json
python -B maintenance/build_manifest.py --check                  # Compare only
python -B maintenance/mutation_probe.py tools/audit_tools.py <empty-directory>  # Use MUTATION_PROBE_WORKERS=2 with limited RAM
```

`-B` prevents bytecode caches in the package. Without `--output`, the structure checker writes to stdout; selftest writes its report only to a file that does not yet exist. Local report names are `maintenance/STRUCTURE_CHECK.json` and `maintenance/TOOL_SELFTEST.json`. `MANIFEST.json` contains package-file hashes for integrity comparison, not proof of substantive truth; it does not hash itself. No archive is required or published. The previous German 3.2 master prompt's hash is recorded as a named constant in the manifest script.

Master prompt changes must keep the structure checker passing; new rules receive a required anchor there and a scenario in `maintenance/EVAL_PLAN.md`. `SKILL.md` must remain below the structure checker's size limit to survive context compaction in full.

## License

MIT; see [LICENSE](LICENSE).
