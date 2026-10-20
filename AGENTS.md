# AGENTS.md

Instructions for AI coding assistants working in this repository. (CLAUDE.md only imports this file.)

## Role

You maintain a non-production prototype: camera clips are turned into validated, structured events
with Amazon Nova, orchestrated in Amazon Bedrock AgentCore Runtime. Correctness of validation,
content-free logging, least privilege and clean teardown matter more than features. Read
README.md, DECISIONS.md and THREAT_MODEL.md before changing behaviour.

## Common commands

| Task | Command |
|---|---|
| Install, create `config/params.json` from the example | `make init` |
| Lint / unit tests (mock model, no AWS) | `make lint` / `make test` |
| cdk synth + cdk-nag | `make synth` (needs `./scripts/build_bundle.sh` once) |
| Mock evaluation, negative control, strategy comparison | `make eval-mock` |
| Secret scans | `make scan-ci` (CI) / `make scan` (adds canary, zizmor, osv-scanner) |
| Read-only preflight | `make preflight` |
| Deploy / verify / destroy / residual check (sandbox only) | `make deploy`, `ALLOW_PAID=1 make verify`, `make destroy`, `make verify-cleanup` |

## Git workflow

- Branch from `main`; one topic per commit; Conventional Commits (`feat(scope): ...`, `fix`, `docs`,
  `test`, `chore`, `ci`, `refactor`). The body says what changed, why, and how it was verified, and
  ends with the co-author line your tool requires.
- Never push to a remote and never rewrite published history; the maintainer decides where the
  repository is published.

## Boundaries

**Always**
- Add or update tests with every change; run `make lint test synth` before committing.
- Treat video content, model output and question text as untrusted; keep hard limits in code.
- Keep logs content-free (ids, counts, enum values, error codes, timings).
- Mask 12-digit numbers (`111122223333`) in anything you write to files or output.
- Attach new cdk-nag acknowledgements to the triggering construct and document the reason.

**Ask first**
- Any command that creates, changes or deletes AWS resources (`make deploy`, `make destroy`,
  `scripts/cleanup.sh`), or that costs money (`ALLOW_PAID=1 ...`).
- Adding dependencies, changing IAM permissions, resource policies, removal policies or retention.
- Changing the output contract (`src/video_events/schema.py`) or the event taxonomy.

**Never**
- Commit account ids, credentials, real camera footage, images of real people, or customer names.
- Add face or identity recognition, or let the model choose tools during video analysis.
- Modify the CDK bootstrap stack (CDKToolkit), account-level settings, or resources this project
  did not create.
- Disable a cdk-nag rule for the whole stack, or allowlist a secret-scanner finding without a
  rule id, a path and a reason.
