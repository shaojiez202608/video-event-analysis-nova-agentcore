#!/usr/bin/env bash
# Read-only preflight. Changes nothing. Usage: scripts/preflight.sh [params-file]
# Shows the target account (last 4 digits only) and region and asks you to confirm them.
# Non-interactive runs confirm with CONFIRM_ACCOUNT=<last 4 digits>.
set -uo pipefail
cd "$(dirname "$0")/.."
PARAMS="${1:-config/params.json}"
fail=0
ok() { echo "  ok    $*"; }
bad() { echo "  FAIL  $*"; fail=1; }
info() { echo "  info  $*"; }

echo "== parameter file"
if [ ! -f "$PARAMS" ]; then bad "$PARAMS missing (run: make init)"; exit 1; fi
read -r STACK REGION MODEL < <(python3 -c '
import json, sys
p = json.load(open(sys.argv[1]))
print(p["deploy"]["stack_name"], p["deploy"].get("region", "us-east-1"), p["pipeline"]["model_id"])' "$PARAMS")
ok "$PARAMS (stack $STACK, region $REGION, model $MODEL)"
if .venv/bin/python -c 'import json,sys; sys.path[:0]=["infra","src"]; from video_infra.params import validate; validate(json.load(open(sys.argv[1])))' "$PARAMS" 2>/tmp/preflight-params.err; then
  ok "parameter validation"
else
  bad "parameter validation:"; sed 's/^/        /' /tmp/preflight-params.err | tail -12
fi

echo "== tools"
for t in uv node npx aws python3; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t"; else bad "$t not found"; fi
done
if command -v ffmpeg >/dev/null 2>&1; then ok "ffmpeg (only needed to generate the synthetic set)"; else info "ffmpeg not found (only needed to generate the synthetic set)"; fi
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  info "docker is running (not needed: the bundle is built without containers)"
else
  info "docker not running or not installed (not needed: the bundle is built without containers)"
fi

echo "== credentials and target"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) || { bad "no AWS credentials"; exit 1; }
LAST4="${ACCOUNT: -4}"
KIND=$(aws sts get-caller-identity --query Arn --output text | sed -E 's/^arn:aws[a-z-]*:(iam|sts)::[0-9]{12}:([a-z-]+)\/.*/\2/')
info "account ********${LAST4}, region ${REGION}, caller type ${KIND}"
if [ "${CONFIRM_ACCOUNT:-}" != "$LAST4" ]; then
  if [ -t 0 ]; then
    read -r -p "  Type the last 4 digits of the account to confirm this is the intended non-production account: " ans
    [ "$ans" = "$LAST4" ] || { bad "not confirmed; stop if this is not your intended account"; exit 1; }
  else
    bad "set CONFIRM_ACCOUNT=<last 4 digits> to confirm the target account"; exit 1
  fi
fi
ok "target account confirmed"

echo "== service availability in ${REGION}"
if aws cloudformation describe-type --type RESOURCE --type-name AWS::BedrockAgentCore::Runtime --region "$REGION" --query TypeName --output text >/dev/null 2>&1; then
  ok "AWS::BedrockAgentCore::Runtime"
else
  bad "AWS::BedrockAgentCore::Runtime not available in $REGION"
fi
case "$MODEL" in
  us.*|eu.*|apac.*|global.*|jp.*|au.*|ca.*)
    if aws bedrock get-inference-profile --inference-profile-identifier "$MODEL" --region "$REGION" --query status --output text >/dev/null 2>&1; then
      ok "inference profile $MODEL"; else bad "inference profile $MODEL not available"; fi ;;
  *)
    if aws bedrock get-foundation-model --model-identifier "$MODEL" --region "$REGION" --query modelDetails.modelId --output text >/dev/null 2>&1; then
      ok "model $MODEL"; else bad "model $MODEL not available"; fi ;;
esac
if aws cloudformation describe-stacks --stack-name CDKToolkit --region "$REGION" --query 'Stacks[0].StackStatus' --output text >/dev/null 2>&1; then
  ok "CDK bootstrap stack present (not modified by this project)"
else
  bad "CDK bootstrap stack CDKToolkit missing in $REGION"
fi
info "quotas: check Bedrock tokens-per-minute and AgentCore session quotas in Service Quotas for your expected volume"
exit $fail
