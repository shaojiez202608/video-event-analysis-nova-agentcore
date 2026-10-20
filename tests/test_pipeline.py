from datetime import UTC, datetime

import pytest
from conftest import clip_bytes

from video_events.config import PipelineConfig
from video_events.evaluation import run_suite, score_clip
from video_events.frames import probe, sample_frames, sample_times
from video_events.models import MockVisionModel
from video_events.pipeline import VideoMeta, analyze_video, plan_segments
from video_events.schema import RESULT_SCHEMA


def cfg(**kw):
    return PipelineConfig(model_mode="mock", **kw).validate()


def test_key_parsing():
    m = VideoMeta.from_key("uploads/cam-front/20261019T081200Z_s03_delivery.mp4")
    assert m.camera_id == "cam-front"
    assert m.recorded_at == datetime(2026, 10, 19, 8, 12, tzinfo=UTC)
    assert m.video_id == "cam-front-20261019T081200Z-s03_delivery"


@pytest.mark.parametrize("key", ["uploads/../etc/passwd", "other/cam/20261019T081200Z_x.mp4",
                                 "uploads/cam/2026-10-19_x.mp4", "uploads/cam/20261019T081200Z_x.exe"])
def test_key_parsing_rejects_bad_keys(key):
    with pytest.raises(ValueError):
        VideoMeta.from_key(key)


def test_sample_times():
    assert sample_times(10, 2) == [1.0, 3.0, 5.0, 7.0, 9.0]
    assert sample_times(1, 4) == [1.0]  # short clips still get one frame
    assert sample_times(0, 1) == []


def test_plan_segments_respects_image_cap():
    class F:
        def __init__(self, t):
            self.t = t

    frames = [F(i + 0.5) for i in range(30)]
    segs = plan_segments(frames, 30, 10, max_images=4)
    assert all(len(s.frames) <= 4 for s in segs)
    assert sum(len(s.frames) for s in segs) == 30
    assert len(plan_segments(frames, 30, 0, max_images=5)) == 6  # whole clip, split by cap


def test_probe_and_sample(synthetic_set):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    info = probe(data)
    assert abs(info.duration_s - clip["duration_s"]) < 0.5
    frames = sample_frames(data, 2.0, width=320)
    assert len(frames) == 15
    assert frames[0].jpeg[:2] == b"\xff\xd8"  # JPEG SOI marker
    assert len(frames[0].thumb_rgb) == 80 * 45 * 3


@pytest.mark.parametrize("strategy,interval,segment", [("video", 1, 10), ("frames", 1.0, 10.0), ("frames", 2.0, 0.0)])
def test_end_to_end_mock_detects_fire(synthetic_set, strategy, interval, segment):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    c = cfg(strategy=strategy, frame_interval_s=interval, segment_length_s=segment)
    res = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), c, MockVisionModel())
    assert res["status"] == "ok"
    assert res["scene"]["type"] == "yard"
    fire = [e for e in res["events"] if e["type"] == "fire_smoke"]
    assert len(fire) == 1 and fire[0]["risk_level"] == "emergency"
    assert abs(fire[0]["start_s"] - 12) <= 2.5
    assert res["overall_risk_level"] == "emergency"
    assert res["metrics"]["model_calls"] >= 1
    assert any(n["rules"] for n in res["notifications"])
    for key in RESULT_SCHEMA["required"]:
        assert key in res
    assert set(res["localized"]) == {"zh-CN", "en-US", "ru-RU"}


def test_no_event_clip_is_clean(synthetic_set):
    data, clip = clip_bytes(synthetic_set, "s01_empty_home")
    res = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), cfg(), MockVisionModel())
    assert res["events"] == [] and res["overall_risk_level"] == "none"
    assert res["scene"]["type"] == "home"


def test_sparse_sampling_misses_short_event(synthetic_set):
    data, clip = clip_bytes(synthetic_set, "s12_short_fire")
    dense = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), cfg(strategy="frames", frame_interval_s=1.0),
                          MockVisionModel())
    sparse = analyze_video(data, VideoMeta.from_key(clip["s3_key"]),
                           cfg(strategy="frames", frame_interval_s=4.0, segment_length_s=0), MockVisionModel())
    assert score_clip(clip, dense).tp == 1
    assert score_clip(clip, sparse).tp == 0
    assert sparse["metrics"]["model_calls"] < dense["metrics"]["model_calls"]


def test_retry_then_success(synthetic_set):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    model = MockVisionModel(faults=["not_json", None])
    res = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), cfg(max_retries=1), model)
    assert res["status"] == "ok"
    assert res["metrics"]["retries"] == 1 and res["metrics"]["validation_failures"] == 1
    assert res["metrics"]["model_calls"] == 2


def test_fallback_after_retries_exhausted(synthetic_set):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    model = MockVisionModel(faults=["not_json", "bad_schema"])
    res = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), cfg(max_retries=1), model)
    assert res["status"] == "fallback" and res["needs_review"] is True
    assert res["events"] == [] and res["fallback_reason"]
    assert res["overall_risk_level"] == "none"


def test_bad_events_dropped_status_partial(synthetic_set):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    res = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), cfg(), MockVisionModel(faults=["bad_event"]))
    assert res["status"] == "partial"
    reasons = sorted(r["reason"] for r in res["rejected"])
    assert reasons == ["start_after_end", "time_out_of_clip", "type_not_in_whitelist"]
    assert [e["type"] for e in res["events"]] == ["fire_smoke"]


def test_partial_segment_failure(synthetic_set):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    c = cfg(strategy="frames", frame_interval_s=2.0, segment_length_s=10.0, max_retries=0)
    res = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), c, MockVisionModel(faults=["not_json"]))
    assert res["status"] == "partial" and res["metrics"]["segments"] == 3


def test_logs_do_not_contain_content(synthetic_set, caplog):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    with caplog.at_level("INFO", logger="video_events"):
        res = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), cfg(), MockVisionModel())
    text = caplog.text
    assert "video_analyzed" in text
    assert res["summary"] not in text
    for e in res["events"]:
        assert e["description"] not in text


def test_run_suite_mock(synthetic_set):
    summary, per_clip = run_suite(str(synthetic_set / "manifest.json"), cfg(strategy="video"), MockVisionModel())
    assert summary["clips"] == 7
    assert summary["f1"] >= 0.8, per_clip
    assert summary["scene_accuracy"] == 1.0
