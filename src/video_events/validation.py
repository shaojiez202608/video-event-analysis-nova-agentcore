"""Hard validation of model output (FR-5).

Order of checks:
1. Parse: the text must contain one JSON object (code fences are tolerated).
2. Top-level schema (scene, events array, summary). Failure -> the whole response
   is invalid; the pipeline retries and then falls back.
3. Per event: schema + whitelist, category/type consistency, finite numbers,
   time range inside the clip (small tolerance is clamped, more is rejected).
   Bad events are dropped and listed under ``rejected`` with a reason; the rest
   of the response is kept.
4. Text hygiene: control characters removed, identity-like phrases redacted.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from .schema import MODEL_EVENT_SCHEMA, MODEL_OUTPUT_SCHEMA
from .taxonomy import category_of, risk_rank

_TOP = Draft202012Validator(MODEL_OUTPUT_SCHEMA)
_EVT = Draft202012Validator(MODEL_EVENT_SCHEMA)

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# Phrases that suggest identification of a person. Matching text is replaced,
# not trusted. This is a coarse guard; the prompt and the model's own
# limitations are the primary controls.
_IDENTITY = re.compile(
    r"(\bnamed\b|\bname is\b|\bidentified as\b|\brecogni[sz]ed as\b|\bfacial\b|\bface (match|id)|"
    r"\bwho is\b|名叫|名字是|身份是|认出|人脸|是.{0,6}(先生|女士|小姐))",
    re.IGNORECASE,
)
REDACTED = "[description removed: identity-like content]"


class ResponseInvalid(ValueError):
    """The whole model response is unusable."""


@dataclass
class ValidatedOutput:
    scene: dict[str, str]
    events: list[dict[str, Any]]
    summary: str
    rejected: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_json_text(text: str) -> Any:
    s = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    if fence:
        s = fence.group(1)
    if not s.startswith("{"):
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end <= start:
            raise ResponseInvalid("no JSON object in response")
        s = s[start : end + 1]
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        raise ResponseInvalid(f"invalid JSON: {exc.msg}") from exc


def clean_text(value: str, limit: int) -> tuple[str, bool]:
    v = _CTRL.sub(" ", value).strip()[:limit]
    if _IDENTITY.search(v):
        return REDACTED, True
    return v, False


def _short(err) -> str:
    path = "/".join(str(p) for p in err.absolute_path) or "<root>"
    return f"{path}: {err.message[:120]}"


def validate_model_output(
    obj: Any, clip_duration_s: float, tolerance_s: float = 1.0
) -> ValidatedOutput:
    if not isinstance(obj, dict):
        raise ResponseInvalid("top level is not an object")
    errors = sorted(_TOP.iter_errors(obj), key=lambda e: list(e.absolute_path))
    if errors:
        raise ResponseInvalid("schema: " + "; ".join(_short(e) for e in errors[:3]))

    warnings: list[str] = []
    scene_desc, red = clean_text(obj["scene_description"], 300)
    if red:
        warnings.append("identity_text_redacted")
    summary, red = clean_text(obj["summary"], 600)
    if red:
        warnings.append("identity_text_redacted")

    events: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for idx, ev in enumerate(obj["events"]):
        reason = _event_problem(ev)
        if reason:
            rejected.append({"index": idx, "reason": reason, "type": _safe_type(ev)})
            continue
        ev = dict(ev)
        start, end = float(ev["start_s"]), float(ev["end_s"])
        if start > end:
            rejected.append({"index": idx, "reason": "start_after_end", "type": ev["type"]})
            continue
        if start < -tolerance_s or end > clip_duration_s + tolerance_s:
            rejected.append({"index": idx, "reason": "time_out_of_clip", "type": ev["type"]})
            continue
        ev["start_s"] = round(min(max(start, 0.0), clip_duration_s), 2)
        ev["end_s"] = round(min(max(end, 0.0), clip_duration_s), 2)
        expected_cat = category_of(ev["type"])
        if ev["category"] != expected_cat:
            warnings.append(f"category_corrected:{ev['type']}")
            ev["category"] = expected_cat
        ev["description"], red = clean_text(ev["description"], 300)
        if red:
            warnings.append("identity_text_redacted")
        events.append(ev)

    return ValidatedOutput(
        scene={"type": obj["scene_type"], "description": scene_desc},
        events=events,
        summary=summary,
        rejected=rejected,
        warnings=warnings,
    )


def _safe_type(ev: Any) -> str:
    t = ev.get("type") if isinstance(ev, dict) else None
    return t if isinstance(t, str) and category_of(t) else "<invalid>"


def _event_problem(ev: Any) -> str | None:
    if not isinstance(ev, dict):
        return "event_not_object"
    errs = list(_EVT.iter_errors(ev))
    if errs:
        first = errs[0]
        field_name = first.absolute_path[0] if first.absolute_path else first.validator
        if field_name == "type" or (first.validator == "enum" and "type" in list(first.absolute_path)):
            return "type_not_in_whitelist"
        return f"schema:{field_name}"
    for k in ("start_s", "end_s"):
        if not math.isfinite(float(ev[k])):
            return "non_finite_time"
    return None


def merge_events(events: list[dict[str, Any]], gap_s: float) -> list[dict[str, Any]]:
    """Merge same-type events that touch or are within gap_s (across segments)."""
    out: list[dict[str, Any]] = []
    for ev in sorted(events, key=lambda e: (e["type"], e["start_s"])):
        last = out[-1] if out else None
        if last and last["type"] == ev["type"] and ev["start_s"] <= last["end_s"] + gap_s:
            last["end_s"] = max(last["end_s"], ev["end_s"])
            if risk_rank(ev["risk_level"]) > risk_rank(last["risk_level"]):
                last["risk_level"] = ev["risk_level"]
            order = ["low", "medium", "high"]
            if order.index(ev["confidence"]) > order.index(last["confidence"]):
                last["confidence"] = ev["confidence"]
            continue
        out.append(dict(ev))
    out.sort(key=lambda e: (e["start_s"], e["type"]))
    return out
