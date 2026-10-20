"""Scoring against the synthetic labels.

Matching: a predicted event matches an expected event of the same type when
their temporal IoU >= ``iou_threshold`` (default 0.3). Each expected event can
be matched once (greedy, by IoU). Predicted events whose type is listed in the
clip's ``optional_types`` are ignored (neither TP nor FP).
"""

from __future__ import annotations

from dataclasses import dataclass, field


def tiou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    if union <= 0:
        return 1.0 if abs(a[0] - b[0]) < 1e-6 else 0.0
    return inter / union


@dataclass
class ClipScore:
    clip_id: str
    tp: int
    fp: int
    fn: int
    type_tp: int  # type-only matches (ignoring time)
    ious: list[float] = field(default_factory=list)
    scene_ok: bool = False
    status: str = "ok"
    missed: list[str] = field(default_factory=list)
    spurious: list[str] = field(default_factory=list)


def score_clip(clip: dict, result: dict, iou_threshold: float = 0.3) -> ClipScore:
    expected = [dict(e) for e in clip["expected_events"]]
    optional = set(clip.get("optional_types", []))
    preds = [e for e in result["events"] if e["type"] not in optional]
    used: set[int] = set()
    tp, ious, spurious = 0, [], []
    for p in sorted(preds, key=lambda e: e["start_s"]):
        best, best_iou = None, 0.0
        for i, e in enumerate(expected):
            if i in used or e["type"] != p["type"]:
                continue
            v = tiou((p["start_s"], p["end_s"]), (e["start_s"], e["end_s"]))
            if v > best_iou:
                best, best_iou = i, v
        if best is not None and best_iou >= iou_threshold:
            used.add(best)
            tp += 1
            ious.append(best_iou)
        else:
            spurious.append(p["type"])
    exp_types = [e["type"] for e in expected]
    pred_types = [p["type"] for p in preds]
    type_tp = sum(min(exp_types.count(t), pred_types.count(t)) for t in set(exp_types))
    missed = [e["type"] for i, e in enumerate(expected) if i not in used]
    return ClipScore(
        clip_id=clip["id"], tp=tp, fp=len(preds) - tp, fn=len(expected) - tp, type_tp=type_tp, ious=ious,
        scene_ok=result["scene"]["type"] == clip["scene"], status=result["status"], missed=missed,
        spurious=spurious,
    )


def aggregate(scores: list[ClipScore], clips: list[dict]) -> dict:
    tp = sum(s.tp for s in scores)
    fp = sum(s.fp for s in scores)
    fn = sum(s.fn for s in scores)
    n_expected = sum(len(c["expected_events"]) for c in clips)
    type_tp = sum(s.type_tp for s in scores)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    all_ious = [v for s in scores for v in s.ious]
    by_id = {c["id"]: c for c in clips}
    no_event = [s for s in scores if not by_id[s.clip_id]["expected_events"]]
    short = [s for s in scores if "short" in by_id[s.clip_id]["tags"]]
    return {
        "clips": len(scores),
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "f1": round(f1, 3),
        "type_recall": round(type_tp / n_expected, 3) if n_expected else 1.0,
        "mean_tiou": round(sum(all_ious) / len(all_ious), 3) if all_ious else 0.0,
        "scene_accuracy": round(sum(s.scene_ok for s in scores) / len(scores), 3) if scores else 0.0,
        "no_event_clean_rate": round(sum(s.fp == 0 for s in no_event) / len(no_event), 3) if no_event else 1.0,
        "short_event_recall": round(sum(s.tp for s in short) / max(1, sum(s.tp + s.fn for s in short)), 3),
        "fallback_clips": sum(s.status == "fallback" for s in scores),
    }


def run_suite(manifest_path: str, cfg, model, only: list[str] | None = None, results_dir: str | None = None):
    """Run the pipeline over every clip in a manifest and score it."""
    import json
    from pathlib import Path

    from .pipeline import VideoMeta, analyze_video
    from .store import LocalStore

    root = Path(manifest_path).parent
    doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    clips = [c for c in doc["clips"] if not only or c["id"] in only]
    store = LocalStore(results_dir) if results_dir else None
    scores, totals = [], {"model_calls": 0, "images_sent": 0, "input_tokens": 0, "output_tokens": 0,
                          "retries": 0, "validation_failures": 0, "model_errors": 0, "rejected_events": 0,
                          "total_ms": 0}
    per_clip = []
    for clip in clips:
        data = (root / clip["file"]).read_bytes()
        meta = VideoMeta.from_key(clip["s3_key"])
        result = analyze_video(data, meta, cfg, model)
        if store:
            store.put(result)
        s = score_clip(clip, result)
        scores.append(s)
        for k in totals:
            totals[k] += result["metrics"].get(k, 0) or 0
        per_clip.append({"id": clip["id"], "tp": s.tp, "fp": s.fp, "fn": s.fn, "scene_ok": s.scene_ok,
                         "status": result["status"], "missed": s.missed, "spurious": s.spurious,
                         "model_calls": result["metrics"]["model_calls"],
                         "input_tokens": result["metrics"]["input_tokens"]})
    summary = aggregate(scores, clips)
    summary.update(totals)
    return summary, per_clip
