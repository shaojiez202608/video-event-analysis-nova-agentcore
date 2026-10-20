import pytest

from video_events import i18n
from video_events.config import PipelineConfig
from video_events.pipeline import postprocess
from video_events.rules import evaluate, load_rules


def result_with(events, recorded="2026-10-19T23:30:00Z"):
    return {"video": {"video_id": "cam-cam1-x", "recorded_at": recorded}, "scene": {"type": "yard"},
            "events": events}


def ev(t="intrusion", cat="risk", level="low", loc="yard", subj="person", start=0.0, end=4.0):
    return {"type": t, "category": cat, "risk_level": level, "location": loc, "subject": subj,
            "start_s": start, "end_s": end, "confidence": "medium", "description": ""}


def test_risk_floor_escalates_not_lowers():
    cfg = PipelineConfig()
    r = postprocess(result_with([ev(level="low"), ev(t="pet_activity", cat="behavior", level="high")]), cfg)
    intr = r["events"][0]
    assert intr["risk_level"] == "high" and intr["model_risk_level"] == "low"
    pet = r["events"][1]
    assert pet["risk_level"] == "high"  # model level kept when above the floor
    assert r["overall_risk_level"] == "high"


def test_risk_floor_overridable():
    cfg = PipelineConfig.from_dict({"risk_floors": {"intrusion": "emergency"}})
    r = postprocess(result_with([ev()]), cfg)
    assert r["events"][0]["risk_level"] == "emergency"


def test_bad_floor_rejected():
    with pytest.raises(ValueError):
        PipelineConfig.from_dict({"risk_floors": {"intrusion": "catastrophic"}})


def test_unknown_config_key_rejected():
    with pytest.raises(ValueError):
        PipelineConfig.from_dict({"strategy": "video", "bogus": 1})


def test_absolute_and_local_times():
    cfg = PipelineConfig(utc_offset_minutes=480)
    r = postprocess(result_with([ev(start=90, end=100)]), cfg)
    e = r["events"][0]
    assert e["abs_start"] == "2026-10-19T23:31:30Z"
    assert e["local_time"] == "07:31:30"
    assert e["event_id"] == "cam-cam1-x#0"


def test_localization_three_locales():
    cfg = PipelineConfig()
    r = postprocess(result_with([ev(t="fire_smoke", level="emergency", subj="fire_smoke")]), cfg)
    loc = r["localized"]
    assert "火情" in loc["zh-CN"]["headlines"][0] and "紧急" in loc["zh-CN"]["headlines"][0]
    assert "Fire or smoke" in loc["en-US"]["headlines"][0]
    assert "Огонь" in loc["ru-RU"]["headlines"][0]
    assert "看院子" in loc["zh-CN"]["overview"]


def test_all_types_have_translations():
    from video_events.taxonomy import ALL_EVENT_TYPES, LOCATIONS, RISK_LEVELS, SCENE_TYPES

    for lc in i18n.SUPPORTED_LOCALES:
        assert set(i18n.EVENT_NAMES[lc]) == set(ALL_EVENT_TYPES)
        assert set(RISK_LEVELS) <= set(i18n.LEVEL_NAMES[lc])
        assert set(i18n.SCENE_NAMES[lc]) == set(SCENE_TYPES)
        assert set(i18n.LOCATION_NAMES[lc]) == set(LOCATIONS)


def test_no_events_overview():
    r = postprocess(result_with([]), PipelineConfig())
    assert r["overall_risk_level"] == "none"
    assert "未发现" in r["localized"]["zh-CN"]["overview"]


def _with_ids(events, times):
    for i, (e, t) in enumerate(zip(events, times, strict=True)):
        e["event_id"] = f"e{i}"
        e["local_time"] = t
    return events


def test_default_rule_risk_only():
    events = _with_ids([ev(level="high"), ev(t="pet_activity", cat="behavior", subj="pet")], ["10:00:00"] * 2)
    out = evaluate(events, load_rules(None))
    assert [n["event_id"] for n in out] == ["e0"]


def test_entrance_people_only_rule():
    rules = load_rules([{"name": "door", "locations": ["entrance"], "subjects": ["person", "courier_like_person"]}])
    events = _with_ids([ev(loc="entrance"), ev(loc="yard"), ev(t="pet_activity", cat="behavior", loc="entrance",
                                                              subj="pet")], ["10:00:00"] * 3)
    assert [n["event_id"] for n in evaluate(events, rules)] == ["e0"]


def test_night_window_crosses_midnight():
    rules = load_rules([{"name": "night_yard", "locations": ["yard"], "subjects": ["person"],
                         "local_time": {"start": "22:00", "end": "06:00"}}])
    events = _with_ids([ev(), ev(), ev()], ["23:10:00", "03:00:00", "12:00:00"])
    assert [n["event_id"] for n in evaluate(events, rules)] == ["e0", "e1"]


@pytest.mark.parametrize("bad", [{"name": "x", "locations": ["moon"]}, {"name": "x", "free_text": "only door"},
                                 {"name": "x", "min_risk_level": "extreme"}])
def test_invalid_rules_rejected(bad):
    with pytest.raises(ValueError):
        load_rules([bad])


@pytest.mark.parametrize("bad", [
    {"watch_rules": [{"name": "x", "locations": ["moon"]}]},
    {"watch_rules": [{"locations": ["yard"]}]},
    {"watch_rules": [{"name": "x", "local_time": {"start": "25:00", "end": "06:00"}}]},
    {"watch_rules": [{"name": "x", "local_time": "22:00-06:00"}]},
    {"watch_rules": [{"name": "x", "subjects": "person"}]},
    {"watch_rules": {"name": "x"}},
    {"locales": ["de-DE"]},
    {"locales": []},
    {"locales": "zh-CN"},
])
def test_config_validate_checks_rules_and_locales(bad):
    from video_events.config import PipelineConfig

    with pytest.raises(ValueError):
        PipelineConfig.from_dict(bad)


def test_repo_param_files_validate():
    import json
    from pathlib import Path

    from video_events.config import PipelineConfig

    root = Path(__file__).resolve().parents[1] / "config"
    for name in ("params.json", "params.customer.example.json"):
        PipelineConfig.from_dict(json.loads((root / name).read_text())["pipeline"])
