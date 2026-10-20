#!/usr/bin/env bash
# Destroy the stack and remove what CloudFormation leaves behind. Asks twice: once for the stack,
# once more before deleting any data (retained buckets) or bootstrap asset objects. A failing step
# prints a warning and the script continues; run scripts/verify_cleanup.sh afterwards.
# Non-interactive: CONFIRM_STACK=<stack name> and CONFIRM_DATA=yes.
# Usage: scripts/cleanup.sh [params-file] [file-with-older-bootstrap-keys]
set -uo pipefail
cd "$(dirname "$0")/.."
PARAMS="${1:-config/params.json}"
OLD_KEYS="${2:-}"
mask() { sed -E 's/[0-9]{12}/111122223333/g'; }
warn() { echo "WARNING: $* (continuing)" >&2; }
read -r STACK PREFIX REGION REMOVAL < <(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))["deploy"]
print(d["stack_name"], d["resource_prefix"], d.get("region", "us-east-1"), d["removal_policy"])' "$PARAMS")
UP="${PREFIX//-/_}"
ask() {  # ask <prompt> <expected> <env value>
  [ "${3:-}" = "$2" ] && return 0
  [ -t 0 ] || { echo "Not confirmed (set the matching CONFIRM_* variable)."; return 1; }
  read -r -p "$1 " a; [ "$a" = "$2" ]
}

ask "Type the stack name ($STACK) to destroy it in $REGION:" "$STACK" "${CONFIRM_STACK:-}" || exit 1

echo "== cdk destroy"
npx -y aws-cdk@2 destroy -c "params=$PARAMS" --force 2>&1 | mask || warn "cdk destroy failed"

echo "== log groups left by services (runtime, CDK handlers)"
for p in "/aws/bedrock-agentcore/runtimes/$UP" "/aws/lambda/$PREFIX" "/aws/lambda/${STACK:0:25}"; do
  for g in $(aws logs describe-log-groups --region "$REGION" --log-group-name-prefix "$p" --query 'logGroups[].logGroupName' --output text); do
    aws logs delete-log-group --region "$REGION" --log-group-name "$g" && echo "deleted $g" || warn "could not delete $g"
  done
done

echo "== workload identities named after the runtime"
for w in $(aws bedrock-agentcore-control list-workload-identities --region "$REGION" --query "workloadIdentities[?starts_with(name, '$UP')].name" --output text 2>/dev/null); do
  aws bedrock-agentcore-control delete-workload-identity --region "$REGION" --name "$w" && echo "deleted $w" || warn "could not delete $w"
done

BUCKETS=$(aws s3api list-buckets --query "Buckets[?starts_with(Name, '$(echo "$STACK" | tr 'A-Z' 'a-z')')].Name" --output text)
KEYS=$(uv run python -c 'import sys; sys.path.insert(0, "scripts"); from verify_cleanup import project_asset_keys; print(" ".join(project_asset_keys(sys.argv[1], sys.argv[2] or None)))' "$STACK" "$OLD_KEYS")
if [ -n "$BUCKETS" ] || [ -n "$KEYS" ]; then
  echo "== data and bootstrap objects"
  [ -n "$BUCKETS" ] && echo "Retained buckets (removal_policy=$REMOVAL): $BUCKETS" | mask
  [ -n "$KEYS" ] && echo "This project's objects in the CDK bootstrap assets bucket: $(echo $KEYS | wc -w) (CDK generic provider packages are kept)"
  if ask "Delete these buckets (all object versions) and asset objects? Type yes:" "yes" "${CONFIRM_DATA:-}"; then
    for b in $BUCKETS; do
      uv run python - "$b" <<'PY' || warn "could not empty $b"
import sys, boto3
boto3.resource("s3").Bucket(sys.argv[1]).object_versions.delete()
PY
      aws s3api delete-bucket --bucket "$b" && echo "deleted bucket" || warn "could not delete a bucket"
    done
    ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
    ASSETS="cdk-hnb659fds-assets-${ACCOUNT}-${REGION}"
    # The bootstrap bucket is versioned: delete every version (and delete marker) of exactly these keys.
    for k in $KEYS; do
      for v in $(aws s3api list-object-versions --bucket "$ASSETS" --prefix "$k" \
                  --query "[Versions[?Key=='$k'].VersionId, DeleteMarkers[?Key=='$k'].VersionId][]" --output text); do
        [ "$v" = "None" ] && continue
        aws s3api delete-object --bucket "$ASSETS" --key "$k" --version-id "$v" >/dev/null \
          && echo "deleted asset $k ($v)" || warn "could not delete asset $k ($v)"
      done
    done
  else
    echo "Kept data buckets and asset objects."
  fi
fi
echo "Now run: scripts/verify_cleanup.sh --params $PARAMS"
