from datetime import UTC, datetime

import pytest

from video_events import query
from video_events.config import PipelineConfig
from video_events.pipeline import postprocess
from video_events.store import LocalStore, result_key

NOW = datetime(2026, 10, 19, 21, 0, tzinfo=UTC)


def make_result(cam, ts, name, scene, events):
    vid = f"{cam}-{ts}-{name}"
    rec = datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    r = {"video": {"video_id": vid, "camera_id": cam, "recorded_at": rec, "duration_s": 120,
                   "source_key": f"uploads/{cam}/{ts}_{name}.mp4"},
         "scene": {"type": scene, "description": ""}, "events": events, "status": "ok"}
    return postprocess(r, PipelineConfig())


def e(t, cat, loc, subj, a, b, level="low", desc="x"):
    return {"type": t, "category": cat, "location": loc, "subject": subj, "start_s": a, "end_s": b,
            "risk_level": level, "confidence": "medium", "description": desc}


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path)
    s.put(make_result("cam-front", "20261019T081200Z", "a", "home",
                      [e("package_delivery", "behavior", "entrance", "courier_like_person", 6, 21)]))
    s.put(make_result("cam-front", "20261019T093000Z", "b", "home",
                      [e("person_lingering", "behavior", "entrance", "person", 5, 95),
                       e("suspicious_loitering", "risk", "entrance", "person", 5, 95, "attention")]))
    s.put(make_result("cam-yard", "20261019T110500Z", "c", "yard",
                      [e("fire_smoke", "risk", "yard", "fire_smoke", 12, 30, "emergency")]))
    s.put(make_result("cam-yard", "20261018T231500Z", "d", "yard",
                      [e("intrusion", "risk", "yard", "person", 13, 16, "high")]))
    s.put(make_result("cam-living", "20261019T130000Z", "e", "pet",
                      [e("pet_activity", "behavior", "indoor", "pet", 0, 30)]))
    return s


CAMS = ["cam-front", "cam-yard", "cam-living"]


def test_result_key_layout():
    r = make_result("cam-front", "20261019T081200Z", "a", "home", [])
    assert result_key(r) == "results/cam-front/2026-10-19/cam-front-20261019T081200Z-a.json"


def test_unsafe_camera_rejected(store):
    with pytest.raises(ValueError):
        store.list_results(["../etc"], NOW, NOW)


def test_who_was_at_door_today(store):
    out = query.ask("今天谁到过门口？", store, CAMS, NOW)
    types = {h["type"] for h in out["events"]}
    assert types == {"package_delivery", "person_lingering", "suspicious_loitering"}
    assert "快递员模样的人" in out["answer"]
    assert "intrusion" not in types  # yesterday


def test_last_night_yard(store):
    out = query.ask("昨晚是否有人靠近院门？", store, CAMS, NOW)
    assert [h["type"] for h in out["events"]] == ["intrusion"]


def test_when_was_fire(store):
    out = query.ask("火情发生在几点？", store, CAMS, NOW)
    assert [h["type"] for h in out["events"]] == ["fire_smoke"]
    assert "11:05:12" in out["answer"]


def test_semantic_search_duration(store):
    flt = query.parse_query_rules("找出所有有人在门口停留超过1分钟的视频", NOW)
    hits = query.search(store, CAMS, flt)
    assert flt.min_duration_s == 60
    assert {h["type"] for h in hits} == {"person_lingering", "suspicious_loitering"}


def test_pet_search_english(store):
    flt = query.parse_query_rules("find clips where a pet appears", NOW)
    hits = query.search(store, CAMS, flt)
    assert [h["type"] for h in hits] == ["pet_activity"]


def test_camera_scope_enforced(store):
    out = query.ask("火情发生在几点？", store, ["cam-front"], NOW)
    assert out["events"] == []
    assert "没有找到" in out["answer"]


def test_answer_language_follows_question(store):
    assert query.ask("When was the fire?", store, CAMS, NOW)["answer"].startswith("Found")
    assert query.ask("Когда был пожар?", store, CAMS, NOW)["answer"].startswith("Найдено")


def test_daily_summary(store):
    d = query.daily_summary(store, CAMS, "2026-10-19")
    assert d["total_events"] == 5
    assert d["risk_overview"]["emergency"] == 1 and d["risk_overview"]["attention"] == 1
    assert d["key_events"][0]["type"] == "fire_smoke"
    assert "09:00-10:00" in d["abnormal_periods"] and "11:00-12:00" in d["abnormal_periods"]
    assert d["recommended_clips"][0]["video_id"].startswith("cam-yard")
    assert set(d["text"]) == {"zh-CN", "en-US", "ru-RU"}


def test_tool_input_validation():
    ok = query.filter_from_tool_input({"start": "2026-10-19T00:00:00Z", "end": "2026-10-19T23:59:59Z",
                                       "event_types": ["fire_smoke"]})
    assert ok.event_types == ["fire_smoke"]
    for bad in [{"start": "2026-10-19T00:00:00Z"},
                {"start": "2026-10-19T00:00:00Z", "end": "2026-10-19T01:00:00Z", "event_types": ["face"]},
                {"start": "2026-10-19T00:00:00Z", "end": "2026-10-19T01:00:00Z", "camera_ids": ["other"]},
                {"start": "2026-01-01T00:00:00Z", "end": "2026-10-19T01:00:00Z"}]:
        with pytest.raises(ValueError):
            query.filter_from_tool_input(bad)


