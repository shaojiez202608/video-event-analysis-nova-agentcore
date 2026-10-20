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
uv run python scripts/run_local.py search "找出所有有人在门口停留超过1分钟的视频" --now 2026-10-19T23:59:00Z
uv run python scripts/run_local.py daily --date 2026-10-19
uv run python scripts/compare_strategies.py
```

## What the add-on features cover (and what they do not)

- **Search** is a keyword parser (Chinese, English, Russian) that turns a question into a structured
  filter over stored events. It is not semantic or embedding search, and the `search` action never
  calls a model, also with `model_mode=bedrock`. Only `ask` in `bedrock` mode uses Nova, with one
  read-only `search_events` tool.
- **Daily summary** is a deterministic aggregation of stored events (counts, abnormal hours, key
  events, clips to review). No model writes it.
- **Multiple languages**: zh-CN, en-US and ru-RU cover the templated headlines, the overview line
  and the daily-summary text only. The model's free-text summary and descriptions stay in English.
- **Watch rules** are one global, structured list in the pipeline configuration (location, subject,
  type, level, local time window). They are not per user and not natural language. High and
  emergency risk events always notify, whatever rules are set.
- **Events are not merged across clips.** "Someone stayed at the door for more than 1 minute" only
  matches an event that lasts that long inside one clip; the synthetic clips are 30-45 s, so that
  demo query returns no events.
- Results that hit a read cap are flagged `truncated: true` and the answer says so.

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

- 154 unit tests pass. cdk-nag (AwsSolutions) reports no unacknowledged findings, also with another
  resource prefix and model id.
- Deployed end to end in us-east-1: S3 to Lambda to AgentCore Runtime to Nova 2 Lite to results
  (16/16 clips, 0 fallback). PyAV runs on arm64 in both the runtime and the Lambda. Q&A, search and
  the daily summary work. A failure drill confirmed that invalid model output and model errors end
  as stored `fallback` results, that internal errors reach the failure queue, and that no picture
  description reached the logs. The stack was destroyed and leftovers were checked
  ([DEPLOYMENT-LOG.md](DEPLOYMENT-LOG.md)).
- With real Nova 2 Lite (3 runs per configuration): frames every 2 s gave F1 0.518 ± 0.028 with
  51 calls; every 4 s gave 0.536 ± 0.032 with 33 calls but found fewer event types and never reported
  the 1.5 s fire. Whole-clip video input returned unusable output and every clip went to `fallback`.
  No configuration raised a false alarm on the no-event clips, including the one with injected text.

## Before production

- End-user authentication and per-user camera authorisation (for example, an inbound authorizer on
  the runtime).
- Bind uploads to device identity.
- Add idempotency, a replay path for the failure queue, and alarms on fallback and partial rates.
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
`infra/` CDK (Python) · `scripts/` generator, evaluation, comparison, build, e2e, failure drill, cleanup check ·
`tests/` · `sample-data/eval/manifest.json` labels (clips are generated).

Governance documents: [DECISIONS.md](DECISIONS.md), [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md),
[THREAT_MODEL.md](THREAT_MODEL.md), [SECURITY-NOTES.md](SECURITY-NOTES.md), [SECURITY.md](SECURITY.md),
[docs/prior-art.md](docs/prior-art.md).

## License

MIT-0. See [LICENSE](LICENSE).
