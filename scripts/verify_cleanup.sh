#!/usr/bin/env bash
# Residual-resource check after `cdk destroy`. Read-only. Prints masked output only.
# Usage: scripts/verify_cleanup.sh [region] [params-file]
# Stack name and resource prefix are read from the params file (default config/params.json).
set -uo pipefail
cd "$(dirname "$0")/.."
REGION="${1:-${AWS_REGION:-us-east-1}}"
PARAMS="${2:-config/params.json}"
read -r STACK PREFIX PROJECT < <(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))["deploy"]
print(d["stack_name"], d["resource_prefix"], d.get("tags", {}).get("Project", "-"))' "$PARAMS")
UPREFIX="${PREFIX//-/_}"   # AgentCore names allow [a-zA-Z0-9_] only
mask() { sed -E 's/[0-9]{12}/<ACCOUNT_ID>/g'; }
section() { echo; echo "== $1"; }
echo "stack=${STACK} prefix=${PREFIX} agentcore_prefix=${UPREFIX} region=${REGION}"

section "CloudFormation stack ${STACK}"
aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query 'Stacks[0].StackStatus' --output text 2>&1 | mask

section "S3 buckets whose name starts with ${STACK} or ${PREFIX}- (object versions + delete markers)"
for b in $(aws s3api list-buckets --query "Buckets[?starts_with(Name, '${STACK}') || starts_with(Name, '${PREFIX}-')].Name" --output text); do
  v=$(aws s3api list-object-versions --bucket "$b" --query 'length(Versions || `[]`)' --output text 2>/dev/null)
  m=$(aws s3api list-object-versions --bucket "$b" --query 'length(DeleteMarkers || `[]`)' --output text 2>/dev/null)
  echo "$b versions=$v delete_markers=$m" | mask
done

section "Log groups (Lambda, CDK custom resources, AgentCore runtime)"
for p in "/aws/lambda/${PREFIX}" "/aws/lambda/${STACK}" "/aws/bedrock-agentcore/runtimes/${UPREFIX}"; do
  aws logs describe-log-groups --region "$REGION" --log-group-name-prefix "$p" \
    --query 'logGroups[].[logGroupName,retentionInDays]' --output text | mask
done

section "Lambda functions ${PREFIX}-* / ${STACK}-*"
aws lambda list-functions --region "$REGION" \
  --query "Functions[?starts_with(FunctionName, '${PREFIX}-') || starts_with(FunctionName, '${STACK}')].FunctionName" --output text | mask

section "SQS queues ${PREFIX}-*"
aws sqs list-queues --region "$REGION" --queue-name-prefix "${PREFIX}-" --query 'QueueUrls' --output text | mask

section "IAM roles ${PREFIX}-* / ${STACK}-*"
aws iam list-roles --query "Roles[?starts_with(RoleName, '${PREFIX}-') || starts_with(RoleName, '${STACK}')].RoleName" --output text | mask

section "IAM customer managed policies ${PREFIX}-* / ${STACK}-*"
aws iam list-policies --scope Local --query "Policies[?starts_with(PolicyName, '${PREFIX}-') || starts_with(PolicyName, '${STACK}')].PolicyName" --output text | mask

section "AgentCore runtimes ${UPREFIX}*"
aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
  --query "agentRuntimes[?starts_with(agentRuntimeName, '${UPREFIX}')].[agentRuntimeName,status]" --output text 2>&1 | mask

section "AgentCore workload identities ${UPREFIX}*"
aws bedrock-agentcore-control list-workload-identities --region "$REGION" \
  --query "workloadIdentities[?starts_with(name, '${UPREFIX}')].name" --output text 2>&1 | mask

section "ECR repositories containing ${PREFIX} / ${UPREFIX}"
aws ecr describe-repositories --region "$REGION" \
  --query "repositories[?contains(repositoryName, '${PREFIX}') || contains(repositoryName, '${UPREFIX}')].repositoryName" \
  --output text 2>&1 | mask

section "API Gateway (REST and HTTP) named ${PREFIX}*"
aws apigateway get-rest-apis --region "$REGION" --query "items[?starts_with(name, '${PREFIX}')].name" --output text | mask
aws apigatewayv2 get-apis --region "$REGION" --query "Items[?starts_with(Name, '${PREFIX}')].Name" --output text | mask

section "SSM parameters /${PREFIX}/*"
aws ssm describe-parameters --region "$REGION" \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/${PREFIX}/" --query 'Parameters[].Name' --output text | mask

section "KMS aliases containing ${PREFIX} and KMS keys tagged Project=${PROJECT} (none are created by this stack)"
aws kms list-aliases --region "$REGION" --query "Aliases[?contains(AliasName, '${PREFIX}')].AliasName" --output text | mask
aws resourcegroupstaggingapi get-resources --region "$REGION" --resource-type-filters kms \
  --tag-filters "Key=Project,Values=${PROJECT}" --query 'ResourceTagMappingList[].ResourceARN' --output text | mask

section "Any resource tagged Project=${PROJECT} (the tagging API can lag behind deletions)"
aws resourcegroupstaggingapi get-resources --region "$REGION" \
  --tag-filters "Key=Project,Values=${PROJECT}" --query 'ResourceTagMappingList[].ResourceARN' --output text | mask
echo
echo "done"
