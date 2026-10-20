# Security

## Reporting a vulnerability

If you discover a potential security issue in this project, notify AWS Security through the
[AWS vulnerability reporting page](https://aws.amazon.com/security/vulnerability-reporting/) or by
email to [aws-security@amazon.com](mailto:aws-security@amazon.com). **Do not open a public GitHub
issue.** Do not include real video, personal data, account ids or credentials in the report.

## What this project deploys

One CloudFormation stack (`infra/video_infra/stack.py`) in a non-production account:

- an S3 media bucket (uploads, sampled frames if enabled, result JSON) and an access-log bucket;
- a dispatcher Lambda function (arm64) triggered by S3 uploads, with an SQS on-failure queue;
- an Amazon Bedrock AgentCore Runtime (direct code deployment, arm64) that samples frames, calls
  Amazon Nova through the Bedrock Converse API, validates the output and writes results;
- an SSM parameter with the pipeline configuration, IAM roles and policies, CloudWatch log groups,
  and CDK custom resources (bucket notifications, auto-delete objects, runtime log retention).

There is no public endpoint: the inputs are S3 uploads and SigV4-signed `InvokeAgentRuntime` calls.

## How data is handled

- Clips, frames and results are personal data in a real deployment. Use synthetic or consented test
  data only. The repository contains no real footage; the evaluation clips are generated.
- Frames stay in memory unless `persist_frames=true`; S3 lifecycle rules expire `uploads/`,
  `frames/` and `results/` (days set in the parameter file).
- Logs carry identifiers, counts, enumerated values, error codes and timings, never descriptions,
  summaries, questions or frames.
- Requests to Nova go to the configured inference profile (default `us.`: US regions only).

## Controls and where they are implemented

| Control | Implementation |
|---|---|
| Video and model output are untrusted; analysis order is fixed in code, the model has no side-effecting tools | `src/video_events/pipeline.py`, `src/agent/main.py`, DECISIONS D2 |
| Hard validation of model output (schema, whitelists, time ranges, identity-text redaction), retry and explicit fallback | `src/video_events/validation.py`, `src/video_events/pipeline.py` |
| Request guards: own bucket only, key pattern, size and duration caps, bounded overrides, per-clip model-call cap | `src/video_events/service.py`, `src/video_events/config.py` |
| Downstream failures become stored `fallback` results or SQS failure-queue messages, never silent drops or HTTP 500 | `src/video_events/pipeline.py`, `src/video_events/service.py`, `src/functions/dispatcher/handler.py` |
| Content-free logs | `src/video_events/metrics.py`, `src/video_events/validation.py` (`_short`) |
| Q&A tool: one read-only tool, schema-validated arguments, camera scope injected by code, no free text returned to the model | `src/video_events/query.py` |
| Least-privilege IAM, runtime trust policy with `aws:SourceAccount` and `aws:SourceArn` | `infra/video_infra/stack.py`, SECURITY-NOTES.md |
| Resource policies: runtime invocable only by the dispatcher and named operators; media objects only by stack roles and operators | `infra/video_infra/stack.py` |
| S3 Block Public Access, TLS only, SSE-S3, access logging; SSE-SQS; log retention | `infra/video_infra/stack.py` |
| Parameter validation at synth; preflight; destructive-change and paid-command guards | `infra/video_infra/params.py`, `scripts/preflight.sh`, `scripts/deploy.sh`, `scripts/guards.py` |
| cdk-nag AwsSolutions with a reason for every acknowledgement; secret scanning with a canary | `infra/app.py`, SECURITY-NOTES.md, `.github/workflows/ci.yml`, `scripts/secret_canary.sh` |

## Known caveats

- No end-user authentication or per-user camera authorisation: any principal allowed by the runtime
  resource policy can query any camera id (THREAT_MODEL R3).
- Visible text in the video can make the model miss events (prompt injection, R1).
- PyAV and its bundled FFmpeg libraries parse untrusted media (R5); see THIRD-PARTY-LICENSES for
  their licences.
- The AgentCore runtime uses `PUBLIC` network mode (outbound to public AWS endpoints).

## Before production

See the hardening checklist in [docs/handover.md](docs/handover.md): end-user authentication
(for example a JWT authorizer on the runtime), upload identity, idempotency and alarms, rate limits
and budgets, KMS keys, VPC mode, data residency, approved risk policy, evaluation on representative
data, dependency patching and licence review, and the retain-by-default parameter file.

## Cleanup

`make destroy` then `make verify-cleanup` (see README "Clean up"). The residual check exits non-zero
if anything of this project is left.
