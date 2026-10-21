# Standards review

Review of this repository against the project's general prototype checklist (sections A-F) and
the AgentCore checklist (items 1-17). The "Initial" columns record the state **before** the
standards pass (committed first); the "Final" column was filled in after the changes and the
deployment check (commit `da3ffcd`, VERIFICATION section 7).

Status values: OK = already met, CHANGE = needs a change, N/A = not applicable (reason given).

## General checklist

| # | Item | Initial | Evidence (before) | Planned handling | Final |
|---|---|---|---|---|---|
| A1 | README first screen: non-production, user responsible, security/legal review, link to hardening | CHANGE | README.md:3-5 only says "Prototype. Not for production use" | Three-point disclaimer + link | Done: README.md:3-9 and README.zh-CN.md:3 (three points + hardening link) |
| A2 | No customer/person names, domains, 12-digit numbers, profile names, `[INSERT` leftovers | CHANGE | grep clean, but `<ACCOUNT_ID>` placeholder in tests/test_models.py:31, tests/test_service.py:97, scripts/e2e_aws.py:30 | Replace with `111122223333`; re-grep | Done: grep for 12-digit numbers (only `111122223333`), names, profiles, `[INSERT`: 0 hits; tests use `111122223333` |
| A3 | Placeholders `<UPPER_SNAKE>`, `example.com`, `111122223333`; synthetic data; screenshot provenance | CHANGE | docs/cleanup.md:14 `<MediaBucketName>`, `<runtime-id>`; no screenshots | Normalise placeholders | Done: `<UPPER_SNAKE>` in docs (cleanup, deployment guide, threat model, decisions); `example.com` in templates; no screenshots; data synthetic |
| B1 | LICENSE with Amazon copyright line; full SECURITY; CoC; CONTRIBUTING | CHANGE | LICENSE:3 personal line; SECURITY.md lacks AWS contact and controls; CODE_OF_CONDUCT.md points to Contributor Covenant | Rewrite all four | Done: LICENSE:3 Amazon line; SECURITY.md (AWS reporting, deployed resources, data handling, controls → files, caveats, hardening, cleanup); CODE_OF_CONDUCT.md (Amazon CoC); CONTRIBUTING.md |
| B2 | THIRD-PARTY-LICENSES (two tables); CHANGELOG + VERSION | CHANGE | files missing | Add, licences from package metadata | Done: THIRD-PARTY-LICENSES (none distributed / downloaded at build or deploy; no relicensing; licences from metadata and official repos); CHANGELOG.md; VERSION 0.1.0; tag v0.1.0 |
| B3 | Issue/PR templates, AGENTS.md + CLAUDE.md, Makefile, .gitignore/.dockerignore | CHANGE | none present; .gitignore lacks config/params.json, cdk.context.json, .env* | Add; .dockerignore N/A (no Docker build) | Done: .github/ISSUE_TEMPLATE/ (forms, config.yml with blank issues off and AWS security link), pull_request_template.md, AGENTS.md + CLAUDE.md (`@AGENTS.md`), Makefile, .gitignore (live config, cdk.context.json, .env*, .deploy/). .dockerignore: N/A, no Docker build (DECISIONS D40) |
| B4 | DECISIONS / OPEN-QUESTIONS / SECURITY-NOTES / THREAT_MODEL (DRAFT) / VERIFICATION / DEPLOYMENT-LOG | CHANGE | all exist; THREAT_MODEL.md:1 has no DRAFT status | Mark DRAFT | Done: THREAT_MODEL.md:3 `Status: DRAFT`; all governance files present |
| C1 | Config as template, input validation, removal policy parameter, uniform tags | CHANGE | config/params.json committed as the live file; deploy section not validated (infra/app.py:23-24) | `*.example.json` + `make init`; validate deploy and pipeline at synth | Done: config/params.example.json + params.customer.example.json, `make init`; infra/video_infra/params.py (regex, ranges, cross-field rules, no 12-digit numbers) runs before synth; removal policy and tags are parameters; IAM/nag follow prefix and model (tests/test_infra.py) |
| C2 | Preflight, destructive-change guard, smoke/e2e test | CHANGE | e2e exists (scripts/e2e_aws.py); no preflight, no diff guard, no paid-command guard | `make preflight`, `ALLOW_DESTRUCTIVE`, `ALLOW_PAID` | Done: scripts/preflight.sh (read-only; last 4 digits, region, tools incl. Docker state, service availability); scripts/deploy.sh refuses deletes/replacements without ALLOW_DESTRUCTIVE=1 (demonstrated: exit 2); ALLOW_PAID=1 guard (scripts/guards.py); `make verify` = e2e + failure drill + deny probe |
| C3 | Verification records with date, region, config, commit, failures, limits | CHANGE | VERIFICATION.md names commits by subject only | Add commit hashes and "does not prove" lines | Done: VERIFICATION.md header table and section 7 (date, region, config, commit `da3ffcd`, failures, "does not prove" lines); DEPLOYMENT-LOG second record |
| D1 | Zero unhandled static findings; trust policies with SourceAccount + SourceArn; resource policies on sensitive resources | CHANGE | cdk-nag clean; AgentRole SourceArn is `...:*` (stack.py:105-107); Lambda role has no conditions (stack.py:209); no runtime or bucket resource policy beyond TLS | Narrow, add, or document with official link | Done: cdk-nag 0 unacknowledged (15 per-construct acknowledgements); runtime role trust: SourceAccount + SourceArn ArnLike `runtime/<name>-*`; Lambda role: documented exception with official link (SECURITY-NOTES, D35); resource policies on the runtime (deny others, deny *ForUser) and the media bucket; deny probe confirmed AccessDenied in AWS |
| D2 | CI (lint, tests, synth, secret scan), SHA-pinned actions, least permissions, Dependabot, gitleaks allowlist reasons | CHANGE | no .github/ | Add workflows, dependabot, .gitleaks.toml | Done: .github/workflows/ci.yml (lint, tests, negative control, bundle + synth + cdk-nag, gitleaks full history + canary), deploy-verify.yml (workflow_dispatch, OIDC, environment, placeholder role ARN), SHA-pinned actions resolved with git ls-remote, `permissions: contents: read`, dependabot.yml (weekly, groups, cooldown, reasons), .gitleaks.toml (default rules, no exceptions), CODEOWNERS. zizmor: 0 findings. Not run on GitHub (local equivalents only) |
| D3 | No account-level settings changed | OK | none changed | — | OK: no account-level setting changed (Transaction Search left to the owner, OPEN-QUESTIONS Q15) |
| E1 | Cleanup script with confirmations; residual check; README list of non-auto-deleted resources | CHANGE | scripts/verify_cleanup.sh exits 0 even with leftovers; no cleanup script; list only in docs/cleanup.md | Add scripts/cleanup.sh, non-zero exit, README list | Done: scripts/cleanup.sh (two confirmations, warn-and-continue, service log groups, workload identities, retained buckets, project bootstrap objects by hash, all versions); scripts/verify_cleanup.py exits 1 on leftovers and confirms tag hits; README "Clean up" lists resources not deleted automatically |
| E2 | Every used region checked, clear conclusion | OK | DEPLOYMENT-LOG.md (us-east-1 only, conclusion stated) | Repeat after final deploy | Done: us-east-1 (only region used) checked after the final destroy: exit 0, "已清理，无残留" (DEPLOYMENT-LOG) |
| F1 | Fact check with official links and dates; cost as dimensions; README section order | CHANGE | docs/architecture.md fact table OK; README order differs from the standard | Reorder README (en/zh) | Done: README (en/zh) in the standard order with fixed closing sections; deploy commands only in README; cost dimensions + official pricing links, measured volume, Budgets link; fact table with official links and dates (docs/architecture.md) |
| F2 | Conventional Commits with verification in the body; no push | CHANGE | history rewritten to Conventional Commits before this pass; bodies of early commits lack verification notes | New commits follow the rule | Done: history rewritten once to Conventional Commits before this pass; every new commit is Conventional with what/why/verification; nothing pushed |

