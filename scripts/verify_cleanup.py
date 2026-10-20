"""Residual-resource check after destroy. Read-only. Exits 1 if anything of this project is left.

    uv run python scripts/verify_cleanup.py [--params config/params.json] [--regions us-east-1 ...]
        [--bootstrap-keys FILE]

Checks per region: CloudFormation stack, S3 buckets (object versions and delete markers), log groups
(Lambda, CDK handlers, AgentCore runtime), Lambda, SQS, IAM roles and customer policies, AgentCore
runtimes and workload identities, ECR, API Gateway, SSM, KMS aliases, tagged resources (each tag hit is
confirmed with the service's own API, because the tagging index lags behind deletions) and the
project's objects in the CDK bootstrap assets bucket (keys from cdk.out/<stack>.assets.json plus an
optional file of older keys). Output masks 12-digit numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guards import mask_text  # noqa: E402

GENERIC_ASSET_MARKERS = ("Custom::", "AWS679f53fac002430cb0da5b7982bd2287", "BucketNotificationsHandler")


def project_asset_keys(stack: str, extra_file: str | None) -> list[str]:
    """Object keys of this project's own assets (code packages, templates). CDK's generic provider
    packages are shared with other stacks and are excluded."""
    keys: list[str] = []
    manifest = Path("cdk.out") / f"{stack}.assets.json"
    if manifest.is_file():
        for asset in json.loads(manifest.read_text())["files"].values():
            if any(m in asset.get("displayName", "") for m in GENERIC_ASSET_MARKERS):
                continue
            keys += [d["objectKey"] for d in asset["destinations"].values()]
    if extra_file and Path(extra_file).is_file():
        keys += [ln.split()[0] for ln in Path(extra_file).read_text().splitlines()
                 if ln.strip() and not ln.startswith("#")]
    return sorted(set(keys))


def check_region(region: str, stack: str, prefix: str, project: str, asset_keys: list[str]) -> list[str]:
    left: list[str] = []
    up = prefix.replace("-", "_")
    sess = boto3.session.Session(region_name=region)

    def add(kind: str, name: str) -> None:
        left.append(f"{region} {kind}: {name}")

    cfn = sess.client("cloudformation")
    try:
        status = cfn.describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
        if status != "DELETE_COMPLETE":
            add("stack", f"{stack} {status}")
    except ClientError:
        pass

    s3 = sess.client("s3")
    for b in s3.list_buckets()["Buckets"]:
        name = b["Name"]
        if name.startswith(stack.lower()) or name.startswith(f"{prefix}-"):
            try:
                v = s3.list_object_versions(Bucket=name)
                add("s3 bucket", f"{name} versions={len(v.get('Versions', []))} "
                                 f"delete_markers={len(v.get('DeleteMarkers', []))}")
            except ClientError:
                add("s3 bucket", name)

    logs = sess.client("logs")
    for p in (f"/aws/lambda/{prefix}", f"/aws/lambda/{stack[:25]}", f"/aws/bedrock-agentcore/runtimes/{up}"):
        for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=p):
            for g in page["logGroups"]:
                add("log group", g["logGroupName"])

    lam = sess.client("lambda")
    for page in lam.get_paginator("list_functions").paginate():
        for f in page["Functions"]:
            if f["FunctionName"].startswith((f"{prefix}-", stack[:25])):
                add("lambda", f["FunctionName"])

    sqs = sess.client("sqs")
    for url in sqs.list_queues(QueueNamePrefix=f"{prefix}-").get("QueueUrls", []):
        add("sqs", url.rsplit("/", 1)[-1])

    if region == REGIONS[0]:  # IAM is global: check once
        iam = sess.client("iam")
        for page in iam.get_paginator("list_roles").paginate():
            for r in page["Roles"]:
                if r["RoleName"].startswith((f"{prefix}-", stack[:25])):
                    add("iam role", r["RoleName"])
        for page in iam.get_paginator("list_policies").paginate(Scope="Local"):
            for pol in page["Policies"]:
                if pol["PolicyName"].startswith((f"{prefix}-", stack[:25])):
                    add("iam policy", pol["PolicyName"])

    try:
        ac = sess.client("bedrock-agentcore-control")
        for r in ac.list_agent_runtimes().get("agentRuntimes", []):
            if r["agentRuntimeName"].startswith(up):
                add("agentcore runtime", f"{r['agentRuntimeName']} {r.get('status')}")
        for w in ac.list_workload_identities().get("workloadIdentities", []):
            if w["name"].startswith(up):
                add("workload identity", w["name"])
    except ClientError as exc:
        add("agentcore check failed", exc.response["Error"]["Code"])

    ecr = sess.client("ecr")
    for r in ecr.describe_repositories().get("repositories", []):
        if prefix in r["repositoryName"] or up in r["repositoryName"]:
            add("ecr", r["repositoryName"])

    for item in sess.client("apigateway").get_rest_apis().get("items", []):
        if item["name"].startswith(prefix):
            add("api gateway", item["name"])
    for item in sess.client("apigatewayv2").get_apis().get("Items", []):
        if item["Name"].startswith(prefix):
            add("api gateway v2", item["Name"])

    ssm = sess.client("ssm")
    for page in ssm.get_paginator("describe_parameters").paginate(
            ParameterFilters=[{"Key": "Name", "Option": "BeginsWith", "Values": [f"/{prefix}/"]}]):
        for prm in page["Parameters"]:
            add("ssm", prm["Name"])

    kms = sess.client("kms")
    for page in kms.get_paginator("list_aliases").paginate():
        for a in page["Aliases"]:
            if prefix in a["AliasName"]:
                add("kms alias", a["AliasName"])

    tagging = sess.client("resourcegroupstaggingapi")
    for page in tagging.get_paginator("get_resources").paginate(
            TagFilters=[{"Key": "Project", "Values": [project]}]):
        for m in page["ResourceTagMappingList"]:
            arn = m["ResourceARN"]
            if exists(sess, arn):
                add("tagged resource (confirmed to exist)", arn)

    if asset_keys:
        account = sess.client("sts").get_caller_identity()["Account"]
        bucket = f"cdk-hnb659fds-assets-{account}-{region}"
        for key in asset_keys:  # versioned bucket: any remaining version counts
            try:
                v = s3.list_object_versions(Bucket=bucket, Prefix=key)
            except ClientError:
                continue
            n = sum(1 for x in v.get("Versions", []) + v.get("DeleteMarkers", []) if x["Key"] == key)
            if n:
                add("bootstrap asset object", f"{key} versions={n}")
    return left


def exists(sess, arn: str) -> bool:
    """Confirm a tagging-API hit with the owning service (the tag index lags behind deletions)."""
    svc = arn.split(":")[2]
    name = arn.split(":")[-1].split("/")[-1]
    try:
        if svc == "lambda":
            sess.client("lambda").get_function(FunctionName=arn)
        elif svc == "s3":
            sess.client("s3").head_bucket(Bucket=name)
        elif svc == "sqs":
            sess.client("sqs").get_queue_url(QueueName=name)
        elif svc == "logs":
            g = arn.split(":log-group:")[-1].rstrip(":*")
            return bool(sess.client("logs").describe_log_groups(logGroupNamePrefix=g)["logGroups"])
        elif svc == "ssm":
            sess.client("ssm").get_parameter(Name="/" + arn.split(":parameter/")[-1])
        elif svc == "bedrock-agentcore":
            sess.client("bedrock-agentcore-control").get_agent_runtime(agentRuntimeId=name)
        elif svc == "iam":
            sess.client("iam").get_role(RoleName=name)
        elif svc == "kms":
            return sess.client("kms").describe_key(KeyId=arn)["KeyMetadata"]["KeyState"] != "PendingDeletion"
        else:
            return True  # unknown type: report it
        return True
    except ClientError:
        return False


REGIONS: list[str] = []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", default="config/params.json")
    ap.add_argument("--regions", nargs="*")
    ap.add_argument("--bootstrap-keys", help="file with older asset object keys of this project (one per line)")
    args = ap.parse_args()
    params = Path(args.params) if Path(args.params).is_file() else Path("config/params.example.json")
    d = json.loads(params.read_text())["deploy"]
    REGIONS[:] = args.regions or [d.get("region", "us-east-1")]
    keys = project_asset_keys(d["stack_name"], args.bootstrap_keys)
    print(f"stack={d['stack_name']} prefix={d['resource_prefix']} regions={REGIONS} project_asset_keys={len(keys)}")
    left: list[str] = []
    for r in REGIONS:
        left += check_region(r, d["stack_name"], d["resource_prefix"], d.get("tags", {}).get("Project", "-"), keys)
    for item in left:
        print(mask_text("LEFT  " + item))
    if left:
        print(mask_text(f"Residual resources remain: {len(left)}"))
        sys.exit(1)
    print("Cleaned up: nothing of this project remains in the checked regions.")


if __name__ == "__main__":
    main()
