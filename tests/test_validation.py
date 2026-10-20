import json

import pytest

from video_events.validation import (
    REDACTED,
    ResponseInvalid,
    merge_events,
    parse_json_text,
    validate_model_output,
)


def ev(**kw):
    base = {"category": "risk", "type": "fire_smoke", "start_s": 1.0, "end_s": 5.0, "risk_level": "high",
            "confidence": "high", "location": "yard", "subject": "fire_smoke", "description": "flames"}
    base.update(kw)
    return base


def doc(events=None, **kw):
    d = {"scene_type": "yard", "scene_description": "a yard", "events": events or [], "summary": "ok"}
    d.update(kw)
    return d


def test_parse_plain_and_fenced_json():
    assert parse_json_text('{"a": 1}') == {"a": 1}
    assert parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_text('Here you go: {"a": 1} thanks') == {"a": 1}


@pytest.mark.parametrize("text", ["no json here", "{not valid", ""])
def test_parse_rejects_non_json(text):
    with pytest.raises(ResponseInvalid):
        parse_json_text(text)


def test_top_level_schema_failure_raises():
    with pytest.raises(ResponseInvalid):
        validate_model_output({"events": "none"}, 30)
    with pytest.raises(ResponseInvalid):
        validate_model_output(doc(extra_field=1), 30)
    with pytest.raises(ResponseInvalid):
        validate_model_output(doc(scene_type="spaceship"), 30)
    with pytest.raises(ResponseInvalid):
        validate_model_output([1, 2], 30)


def test_valid_event_passes():
    out = validate_model_output(doc([ev()]), 30)
    assert len(out.events) == 1 and not out.rejected


def test_unknown_type_rejected_but_rest_kept():
    out = validate_model_output(doc([ev(), ev(type="face_match")]), 30)
    assert len(out.events) == 1
    assert out.rejected[0]["reason"] == "type_not_in_whitelist"
    assert out.rejected[0]["type"] == "<invalid>"


@pytest.mark.parametrize(
    "bad,reason",
    [
        (ev(start_s=10, end_s=2), "start_after_end"),
        (ev(start_s=-5, end_s=2), "time_out_of_clip"),
        (ev(start_s=1, end_s=99), "time_out_of_clip"),
        (ev(risk_level="catastrophic"), "schema:risk_level"),
        (ev(start_s="soon"), "schema:start_s"),
        (ev(name="John"), "schema:additionalProperties"),
        ("not an object", "event_not_object"),
    ],
)
def test_bad_events_rejected_with_reason(bad, reason):
    out = validate_model_output(doc([bad]), 30)
    assert out.events == []
    assert out.rejected[0]["reason"] == reason


def test_small_overrun_is_clamped():
    out = validate_model_output(doc([ev(start_s=-0.5, end_s=30.6)]), 30, tolerance_s=1.0)
    assert out.events[0]["start_s"] == 0.0 and out.events[0]["end_s"] == 30.0


def test_non_finite_time_rejected():
    text = '{"scene_type": "yard", "scene_description": "", "events": [' + json.dumps(ev()).replace(
        '"start_s": 1.0', '"start_s": NaN') + '], "summary": ""}'
    out = validate_model_output(parse_json_text(text), 30)
    assert out.rejected and not out.events


def test_category_is_corrected_from_type():
    out = validate_model_output(doc([ev(category="behavior")]), 30)
    assert out.events[0]["category"] == "risk"
    assert "category_corrected:fire_smoke" in out.warnings


def test_identity_like_text_redacted():
    out = validate_model_output(doc([ev(description="The man named Bob Smith walks in")],
                                    summary="这个人名叫张三"), 30)
    assert out.events[0]["description"] == REDACTED
    assert out.summary == REDACTED
    assert "identity_text_redacted" in out.warnings


def test_control_characters_removed():
    out = validate_model_output(doc([ev(description="line\x00one\x1b[31m")]), 30)
    assert "\x00" not in out.events[0]["description"] and "\x1b" not in out.events[0]["description"]


def test_merge_same_type_across_segments():
    a = ev(start_s=0, end_s=10, risk_level="high", confidence="low")
    b = ev(start_s=11, end_s=20, risk_level="emergency", confidence="high")
    c = ev(type="pet_activity", category="behavior", start_s=5, end_s=6, risk_level="low")
    merged = merge_events([a, b, c], gap_s=2.0)
    fire = [e for e in merged if e["type"] == "fire_smoke"]
    assert len(fire) == 1 and fire[0]["end_s"] == 20 and fire[0]["risk_level"] == "emergency"
    assert fire[0]["confidence"] == "high"
    assert len(merged) == 2


def test_merge_keeps_separate_when_gap_large():
    merged = merge_events([ev(start_s=0, end_s=2), ev(start_s=10, end_s=12)], gap_s=2.0)
    assert len(merged) == 2


@pytest.mark.parametrize("bad", [doc(scene_description="SECRET-TEXT " * 40), doc(scene_type="SECRET-TEXT"),
                                 doc(extra_SECRET_TEXT=1), doc(summary="SECRET-TEXT " * 80)])
def test_schema_error_message_has_no_instance_values(bad):
    with pytest.raises(ResponseInvalid) as info:
        validate_model_output(bad, 30)
    msg = str(info.value)
    assert "SECRET" not in msg
    assert msg.startswith("schema: ")
