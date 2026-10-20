"""Check that the resource policies really deny an unexpected principal (sandbox only).

    uv run python scripts/deny_probe_aws.py [--params config/params.json]

Creates a temporary IAM role `<prefix>-deny-probe` that the current principal can assume, gives it
an identity policy that *allows* InvokeAgentRuntime on the runtime and s3:GetObject/PutObject on the
media bucket, assumes it, and expects both calls to be denied by the resource policies (the role is
not the dispatcher and not an operator). The role is deleted in a `finally` block. No model call is
made. Output masks 12-digit numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_aws import mask, outputs  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", default="config/params.json")
    ap.add_argument("--report")
    args = ap.parse_args()
    d = json.loads(Path(args.params).read_text())["deploy"]
    region = d.get("region", "us-east-1")
    out = outputs(d["stack_name"], region)
    arn, bucket = out["AgentRuntimeArn"], out["MediaBucketName"]
    sts = boto3.client("sts", region_name=region)
    me = sts.get_caller_identity()["Arn"]
    iam = boto3.client("iam")
    role = f"{d['resource_prefix']}-deny-probe"
    # An object that exists (written by the e2e run), so a denial cannot be confused with NoSuchKey.
    listed = boto3.client("s3", region_name=region).list_objects_v2(Bucket=bucket, Prefix="results/", MaxKeys=1)
    existing = (listed.get("Contents") or [{"Key": "results/none.json"}])[0]["Key"]
    report = {}
    iam.create_role(
        RoleName=role, Description="Temporary probe for resource-policy denies (deleted by the script)",
        AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Principal": {"AWS": me}, "Action": "sts:AssumeRole"}]}),
        Tags=[{"Key": k, "Value": v} for k, v in d.get("tags", {}).items()])
    try:
        iam.put_role_policy(RoleName=role, PolicyName="probe", PolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Action": "bedrock-agentcore:InvokeAgentRuntime",
                 "Resource": [arn, f"{arn}/*"]},
                {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
                 "Resource": f"arn:aws:s3:::{bucket}/*"}]}))
        creds = None
        for _ in range(12):  # IAM propagation
            try:
                creds = sts.assume_role(RoleArn=iam.get_role(RoleName=role)["Role"]["Arn"],
                                        RoleSessionName="deny-probe")["Credentials"]
                break
            except ClientError:
                time.sleep(5)
        if creds is None:
            sys.exit("could not assume the probe role")
        time.sleep(10)
        sess = boto3.session.Session(aws_access_key_id=creds["AccessKeyId"],
                                     aws_secret_access_key=creds["SecretAccessKey"],
                                     aws_session_token=creds["SessionToken"], region_name=region)

        def attempt(name, fn):
            try:
                fn()
                report[name] = "ALLOWED (unexpected)"
            except ClientError as exc:
                report[name] = exc.response["Error"]["Code"]

        ac = sess.client("bedrock-agentcore")
        attempt("invoke_agent_runtime", lambda: ac.invoke_agent_runtime(
            agentRuntimeArn=arn, qualifier="DEFAULT", runtimeSessionId=f"probe-{uuid.uuid4().hex}",
            contentType="application/json", accept="application/json", payload=b'{"action": "health"}'))
        s3 = sess.client("s3")
        attempt("s3_put_object", lambda: s3.put_object(Bucket=bucket, Key="uploads/probe/x.txt", Body=b"x"))
        attempt("s3_get_object", lambda: s3.get_object(Bucket=bucket, Key=existing))
    finally:
        try:
            iam.delete_role_policy(RoleName=role, PolicyName="probe")
        except ClientError:
            pass
        iam.delete_role(RoleName=role)
        report["probe_role_deleted"] = True
    print(mask(report))
    if args.report:
        Path(args.report).write_text(mask(report), encoding="utf-8")
    denied = all(v in ("AccessDeniedException", "AccessDenied") for k, v in report.items() if k != "probe_role_deleted")
    sys.exit(0 if denied else 1)


if __name__ == "__main__":
    main()
