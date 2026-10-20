# Handover

## What this is

A prototype that turns short camera clips into validated, structured events (risk and behaviour
types, risk level, time range, scene, location, non-identity subject) and a summary. It runs
Amazon Nova through Bedrock, orchestrated in Amazon Bedrock AgentCore Runtime. It also contains a
synthetic evaluation set, a strategy comparison, post-processing (localised text and watch rules),
and simple Q&A, search and daily summary over the stored results. **It is not for production use.**

## How to run it

- Locally, without AWS: `uv sync && uv run pytest` and the demo in demo-script.md part A.
- Deployed: deployment-guide.md. Remove it again with cleanup.md.

## Defaults that differ between sandbox and hand-over

| Setting | `config/params.json` (sandbox) | `config/params.customer.example.json` (hand-over) |
|---|---|---|
| `deploy.removal_policy` | `destroy`: buckets and objects are deleted with the stack | `retain`: buckets are kept when the stack is deleted |
| `pipeline.prompt_hint` | describes the synthetic rendering convention | empty |

Review the retention days (`video_retention_days`, `frame_retention_days`, `result_retention_days`)
with the data owner before any use beyond synthetic data.

## Measured behaviour (see VERIFICATION.md for the numbers)

- On the synthetic set, sending sampled frames with timestamps (`strategy=frames`) gave valid,
  scorable output from Nova 2 Lite. Sending the whole clip (`strategy=video`) often returned output
  with missing required fields, which the validator turned into `fallback` results. This is why
  the deployed default is `frames`. Re-measure on representative non-production footage before
  choosing a strategy.
- Forced tool output (`output_mode=tool`) did not guarantee that nested required fields were
  present. The validator is still needed.

## Hardening checklist before production

1. **Authentication and authorisation for end users.** Add an inbound authorizer to the AgentCore
   Runtime (`AuthorizerConfiguration`, for example JWT), map users to camera ids in a backend, and
   remove direct IAM invocation by operators.
2. **Upload identity.** Bind uploads to device identity (for example per-device credentials or
   presigned URLs issued by a backend) and stop deriving camera ids from object keys alone.
3. **Retries and failure handling.** Add a DLQ or on-failure destination for the dispatcher. Make
   analysis idempotent per object version, and alarm on `fallback` and `partial` rates.
4. **Rate limits and budgets.** Set per-camera limits and budget alarms on model usage. Review
   service quotas for the expected volume (OPEN-QUESTIONS Q2).
5. **Encryption keys.** Decide whether SSE-S3 is enough or whether customer-managed KMS keys are
   needed for buckets and logs.
6. **Network.** Consider VPC network mode for the runtime and VPC endpoints for S3, Bedrock and SSM.
7. **Data residency.** Choose the inference profile scope (single region, geography, global) to
   match residency requirements (OPEN-QUESTIONS Q5).
8. **Risk policy.** Replace the placeholder risk floors and thresholds with business-approved
   values (Q3, Q4).
9. **Detection robustness.** For high-stakes types (fire, violence), consider a second signal
   such as a dedicated detector or sensors, and keep human review. Prompt injection can cause
   misses (THREAT_MODEL R1).
10. **Evaluation on representative data.** The synthetic set does not measure real-world accuracy.
    Build a consented, non-production labelled set, using the same manifest format.
11. **Dependency patching and licences.** Keep PyAV and its bundled FFmpeg libraries current (they
    parse untrusted media), and review the licences (Q9).
12. **Runtime versions.** Move the Lambda and the AgentCore runtime together to the newest Python
    runtime when the arm64 wheels are available (the cdk-nag L1 acknowledgement).
13. **Observability.** Add dashboards and alarms on the EMF metrics (`VideoEventAnalysis`
    namespace) and enable AgentCore observability if traces are needed.
14. **Retain by default.** Use the hand-over parameter file (`removal_policy=retain`).

## Backlog (not implemented)

- Natural-language to watch-rule conversion ("只关注门口人员" → structured rule), with validation.
- Translating the free-text summary with the model (the localised text now comes only from templates).
- Semantic search with embeddings (for example multimodal embeddings) instead of keyword parsing;
  an index (DynamoDB or OpenSearch) instead of listing S3 objects.
- An LLM-written daily summary on top of the deterministic aggregation.
- Clip segmentation into smaller video files (needs an encoder dependency); images by S3 URI
  for larger frame batches.
- Adaptive sampling (denser around motion), and a scene type set by the user instead of
  inferred by the model (Q12).
- Streaming ingest (for example from a video stream service) and near-real-time alerts.
- A web UI.

## Ownership notes

- Model id, sampling and rules are in SSM (`/proto-video/pipeline-config`). Update the parameter
  file and redeploy rather than editing SSM by hand.
- After `cdk destroy`, run `scripts/verify_cleanup.sh` and delete the log groups that Lambda created for the
  CDK custom-resource handlers (cleanup.md). The AgentCore runtime log group is owned by the stack.
