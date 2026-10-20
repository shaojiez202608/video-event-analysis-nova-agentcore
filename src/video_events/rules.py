"""Custom watch rules (post-processing, no model call).

A rule is structured configuration, not free text, e.g.::

    {"name": "entrance_people_only", "locations": ["entrance"],
     "subjects": ["person", "courier_like_person", "multiple_people"]}
    {"name": "night_yard_person", "locations": ["yard"], "subjects": ["person"],
     "local_time": {"start": "22:00", "end": "06:00"}}

All listed conditions must hold (AND); list values are alternatives (OR).
An event that matches at least one rule produces a notification. With no rules
configured, the default is: notify on risk events at level "attention" or above.
Turning natural-language requests ("only tell me about the door") into these
rules is listed in the handover backlog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Any

from .taxonomy import ALL_EVENT_TYPES, LOCATIONS, RISK_LEVELS, SUBJECTS, risk_rank

DEFAULT_RULES = [{"name": "default_risk_alert", "categories": ["risk"], "min_risk_level": "attention"}]

_ALLOWED_KEYS = {"name", "event_types", "categories", "locations", "subjects", "min_risk_level", "local_time"}


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


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
        extra = set(d) - _ALLOWED_KEYS
        if extra:
            raise ValueError(f"unknown rule keys: {sorted(extra)}")
        for key, allowed in (("event_types", ALL_EVENT_TYPES), ("locations", LOCATIONS), ("subjects", SUBJECTS),
                             ("categories", ("risk", "behavior"))):
            bad = set(d.get(key, [])) - set(allowed)
            if bad:
                raise ValueError(f"rule {d.get('name')}: invalid {key}: {sorted(bad)}")
        if d.get("min_risk_level") and d["min_risk_level"] not in RISK_LEVELS:
            raise ValueError("invalid min_risk_level")
        window = None
        if d.get("local_time"):
            window = (_parse_hhmm(d["local_time"]["start"]), _parse_hhmm(d["local_time"]["end"]))
        return cls(
            name=str(d["name"]),
            event_types=list(d.get("event_types", [])),
            categories=list(d.get("categories", [])),
            locations=list(d.get("locations", [])),
            subjects=list(d.get("subjects", [])),
            min_risk_level=d.get("min_risk_level"),
            window=window,
        )

    def matches(self, ev: dict[str, Any], local_clock: time | None) -> bool:
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
            start, end = self.window
            inside = start <= local_clock < end if start <= end else (local_clock >= start or local_clock < end)
            if not inside:
                return False
        return True


def load_rules(raw: list[dict[str, Any]] | None) -> list[WatchRule]:
    return [WatchRule.from_dict(r) for r in (raw or DEFAULT_RULES)]


def evaluate(events: list[dict[str, Any]], rules: list[WatchRule]) -> list[dict[str, Any]]:
    out = []
    for ev in events:
        clock = None
        if ev.get("local_time"):
            clock = _parse_hhmm(ev["local_time"][:5])
        matched = [r.name for r in rules if r.matches(ev, clock)]
        if matched:
            out.append({"event_id": ev["event_id"], "type": ev["type"], "risk_level": ev["risk_level"],
                        "rules": matched})
    return out
