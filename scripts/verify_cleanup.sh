#!/usr/bin/env bash
# Residual-resource check after `cdk destroy`. Read-only. Prints masked output only.
# Usage: scripts/verify_cleanup.sh [region]
set -uo pipefail
REGION="${1:-${AWS_REGION:-us-east-1}}"
STACK="proto-video-event-analysis"
mask() { sed -E 's/[0-9]{12}/<ACCOUNT_ID>/g'; }
section() { echo; echo "== $1"; }

section "CloudFormation stack ${STACK}"
aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query 'Stacks[0].StackStatus' --output text 2>&1 | mask

section "S3 buckets named proto-video-* (with object/version counts)"
for b in $(aws s3api list-buckets --query "Buckets[?starts_with(Name, 'proto-video-')].Name" --output text); do
  n=$(aws s3api list-object-versions --bucket "$b" --query 'length(Versions || `[]`)' --output text 2>/dev/null)
  echo "$b versions=$n" | mask
done

section "Log groups"
for p in /aws/lambda/proto-video /aws/bedrock-agentcore/runtimes/proto_video; do
  aws logs describe-log-groups --region "$REGION" --log-group-name-prefix "$p" \
    --query 'logGroups[].logGroupName' --output text | mask
done

section "Lambda functions proto-video-*"
aws lambda list-functions --region "$REGION" \
  --query "Functions[?starts_with(FunctionName, 'proto-video-')].FunctionName" --output text | mask

section "IAM roles proto-video-*"
aws iam list-roles --query "Roles[?starts_with(RoleName, 'proto-video-')].RoleName" --output text | mask

section "IAM customer managed policies proto-video-*"
aws iam list-policies --scope Local --query "Policies[?starts_with(PolicyName, 'proto-video-')].PolicyName" --output text | mask

section "AgentCore runtimes proto_video*"
aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
  --query "agentRuntimes[?starts_with(agentRuntimeName, 'proto_video')].[agentRuntimeName,status]" --output text 2>&1 | mask

section "AgentCore workload identities proto_video*"
aws bedrock-agentcore-control list-workload-identities --region "$REGION" \
  --query "workloadIdentities[?starts_with(name, 'proto_video')].name" --output text 2>&1 | mask

section "ECR repositories containing proto-video / proto_video"
aws ecr describe-repositories --region "$REGION" \
  --query "repositories[?contains(repositoryName, 'proto-video') || contains(repositoryName, 'proto_video')].repositoryName" \
  --output text 2>&1 | mask

section "API Gateway (REST and HTTP) named proto-video*"
aws apigateway get-rest-apis --region "$REGION" --query "items[?starts_with(name, 'proto-video')].name" --output text | mask
aws apigatewayv2 get-apis --region "$REGION" --query "Items[?starts_with(Name, 'proto-video')].Name" --output text | mask

section "SSM parameters /proto-video/*"
aws ssm describe-parameters --region "$REGION" \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/proto-video/" --query 'Parameters[].Name' --output text | mask

section "KMS keys tagged Project=video-event-analysis (none are created by this stack)"
aws resourcegroupstaggingapi get-resources --region "$REGION" --resource-type-filters kms \
  --tag-filters Key=Project,Values=video-event-analysis --query 'ResourceTagMappingList[].ResourceARN' --output text | mask

section "Any resource tagged Project=video-event-analysis"
aws resourcegroupstaggingapi get-resources --region "$REGION" \
  --tag-filters Key=Project,Values=video-event-analysis --query 'ResourceTagMappingList[].ResourceARN' --output text | mask

section "Any resource tagged Teardown=after-test AND Project=video-event-analysis"
aws resourcegroupstaggingapi get-resources --region "$REGION" \
  --tag-filters Key=Project,Values=video-event-analysis Key=Teardown,Values=after-test \
  --query 'ResourceTagMappingList[].ResourceARN' --output text | mask
echo
echo "done"
