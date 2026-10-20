"""End-to-end analysis of one clip: plan calls -> model -> validate -> merge ->
post-process -> result document with metrics (FR-2, FR-3, FR-5, FR-8)."""

from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from . import i18n, rules
from .config import PipelineConfig
from .frames import SampledFrame, VideoInfo, probe, sample_frames
from .metrics import log_event
from .models import ModelRequest, VisionModel
from .schema import SCHEMA_VERSION
from .taxonomy import max_level
from .validation import ResponseInvalid, merge_events, parse_json_text, validate_model_output

log = logging.getLogger("video_events")

KEY_RE = re.compile(
    r"^uploads/(?P<camera>[A-Za-z0-9_-]{1,64})/(?P<ts>\d{8}T\d{6}Z)_(?P<name>[A-Za-z0-9_.-]{1,100})\.(?P<ext>mp4|mov|mkv|webm)$"
)


@dataclass
class VideoMeta:
    video_id: str
    camera_id: str
    recorded_at: datetime  # UTC
    source_key: str = ""
    video_format: str = "mp4"

    @classmethod
    def from_key(cls, key: str) -> VideoMeta:
        """Parse uploads/<camera_id>/<YYYYMMDDTHHMMSSZ>_<name>.<ext>."""
        m = KEY_RE.match(key)
        if not m:
            raise ValueError("object key does not match uploads/<camera_id>/<YYYYMMDDTHHMMSSZ>_<name>.<ext>")
        ts = datetime.strptime(m["ts"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        return cls(
            video_id=f"{m['camera']}-{m['ts']}-{m['name']}",
            camera_id=m["camera"],
            recorded_at=ts,
            source_key=key,
            video_format=m["ext"],
        )


@dataclass
class Segment:
    start_s: float
    end_s: float
    frames: list[SampledFrame]


def plan_segments(frames: list[SampledFrame], duration_s: float, segment_len_s: float, max_images: int) -> list[Segment]:
    """Group frames by segment length, then split so no call exceeds max_images."""
    seg_len = segment_len_s if segment_len_s > 0 else max(duration_s, 1e-6)
    buckets: dict[int, list[SampledFrame]] = {}
    for f in frames:
        buckets.setdefault(min(int(f.t // seg_len), max(0, math.ceil(duration_s / seg_len) - 1)), []).append(f)
    segs: list[Segment] = []
    for idx in sorted(buckets):
        group = buckets[idx]
        for i in range(0, len(group), max_images):
            chunk = group[i : i + max_images]
            segs.append(Segment(round(idx * seg_len, 3), round(min(duration_s, (idx + 1) * seg_len), 3), chunk))
    return segs


def _call_with_validation(model: VisionModel, req: ModelRequest, cfg: PipelineConfig, metrics: dict):
    """One logical call with up to cfg.max_retries re-asks on invalid output."""
    last_err = None
    for attempt in range(cfg.max_retries + 1):
        if attempt:
            metrics["retries"] += 1
            req.retry_hint = str(last_err)[:200]
        resp = model.analyze(req)
        metrics["model_calls"] += 1
        metrics["input_tokens"] += resp.input_tokens
        metrics["output_tokens"] += resp.output_tokens
        metrics["model_latency_ms"] += resp.latency_ms
        metrics["images_sent"] += resp.images_sent
        try:
            return validate_model_output(parse_json_text(resp.text), req.clip_duration_s, cfg.time_tolerance_s)
        except ResponseInvalid as exc:
            metrics["validation_failures"] += 1
            last_err = exc
            log_event("model_output_invalid", attempt=attempt, reason=str(exc)[:200])
    return last_err


def analyze_video(
    video: bytes | str,
    meta: VideoMeta,
    cfg: PipelineConfig,
    model: VisionModel,
    s3_uri: str | None = None,
    bucket_owner: str | None = None,
    video_size: int | None = None,
    frame_sink=None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    metrics: dict[str, Any] = dict(
        strategy=cfg.strategy, frame_interval_s=cfg.frame_interval_s if cfg.strategy == "frames" else None,
        segment_length_s=cfg.segment_length_s if cfg.strategy == "frames" else None,
        model_calls=0, retries=0, validation_failures=0, input_tokens=0, output_tokens=0,
        model_latency_ms=0, images_sent=0, frames_sampled=0, rejected_events=0, segments=0,
    )
    info: VideoInfo = probe(video)
    reqs: list[ModelRequest] = []
    if cfg.strategy == "video":
        size = video_size if video_size is not None else (len(video) if isinstance(video, bytes) else None)
        use_inline = isinstance(video, bytes) and (size or 0) <= cfg.inline_video_max_bytes
        if not use_inline and not s3_uri:
            if isinstance(video, str):
                with open(video, "rb") as fh:
                    video, use_inline = fh.read(), True
            else:
                raise ValueError("clip too large for inline input and no S3 URI given")
        reqs.append(ModelRequest(
            kind="video", clip_duration_s=info.duration_s, seg_start_s=0.0, seg_end_s=info.duration_s,
            video_bytes=video if use_inline else None, video_s3_uri=None if use_inline else s3_uri,
            video_bucket_owner=bucket_owner, video_format=meta.video_format,
        ))
    else:
        frames = sample_frames(video, cfg.frame_interval_s, cfg.frame_width)
        metrics["frames_sampled"] = len(frames)
        if frame_sink is not None and cfg.persist_frames:
            frame_sink(meta, frames)
        for seg in plan_segments(frames, info.duration_s, cfg.segment_length_s, cfg.max_images_per_call):
            reqs.append(ModelRequest(
                kind="frames", clip_duration_s=info.duration_s, seg_start_s=seg.start_s, seg_end_s=seg.end_s,
                frames=seg.frames, frame_interval_s=cfg.frame_interval_s,
            ))
    metrics["segments"] = len(reqs)

    pairs, failures = [], []
    for req in reqs:
        out = _call_with_validation(model, req, cfg, metrics)
        if isinstance(out, Exception):
            failures.append(out)
        else:
            pairs.append((req, out))
    outputs = [o for _, o in pairs]

    gap = cfg.merge_gap_s + (cfg.frame_interval_s if cfg.strategy == "frames" else 0.0)
    events = merge_events([e for o in outputs for e in o.events], gap)
    rejected = [r for o in outputs for r in o.rejected]
    warnings = sorted({w for o in outputs for w in o.warnings})
    metrics["rejected_events"] = len(rejected)

    if not outputs:
        status = "fallback"
    elif failures or rejected:
        status = "partial"
    else:
        status = "ok"

    scene = _vote_scene([o.scene for o in outputs])
    if len(outputs) == 1:
        summary = outputs[0].summary
    else:
        summary = " ".join(f"[{r.seg_start_s:.0f}-{r.seg_end_s:.0f}s] {o.summary}" for r, o in pairs)[:1200]

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "video": {
            "video_id": meta.video_id,
            "camera_id": meta.camera_id,
            "recorded_at": meta.recorded_at.isoformat().replace("+00:00", "Z"),
            "duration_s": info.duration_s,
            "source_key": meta.source_key,
        },
        "model": model.name,
        "status": status,
        "needs_review": status != "ok",
        "fallback_reason": "; ".join(str(f)[:200] for f in failures) or None,
        "scene": scene,
        "events": events,
        "summary": summary,
        "rejected": rejected,
        "warnings": warnings,
    }
    postprocess(result, cfg)
    metrics["total_ms"] = int((time.perf_counter() - t0) * 1000)
    result["metrics"] = metrics
    log_event(
        "video_analyzed", video_id=meta.video_id, status=status, events=len(result["events"]),
        overall_risk_level=result["overall_risk_level"],
        **{k: metrics[k] for k in ("strategy", "model_calls", "retries", "validation_failures", "input_tokens",
                                   "output_tokens", "images_sent", "rejected_events", "total_ms")},
    )
    return result


def _vote_scene(scenes: list[dict[str, str]]) -> dict[str, str]:
    if not scenes:
        return {"type": "unknown", "description": ""}
    counts = Counter(s["type"] for s in scenes if s["type"] != "unknown")
    if not counts:
        return scenes[0]
    best = counts.most_common(1)[0][0]
    return next(s for s in scenes if s["type"] == best)


def postprocess(result: dict[str, Any], cfg: PipelineConfig) -> dict[str, Any]:
    """Risk floors, event ids, absolute/local times, localisation, watch rules."""
    recorded = datetime.fromisoformat(result["video"]["recorded_at"].replace("Z", "+00:00"))
    local_tz = timedelta(minutes=cfg.utc_offset_minutes)
    for i, ev in enumerate(result["events"]):
        floor = cfg.risk_floors.get(ev["type"], "low")
        final = max_level(ev["risk_level"], floor)
        ev["model_risk_level"] = ev["risk_level"]
        ev["risk_level"] = final
        ev["event_id"] = f"{result['video']['video_id']}#{i}"
        start = recorded + timedelta(seconds=ev["start_s"])
        end = recorded + timedelta(seconds=ev["end_s"])
        ev["abs_start"] = start.isoformat().replace("+00:00", "Z")
        ev["abs_end"] = end.isoformat().replace("+00:00", "Z")
        ev["local_time"] = (start + local_tz).strftime("%H:%M:%S")
    levels = [e["risk_level"] for e in result["events"] if e["category"] == "risk"]
    result["overall_risk_level"] = max_level(*levels) if levels else ("low" if result["events"] else "none")
    result["localized"] = i18n.localize(result, cfg.locales)
    result["notifications"] = rules.evaluate(result["events"], rules.load_rules(cfg.watch_rules))
    return result
