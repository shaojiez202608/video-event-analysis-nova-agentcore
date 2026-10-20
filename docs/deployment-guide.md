# Deployment guide

Prototype, non-production accounts only.

## Prerequisites

- An AWS account and Region where Amazon Bedrock AgentCore Runtime and the chosen Nova model or
  inference profile are available. Check with:
  `aws bedrock list-inference-profiles --region <REGION>` and (both are run by `make preflight`)
  `aws cloudformation describe-type --type RESOURCE --type-name AWS::BedrockAgentCore::Runtime --region <REGION>`.
- The CDK environment is already bootstrapped (`cdk bootstrap` is not part of this project).
- Local tools: Python 3.13 with [uv](https://docs.astral.sh/uv/), Node.js (for `npx -y aws-cdk@2`),
  `ffmpeg` (only needed to generate the synthetic set), and AWS CLI v2. Docker is **not** required.
- Credentials for an IAM principal that can deploy CloudFormation stacks with IAM roles.

## Parameters

All parameters live in one file. `make init` copies the template `config/params.example.json`
(sandbox) to `config/params.json`, which is gitignored. For a hand-over deployment start from
`config/params.customer.example.json` and pass it with `make deploy PARAMS=<FILE>`. The account id is
never part of the file: CDK takes it from the credentials (`CDK_DEFAULT_ACCOUNT`), and the operator
principal is passed as `-c operator_principal_arns=...` by `scripts/deploy.sh`. Every value is
validated at synth time (`infra/video_infra/params.py`), including cross-field rules.

| Key | Meaning |
|---|---|
| `deploy.stack_name`, `deploy.resource_prefix` | Stack name and resource name prefix (`proto-video-…`; 3-32 chars of a-z, 0-9, `-`) |
| `deploy.region` | Target region |
| `deploy.removal_policy` | `destroy` deletes buckets and their objects with the stack; `retain` keeps them |
| `deploy.orchestrator` | `agentcore` (default) or `lambda` (run the pipeline in the dispatcher Lambda, no AgentCore) |
| `deploy.video_retention_days`, `frame_retention_days`, `result_retention_days` | S3 lifecycle expiry per prefix |
| `deploy.log_retention_days` | Lambda log group retention |
| `deploy.lambda_timeout_s`, `lambda_memory_mb` | Dispatcher Lambda settings |
| `deploy.agent_idle_timeout_s`, `agent_max_lifetime_s` | AgentCore Runtime session lifecycle |
| `deploy.tags` | Tags applied to every resource |
| `pipeline.*` | Pipeline config (strategy, sampling, model id, output mode, retries, risk floors, locales, watch rules, time zone offset, frame persistence). Stored in SSM Parameter Store at `/<PREFIX>/pipeline-config`. |

`pipeline.prompt_hint` in `config/params.json` explains how the synthetic clips are drawn, for the
sandbox test. Leave it empty for real cameras, as the customer example file does.

## Deploy

The deploy commands are in the [README "Deploy" section](../README.md#deploy) (the only place they
are written down). `make deploy` wraps `cdk deploy` with the preflight, parameter validation, the
destructive-change guard and the operator principal for the resource policies.

`build_bundle.sh` downloads Linux arm64 wheels with
`uv pip install --python-platform aarch64-manylinux_2_28 --only-binary=:all:`. It does not need
Docker. The same bundle serves the AgentCore Runtime (as a zip) and the dispatcher Lambda (as a
directory asset).

Stack outputs: `MediaBucketName`, `AgentRuntimeArn`, `AgentRuntimeId`, `RuntimeLogGroupName`,
`DispatcherFunctionName`, `DispatchFailureQueueUrl`, `PipelineConfigParam`.

## Check the deployment

See the [README "Use and verify" section](../README.md#use-and-verify) (`ALLOW_PAID=1 make verify`).
A subset of clips: `ALLOW_PAID=1 uv run python scripts/e2e_aws.py --clips s01_empty_home s03_delivery s05_fire_yard`.

The e2e script runs the `health` action in the runtime and in the Lambda, which imports PyAV on
arm64. It then uploads clips to `uploads/<CAMERA_ID>/<UTC_TIMESTAMP>_<NAME>.mp4`, waits for
`results/…json`, validates and scores the results, and calls `ask`, `search` and `daily_summary`.

`scripts/failure_drill_aws.py` (sandbox only) then exercises the failure paths: it temporarily
changes the stack's SSM configuration to force invalid model output and a model error, checks that
both clips get stored `fallback` results, sends an event for a missing object to check the SQS
failure queue, restores the parameter, and searches the log groups for picture text.

## Uploading your own (non-production, synthetic) clips

Object keys must follow `uploads/<CAMERA_ID>/<YYYYMMDDTHHMMSSZ>_<NAME>.<mp4|mov|mkv|webm>`. Other
keys are rejected. The camera id and timestamp come from the key.

## Switching orchestrators

Set `deploy.orchestrator` to `lambda` and redeploy. The Lambda then gets Bedrock, S3 and SSM
permissions (the same data reach as the runtime role, see THREAT_MODEL section 3) and runs the same
`video_events.service` code. The AgentCore runtime is still created
so that `ask`, `search` and `daily_summary` keep working. Removing it is a small change to
`infra/video_infra/stack.py`.

## Cost

Billing dimensions only: Bedrock model input and output tokens (video frames and images count as
input tokens), AgentCore Runtime, Lambda, S3 storage and requests, and CloudWatch Logs. See
[Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) and
[Amazon Bedrock AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/).
