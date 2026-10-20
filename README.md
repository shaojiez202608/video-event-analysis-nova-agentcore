# Camera event analysis with Amazon Nova and Amazon Bedrock AgentCore

> **Prototype. Not for production use.** It is meant for prototype deployments in
> non-production accounts with synthetic or consented test data. See the
> [hardening checklist](#before-production) below.

Suggested repository name: `video-event-analysis-nova-agentcore`. [中文说明](README.zh-CN.md)

This project turns short clips from a home or small-business camera into **structured, validated
events** and a summary:

- **Risk events**: fire or smoke, violence, intrusion, forced entry, suspicious loitering, possible
  theft. Each gets a risk level (emergency, high, needs attention, low).
- **Behaviour events**: person lingering, package delivery, entry or exit, vehicle parking, object
  carrying, pet activity.
- Every event carries a start and end time, a confidence level, a location and a **non-identity**
  subject ("person", "courier-like person"). There is no face or identity recognition.
- A **scene type** (home, pet, elderly, child, store, yard, fish pond) and a short summary.

It also includes:

- **Hard validation**: JSON schema, a closed vocabulary for every field, time-range checks, retry,
  and an explicit fallback marked `needs_review`.
- A **synthetic evaluation set**: schematic clips generated with ffmpeg, with exact labels, covering
  no-event, single, multi, noise, night, short-event and prompt-injection cases.
- A **strategy comparison** of whole-clip video input against sampled frames at different
  intervals and segment lengths, reporting model calls, images and tokens next to F1, temporal IoU
  and short-event recall.
- **Post-processing**: localised text (zh-CN, en-US, ru-RU), structured watch rules ("people at
  the entrance only", "a person in the yard at night"), and Q&A, search and a daily summary over the
  stored events.
- A **mock mode**, so the whole flow runs without AWS.

## Architecture

```
S3 uploads/ ──event──> Lambda (arm64) ──InvokeAgentRuntime──> AgentCore Runtime (arm64, zip)
                                                                │  PyAV frame sampling
                                                                │  Nova 2 Lite (Converse)
                                                                │  validate → merge → post-process
                                                                └─> S3 results/<camera>/<date>/<clip>.json
operator ──InvokeAgentRuntime──> ask / search / daily_summary (Nova + one read-only tool)
```

See [docs/architecture.md](docs/architecture.md) for diagrams, trade-offs, limits and the dated
fact check.

## Quick start (local, no AWS)

```bash
uv sync
uv run python scripts/gen_synthetic.py --out sample-data/eval      # needs ffmpeg
uv run pytest
uv run python scripts/run_local.py analyze --all --strategy video
uv run python scripts/run_local.py ask "今天谁到过门口？" --now 2026-10-19T23:59:00Z
uv run python scripts/compare_strategies.py
```

## Deploy (sandbox)

```bash
./scripts/build_bundle.sh                      # arm64 wheels without Docker
npx -y aws-cdk@2 deploy -c params=config/params.json --require-approval never
uv run python scripts/e2e_aws.py --all
npx -y aws-cdk@2 destroy -c params=config/params.json --force && ./scripts/verify_cleanup.sh
```

All parameters are in one file (`config/params.json`; `config/params.customer.example.json` is the
hand-over variant, which retains data by default). See
[deployment guide](docs/deployment-guide.md), [demo script](docs/demo-script.md),
[handover](docs/handover.md) and [cleanup](docs/cleanup.md).

## Results so far

Measured on the synthetic set only; real-world accuracy is not measured. Details are in
[VERIFICATION.md](VERIFICATION.md).

- 93 unit tests pass. cdk-nag (AwsSolutions) reports no unacknowledged findings.
- Deployed end to end in us-east-1: S3 to Lambda to AgentCore Runtime to Nova 2 Lite to results.
  PyAV runs on arm64 in both the runtime and the Lambda. Q&A, search and the daily summary work.
  The stack was destroyed afterwards and leftovers were checked ([DEPLOYMENT-LOG.md](DEPLOYMENT-LOG.md)).
- With real Nova 2 Lite, sampled frames with timestamps gave stable, valid output. Whole-clip video
  input often missed required fields, and the validator routed those clips to `fallback`. No
  strategy produced a false alarm on the no-event clips, including the one with injected text.

## Before production

- End-user authentication and per-user camera authorisation (for example, an inbound authorizer on
  the runtime).
- Bind uploads to device identity.
- Add a dead-letter queue, idempotency, and alarms on fallback and partial rates.
- Add rate limits and budget alarms.
- Decide on KMS keys and VPC network mode.
- Set the inference profile scope to match data residency.
- Get business sign-off on risk levels and thresholds.
- Evaluate on representative, consented data.
- Patch PyAV/FFmpeg and review their licences.
- Use the retain-by-default parameter file.

The full list is in [docs/handover.md](docs/handover.md).

## Repository map

`src/video_events/` core library · `src/agent/` AgentCore entrypoint · `src/functions/dispatcher/` Lambda ·
`infra/` CDK (Python) · `scripts/` generator, evaluation, comparison, build, e2e, cleanup check ·
`tests/` · `sample-data/eval/manifest.json` labels (clips are generated).

Governance documents: [DECISIONS.md](DECISIONS.md), [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md),
[THREAT_MODEL.md](THREAT_MODEL.md), [SECURITY-NOTES.md](SECURITY-NOTES.md), [SECURITY.md](SECURITY.md),
[docs/prior-art.md](docs/prior-art.md).

## License

MIT-0. See [LICENSE](LICENSE).
