"""Search, question answering and daily summaries over stored structured events.

Everything here reads the validated event records produced by the pipeline;
no video is re-analysed. The camera scope always comes from the caller
(code), never from the model or the question text.

- search(): structured filter -> matching events.
- parse_query_rules(): keyword parser (zh/en) that turns a question into a
  filter. Used in mock mode and as the offline baseline.
- ask(): mock mode = parser + template answer; bedrock mode = Nova with one
  tool (search_events) whose arguments are validated before execution.
- daily_summary(): deterministic aggregation, no model call.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from jsonschema import Draft202012Validator

from . import i18n
from .prompts import QA_SYSTEM_PROMPT
from .store import EventStore, ResultList
from .taxonomy import ALL_EVENT_TYPES, LOCATIONS, RISK_LEVELS, SCENE_TYPES, SUBJECTS, risk_rank
from .validation import clean_text

PERSON_SUBJECTS = ["person", "courier_like_person", "multiple_people"]


@dataclass
class EventFilter:
    start: datetime
    end: datetime
    event_types: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    scene_types: list[str] = field(default_factory=list)
    min_risk_level: str | None = None
    min_duration_s: float | None = None

    def describe(self) -> dict:
        d = asdict(self)
        d["start"], d["end"] = self.start.isoformat(), self.end.isoformat()
        return d


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _utc(t: datetime) -> datetime:
    """Naive times are taken as UTC (as the tool schema says); aware times are converted, not relabelled."""
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def search(store: EventStore, camera_ids: list[str], flt: EventFilter, limit: int = 50) -> ResultList:
    """Matching events, at most ``limit``. ``.truncated`` is True when the store hit its read cap or
    more than ``limit`` events matched, so callers can say the answer may be incomplete."""
    hits = []
    results = store.list_results(camera_ids, flt.start - timedelta(hours=1), flt.end)
    for res in results:
        if flt.scene_types and res["scene"]["type"] not in flt.scene_types:
            continue
        for ev in res["events"]:
            start, end = _ts(ev["abs_start"]), _ts(ev["abs_end"])
            if end < flt.start or start > flt.end:
                continue
            if flt.event_types and ev["type"] not in flt.event_types:
                continue
            if flt.locations and ev.get("location") not in flt.locations:
                continue
            if flt.subjects and ev.get("subject") not in flt.subjects:
                continue
            if flt.min_risk_level and risk_rank(ev["risk_level"]) < risk_rank(flt.min_risk_level):
                continue
            dur = ev["end_s"] - ev["start_s"]
            if flt.min_duration_s is not None and dur < flt.min_duration_s:
                continue
            hits.append({
                "event_id": ev["event_id"], "video_id": res["video"]["video_id"],
                "camera_id": res["video"]["camera_id"], "source_key": res["video"]["source_key"],
                "type": ev["type"], "risk_level": ev["risk_level"], "location": ev.get("location"),
                "subject": ev.get("subject"), "abs_start": ev["abs_start"], "abs_end": ev["abs_end"],
                "local_time": ev.get("local_time"), "duration_s": round(dur, 1),
                "description": ev.get("description", ""),
            })
    hits.sort(key=lambda h: h["abs_start"])
    return ResultList(hits[:limit], truncated=bool(getattr(results, "truncated", False)) or len(hits) > limit)


TRUNCATED_NOTE = {
    "zh-CN": "（结果可能不完整：已达到读取上限，请缩小时间范围或摄像头范围。）",
    "en-US": " (Results may be incomplete: the read limit was reached; narrow the time range or cameras.)",
    "ru-RU": " (Результаты могут быть неполными: достигнут лимит чтения; сузьте период или список камер.)",
}


# ---------------------------------------------------------------- rule parser

_KEYWORDS: list[tuple[re.Pattern, dict]] = [
    (re.compile(r"火|烟|fire|smoke|пожар|огонь|дым", re.I), {"event_types": ["fire_smoke"]}),
    (re.compile(r"宠物|猫|狗|pet|dog|cat|питом", re.I), {"event_types": ["pet_activity"]}),
    (re.compile(r"快递|包裹|deliver|parcel|package|посыл|доставк", re.I), {"event_types": ["package_delivery"]}),
    (re.compile(r"车|vehicle|car\b|машин|автомоб", re.I), {"event_types": ["vehicle_parking"]}),
    (re.compile(r"撬|forced|pry|взлом", re.I), {"event_types": ["forced_entry"]}),
    (re.compile(r"偷|盗|theft|steal|stole|краж", re.I), {"event_types": ["theft_indication"]}),
    (re.compile(r"打架|暴力|fight|violen|насили|драк", re.I), {"event_types": ["violence"]}),
    (re.compile(r"入侵|闯入|intru|вторж", re.I), {"event_types": ["intrusion"]}),
    (re.compile(r"徘徊|停留|逗留|loiter|linger|stay|задерж", re.I),
     {"event_types": ["person_lingering", "suspicious_loitering"]}),
    (re.compile(r"搬|carry|переноc|перенос", re.I), {"event_types": ["object_carrying"]}),
    (re.compile(r"进出|进门|出门|enter|exit|вход|выход", re.I), {"event_types": ["entry_exit"]}),
    (re.compile(r"谁|有人|人员|someone|anyone|who|person|people|кто|человек", re.I),
     {"subjects": PERSON_SUBJECTS}),
    (re.compile(r"门口|门|door|entrance|двер|вход", re.I), {"locations": ["entrance"]}),
    (re.compile(r"院|yard|garden|двор", re.I), {"locations": ["yard", "driveway"]}),
    (re.compile(r"店|store|shop|магазин", re.I), {"locations": ["storefront"]}),
    (re.compile(r"风险|危险|异常|risk|danger|alert|опасн|риск", re.I), {"min_risk_level": "attention"}),
]
_MINUTES = re.compile(r"(?:超过|大于|over|more than|longer than|более)\s*(\d+(?:\.\d+)?)\s*(分钟|min|минут)", re.I)
_SECONDS = re.compile(r"(?:超过|大于|over|more than|longer than|более)\s*(\d+(?:\.\d+)?)\s*(秒|s\b|sec|секунд)", re.I)


def _local_day_bounds(now_utc: datetime, offset: timedelta, days_back: int) -> tuple[datetime, datetime]:
    local = now_utc + offset
    day_start = datetime(local.year, local.month, local.day, tzinfo=UTC) - offset - timedelta(days=days_back)
    return day_start, day_start + timedelta(days=1) - timedelta(microseconds=1)


def parse_query_rules(text: str, now_utc: datetime, utc_offset_minutes: int = 0) -> EventFilter:
    off = timedelta(minutes=utc_offset_minutes)
    t = text.strip()
    if re.search(r"昨晚|昨夜|last night|прошлой ночью|вчера вечером", t, re.I):
        today_start, _ = _local_day_bounds(now_utc, off, 0)
        start, end = today_start - timedelta(hours=6), today_start + timedelta(hours=6)
    elif re.search(r"昨天|yesterday|вчера", t, re.I):
        start, end = _local_day_bounds(now_utc, off, 1)
    elif re.search(r"今天|今日|today|сегодня", t, re.I):
        start, end = _local_day_bounds(now_utc, off, 0)
    else:
        start, end = now_utc - timedelta(days=7), now_utc
    flt = EventFilter(start=start, end=end)
    for pat, upd in _KEYWORDS:
        if pat.search(t):
            for k, v in upd.items():
                if isinstance(v, list):
                    merged = getattr(flt, k) + [x for x in v if x not in getattr(flt, k)]
                    setattr(flt, k, merged)
                else:
                    setattr(flt, k, v)
    # "who/anyone" plus a specific non-person event type: drop the subject filter.
    if flt.subjects and flt.event_types and not set(flt.event_types) & {
        "person_lingering", "suspicious_loitering", "entry_exit", "package_delivery", "intrusion",
        "forced_entry", "theft_indication", "violence", "object_carrying",
    }:
        flt.subjects = []
    m = _MINUTES.search(t) or _SECONDS.search(t)
    if m:
        val = float(m.group(1))
        flt.min_duration_s = val * 60 if m.group(2).lower().startswith(("分", "min", "мин")) else val
    return flt


def _lang(text: str) -> str:
    if re.search(r"[一-鿿]", text):
        return "zh-CN"
    if re.search(r"[Ѐ-ӿ]", text):
        return "ru-RU"
    return "en-US"


_SUBJECT_WORDS = {
    "zh-CN": {"person": "有人", "courier_like_person": "快递员模样的人", "multiple_people": "多人",
              "vehicle": "车辆", "pet": "宠物", "object": "物体", "fire_smoke": "火情/烟雾", "unknown": "画面中"},
    "en-US": {"person": "a person", "courier_like_person": "a courier-like person",
              "multiple_people": "several people", "vehicle": "a vehicle", "pet": "a pet", "object": "an object",
              "fire_smoke": "fire or smoke", "unknown": "something"},
    "ru-RU": {"person": "человек", "courier_like_person": "человек, похожий на курьера",
              "multiple_people": "несколько человек", "vehicle": "автомобиль", "pet": "питомец",
              "object": "предмет", "fire_smoke": "огонь или дым", "unknown": "объект"},
}
_NONE = {"zh-CN": "没有找到符合条件的事件。", "en-US": "No matching events were found.",
         "ru-RU": "Подходящих событий не найдено."}


def template_answer(question: str, hits: list[dict]) -> str:
    lc = _lang(question)
    if not hits:
        return _NONE[lc]
    parts = []
    for h in hits[:5]:
        who = _SUBJECT_WORDS[lc].get(h.get("subject") or "unknown", "")
        ev = i18n.EVENT_NAMES[lc][h["type"]]
        loc = i18n.LOCATION_NAMES[lc].get(h.get("location") or "unknown", "")
        if lc == "zh-CN":
            parts.append(f"{h['local_time']} {who}（{ev}，{loc}，约{h['duration_s']:.0f}秒）")
        elif lc == "ru-RU":
            parts.append(f"{h['local_time']}: {who} — {ev}, {loc}, ~{h['duration_s']:.0f} с")
        else:
            parts.append(f"{h['local_time']}: {who} — {ev} at {loc}, about {h['duration_s']:.0f}s")
    more = len(hits) - 5
    sep = "；" if lc == "zh-CN" else "; "
    tail = {"zh-CN": f"（另有 {more} 条）", "en-US": f" (+{more} more)", "ru-RU": f" (ещё {more})"}[lc] if more > 0 else ""
    head = {"zh-CN": f"找到 {len(hits)} 条：", "en-US": f"Found {len(hits)}: ", "ru-RU": f"Найдено {len(hits)}: "}[lc]
    return head + sep.join(parts) + tail


# ---------------------------------------------------------------- Nova tool use

SEARCH_TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "start": {"type": "string", "description": "ISO-8601 UTC start time"},
        "end": {"type": "string", "description": "ISO-8601 UTC end time"},
        "event_types": {"type": "array", "items": {"enum": list(ALL_EVENT_TYPES)}},
        "locations": {"type": "array", "items": {"enum": list(LOCATIONS)}},
        "subjects": {"type": "array", "items": {"enum": list(SUBJECTS)}},
        "scene_types": {"type": "array", "items": {"enum": list(SCENE_TYPES)}},
        "min_risk_level": {"enum": list(RISK_LEVELS)},
        "min_duration_s": {"type": "number", "minimum": 0},
    },
    "required": ["start", "end"],
}
_TOOL_VALIDATOR = Draft202012Validator(SEARCH_TOOL_SCHEMA)
MAX_TOOL_ROUNDS = 3
MAX_WINDOW = timedelta(days=31)


def filter_from_tool_input(args: dict) -> EventFilter:
    errs = list(_TOOL_VALIDATOR.iter_errors(args))
    if errs:
        path = "/".join(str(p) for p in errs[0].absolute_path) or "<root>"
        raise ValueError(f"invalid tool input: {path}: {errs[0].validator}")
    try:
        start, end = _utc(_ts(args["start"])), _utc(_ts(args["end"]))
    except ValueError as exc:
        raise ValueError("invalid tool input: start/end must be ISO-8601") from exc
    if end < start or end - start > MAX_WINDOW:
        raise ValueError("invalid time window")
    return EventFilter(
        start=start, end=end, event_types=args.get("event_types", []), locations=args.get("locations", []),
        subjects=args.get("subjects", []), scene_types=args.get("scene_types", []),
        min_risk_level=args.get("min_risk_level"), min_duration_s=args.get("min_duration_s"),
    )


def _tool_hits_view(hits: list[dict]) -> list[dict]:
    # Only enumerated fields and times go back to the model, not free-text descriptions.
    keys = ("event_id", "type", "risk_level", "location", "subject", "abs_start", "abs_end", "local_time",
            "duration_s")
    return [{k: h[k] for k in keys} for h in hits]


def ask_bedrock(question: str, store: EventStore, camera_ids: list[str], now_utc: datetime, model_id: str,
                utc_offset_minutes: int = 0, client=None) -> dict:
    import boto3

    client = client or boto3.client("bedrock-runtime")
    tool = {"toolSpec": {"name": "search_events",
                         "description": "Search structured camera events for the user's cameras.",
                         "inputSchema": {"json": SEARCH_TOOL_SCHEMA}}}
    sys_text = (QA_SYSTEM_PROMPT + f"\nCurrent UTC time: {now_utc.isoformat()}. "
                f"User local time is UTC{utc_offset_minutes / 60:+.1f}h; local_time fields are already local.")
    messages: list[dict] = [{"role": "user", "content": [{"text": question[:500]}]}]
    used: list[dict] = []
    truncated = False
    usage = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "tool_calls": 0}
    lang = _lang(question)

    def done(text: str) -> dict:
        answer, _ = clean_text(text, 1000)  # same hygiene as model text in results
        answer = answer or _NONE[lang]
        if truncated:
            answer += TRUNCATED_NOTE[lang]
        return {"answer": answer, "events": used, "usage": usage, "mode": "bedrock", "truncated": truncated}

    for _ in range(MAX_TOOL_ROUNDS + 1):
        resp = client.converse(modelId=model_id, system=[{"text": sys_text}], messages=messages,
                               toolConfig={"tools": [tool]}, inferenceConfig={"maxTokens": 800, "temperature": 0})
        usage["model_calls"] += 1
        usage["input_tokens"] += resp.get("usage", {}).get("inputTokens", 0)
        usage["output_tokens"] += resp.get("usage", {}).get("outputTokens", 0)
        msg = resp["output"]["message"]
        messages.append(msg)
        tool_uses = [c["toolUse"] for c in msg["content"] if "toolUse" in c]
        if resp.get("stopReason") != "tool_use" or not tool_uses or usage["tool_calls"] >= MAX_TOOL_ROUNDS:
            return done("".join(c.get("text", "") for c in msg["content"]).strip())
        results = []
        for tu in tool_uses:
            usage["tool_calls"] += 1
            try:
                hits = search(store, camera_ids, filter_from_tool_input(tu.get("input") or {}))
                used.extend(hits)
                truncated = truncated or hits.truncated
                payload = {"json": {"events": _tool_hits_view(hits), "truncated": hits.truncated}}
                results.append({"toolResult": {"toolUseId": tu["toolUseId"], "content": [payload]}})
            except ValueError as exc:
                results.append({"toolResult": {"toolUseId": tu["toolUseId"], "status": "error",
                                               "content": [{"text": str(exc)}]}})
        messages.append({"role": "user", "content": results})
    return done("")


def ask(question: str, store: EventStore, camera_ids: list[str], now_utc: datetime, mode: str = "mock",
        model_id: str = "", utc_offset_minutes: int = 0, client=None) -> dict:
    if mode == "bedrock":
        return ask_bedrock(question, store, camera_ids, now_utc, model_id, utc_offset_minutes, client)
    flt = parse_query_rules(question, now_utc, utc_offset_minutes)
    hits = search(store, camera_ids, flt)
    answer = template_answer(question, hits) + (TRUNCATED_NOTE[_lang(question)] if hits.truncated else "")
    return {"answer": answer, "events": list(hits), "filter": flt.describe(), "mode": "mock",
            "truncated": hits.truncated}


# ---------------------------------------------------------------- daily summary

def daily_summary(store: EventStore, camera_ids: list[str], local_date: str, utc_offset_minutes: int = 0,
                  locales: list[str] | None = None, top_n: int = 5) -> dict:
    off = timedelta(minutes=utc_offset_minutes)
    d = datetime.fromisoformat(local_date).replace(tzinfo=UTC)
    start, end = d - off, d - off + timedelta(days=1) - timedelta(microseconds=1)
    hits = search(store, camera_ids, EventFilter(start=start, end=end), limit=10_000)
    counts = {lvl: 0 for lvl in RISK_LEVELS}
    by_type: dict[str, int] = {}
    for h in hits:
        counts[h["risk_level"]] += 1
        by_type[h["type"]] = by_type.get(h["type"], 0) + 1
    ranked = sorted(hits, key=lambda h: (-risk_rank(h["risk_level"]), h["abs_start"]))
    key_events = ranked[:top_n]
    # Abnormal periods: local hours that contain events at "attention" or above.
    hours = sorted({(_ts(h["abs_start"]) + off).hour for h in hits if risk_rank(h["risk_level"]) >= 1})
    periods, run = [], []
    for hr in hours:
        if run and hr == run[-1] + 1:
            run.append(hr)
        else:
            if run:
                periods.append(run)
            run = [hr]
    if run:
        periods.append(run)
    abnormal = [f"{p[0]:02d}:00-{(p[-1] + 1) % 24:02d}:00" for p in periods]
    recommended = []
    for h in key_events:
        if h["video_id"] not in [r["video_id"] for r in recommended] and risk_rank(h["risk_level"]) >= 1:
            recommended.append({"video_id": h["video_id"], "source_key": h["source_key"],
                                "local_time": h["local_time"], "type": h["type"]})
    text = {}
    for lc in locales or ["zh-CN", "en-US", "ru-RU"]:
        if lc not in i18n.SUPPORTED_LOCALES:
            continue
        lv = i18n.LEVEL_NAMES[lc]
        overview = ", ".join(f"{lv[k]} {counts[k]}" for k in reversed(RISK_LEVELS))
        heads = [f"{h['local_time']} {i18n.EVENT_NAMES[lc][h['type']]} ({lv[h['risk_level']]})" for h in key_events]
        label = {"zh-CN": ("当日事件", "风险概览", "异常时段", "重点"),
                 "en-US": ("Events today", "Risk overview", "Abnormal periods", "Key events"),
                 "ru-RU": ("События за день", "Обзор рисков", "Аномальные периоды", "Главное")}[lc]
        text[lc] = (f"{label[0]}: {len(hits)}. {label[1]}: {overview}. "
                    f"{label[2]}: {', '.join(abnormal) or '-'}. {label[3]}: {'; '.join(heads) or '-'}."
                    + (TRUNCATED_NOTE[lc] if hits.truncated else ""))
    return {
        "date": local_date, "camera_ids": camera_ids, "total_events": len(hits), "risk_overview": counts,
        "events_by_type": by_type, "key_events": key_events, "abnormal_periods": abnormal,
        "recommended_clips": recommended, "text": text, "truncated": hits.truncated,
    }


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
