# Camera event analysis with Amazon Nova and Amazon Bedrock AgentCore

> **Disclaimer.** This is a prototype for demonstration and learning only. It is **not intended for
> production use**. You are responsible for anything you deploy with it, for how you use it, and for
> the consequences. Before using it with any real data, review it with your own security and legal
> teams and follow the [AWS Shared Responsibility Model](https://aws.amazon.com/compliance/shared-responsibility-model/).
> Use a non-production (ideally disposable) account and only synthetic or consented test data. See
> the [hardening checklist](docs/handover.md#hardening-checklist-before-production) before any
> production use.

[中文说明](README.zh-CN.md) · Version 0.1.0 ([CHANGELOG](CHANGELOG.md))

This project turns short clips from a home or small-business camera into **structured, validated
events** and a summary:

- **Risk events**: fire or smoke, violence, intrusion, forced entry, suspicious loitering, possible
  theft, each with a risk level (emergency, high, needs attention, low).
- **Behaviour events**: person lingering, package delivery, entry or exit, vehicle parking, object
  carrying, pet activity.
- Every event has start and end times, a confidence level, a location and a **non-identity**
  subject ("person", "courier-like person"). There is no face or identity recognition.
- A **scene type** (home, pet, elderly, child, store, yard, fish pond) and a short summary.

It also includes hard validation with retry and an explicit `needs_review` fallback, a synthetic
evaluation set with a negative control, a strategy comparison (whole-clip video against sampled
frames), localised text, structured watch rules, Q&A, keyword search and a daily summary over the
stored events, and a mock mode that runs without AWS.

## Architecture

```
S3 uploads/ ──event──> Lambda (arm64) ──InvokeAgentRuntime──> AgentCore Runtime (arm64, zip)
     │                    └─ on failure ─> SQS queue           │  PyAV frame sampling
     │                                                         │  Nova 2 Lite (Converse)
     │                                                         │  validate → merge → post-process
     └──────────────────────────────── results/<CAMERA_ID>/<DATE>/<CLIP>.json <─┘
operator ──InvokeAgentRuntime (SigV4, allowed by the runtime resource policy)──> ask / search / daily_summary
```

Components, trust boundaries, deployment topology, trade-offs and the dated fact check are in
[docs/architecture.md](docs/architecture.md) (the mermaid diagrams there are the source of truth).

## Prerequisites and cost

- A non-production AWS account and Region where Amazon Bedrock AgentCore Runtime and the configured
  Nova inference profile are available (`make preflight` checks both), already bootstrapped for CDK.
- Python 3.13 with [uv](https://docs.astral.sh/uv/), Node.js (for `npx -y aws-cdk@2`), AWS CLI v2,
  `ffmpeg` (only to generate the synthetic set). **Docker is not needed.**
- An IAM principal that can deploy CloudFormation stacks with IAM roles.

Cost: you pay for what you use; no free tier is assumed. Billing dimensions are Amazon Bedrock input
and output tokens (video frames and images count as input tokens), AgentCore Runtime (session
compute and memory), Lambda requests and duration, S3 storage and requests, SQS requests, CloudWatch
Logs ingestion and storage, and custom metrics. See [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/),
[Amazon Bedrock AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/),
[AWS Lambda pricing](https://aws.amazon.com/lambda/pricing/), [Amazon S3 pricing](https://aws.amazon.com/s3/pricing/)
and [Amazon CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/). For a measured example of
volume: one run of the 16-clip synthetic set with the default configuration made 51 model calls with
about 164,000 input tokens (VERIFICATION.md). Consider an
[AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create.html) alert on
the account.

## Deploy

All parameters are in one file, created from the template:

```bash
make init                                   # uv sync; copies config/params.example.json to config/params.json
./scripts/build_bundle.sh                   # arm64 wheels + code, no Docker
CONFIRM_ACCOUNT=<LAST_4_DIGITS> make deploy # preflight, refuses deletions/replacements, then cdk deploy
```

`make deploy` runs the read-only preflight (account last 4 digits and region confirmation, tools,
service availability), validates the parameter file (synth fails on any invalid value), shows the
`cdk diff` and refuses deletions or replacements unless `ALLOW_DESTRUCTIVE=1`, and passes the
deploying IAM principal as the operator allowed by the resource policies. The account id never goes
into a file. `config/params.customer.example.json` is the hand-over variant (retains data). Every
parameter is described in [docs/deployment-guide.md](docs/deployment-guide.md).

## Use and verify

```bash
uv run python scripts/gen_synthetic.py --out sample-data/eval   # 16 synthetic clips
ALLOW_PAID=1 make verify                                         # e2e (all clips) + failure drill
```

`make verify` runs the `health` action in the runtime and the Lambda (PyAV on arm64), uploads the
clips to `uploads/<CAMERA_ID>/<YYYYMMDDTHHMMSSZ>_<NAME>.mp4`, waits for `results/…json`, validates
and scores them, calls `ask`, `search` and `daily_summary`, checks the foreign-bucket guard, and then
runs the failure drill (invalid model output, model error, failure queue, log scan). The demo
script is in [docs/demo-script.md](docs/demo-script.md).

Results so far (synthetic set only; real-world accuracy is not measured; details in
[VERIFICATION.md](VERIFICATION.md)):

- With real Nova 2 Lite (3 runs per configuration): frames every 2 s gave F1 0.518 ± 0.028 with 51
  calls; every 4 s gave 0.536 ± 0.032 with 33 calls but found fewer event types and never reported
  the 1.5 s fire. Whole-clip video input returned unusable output for every clip (`fallback`). No
  configuration raised a false alarm on the no-event clips, including the one with injected text.
- The negative control (a deliberately wrong model) scores F1 0.04 and is reported as failing.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `config/params.json not found` | Run `make init`. |
| `invalid parameter file: ...` during synth | The listed values break a rule in `infra/video_infra/params.py`; fix them. |
| Preflight stops at the account check | Set `CONFIRM_ACCOUNT` to the last 4 digits of the intended account, or stop if it is the wrong account. |
| `make deploy` exits with "deletes or replaces resources" | Review the diff; re-run with `ALLOW_DESTRUCTIVE=1` only if intended. |
| `AccessDeniedException` on `InvokeAgentRuntime` | The caller is not the dispatcher or an operator in the runtime resource policy. Redeploy with `make deploy` as that principal (or pass `-c operator_principal_arns=...`). |
| Upload produces no result | Key does not match `uploads/<CAMERA_ID>/<YYYYMMDDTHHMMSSZ>_<NAME>.<mp4\|mov\|mkv\|webm>` (logged as `request_rejected`), or an internal error: check the `<PREFIX>-dispatch-failures` queue. |
| Result has `status: fallback` | Model output failed validation or the model call failed; `fallback_reason` holds the field path / error code. |
| `... calls paid AWS services` | Set `ALLOW_PAID=1` for commands that invoke Amazon Bedrock. |

## Security and limitations

Security controls, IAM scopes, resource policies and every cdk-nag acknowledgement are in
[SECURITY.md](SECURITY.md) and [SECURITY-NOTES.md](SECURITY-NOTES.md); threats and residual risks in
[THREAT_MODEL.md](THREAT_MODEL.md) (draft). What the add-on features cover, and what they do not:

- **Search** is a keyword parser (Chinese, English, Russian) that turns a question into a structured
  filter over stored events. It is not semantic or embedding search, and the `search` action never
  calls a model, also with `model_mode=bedrock`. Only `ask` in `bedrock` mode uses Nova, with one
  read-only `search_events` tool.
- **Daily summary** is a deterministic aggregation of stored events; no model writes it.
- **Multiple languages**: zh-CN, en-US and ru-RU cover the templated headlines, the overview line and
  the daily-summary text only. The model's free-text summary and descriptions stay in English.
- **Watch rules** are one global, structured list in the pipeline configuration (location, subject,
  type, level, local time window), not per user and not natural language. High and emergency risk
  events always notify.
- **Events are not merged across clips.** "Someone stayed at the door for more than 1 minute" only
  matches an event that lasts that long inside one clip; the synthetic clips are 30-45 s, so that
  demo query returns no events.
- Results that hit a read cap are flagged `truncated: true` and the answer says so.
- No end-user authentication; callers are IAM principals. Prompt injection through visible text can
  cause missed events. See the [hardening checklist](docs/handover.md#hardening-checklist-before-production).

## Clean up

```bash
make destroy         # asks for the stack name, and again before deleting data or bootstrap objects
make verify-cleanup  # read-only; exits non-zero if anything of this project is left
```

Do not assume everything is gone because a command finished; run the residual check. Resources that
are **not** deleted automatically by `cdk destroy`, and how they are handled:

| Resource | Handling |
|---|---|
| Log groups of the CDK custom-resource Lambdas (`/aws/lambda/<STACK>-…`) | Deleted by `make destroy` |
| AgentCore runtime log group `/aws/bedrock-agentcore/runtimes/<RUNTIME_ID>-DEFAULT` (created by the service) | Deleted by the `RuntimeLogRetention` custom resource on destroy; `make destroy` deletes it if still there |
| Workload identity named after the runtime (created by the service) | Normally removed with the runtime; `make destroy` deletes it if still there |
| Media and access-log buckets with `removal_policy=retain` (hand-over file) | Kept with their data; `make destroy` deletes them only after a second confirmation |
| This project's code packages and templates in the CDK bootstrap assets bucket | Deleted by `make destroy` (by asset hash; CDK's generic provider packages and the CDKToolkit stack are not touched) |
| CDK bootstrap stack (CDKToolkit) | Never touched |
| CloudWatch custom metrics (`VideoEventAnalysis`) | Cannot be deleted; they expire when no new data arrives |

More detail: [docs/cleanup.md](docs/cleanup.md).

## Repository structure

| Path | Content |
|---|---|
| `src/video_events/` | Core library: taxonomy, schema, validation, sampling, models, pipeline, post-processing, store, query |
| `src/agent/` | AgentCore Runtime entrypoint |
| `src/functions/dispatcher/` | S3-triggered dispatcher Lambda |
| `infra/` | CDK (Python) app, stack, parameter validation, bootstrap asset key history |
| `config/` | Parameter templates (`params.example.json` sandbox, `params.customer.example.json` hand-over) |
| `scripts/` | Synthetic generator, evaluation, strategy comparison, bundle build, preflight, deploy, e2e, failure drill, cleanup, residual check, secret canary |
| `tests/` | Unit and infrastructure tests |
| `sample-data/eval/manifest.json` | Versioned labels of the synthetic set (clips are generated) |
| `docs/` | Architecture, deployment guide, demo, cleanup, handover, prior art, standards review |
| `.github/` | CI, manual deploy-verify workflow, Dependabot, issue and PR templates, CODEOWNERS |

Governance: [DECISIONS.md](DECISIONS.md), [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md),
[THREAT_MODEL.md](THREAT_MODEL.md), [SECURITY-NOTES.md](SECURITY-NOTES.md),
[VERIFICATION.md](VERIFICATION.md), [DEPLOYMENT-LOG.md](DEPLOYMENT-LOG.md),
[THIRD-PARTY-LICENSES](THIRD-PARTY-LICENSES), [AGENTS.md](AGENTS.md).

## Local testing (no AWS)

```bash
make init
uv run python scripts/gen_synthetic.py --out sample-data/eval      # needs ffmpeg
make lint test                                                     # ruff + unit tests (mock model)
make eval-mock                                                     # evaluation, negative control, strategy grid
uv run python scripts/run_local.py analyze --all --strategy video
uv run python scripts/run_local.py ask "今天谁到过门口？" --now 2026-10-19T23:59:00Z
uv run python scripts/run_local.py search "找出所有有人在门口停留超过1分钟的视频" --now 2026-10-19T23:59:00Z
uv run python scripts/run_local.py daily --date 2026-10-19
```

## Contributing

See [CONTRIBUTING](CONTRIBUTING.md) for more information.

## Security

See [SECURITY](SECURITY.md) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
