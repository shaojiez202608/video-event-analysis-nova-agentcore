"""Custom watch rules (post-processing, no model call).

A rule is structured configuration, not free text, e.g.::

    {"name": "entrance_people_only", "locations": ["entrance"],
     "subjects": ["person", "courier_like_person", "multiple_people"]}
    {"name": "night_yard_person", "locations": ["yard"], "subjects": ["person"],
     "local_time": {"start": "22:00", "end": "06:00"}}

All listed conditions must hold (AND); list values are alternatives (OR).
An event that matches at least one rule produces a notification. With no rules
configured, the default is: notify on risk events at level "attention" or above.

Safety floor: risk events at level "high" or "emergency" always produce a
notification (rule name ``always_high_risk``), even when the configured rules
would not match them (for example a yard fire with only an "entrance people"
rule). Custom rules can add notifications but cannot switch these off.

A ``local_time`` window matches when any part of the event overlaps it
(start and end local times, minute resolution), not only the start time.

Rules are one global list in the pipeline configuration (not per user) and are
structured data; natural-language rules are not supported.
Turning natural-language requests ("only tell me about the door") into these
rules is listed in the handover backlog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Any

from .taxonomy import ALL_EVENT_TYPES, LOCATIONS, RISK_LEVELS, SUBJECTS, risk_rank

DEFAULT_RULES = [{"name": "default_risk_alert", "categories": ["risk"], "min_risk_level": "attention"}]
SAFETY_RULE = {"name": "always_high_risk", "categories": ["risk"], "min_risk_level": "high"}

_ALLOWED_KEYS = {"name", "event_types", "categories", "locations", "subjects", "min_risk_level", "local_time"}


def _parse_hhmm(value: str) -> time:
    try:
        hh, mm = str(value).split(":")
        return time(int(hh), int(mm))
    except (TypeError, ValueError) as exc:
        raise ValueError("local_time values must be HH:MM") from exc


@dataclass
class WatchRule:
    name: str
    event_types: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    min_risk_level: str | None = None
    window: tuple[time, time] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WatchRule:
        if not isinstance(d, dict) or not isinstance(d.get("name"), str) or not d["name"]:
            raise ValueError("each watch rule must be an object with a non-empty name")
        extra = set(d) - _ALLOWED_KEYS
        if extra:
            raise ValueError(f"unknown rule keys: {sorted(extra)}")
        for key, allowed in (("event_types", ALL_EVENT_TYPES), ("locations", LOCATIONS), ("subjects", SUBJECTS),
                             ("categories", ("risk", "behavior"))):
            if not isinstance(d.get(key, []), list):
                raise ValueError(f"rule {d['name']}: {key} must be a list")
            bad = set(d.get(key, [])) - set(allowed)
            if bad:
                raise ValueError(f"rule {d.get('name')}: invalid {key}: {sorted(bad)}")
        if d.get("min_risk_level") and d["min_risk_level"] not in RISK_LEVELS:
            raise ValueError("invalid min_risk_level")
        window = None
        if d.get("local_time"):
            lt = d["local_time"]
            if not isinstance(lt, dict) or set(lt) != {"start", "end"}:
                raise ValueError(f"rule {d['name']}: local_time must be {{'start': 'HH:MM', 'end': 'HH:MM'}}")
            window = (_parse_hhmm(lt["start"]), _parse_hhmm(lt["end"]))
        return cls(
            name=str(d["name"]),
            event_types=list(d.get("event_types", [])),
            categories=list(d.get("categories", [])),
            locations=list(d.get("locations", [])),
            subjects=list(d.get("subjects", [])),
            min_risk_level=d.get("min_risk_level"),
            window=window,
        )

    def matches(self, ev: dict[str, Any], local_clock: time | None, duration_s: float = 0.0) -> bool:
        if self.event_types and ev["type"] not in self.event_types:
            return False
        if self.categories and ev["category"] not in self.categories:
            return False
        if self.locations and ev.get("location") not in self.locations:
            return False
        if self.subjects and ev.get("subject") not in self.subjects:
            return False
        if self.min_risk_level and risk_rank(ev["risk_level"]) < risk_rank(self.min_risk_level):
            return False
        if self.window:
            if local_clock is None:
                return False
            if not _overlaps(self.window, local_clock, duration_s):
                return False
        return True


def _sod(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def _inside(window: tuple[time, time], sec: int) -> bool:
    start, end = _sod(window[0]), _sod(window[1])
    sec %= 86400
    return start <= sec < end if start <= end else (sec >= start or sec < end)


def _overlaps(window: tuple[time, time], clock: time, duration_s: float) -> bool:
    """True if [clock, clock + duration] touches the window. Window edges are whole minutes, so
    checking the start, every minute boundary inside the event and the end is exact."""
    start = _sod(clock)
    end = start + max(0, int(duration_s))
    points = [start, end] + list(range((start // 60 + 1) * 60, end, 60))
    return any(_inside(window, p) for p in points)


def load_rules(raw: list[dict[str, Any]] | None) -> list[WatchRule]:
    return [WatchRule.from_dict(r) for r in (raw or DEFAULT_RULES)]


_SAFETY = WatchRule.from_dict(SAFETY_RULE)


def _clock(value: str) -> time:
    parts = [int(p) for p in value.split(":")]
    return time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)


def evaluate(events: list[dict[str, Any]], rules: list[WatchRule]) -> list[dict[str, Any]]:
    out = []
    for ev in events:
        clock = _clock(ev["local_time"]) if ev.get("local_time") else None
        duration = max(0.0, float(ev.get("end_s", 0.0)) - float(ev.get("start_s", 0.0)))
        matched = [r.name for r in rules if r.matches(ev, clock, duration)]
        if not matched and _SAFETY.matches(ev, clock, duration):
            matched = [_SAFETY.name]
        if matched:
            out.append({"event_id": ev["event_id"], "type": ev["type"], "risk_level": ev["risk_level"],
                        "rules": matched})
    return out