class FakeBedrock:
    """Scripted Converse responses: first a tool call, then a final answer."""

    def __init__(self, tool_input):
        self.calls = []
        self.tool_input = tool_input

    def converse(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            return {"stopReason": "tool_use", "usage": {"inputTokens": 10, "outputTokens": 5},
                    "output": {"message": {"role": "assistant", "content": [
                        {"toolUse": {"toolUseId": "t1", "name": "search_events", "input": self.tool_input}}]}}}
        return {"stopReason": "end_turn", "usage": {"inputTokens": 20, "outputTokens": 8},
                "output": {"message": {"role": "assistant", "content": [{"text": "At 11:05 there was fire."}]}}}


def test_ask_bedrock_tool_loop(store):
    fake = FakeBedrock({"start": "2026-10-19T00:00:00Z", "end": "2026-10-19T23:59:59Z",
                        "event_types": ["fire_smoke"]})
    out = query.ask("When was the fire?", store, ["cam-yard"], NOW, mode="bedrock", model_id="m", client=fake)
    assert out["answer"].startswith("At 11:05")
    assert [h["type"] for h in out["events"]] == ["fire_smoke"]
    assert out["usage"]["model_calls"] == 2 and out["usage"]["tool_calls"] == 1
    tool_result = fake.calls[1]["messages"][-2]["content"][0]["toolResult"]["content"][0]["json"]
    assert "description" not in tool_result["events"][0]  # free text never goes back to the model


def test_ask_bedrock_invalid_tool_input_returns_error(store):
    fake = FakeBedrock({"start": "2026-10-19T00:00:00Z", "end": "2026-10-19T23:59:59Z",
                        "event_types": ["face_recognition"]})
    out = query.ask("Who?", store, ["cam-yard"], NOW, mode="bedrock", model_id="m", client=fake)
    assert out["events"] == []
    assert fake.calls[1]["messages"][-2]["content"][0]["toolResult"]["status"] == "error"


class _CappedStore:
    """Wraps a LocalStore and reports a read cap, like S3Store with max_objects reached."""

    def __init__(self, inner):
        self.inner = inner

    def list_results(self, cams, start, end):
        from video_events.store import ResultList

        return ResultList(self.inner.list_results(cams, start, end), truncated=True)


def test_truncation_is_reported(store):
    capped = _CappedStore(store)
    out = query.ask("今天谁到过门口？", capped, ["cam-front"], NOW)
    assert out["truncated"] is True and "结果可能不完整" in out["answer"]
    summary = query.daily_summary(capped, ["cam-front"], "2026-10-19")
    assert summary["truncated"] is True and "incomplete" in summary["text"]["en-US"]
    assert query.ask("今天谁到过门口？", store, ["cam-front"], NOW)["truncated"] is False
    fake = FakeBedrock({"start": "2026-10-19T00:00:00Z", "end": "2026-10-19T23:59:59Z"})
    out = query.ask("What happened?", capped, ["cam-yard"], NOW, mode="bedrock", model_id="m", client=fake)
    assert out["truncated"] is True and out["answer"].endswith("narrow the time range or cameras.)")
    assert fake.calls[1]["messages"][-2]["content"][0]["toolResult"]["content"][0]["json"]["truncated"] is True


def test_search_limit_sets_truncated(store):
    flt = query.EventFilter(start=datetime(2026, 10, 18, tzinfo=UTC), end=datetime(2026, 10, 20, tzinfo=UTC))
    assert query.search(store, ["cam-front", "cam-yard"], flt, limit=2).truncated is True
    assert query.search(store, ["cam-front", "cam-yard"], flt, limit=50).truncated is False


def test_s3_store_cap_sets_truncated():
    import io
    import json as _json

    from video_events.store import S3Store

    class FakeS3:
        def get_paginator(self, name):
            class P:
                def paginate(self, Bucket, Prefix):
                    yield {"Contents": [{"Key": f"{Prefix}{i}.json"} for i in range(5)]}
            return P()

        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(_json.dumps({"k": Key}).encode())}

    out = S3Store("b", client=FakeS3(), max_objects=3).list_results(["cam"], NOW, NOW)
    assert len(out) == 3 and out.truncated is True
    out = S3Store("b", client=FakeS3(), max_objects=10).list_results(["cam"], NOW, NOW)
    assert len(out) == 5 and out.truncated is False


def test_tool_times_with_offsets_are_converted_not_relabelled():
    flt = query.filter_from_tool_input({"start": "2026-10-19T08:00:00+08:00", "end": "2026-10-19T03:00:00"})
    assert flt.start == datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
    assert flt.end == datetime(2026, 10, 19, 3, 0, tzinfo=UTC)


def test_bedrock_final_answer_is_cleaned(store):
    class Naming(FakeBedrock):
        def converse(self, **kw):
            resp = super().converse(**kw)
            if len(self.calls) > 1:
                resp["output"]["message"]["content"] = [{"text": "The person at the door is named John.\x07"}]
            return resp

    fake = Naming({"start": "2026-10-19T00:00:00Z", "end": "2026-10-19T23:59:59Z"})
    out = query.ask("Who came?", store, ["cam-front"], NOW, mode="bedrock", model_id="m", client=fake)
    assert "John" not in out["answer"] and out["answer"] == "[description removed: identity-like content]"
