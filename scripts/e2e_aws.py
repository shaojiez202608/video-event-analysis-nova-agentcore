"""End-to-end check against a deployed stack (real S3 trigger, AgentCore Runtime, Nova).

    uv run python scripts/e2e_aws.py --clips s01_empty_home s03_delivery s05_fire_yard
    uv run python scripts/e2e_aws.py --all

Prints only masked identifiers: 12-digit numbers are replaced with <ACCOUNT_ID>.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.config import Config
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_events.evaluation import aggregate, score_clip  # noqa: E402
from video_events.schema import RESULT_SCHEMA  # noqa: E402
from video_events.store import result_key  # noqa: E402


def mask(obj) -> str:
    return re.sub(r"\b\d{12}\b", "<ACCOUNT_ID>", json.dumps(obj, ensure_ascii=False, default=str))


def outputs(stack: str, region: str) -> dict:
    cfn = boto3.client("cloudformation", region_name=region)
    st = cfn.describe_stacks(StackName=stack)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in st.get("Outputs", [])}


def invoke(ac, arn: str, payload: dict) -> dict:
    resp = ac.invoke_agent_runtime(agentRuntimeArn=arn, qualifier="DEFAULT",
                                   runtimeSessionId=f"e2e-{uuid.uuid4().hex}", contentType="application/json",
                                   accept="application/json", payload=json.dumps(payload).encode())
    return json.loads(resp["response"].read() or b"{}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", default="proto-video-event-analysis")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--manifest", default="sample-data/eval/manifest.json")
    ap.add_argument("--clips", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--report", help="write a JSON report here")
    args = ap.parse_args()

    out = outputs(args.stack, args.region)
    bucket, arn, fn = out["MediaBucketName"], out["AgentRuntimeArn"], out["DispatcherFunctionName"]
    account = boto3.client("sts", region_name=args.region).get_caller_identity()["Account"]  # not printed
    ac = boto3.client("bedrock-agentcore", region_name=args.region, config=Config(read_timeout=900))
    s3 = boto3.client("s3", region_name=args.region)
    lam = boto3.client("lambda", region_name=args.region)
    report: dict = {"steps": []}

    def step(name, value):
        print(f"== {name}\n{mask(value)}\n")
        report["steps"].append({"name": name, "value": json.loads(mask(value))})

    step("agentcore health", invoke(ac, arn, {"action": "health"}))
    h = lam.invoke(FunctionName=fn, Payload=json.dumps({"action": "health"}).encode())
    step("lambda health", json.loads(h["Payload"].read()))

    root = Path(args.manifest).parent
    clips = json.loads(Path(args.manifest).read_text())["clips"]
    if not args.all:
        clips = [c for c in clips if c["id"] in (args.clips or ["s01_empty_home", "s03_delivery", "s05_fire_yard"])]
    t0 = time.time()
    for c in clips:
        s3.put_object(Bucket=bucket, Key=c["s3_key"], Body=(root / c["file"]).read_bytes(),
                      ContentType="video/mp4", ExpectedBucketOwner=account)
    step("uploaded", [c["s3_key"] for c in clips])

    validator = Draft202012Validator(RESULT_SCHEMA)
    pending, results = {c["id"]: c for c in clips}, {}
    while pending and time.time() - t0 < args.timeout:
        time.sleep(10)
        for cid, c in list(pending.items()):
            from video_events.pipeline import VideoMeta

            meta = VideoMeta.from_key(c["s3_key"])
            key = result_key({"video": {"camera_id": meta.camera_id, "video_id": meta.video_id,
                                        "recorded_at": meta.recorded_at.isoformat()}})
            try:
                body = s3.get_object(Bucket=bucket, Key=key, ExpectedBucketOwner=account)["Body"].read()
            except s3.exceptions.NoSuchKey:
                continue
            results[cid] = json.loads(body)
            pending.pop(cid)
    step("elapsed_s / missing", {"elapsed_s": round(time.time() - t0), "missing": sorted(pending)})

    scores, rows = [], []
    for c in clips:
        r = results.get(c["id"])
        if not r:
            continue
        errs = [e.message for e in validator.iter_errors(r)]
        s = score_clip(c, r)
        scores.append(s)
        rows.append({"clip": c["id"], "schema_errors": errs, "status": r["status"], "scene": r["scene"]["type"],
                     "expected": [e["type"] for e in c["expected_events"]],
                     "predicted": [(e["type"], e["start_s"], e["end_s"], e["risk_level"]) for e in r["events"]],
                     "rejected": r["rejected"], "metrics": {k: r["metrics"][k] for k in (
                         "strategy", "model_calls", "retries", "validation_failures", "input_tokens", "output_tokens",
                         "total_ms")},
                     "notifications": len(r["notifications"]), "tp": s.tp, "fp": s.fp, "fn": s.fn})
    step("results", rows)
    if scores:
        step("aggregate", aggregate(scores, [c for c in clips if c["id"] in results]))

    cams = sorted({c["camera_id"] for c in clips})
    step("ask zh", invoke(ac, arn, {"action": "ask", "question": "火情发生在几点？", "camera_ids": cams,
                                     "now": "2026-10-19T23:59:00Z"}))
    step("ask en", invoke(ac, arn, {"action": "ask", "question": "Did anyone come to the front door today?",
                                     "camera_ids": cams, "now": "2026-10-19T23:59:00Z"}))
    step("search", invoke(ac, arn, {"action": "search", "query": "找宠物出现的片段", "camera_ids": cams,
                                     "now": "2026-10-19T23:59:00Z"}))
    step("daily_summary", invoke(ac, arn, {"action": "daily_summary", "date": "2026-10-19", "camera_ids": cams}))
    if clips:
        step("analyze frames strategy (direct invoke)", invoke(ac, arn, {
            "action": "analyze", "bucket": bucket, "key": clips[-1]["s3_key"],
            "overrides": {"strategy": "frames", "frame_interval_s": 2.0, "segment_length_s": 10.0}}))
    step("guard: foreign bucket", invoke(ac, arn, {"action": "analyze", "bucket": "not-our-bucket",
                                                   "key": clips[0]["s3_key"] if clips else "uploads/x"}))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
