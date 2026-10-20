# Standards review

Review of this repository against the project's general prototype checklist (sections A-F) and
the AgentCore checklist (items 1-17). This first version records the state **before** the
standards pass; the "Final" column is filled in after the changes.

Status values: OK = already met, CHANGE = needs a change, N/A = not applicable (reason given).

## General checklist

| # | Item | Initial | Evidence (before) | Planned handling | Final |
|---|---|---|---|---|---|
| A1 | README first screen: non-production, user responsible, security/legal review, link to hardening | CHANGE | README.md:3-5 only says "Prototype. Not for production use" | Three-point disclaimer + link | |
| A2 | No customer/person names, domains, 12-digit numbers, profile names, `[INSERT` leftovers | CHANGE | grep clean, but `<ACCOUNT_ID>` placeholder in tests/test_models.py:31, tests/test_service.py:97, scripts/e2e_aws.py:30 | Replace with `111122223333`; re-grep | |
| A3 | Placeholders `<UPPER_SNAKE>`, `example.com`, `111122223333`; synthetic data; screenshot provenance | CHANGE | docs/cleanup.md:14 `<MediaBucketName>`, `<runtime-id>`; no screenshots | Normalise placeholders | |
| B1 | LICENSE with Amazon copyright line; full SECURITY; CoC; CONTRIBUTING | CHANGE | LICENSE:3 personal line; SECURITY.md lacks AWS contact and controls; CODE_OF_CONDUCT.md points to Contributor Covenant | Rewrite all four | |
| B2 | THIRD-PARTY-LICENSES (two tables); CHANGELOG + VERSION | CHANGE | files missing | Add, licences from package metadata | |
| B3 | Issue/PR templates, AGENTS.md + CLAUDE.md, Makefile, .gitignore/.dockerignore | CHANGE | none present; .gitignore lacks config/params.json, cdk.context.json, .env* | Add; .dockerignore N/A (no Docker build) | |
| B4 | DECISIONS / OPEN-QUESTIONS / SECURITY-NOTES / THREAT_MODEL (DRAFT) / VERIFICATION / DEPLOYMENT-LOG | CHANGE | all exist; THREAT_MODEL.md:1 has no DRAFT status | Mark DRAFT | |
| C1 | Config as template, input validation, removal policy parameter, uniform tags | CHANGE | config/params.json committed as the live file; deploy section not validated (infra/app.py:23-24) | `*.example.json` + `make init`; validate deploy and pipeline at synth | |
| C2 | Preflight, destructive-change guard, smoke/e2e test | CHANGE | e2e exists (scripts/e2e_aws.py); no preflight, no diff guard, no paid-command guard | `make preflight`, `ALLOW_DESTRUCTIVE`, `ALLOW_PAID` | |
| C3 | Verification records with date, region, config, commit, failures, limits | CHANGE | VERIFICATION.md names commits by subject only | Add commit hashes and "does not prove" lines | |
| D1 | Zero unhandled static findings; trust policies with SourceAccount + SourceArn; resource policies on sensitive resources | CHANGE | cdk-nag clean; AgentRole SourceArn is `...:*` (stack.py:105-107); Lambda role has no conditions (stack.py:209); no runtime or bucket resource policy beyond TLS | Narrow, add, or document with official link | |
| D2 | CI (lint, tests, synth, secret scan), SHA-pinned actions, least permissions, Dependabot, gitleaks allowlist reasons | CHANGE | no .github/ | Add workflows, dependabot, .gitleaks.toml | |
| D3 | No account-level settings changed | OK | none changed | — | |
| E1 | Cleanup script with confirmations; residual check; README list of non-auto-deleted resources | CHANGE | scripts/verify_cleanup.sh exits 0 even with leftovers; no cleanup script; list only in docs/cleanup.md | Add scripts/cleanup.sh, non-zero exit, README list | |
| E2 | Every used region checked, clear conclusion | OK | DEPLOYMENT-LOG.md (us-east-1 only, conclusion stated) | Repeat after final deploy | |
| F1 | Fact check with official links and dates; cost as dimensions; README section order | CHANGE | docs/architecture.md fact table OK; README order differs from the standard | Reorder README (en/zh) | |
| F2 | Conventional Commits with verification in the body; no push | CHANGE | history rewritten to Conventional Commits before this pass; bodies of early commits lack verification notes | New commits follow the rule | |

## AgentCore checklist

| # | Item | Initial | Evidence (before) | Planned handling | Final |
|---|---|---|---|---|---|
| 1 | User id only from verified identity | N/A | No end-user identity exists; callers are IAM principals (SigV4). Camera scope comes from the payload (THREAT_MODEL R3) | Document; authoriser is a hardening item | |
| 2 | `runtimeUserId` / `InvokeAgentRuntimeForUser` restricted; runtime resource policy | CHANGE | `runtimeUserId` not used; no runtime resource policy | Add resource policy: only the dispatcher role and the deploying operator | |
| 3 | Trust policies with SourceAccount and SourceArn | CHANGE | see D1 | Narrow AgentRole SourceArn to this runtime; Lambda role per official docs | |
| 4 | Identity permissions narrowed; actions covered by the service-linked role removed | CHANGE | stack.py:129 grants GetWorkloadAccessToken* although the code never uses workload tokens | Remove and verify in deployment | |
| 5 | Gateway default-deny Cedar policy | N/A | no Gateway | — | |
| 6 | Memory `failedRecords` checked | N/A | no Memory | — | |
| 7 | Eventual consistency | N/A for Memory | results are one S3 object per clip (strongly consistent read-after-write) | — | |
| 8 | Memory namespaces / IAM | N/A | no Memory | — | |
| 9 | `lifecycleConfiguration` explicit and aligned | OK | stack.py lifecycle 300 s idle / 1800 s max; one session per clip | Note alignment in docs | |
| 10 | Credentials fetched per request, not at import | OK | boto3 clients created inside request functions (service.py, models.make_model) | — | |
| 11 | Runtime updates when code changes; explicit dependency order | OK | code asset key is a content hash; runtime depends on its policy | — | |
| 12 | `trace_attributes` | N/A | no agent framework / ADOT; observability is EMF + structured logs | Backlog | |
| 13 | Memory/Gateway APPLICATION_LOGS delivery | N/A | no Memory/Gateway | — | |
| 14 | Transaction Search prerequisite documented | CHANGE | not mentioned | Document as an account-level setting left to the owner | |
| 15 | Versioned eval set with expected results and a deliberately wrong counter-example | CHANGE | manifest has expected events, no version field, no negative control | Add version + negative control that must fail | |
| 16 | Every nag acknowledgement has a reason; scans run | OK (scans partial) | SECURITY-NOTES.md; ASH incomplete | Re-run scans | |
| 17 | Cleanup covers service log groups, bootstrap assets, vended logs | CHANGE | docs/cleanup.md covers log groups; bootstrap assets only recorded | Delete project-owned bootstrap objects by hash | |
