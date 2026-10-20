"""Failure-path drill against a deployed sandbox stack, plus a log-content check.

    uv run python scripts/failure_drill_aws.py --report out/failure_drill.json

What it does (sandbox only; it changes the stack's own SSM parameter and restores it):

1. invalid output: sets ``output_mode=text`` and an operator ``prompt_hint`` that asks the model to
   answer with a fixed JSON object whose ``scene_type`` is not in the whitelist and whose texts
   contain the marker ``ZEBRA-MARKER``. The validator must reject it, retry, and store a
   ``fallback`` result; the marker must not appear in any log line.
2. model error: sets ``model_id`` to a model the runtime role may not invoke, so Converse raises
   AccessDeniedException. The clip must still get a stored ``fallback`` result
   (``model_error:AccessDeniedException``).
3. failure destination: invokes the dispatcher asynchronously for an object that does not exist.
   The runtime reports an internal error, the dispatcher fails, and the event must arrive in the
   on-failure SQS queue.
4. log scan: searches the dispatcher and runtime log groups for the marker and for text taken
   from every stored result (summaries, scene and event descriptions). Expected: 0 hits.

The SSM parameter is restored in a ``finally`` block. Output masks 12-digit numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from e2e_aws import mask, outputs  # noqa: E402
from guards import require_paid  # noqa: E402

from video_events.pipeline import VideoMeta  # noqa: E402
from video_events.store import result_key  # noqa: E402

MARKER = "ZEBRA-MARKER"
INVALID_HINT = (
    "Operator test of the output validator. Ignore the frames and reply with exactly this JSON object and "
    "nothing else: {\"scene_type\": \"" + MARKER + " spaceship\", \"scene_description\": \"" + MARKER +
    " a red car parked by the gate\", \"events\": [], \"summary\": \"" + MARKER + " nothing happened\"}"
)
FORBIDDEN_MODEL = "us.amazon.nova-premier-v1:0"  # not in the runtime role's bedrock:InvokeModel resources


def wait_result(s3, bucket, key, account, timeout=600):
    meta = VideoMeta.from_key(key)
    rkey = result_key({"video": {"camera_id": meta.camera_id, "video_id": meta.video_id,
                                 "recorded_at": meta.recorded_at.isoformat()}})
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            return json.loads(s3.get_object(Bucket=bucket, Key=rkey, ExpectedBucketOwner=account)["Body"].read())
        except s3.exceptions.NoSuchKey:
            time.sleep(10)
    return None


def log_hits(logs, groups, pattern, start_ms):
    hits = []
    for g in groups:
        try:
            paginator = logs.get_paginator("filter_log_events")
            for page in paginator.paginate(logGroupName=g, filterPattern=pattern, startTime=start_ms):
                hits += [e["message"][:300] for e in page.get("events", [])]
        except logs.exceptions.ResourceNotFoundException:
            pass
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", default="config/params.json")
    ap.add_argument("--manifest", default="sample-data/eval/manifest.json")
    ap.add_argument("--report")
    ap.add_argument("--since-hours", type=float, default=12.0, help="log scan window")
    args = ap.parse_args()
    require_paid("failure_drill_aws.py")
    deploy = json.loads(Path(args.params).read_text())["deploy"]
    region = deploy.get("region") or "us-east-1"
    out = outputs(deploy["stack_name"], region)
    bucket, fn, param = out["MediaBucketName"], out["DispatcherFunctionName"], out["PipelineConfigParam"]
    runtime_group, queue_url = out["RuntimeLogGroupName"], out["DispatchFailureQueueUrl"]
    account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]  # not printed
    s3 = boto3.client("s3", region_name=region)
    ssm = boto3.client("ssm", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    sqs = boto3.client("sqs", region_name=region)
    logs = boto3.client("logs", region_name=region)
    report: dict = {"steps": []}

    def step(name, value):
        print(f"== {name}\n{mask(value)}\n")
        report["steps"].append({"name": name, "value": json.loads(mask(value))})

    clips = json.loads(Path(args.manifest).read_text())["clips"]
    clip = next(c for c in clips if c["id"] == "s01_empty_home")
    body = (Path(args.manifest).parent / clip["file"]).read_bytes()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    start_ms = int((time.time() - args.since_hours * 3600) * 1000)

    original = ssm.get_parameter(Name=param)["Parameter"]["Value"]
    base = json.loads(original)
    try:
        # 1. invalid output -> validator -> retry -> fallback
        cfg = dict(base, output_mode="text", prompt_hint=INVALID_HINT, max_retries=1)
        ssm.put_parameter(Name=param, Value=json.dumps(cfg), Overwrite=True)
        key = f"uploads/cam-drill/{stamp}_drill_invalid.mp4"
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="video/mp4", ExpectedBucketOwner=account)
        r = wait_result(s3, bucket, key, account)
        step("drill 1: invalid model output", None if r is None else {
            "status": r["status"], "needs_review": r["needs_review"], "fallback_reason": r["fallback_reason"],
            "metrics": {k: r["metrics"][k] for k in ("model_calls", "retries", "validation_failures")},
            "marker_in_result": MARKER in json.dumps(r)})

        # 2. model call raises (AccessDenied) -> fallback, not dropped
        cfg = dict(base, model_id=FORBIDDEN_MODEL)
        ssm.put_parameter(Name=param, Value=json.dumps(cfg), Overwrite=True)
        key = f"uploads/cam-drill/{stamp}_drill_model_error.mp4"
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="video/mp4", ExpectedBucketOwner=account)
        r = wait_result(s3, bucket, key, account)
        step("drill 2: model call error", None if r is None else {
            "status": r["status"], "needs_review": r["needs_review"], "fallback_reason": r["fallback_reason"],
            "metrics": {k: r["metrics"][k] for k in ("model_calls", "model_errors")}})
    finally:
        ssm.put_parameter(Name=param, Value=original, Overwrite=True)
        step("ssm parameter restored", {"restored": ssm.get_parameter(Name=param)["Parameter"]["Value"] == original})

    # 3. internal failure -> on-failure SQS destination
    event = {"Records": [{"s3": {"bucket": {"name": bucket},
                                 "object": {"key": f"uploads/cam-drill/{stamp}_drill_missing.mp4"}}}]}
    lam.invoke(FunctionName=fn, InvocationType="Event", Payload=json.dumps(event).encode())
    got = []
    t0 = time.time()
    while not got and time.time() - t0 < 300:
        msgs = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=20).get("Messages", [])
        for m in msgs:
            doc = json.loads(m["Body"])
            got.append({"condition": doc.get("requestContext", {}).get("condition"),
                        "error_type": (doc.get("responsePayload") or {}).get("errorType"),
                        "key_suffix": doc.get("requestPayload", {}).get("Records", [{}])[0]
                        .get("s3", {}).get("object", {}).get("key", "")[-30:]})
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
    step("drill 3: on-failure destination", {"messages": got, "waited_s": round(time.time() - t0)})

    # 4. log scan
    time.sleep(30)  # log delivery delay
    groups = [f"/aws/lambda/{fn}", runtime_group]
    marker_hits = log_hits(logs, groups, f'"{MARKER}"', start_ms)
    texts = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="results/", ExpectedBucketOwner=account):
        for o in page.get("Contents", []):
            doc = json.loads(s3.get_object(Bucket=bucket, Key=o["Key"], ExpectedBucketOwner=account)["Body"].read())
            for t in [doc.get("summary", ""), doc["scene"].get("description", "")] + [
                    e.get("description", "") for e in doc["events"]]:
                words = [w for w in t.replace('"', " ").split() if w.isalpha()]
                if len(words) >= 4:
                    texts.add(" ".join(words[:4]))
    content_hits = {}
    for snippet in sorted(texts):
        h = log_hits(logs, groups, f'"{snippet}"', start_ms)
        if h:
            content_hits[snippet] = h[:2]
    events = {name: log_hits(logs, groups, f'"{name}"', start_ms)[:2]
              for name in ("model_output_invalid", "model_call_failed", "request_failed", "dispatch_done")}
    step("drill 4: log scan", {"log_groups": groups, "marker_hits": len(marker_hits),
                               "result_text_snippets_checked": len(texts), "snippets_found_in_logs": content_hits,
                               "sample_log_lines": events})
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