## AgentCore checklist

| # | Item | Initial | Evidence (before) | Planned handling | Final |
|---|---|---|---|---|---|
| 1 | User id only from verified identity | N/A | No end-user identity exists; callers are IAM principals (SigV4). Camera scope comes from the payload (THREAT_MODEL R3) | Document; authoriser is a hardening item | N/A (unchanged): no end-user identity; callers are SigV4 IAM principals restricted by the runtime resource policy; per-user authorisation is hardening item 1 |
| 2 | `runtimeUserId` / `InvokeAgentRuntimeForUser` restricted; runtime resource policy | CHANGE | `runtimeUserId` not used; no runtime resource policy | Add resource policy: only the dispatcher role and the deploying operator | Done: `runtimeUserId` not used; `InvokeAgentRuntimeForUser*` denied for everyone; runtime resource policy allows only the dispatcher and operators (stack.py `RuntimeResourcePolicy`; deny probe AccessDeniedException) |
| 3 | Trust policies with SourceAccount and SourceArn | CHANGE | see D1 | Narrow AgentRole SourceArn to this runtime; Lambda role per official docs | Done for the runtime role (both keys, narrowed SourceArn, verified in deployment); Lambda and CDK handler roles: documented exception (SECURITY-NOTES, official Lambda guide link) |
| 4 | Identity permissions narrowed; actions covered by the service-linked role removed | CHANGE | stack.py:129 grants GetWorkloadAccessToken* although the code never uses workload tokens | Remove and verify in deployment | Done: GetWorkloadAccessToken* removed (code never uses workload tokens); deployment and all invocations succeeded without it |
| 5 | Gateway default-deny Cedar policy | N/A | no Gateway | — | N/A: no Gateway |
| 6 | Memory `failedRecords` checked | N/A | no Memory | — | N/A: no Memory |
| 7 | Eventual consistency | N/A for Memory | results are one S3 object per clip (strongly consistent read-after-write) | — | N/A: no Memory; results are single S3 objects (strong read-after-write) |
| 8 | Memory namespaces / IAM | N/A | no Memory | — | N/A: no Memory |
| 9 | `lifecycleConfiguration` explicit and aligned | OK | stack.py lifecycle 300 s idle / 1800 s max; one session per clip | Note alignment in docs | OK: lifecycle 300 s idle / 1800 s max; parameter validation requires max lifetime >= dispatcher timeout (one session per clip) |
| 10 | Credentials fetched per request, not at import | OK | boto3 clients created inside request functions (service.py, models.make_model) | — | OK: boto3 clients are created per request inside functions; only configuration (not credentials) is cached |
| 11 | Runtime updates when code changes; explicit dependency order | OK | code asset key is a content hash; runtime depends on its policy | — | OK: asset key = content hash, so code changes update the runtime; runtime depends on its policy; resource policy depends on the dispatcher role |
| 12 | `trace_attributes` | N/A | no agent framework / ADOT; observability is EMF + structured logs | Backlog | N/A: no agent framework or ADOT; documented in handover item 13 and DECISIONS D41 |
| 13 | Memory/Gateway APPLICATION_LOGS delivery | N/A | no Memory/Gateway | — | N/A: no Memory or Gateway |
| 14 | Transaction Search prerequisite documented | CHANGE | not mentioned | Document as an account-level setting left to the owner | Done: handover item 13 and OPEN-QUESTIONS Q15 (account-level, not changed) |
| 15 | Versioned eval set with expected results and a deliberately wrong counter-example | CHANGE | manifest has expected events, no version field, no negative control | Add version + negative control that must fail | Done: manifest version 1.0; expected events per clip; WrongVisionModel negative control must fail (F1 0.042, CI step and test); `--min-f1` gate; online/code evaluators listed as future work |
| 16 | Every nag acknowledgement has a reason; scans run | OK (scans partial) | SECURITY-NOTES.md; ASH incomplete | Re-run scans | Done: every acknowledgement has a reason (SECURITY-NOTES); gitleaks (history + canary), detect-secrets, zizmor, osv-scanner, ASH (see VERIFICATION 7.3) run |
| 17 | Cleanup covers service log groups, bootstrap assets, vended logs | CHANGE | docs/cleanup.md covers log groups; bootstrap assets only recorded | Delete project-owned bootstrap objects by hash | Done: README "Clean up", docs/cleanup.md, scripts/cleanup.sh cover service log groups, workload identities and bootstrap assets; vended logs N/A (no Memory/Gateway) |

## Summary

| | General (A-F, 17 items) | AgentCore (17 items) | Total |
|---|---|---|---|
| Met / done | 17 (2 already OK: D3, E2; 15 changed) | 10 (4 already OK: 9, 10, 11, 16; 6 changed: 2, 3, 4, 14, 15, 17) | 27 |
| Not applicable | 0 (.dockerignore inside B3 is N/A: no Docker build) | 7 (1 no end-user identity; 5 no Gateway; 6, 7, 8, 13 no Memory/Gateway; 12 no agent framework/ADOT) | 7 |
| Open | 0 | 0 | 0 |

Partial points that stay open by design: the Lambda execution role trust policy has no
SourceAccount/SourceArn conditions (official Lambda guidance, D35); CI was not run on GitHub; ASH
scanners that need Docker or extra tools did not run locally (VERIFICATION 7.3).
