"""S3 upload -> analysis dispatcher (arm64 Lambda).

ORCHESTRATOR=agentcore (default): forwards the object reference to the
AgentCore Runtime with InvokeAgentRuntime and records the outcome.
ORCHESTRATOR=lambda: runs the same pipeline in-process. This is the
replaceable orchestration layer kept for environments where AgentCore is
not available.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import uuid

import boto3

from video_events.metrics import log_event

logging.getLogger().setLevel(logging.INFO)
logging.getLogger("video_events").setLevel(logging.INFO)

ORCHESTRATOR = os.environ.get("ORCHESTRATOR", "agentcore")
_agentcore = None


def _invoke_agentcore(bucket: str, key: str) -> dict:
    global _agentcore
    if _agentcore is None:
        from botocore.config import Config

        _agentcore = boto3.client("bedrock-agentcore", config=Config(read_timeout=840, retries={"max_attempts": 1}))
    resp = _agentcore.invoke_agent_runtime(
        agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"],
        qualifier="DEFAULT",
        runtimeSessionId=f"analyze-{uuid.uuid4().hex}",  # one session per clip, >= 33 chars
        contentType="application/json",
        accept="application/json",
        payload=json.dumps({"action": "analyze", "bucket": bucket, "key": key}).encode(),
    )
    body = resp["response"].read() if hasattr(resp.get("response"), "read") else resp.get("response", b"")
    return json.loads(body or b"{}")


def handler(event, context):
    if event.get("action") == "health":  # manual check that the arm64 bundle imports (PyAV etc.)
        from video_events.service import handle

        return handle({"action": "health"})
    outcomes = []
    for rec in event.get("Records", []):
        bucket = rec["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(rec["s3"]["object"]["key"])
        if ORCHESTRATOR == "lambda":
            from video_events.service import safe_handle

            out = safe_handle({"action": "analyze", "bucket": bucket, "key": key})
        else:
            out = _invoke_agentcore(bucket, key)
        log_event("dispatch_done", orchestrator=ORCHESTRATOR, status=out.get("status"),
                  error=out.get("error"), events=out.get("events"),
                  model_calls=(out.get("metrics") or {}).get("model_calls"))
        outcomes.append({"key_suffix": key[-40:], "status": out.get("status"), "error": out.get("error")})
    return {"processed": len(outcomes), "outcomes": outcomes}
