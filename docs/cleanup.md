# Cleanup

## 1. Destroy the stack

```bash
npx -y aws-cdk@2 destroy -c params=config/params.json --force
```

With `removal_policy=destroy` (the sandbox default), CDK empties and deletes both buckets. With
`retain` (the hand-over default), the buckets stay. Empty and delete them by hand once the data owner
agrees:

```bash
aws s3 rm s3://<MediaBucketName> --recursive
aws s3api delete-bucket --bucket <MediaBucketName>
```

## 2. Resources the service creates outside the stack

| Resource | Why it exists | How to remove |
|---|---|---|
| Log group `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT` | AgentCore Runtime creates it at run time, so the stack does not own it | `aws logs delete-log-group --log-group-name /aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT` |
| Workload identity named after the runtime | Created by AgentCore for the runtime | Check with `aws bedrock-agentcore-control list-workload-identities`. If one with the runtime name is still there after the runtime is deleted, run `delete-workload-identity --name <name>` |
| CDK asset objects (Lambda code, agent zip) in the bootstrap assets bucket | `cdk deploy` uploads them there | They belong to the shared CDK bootstrap bucket. Leave them, or delete only the exact keys this project uploaded (see DEPLOYMENT-LOG.md) |

## 3. Check for leftovers

```bash
./scripts/verify_cleanup.sh <REGION>
```

The script checks the stack, S3 buckets (with version counts), log groups, Lambda, IAM roles and
policies, AgentCore runtimes and workload identities, ECR, API Gateway, SSM parameters, KMS keys, and
anything tagged `Project=video-event-analysis` / `Teardown=after-test`. This stack creates no KMS
keys, ECR repositories or API Gateway APIs. Those checks confirm that nothing of that kind was
created by accident. The tagging API can list recently deleted resources for a while after
deletion. Confirm any hit with the service's own describe call.
