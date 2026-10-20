# Security notes

## Least-privilege summary

| Principal | Allowed | Scope |
|---|---|---|
| AgentCore runtime execution role | `s3:GetObject` | media bucket `uploads/*`, `results/*` |
| | `s3:PutObject` | media bucket `results/*`, `frames/*` |
| | `s3:ListBucket` | media bucket, condition `s3:prefix` = `results/*` |
| | `bedrock:InvokeModel` | `foundation-model/amazon.nova-2-lite-v1:0` (any region, required by the cross-region profile) and the `us.amazon.nova-2-lite-v1:0` inference profile in this account and region |
| | `ssm:GetParameter` | `/proto-video/pipeline-config` only |
| | CloudWatch Logs | `/aws/bedrock-agentcore/runtimes/*` log groups and streams (as in the AgentCore permissions guide) |
| | X-Ray put/sampling, `cloudwatch:PutMetricData` | `*`, metrics restricted by the `cloudwatch:namespace = bedrock-agentcore` condition |
| | workload access token | default workload identity directory, `proto_video_event_agent-*` identities |
| | read of the code package | the CDK asset object only |
| Trust policy | `bedrock-agentcore.amazonaws.com` | conditions `aws:SourceAccount` and `aws:SourceArn` (this account and region) |
| Dispatcher Lambda role | `bedrock-agentcore:InvokeAgentRuntime` | this runtime ARN and its endpoints |
| | logs | its own log group |
| | (only when `orchestrator=lambda`) Bedrock, S3 and SSM | same scopes as the runtime role |

No IAM users, no access keys, no `*:*` actions, no security groups (no VPC resources), no public endpoints.

## Data protection

- S3: Block Public Access (all four settings), TLS-only bucket policy (`aws:SecureTransport`), SSE-S3
  encryption, TLS 1.2 minimum, server access logs to a separate private bucket, lifecycle expiry for
  `uploads/`, `frames/` and `results/`.
- Frames are not persisted by default (`persist_frames=false`).
- Logs: content-free (ids, counts, enum values, timings). Lambda log group retention comes from config
  (sandbox: 7 days). The AgentCore runtime log group is created by the service (see cleanup.md).
- Configuration is in SSM Parameter Store. There are no secrets in this project.

## cdk-nag (AwsSolutions) acknowledgements

cdk-nag v3 acknowledgements are attached to the construct that triggers each finding (never to
the whole stack), in `VideoEventStack._acknowledge`. Finding ids are built from the same values as
the policies (runtime name from `resource_prefix`, model id from `pipeline.model_id`), so changing
either still synthesises without unacknowledged findings (`tests/test_infra.py` covers another
prefix, an `eu.` profile, an on-demand model id and `orchestrator=lambda`).

| Rule | Attached to | Reason |
|---|---|---|
| AwsSolutions-S1 | `AccessLogs` bucket | It is the server-access-log destination. Logging it to itself would loop. |
| AwsSolutions-SQS3 | `DispatchFailures` queue | The queue is itself the dispatcher's on-failure destination. |
| AwsSolutions-IAM5 | `AgentPolicy` (runtime execution role) | Wildcards limited to: object prefixes `uploads/*`, `results/*`, `frames/*` of this stack's media bucket; the AgentCore runtime log-group family and its streams, as in the documented execution role; `logs:DescribeLogGroups` (list action); X-Ray actions without resource-level permissions and `cloudwatch:PutMetricData` with a namespace condition (`Resource::*`); the workload identities `<runtime name>-*` that the service creates; the foundation-model ARN in any region, only when `model_id` is a cross-region inference profile (an on-demand model id is scoped to the stack region and needs no acknowledgement). |
| AwsSolutions-IAM5 | `DispatcherPolicy` (dispatcher role) | `<runtime ARN>/*`: endpoints of this one runtime. With `orchestrator=lambda`, also the same media-bucket prefixes and foundation-model ARN as above. |
| AwsSolutions-L1 | `Dispatcher` function (the project's own Lambda) | Python 3.13 on purpose: the Lambda shares one arm64 wheel bundle with the AgentCore Runtime, whose newest direct-code runtime is `PYTHON_3_13`. Both move together (handover item 12). |
| AwsSolutions-IAM4 | CDK-managed `BucketNotificationsHandler…` role | The CDK singleton that configures bucket notifications at deploy time uses `AWSLambdaBasicExecutionRole`. The project's own roles use inline policies. |

The project's own Lambda function is arm64. The CDK-managed custom-resource handlers (bucket
notifications, auto-delete objects) use the architecture CDK chooses. They run only during deploy
and destroy.

## Known risks

See THREAT_MODEL.md for the full list. The main ones:

- No end-user authentication or per-user camera authorisation. Callers are trusted IAM principals.
- Prompt injection through visible text can make the model miss events. Validation limits the
  damage but cannot detect a false negative.
- PyAV and the bundled FFmpeg libraries parse untrusted media, so keep them patched.
- `PUBLIC` network mode on AgentCore Runtime: outbound traffic goes to public AWS endpoints, and
  inbound calls require SigV4.
