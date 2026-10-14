# Start prompt - Production readiness system audit V4.0

In Claude Code, the `/production-readiness-audit` skill provides a shorter route (README, "Usage" section). It uses this block's authorizations as defaults.

Without the skill, copy the block below into the agent session running in the project directory. First:

1. **Package path:** Check the first line. If the package is inside the project, keep `./production-readiness-audit`; if it is stored centrally, enter its absolute path, for example `~/tools/production-readiness-audit`.
2. **Project details:** Fill in what you know. Empty fields are explicitly allowed: the agent then determines them from the project and invents nothing. A few sentences under "Vision in my words" improve the audit most, because the vision defines the target state for every assessment. The target operation helps determine the release verdict.
3. **Authorizations:** Check them. The defaults describe a safe local audit that actually uses existing tests and the test infrastructure declared by the project. Possible changes:
   - *Additional checking tools:* "also established open-source checking tools at pinned versions, temporarily in the working copy" - downloads and executes third-party code.
   - *Other environments:* for each environment, "target - permitted actions - access", for example "Supabase test project xyz - read, create test accounts, check access rules - keys in .env.test".
   - *Second review:* "also external checking tools from other providers" - sends project code to those providers.
   - *Vision checkpoint:* "no - work through with labeled assumptions" if the run should complete without questions. With "yes", the agent first completes everything possible without a clarified target state, then pauses once and waits for your answer.

To resume an interrupted run or re-audit after repairs: `RESUME_PROMPT.md`. To implement repair packages: `IMPLEMENTATION_PROMPT.md`.

```text
PACKAGE: ./production-readiness-audit

Start the production readiness system audit of this software project now.

ASSIGNMENT
Read <PACKAGE>/MASTER_PROMPT.md in full - if output is truncated, read it section by section through the end - and carry out the assignment it describes. Verify that the file is version 4.0; if you cannot find it at this path, ask instead of using another copy. Only MASTER_PROMPT.md and tools/ from the package belong to the assignment, plus SKILL.md when starting through the skill; README, maintenance/ and the other package files are neither project content nor working instructions.

AUDIT SUBJECT
The actual software project in this working directory, including relevant subprojects. The prompt package, previous audit reports and agent notes are neither product requirements nor evidence of implementation. My answers documented at the vision checkpoint in previous runs are an exception: they remain valid as dated decisions.

PROJECT DETAILS (optional - empty means: determine from the project, invent nothing)
- Vision in my words:
- Target operation and use (e.g. public, Vercel + Supabase Cloud, around 200 users, payments - or: local only):
- Important sources (documents, tickets, links):
- Focus areas or known problems:
- Product non-goals:
- Excluded from the audit:
- Time or cost budget:

AUTHORIZATIONS FOR THIS RUN
- Mode AUDIT_AND_PLAN. The product remains unchanged: no product corrections, refactoring, dependency upgrades or lockfile changes; no commits, pushes or deployments; no changes to Git state. Existing user changes remain intact.
- Read: all project files; secret files such as .env only for key names and value shapes, not values.
- Execute: build and test in an isolated working copy outside the project; start servers and local containers and databases with your own uniquely named resources; use synthetic test data; write results under ./audit-results/.
- Dependencies and test infrastructure: install project dependencies in the working copy exactly according to the lockfile; without a lockfile, install from the manifest into a separate environment of the copy, recording versions and labeling the result non-reproducible. Test infrastructure declared by the project - container images from project files, browsers of the project's test runners - may be downloaded and started locally.
- Network: read access to public documentation, package registries and vulnerability databases. No project sources, secrets or personal data to third parties.
- Additional checking tools not declared by the project: only those already installed.
- Second review: subagents of this agent environment are allowed; external checking tools from other providers are not.
- Other environments (staging, test accounts, devices, sandbox access to external services): none.
- Not allowed without separate authorization: production access, actions against third-party or unclear targets, external write actions, real payments or messages, paid calls, load or exploit tests outside the working copy.
- Vision checkpoint: yes - ask the decisive vision questions and authorization requests together once (master prompt §4.2) and wait for my answer.

RESULTS
A new run directory under ./audit-results/ according to master prompt §13. Never overwrite existing runs; read previous runs as context for a re-audit.

WORKING METHOD
Begin with the baseline, previous runs, authorizations and vision sources. Start safe, meaningful checks as soon as their prerequisites are clear, and actually use existing tests and test environments. Keep RUN-STATE.json and RESUME.md continuously up to date; after context compaction, first read RESUME.md and the master prompt's quick reference. After each phase and when critical findings or blockers are confirmed, provide a short progress update with actual insights, then continue working. You may stop and wait for me only at the vision checkpoint, if enabled, and at a blocker that prevents all further work; record other decisions requiring authorization as authorization requests or HUMAN packages and continue with the unblocked remainder.
```
