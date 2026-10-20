# Cleanup

## 1. Destroy the stack

The commands are in the [README "Clean up" section](../README.md#clean-up): `make destroy`, then
`make verify-cleanup`. `scripts/cleanup.sh` (behind `make destroy`) asks for the stack name, runs
`cdk destroy`, deletes the service-created log groups and workload identities that are left, and
asks a second time before it deletes retained buckets (all object versions) and this project's
objects in the CDK bootstrap assets bucket. A failing step prints a warning and the script continues.

With `removal_policy=destroy` (the sandbox default), CDK empties and deletes both buckets. With
`retain` (the hand-over default), the buckets stay until you confirm their deletion in
`make destroy` (agree with the data owner first).

## 2. Resources the service creates outside the stack

| Resource | Why it exists | How to remove |
|---|---|---|
| Log groups of the two CDK-managed custom-resource Lambdas (`/aws/lambda/<STACK>-…`) | Lambda creates them on first run; CDK does not own them | `aws logs delete-log-group --log-group-name <LOG_GROUP_NAME>` |
| Log group `/aws/bedrock-agentcore/runtimes/<RUNTIME_ID>-DEFAULT` | The service creates it while the runtime is created. The custom resource `RuntimeLogRetention` sets the configured retention right after that and deletes the group on destroy. Check that it is gone; if the service wrote to it again during deletion, delete it by hand | `aws logs delete-log-group --log-group-name /aws/bedrock-agentcore/runtimes/<RUNTIME_ID>-DEFAULT` |
| Log group of the CDK `AwsCustomResource` handler (`/aws/lambda/<STACK>-AWS679f53fac002430cb0da5b7-…`) | Created by Lambda when the handler runs | `aws logs delete-log-group --log-group-name <LOG_GROUP_NAME>` |
| Workload identity named after the runtime | Created by AgentCore for the runtime | Check with `aws bedrock-agentcore-control list-workload-identities`. If one with the runtime name is still there after the runtime is deleted, run `delete-workload-identity --name <NAME>` |
| This project's asset objects (agent zip, Lambda code, templates) in the bootstrap assets bucket | `cdk deploy` uploads them there | `make destroy` deletes exactly these keys (current build from `cdk.out/<STACK>.assets.json`, older builds from `infra/bootstrap-asset-keys.txt`), every version. CDK's generic provider packages and the CDKToolkit stack are not touched. Keys that cannot be attributed are left and reported |

## 3. Check for leftovers

`make verify-cleanup` (`scripts/verify_cleanup.py`, read-only) checks, in every region passed with
`--regions` (default: the region in the parameter file): the stack, S3 buckets (object versions and
delete markers), log groups, Lambda, SQS, IAM roles and policies, AgentCore runtimes and workload
identities, ECR, API Gateway, SSM parameters, KMS aliases, resources tagged with the project tag, and
this project's bootstrap asset objects. The tagging API can list recently deleted resources for a
while, so every tag hit is confirmed with the owning service's own API before it counts. The script
exits non-zero if anything is left. This stack creates no KMS keys, ECR repositories or API Gateway
APIs; those checks confirm that nothing of that kind was created by accident.
