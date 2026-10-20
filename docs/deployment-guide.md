# Deployment guide

Prototype, non-production accounts only.

## Prerequisites

- An AWS account and Region where Amazon Bedrock AgentCore Runtime and the chosen Nova model or
  inference profile are available. Check with:
  `aws bedrock list-inference-profiles --region <REGION>` and
  `aws cloudformation describe-type --type RESOURCE --type-name AWS::BedrockAgentCore::Runtime --region <REGION>`.
- The CDK environment is already bootstrapped (`cdk bootstrap` is not part of this project).
- Local tools: Python 3.13 with [uv](https://docs.astral.sh/uv/), Node.js (for `npx -y aws-cdk@2`),
  `ffmpeg` (only needed to generate the synthetic set), and AWS CLI v2. Docker is **not** required.
- Credentials for an IAM principal that can deploy CloudFormation stacks with IAM roles.

## Parameters

All parameters live in one file. Use `config/params.json` for a sandbox, or start from
`config/params.customer.example.json` for a hand-over deployment.

| Key | Meaning |
|---|---|
| `deploy.stack_name`, `deploy.resource_prefix` | Stack name and resource name prefix (`proto-video-…`) |
| `deploy.region` | Target region |
| `deploy.removal_policy` | `destroy` deletes buckets and their objects with the stack; `retain` keeps them |
| `deploy.orchestrator` | `agentcore` (default) or `lambda` (run the pipeline in the dispatcher Lambda, no AgentCore) |
| `deploy.video_retention_days`, `frame_retention_days`, `result_retention_days` | S3 lifecycle expiry per prefix |
| `deploy.log_retention_days` | Lambda log group retention |
| `deploy.lambda_timeout_s`, `lambda_memory_mb` | Dispatcher Lambda settings |
| `deploy.agent_idle_timeout_s`, `agent_max_lifetime_s` | AgentCore Runtime session lifecycle |
| `deploy.tags` | Tags applied to every resource |
| `pipeline.*` | Pipeline config (strategy, sampling, model id, output mode, retries, risk floors, locales, watch rules, time zone offset, frame persistence). Stored in SSM Parameter Store at `/<prefix>/pipeline-config`. |

`pipeline.prompt_hint` in `config/params.json` explains how the synthetic clips are drawn, for the
sandbox test. Leave it empty for real cameras, as the customer example file does.

## Deploy (one command after the build)

```bash
uv sync                                  # local tools and CDK libraries
./scripts/build_bundle.sh                # arm64 wheels + code -> build/bundle, build/agent.zip
npx -y aws-cdk@2 deploy -c params=config/params.json --require-approval never
```

`build_bundle.sh` downloads Linux arm64 wheels with
`uv pip install --python-platform aarch64-manylinux_2_28 --only-binary=:all:`. It does not need
Docker. The same bundle serves the AgentCore Runtime (as a zip) and the dispatcher Lambda (as a
directory asset).

Stack outputs: `MediaBucketName`, `AgentRuntimeArn`, `AgentRuntimeId`, `DispatcherFunctionName`,
`PipelineConfigParam`.

## Check the deployment

```bash
uv run python scripts/gen_synthetic.py --out sample-data/eval    # if not generated yet
uv run python scripts/e2e_aws.py --clips s01_empty_home s03_delivery s05_fire_yard
```

The e2e script runs the `health` action in the runtime and in the Lambda, which imports PyAV on
arm64. It then uploads clips to `uploads/<camera>/<UTC timestamp>_<name>.mp4`, waits for
`results/…json`, validates and scores the results, and calls `ask`, `search` and `daily_summary`.

## Uploading your own (non-production, synthetic) clips

Object keys must follow `uploads/<camera_id>/<YYYYMMDDTHHMMSSZ>_<name>.<mp4|mov|mkv|webm>`. Other
keys are rejected. The camera id and timestamp come from the key.

## Switching orchestrators

Set `deploy.orchestrator` to `lambda` and redeploy. The Lambda then gets Bedrock, S3 and SSM
permissions and runs the same `video_events.service` code. The AgentCore runtime is still created
so that `ask`, `search` and `daily_summary` keep working. Removing it is a small change to
`infra/video_infra/stack.py`.

## Cost

Billing dimensions only: Bedrock model input and output tokens (video frames and images count as
input tokens), AgentCore Runtime, Lambda, S3 storage and requests, and CloudWatch Logs. See
[Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) and
[Amazon Bedrock AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/).
