#!/usr/bin/env bash
# Guarded deploy: preflight -> cdk diff (refuses deletions/replacements unless ALLOW_DESTRUCTIVE=1)
# -> cdk deploy. The deploying IAM principal is passed as the operator allowed by the resource
# policies. Output is masked (12-digit numbers). Usage: scripts/deploy.sh [params-file]
set -euo pipefail
cd "$(dirname "$0")/.."
PARAMS="${1:-config/params.json}"
mask() { sed -E 's/[0-9]{12}/111122223333/g'; }
CDK=(npx -y aws-cdk@2)

./scripts/preflight.sh "$PARAMS"
[ -f build/agent.zip ] || ./scripts/build_bundle.sh

CALLER=$(aws sts get-caller-identity --query Arn --output text)
# Resource policies match the IAM role, not the STS session: turn an assumed-role ARN into the role ARN.
OPERATOR=$(echo "$CALLER" | sed -E 's#^arn:(aws[a-z-]*):sts::([0-9]{12}):assumed-role/([^/]+)/.*#arn:\1:iam::\2:role/\3#')
CTX=(-c "params=$PARAMS" -c "operator_principal_arns=$OPERATOR")

echo "== cdk diff"
DIFF=$("${CDK[@]}" diff "${CTX[@]}" 2>&1 || true)
echo "$DIFF" | mask | grep -vE '^\s*$' | tail -60
if echo "$DIFF" | grep -qE '^\[-\]|requires replacement|may be replaced|\(replace\)'; then
  if [ "${ALLOW_DESTRUCTIVE:-}" != "1" ]; then
    echo "The change set deletes or replaces resources. Review the diff above and re-run with ALLOW_DESTRUCTIVE=1." >&2
    exit 2
  fi
  echo "ALLOW_DESTRUCTIVE=1: continuing with deletions/replacements."
fi

echo "== cdk deploy"
"${CDK[@]}" deploy "${CTX[@]}" --require-approval never 2>&1 | mask
