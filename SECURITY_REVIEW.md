# Final security review

For audit use, read only in P8 after checkpoint verification; general master rules apply. This is a defensive review of the user's own project within the start assignment's authorizations. Authorized implementation reads only sections 2 and 6 in its dedicated last wave/session, without P8 entry/closure procedures.

## 1. Entry conditions

If the checkpoint was not verified under master prompt §16.1, stop reading and verify first; missing/invalid cases follow §16. The checkpoint is never modified. Compare baseline/copy, record disclosed execution details (`executed_by: undisclosed` rather than guessing), and resume recorded P8 checks.

## 2. Safety boundaries

Use only authorized targets and existing authorized tools/environments. No security tests against production or unauthorized third parties, no destructive or uncontrolled load tests, no real payments or messages, no exploit development. Never test exposed credentials against real services. Use harmless synthetic test values and isolated data in the working copy, with target allowlist, controlled network, timeouts and allocated resource limits. Check host mounts, privilege, shared resources and external effects before execution. Stop on unexpected effects or boundary violations. Secret values and personal data appear in no artifact or provider transmission; follow master prompt §2 and §6 if a log contains a value. Additional providers, installations and paid services require authorization.

## 3. Scope from the neutral inventory

Derive checks only from `security_scope` entries and target operation; reconcile boundaries, stores and deferred suite/test-value coverage. Add discovered areas with factual reasons and mandatory checks. A missing tool is a coverage gap. Retire an irrelevant entry by removing it and its obsolete checks/references, recording IDs/reasons in H; alternatively resolve its mandatory check PASS with qualifying static evidence of irrelevance. Do not manufacture one finding per class. Classes without an inventoried surface need only one collective not-applicable note with a project reason. Decide NOT_APPLICABLE only here, after reviewing scope.

Import previous security items from their neutral index now, including items whose classification was unknown. Read their detail files only now and reassess original conditions, implemented packages, current quotations and evidence. Old IDs/history remain; old findings are not current confirmations. No classification inferred from titles before this phase.

## 4. Checks derived from applicable areas

The following topics guide scope-derived checks; they are not a checklist to fill:

- Identity, authentication, sessions, locked accounts, expiration and account recovery.
- Access rules by role, object and tenant: permitted use and rejected unauthorized use, own/other resources, server-side enforcement, frontend-only restrictions and alternative access paths. Trace the real integration and data effects.
- External input: queries, commands, markup output, cross-site requests, outbound requests, file paths, uploads, parsers and deserialization, only where those paths exist. Scope-derived checks cover relevant injection, XSS, CSRF, SSRF and path-manipulation cases using harmless synthetic values.
- Secrets and keys, cryptographic use, TLS, safe defaults and least privilege.
- Rate limits and incorrect use of business rules, where required by the target basis.
- Isolation of executed external content: plugins, supplied scripts and containers, only where inventoried.
- Build and supply chain: privileged CI jobs, secret access, third-party code in builds, artifact provenance, integrity, bypassable release checks and dependency advisories. Assess context and reachability, including the build path.
- Integration signatures, verification/rejection of unsigned requests, token renewal and the access implications of webhook repetition; functional idempotency remains part of ordinary journey checks.
- AI components: untrusted instructions/content, tool permissions and unintended data transmission across evidenced boundaries. Do not claim universal absence from a few unobserved calls.
- Security promises such as "secure" or "end-to-end encrypted": import the recorded claims into `doc_claims` only now, then assign truth verdicts under master prompt §7.2. A consequential false claim needs finding admission; generic marketing does not automatically create a blocker.

Check relevant integration, data integrity and recovery effects. Consolidate shared causes without discarding different impacts; confirm each occurrence separately.

## 5. Methods and sources

Use existing suitable authorized tools and environments. Deliberately consider platform-specific security and data pitfalls from official documentation actually read in this run for the platforms and versions used, such as access rules and server-executable actions. Follow master prompt §10.1 primary-source rules; memory, search snippets and guessed links are insufficient.

OWASP ASVS/WSTG and NIST SSDF are optional candidate-check frameworks, not the target state. A finding needs the evidenced vision, target operation or necessary property and demonstrated deficiency. Cite IDs and versions only from primary sources read in this run; otherwise name the topic. Separate drafts, obsolete versions and binding requirements. No currency claims without research.

## 6. Evidence and finding admission

Apply master prompt §10, §10.1, §11, §13.2 and Appendix A: qualifying evidence needs storage/hash, freshness and origin (run metadata, verified code_refs or within-run UI/manual observer/description). Handwritten or relabelled notes do not establish observation. Checks do not authenticate screenshots or prove a causal chain.

