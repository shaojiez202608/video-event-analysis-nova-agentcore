"""Application service shared by the AgentCore entrypoint and the Lambda fallback.

Payload actions:
  {"action": "analyze", "bucket": "...", "key": "uploads/<cam>/<ts>_<name>.mp4", "overrides": {...}}
  {"action": "ask", "question": "...", "camera_ids": ["cam1"], "now": "ISO-8601 (optional)"}
  {"action": "search", "query": "...", "camera_ids": [...]}
  {"action": "daily_summary", "date": "YYYY-MM-DD", "camera_ids": [...]}
  {"action": "health"}

Guards implemented here (not in the model): the bucket must be the configured
media bucket, keys must match the upload pattern, object size and clip duration
are capped, and override keys are restricted to sampling parameters.
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
from datetime import UTC, datetime
from typing import Any

from . import query
from .config import PipelineConfig, load_config
from .metrics import emf_line, log_event
from .models import make_model
from .pipeline import VideoMeta, analyze_video
from .store import S3Store

ALLOWED_OVERRIDES = {"strategy", "frame_interval_s", "segment_length_s"}
MAX_OBJECT_BYTES = int(os.environ.get("MAX_OBJECT_BYTES", str(500 * 1024 * 1024)))
MAX_DURATION_S = float(os.environ.get("MAX_DURATION_S", "900"))
CAMERA_ID_MAX = 20

_cfg: PipelineConfig | None = None


def config() -> PipelineConfig:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


def _media_bucket() -> str:
    return os.environ["MEDIA_BUCKET"]


def _account_id_for(bucket: str) -> str | None:
    return os.environ.get("MEDIA_BUCKET_OWNER") or None


def _camera_ids(payload: dict) -> list[str]:
    cams = payload.get("camera_ids") or []
    if not isinstance(cams, list) or not cams or len(cams) > CAMERA_ID_MAX:
        raise ValueError("camera_ids must be a non-empty list")
    return [str(c) for c in cams]


def analyze_s3_object(bucket: str, key: str, overrides: dict | None = None, s3=None) -> dict:
    import boto3

    if bucket != _media_bucket():
        raise PermissionError("bucket is not the configured media bucket")
    meta = VideoMeta.from_key(key)
    extra = set(overrides or {}) - ALLOWED_OVERRIDES
    if extra:
        raise ValueError(f"overrides not allowed: {sorted(extra)}")
    cfg = config().with_overrides(**(overrides or {}))
    s3 = s3 or boto3.client("s3")
    head = s3.head_object(Bucket=bucket, Key=key)
    size = int(head["ContentLength"])
    if size > MAX_OBJECT_BYTES:
        raise ValueError("object too large for this prototype")
    s3_uri = f"s3://{bucket}/{key}"
    need_bytes = cfg.strategy == "frames" or cfg.model_mode == "mock" or size <= cfg.inline_video_max_bytes
    video: bytes | str
    if need_bytes:
        video = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    else:
        # Large clip with the native video strategy: probe from a temp file, send by S3 URI.
        fd, path = tempfile.mkstemp(suffix=f".{meta.video_format}")
        os.close(fd)
        s3.download_file(bucket, key, path)
        video = path
    from .frames import probe

    info = probe(video)
    if info.duration_s > MAX_DURATION_S:
        raise ValueError("clip too long for this prototype")
    try:
        result = analyze_video(video, meta, cfg, make_model(cfg), s3_uri=s3_uri,
                               bucket_owner=_account_id_for(bucket), video_size=size,
                               frame_sink=_frame_sink(s3, bucket) if cfg.persist_frames else None)
    finally:
        if isinstance(video, str) and os.path.exists(video):
            os.remove(video)
    result_key = S3Store(bucket, client=s3).put(result)
    print(emf_line(result["metrics"]))  # CloudWatch EMF: counts and timings only
    return {"result_key": result_key, "status": result["status"], "overall_risk_level": result["overall_risk_level"],
            "events": len(result["events"]), "metrics": result["metrics"], "result": result}


def _frame_sink(s3, bucket):
    def sink(meta: VideoMeta, frames):
        for f in frames:
            s3.put_object(Bucket=bucket, Key=f"frames/{meta.camera_id}/{meta.video_id}/{f.t:08.2f}.jpg",
                          Body=f.jpeg, ContentType="image/jpeg")
    return sink


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action", "analyze")
    if action == "health":
        # No config or AWS calls: only proves that the arm64 bundle (PyAV etc.) imports and runs.
        import av

        from .frames import sample_times

        return {"ok": True, "python": platform.python_version(), "machine": platform.machine(),
                "libc": "-".join(platform.libc_ver()), "pyav": av.__version__,
                "mjpeg_encoder": av.codec.Codec("mjpeg", "w").name, "sample_times": sample_times(4, 2)}
    cfg = config()
    if action == "analyze":
        out = analyze_s3_object(str(payload["bucket"]), str(payload["key"]), payload.get("overrides"))
        if not payload.get("include_result", False):
            out.pop("result", None)
        return out
    store = S3Store(_media_bucket())
    now = datetime.fromisoformat(payload["now"].replace("Z", "+00:00")) if payload.get("now") else datetime.now(UTC)
    if action == "ask":
        q = str(payload.get("question", ""))[:500]
        out = query.ask(q, store, _camera_ids(payload), now, mode=cfg.model_mode, model_id=cfg.model_id,
                        utc_offset_minutes=cfg.utc_offset_minutes)
        log_event("qa_answered", mode=out["mode"], hits=len(out["events"]))
        return out
    if action == "search":
        flt = query.parse_query_rules(str(payload.get("query", ""))[:500], now, cfg.utc_offset_minutes)
        hits = query.search(store, _camera_ids(payload), flt)
        log_event("search_done", hits=len(hits))
        return {"filter": flt.describe(), "events": hits}
    if action == "daily_summary":
        return query.daily_summary(store, _camera_ids(payload), str(payload["date"]), cfg.utc_offset_minutes,
                                   cfg.locales)
    raise ValueError(f"unknown action: {action}")


def safe_handle(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return handle(payload)
    except (ValueError, PermissionError, KeyError) as exc:
        log_event("request_rejected", action=str(payload.get("action", ""))[:20], error=type(exc).__name__)
        return {"error": type(exc).__name__, "message": str(exc)[:200]}


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
