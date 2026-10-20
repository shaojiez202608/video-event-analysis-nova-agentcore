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

## cdk-nag (AwsSolutions) suppressions

| Rule | Where | Reason |
|---|---|---|
| AwsSolutions-S1 | access-log bucket | It is the server-access-log destination. Logging it to itself would loop. |
| AwsSolutions-IAM5 | stack (the project's roles and CDK custom-resource roles) | The wildcards are limited to: object prefixes in this stack's media bucket; the AgentCore runtime log-group family and its streams, as documented for the execution role; X-Ray actions that do not support resource-level permissions; `cloudwatch:PutMetricData` with a namespace condition; the foundation-model ARN in any region, because a cross-region inference profile routes to several regions; runtime endpoint sub-resources; CDK asset read; and CDK-managed custom-resource handlers (bucket notifications, auto-delete objects). |
| AwsSolutions-IAM4 | CDK-managed custom-resource roles | Only the CDK singleton handlers use `AWSLambdaBasicExecutionRole`. The project's own roles use inline policies. |
| AwsSolutions-L1 | CDK-managed custom-resource functions | The project's own function uses Python 3.13 to match the arm64 wheels and the AgentCore runtime. The CDK handlers pick their own runtimes. |

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