Every security candidate names actor/access level, entry point, missing protection and harm. CONFIRMED needs a demonstrated reachable path in the target profile; UNCONFIRMED needs a specific suspected path, trigger and deciding check, without claiming established reachability. Without either basis keep a P8 check, not a finding. Severity follows exposure, not a hypothetical maximum; seek counter-guards and alternatives. Generic hardening without necessity is a recommendation.

An advisory becomes a finding only if its ID and affected range were read from a primary source in this run, the affected version is present in the lockfile or delivered artifact, and reachable use or exposure is demonstrated. Never cite CVE or advisory IDs from memory. Unsupported or unreachable hits go to collective dependency notes with reachability status; create a task only for a relevant unresolved decision or check, not automatically for every hit. A lockfile deficiency requires an evidenced reproducibility obligation and demonstrated failure.

Use minimal local reproductions with harmless synthetic test values. Keep detailed inputs in the finding file without secrets; REPORT.md contains no harmful test strings. Static confirmation must trace the complete reachable chain and never claims runtime execution. Audit coverage gaps and weak tests alone never establish a product blocker. A demonstrated malfunction of a delivered release-control mechanism remains a sourced product or operability defect under Appendix A. Never inflate confirmation, severity or blocker status to obtain NO_GO.

## 7. Reviewers and second review

Use available authorized reviewers under master prompt §9. An optional reviewer is not a prerequisite; if unavailable, record the missing perspective and continue. Explicitly binding independent acceptance cannot be replaced by self-review: record its exact evidence gap as BLOCKED. Your own security review never falls away.

Second review of every CRITICAL/HIGH security finding and every security release blocker happens in P8. Where available, use a fresh independent reviewer with a bounded brief: baseline, objective, target source, paths, permitted methods/targets, evidence format, allocated global budget and provider limits, not your verdict. Ask it to disprove or limit the finding. Delegates do not write audit.json or RUN-STATE.json.

Cross-check material claims and record review under master prompt §9 and Appendix A: LIMITED/DISPUTED consequences, `countercheck_evidence_ids` and UNAVAILABLE records apply identically. Reviewer-raised issues need admission; no majority votes.

## 8. Recording and completion

Set `security_review` to IN_PROGRESS with actual start and executor; update live state after each check using the atomic-replacement procedure in master prompt §13.1, retaining the last valid state. Append finding IDs with `category: SECURITY`, regardless of domain; update `security_scope`, H and relevant M/Q rows, claims, evidence, gates and repair packages. Keep hypotheses separate from confirmed totals. Security packages belong in a dedicated last wave/session; dependencies and conflicts remain explicit.

Apply Appendix A: an unexecuted mandatory security check prevents DONE; FAIL can complete review but affects gates. NOT_APPLICABLE needs a project reason, empty scope and consistent H NOT_APPLICABLE; clear start/executor, set completion and record executor in RUN-STATE. Consistency cannot prove inventory completeness. If performed review leaves evidenced mandatory BLOCKED checks, use security status BLOCKED with reason, keep those checks BLOCKED with catch-up plans and HUMAN/VERIFY tasks; P9 may close PARTIAL with no GO. NOT_PERFORMED is only for a user decision not to run review, sourced by `sources` with `user:` ref.

## 9. Interruption, refusal or model change

On refusal, disclosed model change, tool failure or context stop, preserve checkpoint and last valid live state; persist reason, remaining checks and exact continuation, stop own services and retain the copy. No silent model/provider/data-flow changes. Leave unexecuted checks NOT_RUN with catch-up plans; BLOCKED requires evidenced impediment. Completed checks retain results. Use PENDING before review, IN_PROGRESS/BLOCKED after start under Appendix A; BLOCKED needs reason/start/executor. The run stays open; no GO.

Resume under master prompt §16 with live/checkpoint/baseline checks; warn that restoring loses progress since interim closure, never overwrite usable progress automatically. NOT_PERFORMED, decision source and HUMAN/VERIFY task references apply only to the user's decision not to run review; refusal alone is no such decision. Performed-but-BLOCKED review follows section 8 and master prompt §16.1.

## 10. Merge and final closure (P9)

Continue to P9 even when entered through `security` mode: consolidate, validate, review semantics, compare baseline and clean up under master prompt §16.1, including PARTIAL closure with security status BLOCKED; if P9 cannot finish, leave the run open with its next step.

Continue with P9 consolidation and final closure in the master prompt.
