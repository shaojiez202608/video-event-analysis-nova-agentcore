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
    assert sample_times(1, 4) == [0.5]  # short clips still get one frame, inside the clip
    assert sample_times(0, 1) == []


@pytest.mark.parametrize("duration,interval,expected", [
    (3.9, 2.0, [1.0, 2.95]),          # tail [2, 3.9) used to be skipped
    (7.9, 4.0, [2.0, 5.95]),
    (10.05, 2.0, [1.0, 3.0, 5.0, 7.0, 9.0]),  # 0.05 s container rounding: no extra sample
    (10.5, 2.0, [1.0, 3.0, 5.0, 7.0, 9.0, 10.25]),
    (0.3, 2.0, [0.15]),
])
def test_sample_times_cover_tail(duration, interval, expected):
    times = sample_times(duration, interval)
    assert times == expected
    assert all(0 < t < duration for t in times)
    # every point of the clip is within one interval of a sample
    assert duration - times[-1] <= interval


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


class _FlakyModel(MockVisionModel):
    """Raises a botocore ClientError on selected calls (1-based), like a throttled Converse call."""

    def __init__(self, fail_calls, code="ThrottlingException"):
        super().__init__()
        self.fail_calls, self.code = set(fail_calls), code

    def analyze(self, req):
        from botocore.exceptions import ClientError

        if self.calls + 1 in self.fail_calls:
            self.calls += 1
            raise ClientError({"Error": {"Code": self.code, "Message": "secret detail"}}, "Converse")
        return super().analyze(req)


def test_model_error_in_one_segment_gives_partial(synthetic_set, caplog):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    c = cfg(strategy="frames", frame_interval_s=2.0, segment_length_s=10.0, max_retries=1)
    with caplog.at_level("INFO", logger="video_events"):
        res = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), c, _FlakyModel({2}))
    assert res["status"] == "partial" and res["needs_review"] is True
    assert res["fallback_reason"] == "model_error:ThrottlingException"
    assert res["metrics"]["model_errors"] == 1 and res["metrics"]["segments"] == 3
    assert "model_call_failed" in caplog.text and "secret detail" not in caplog.text


def test_model_error_everywhere_gives_fallback(synthetic_set):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    res = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), cfg(strategy="video"),
                        _FlakyModel({1, 2, 3}, code="AccessDeniedException"))
    assert res["status"] == "fallback" and res["events"] == []
    assert res["fallback_reason"] == "model_error:AccessDeniedException"
    assert res["metrics"]["model_calls"] == 1  # no validation retry for a client error


@pytest.mark.parametrize("fault", ["long_scene", "bad_scene_type"])
def test_invalid_output_text_not_logged(synthetic_set, caplog, fault):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    model = MockVisionModel(faults=[fault, fault])
    with caplog.at_level("INFO", logger="video_events"):
        res = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), cfg(strategy="video", max_retries=1), model)
    assert res["status"] == "fallback" and res["needs_review"] is True
    assert caplog.text.count("model_output_invalid") == 2
    assert "ZEBRA" not in caplog.text
    assert "ZEBRA" not in res["fallback_reason"]
    assert res["fallback_reason"] in ("schema: scene_description: maxLength", "schema: scene_type: enum")


def test_sample_frames_reach_clip_tail(tmp_path):
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")
    path = tmp_path / "tail.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=3.9",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    frames = sample_frames(str(path), 2.0, width=64)
    assert [f.t for f in frames] == sample_times(probe(str(path)).duration_s, 2.0)
    assert len(frames) == 2 and frames[-1].t > 2.0


def test_planned_calls_above_cap_rejected_before_any_call(synthetic_set):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    model = MockVisionModel()
    c = cfg(strategy="frames", frame_interval_s=0.5, segment_length_s=5.0, max_model_calls_per_clip=10)
    with pytest.raises(ValueError, match="max_model_calls_per_clip"):
        analyze_video(data, VideoMeta.from_key(clip["s3_key"]), c, model)
    assert model.calls == 0


def test_call_budget_stops_retries(synthetic_set):
    data, clip = clip_bytes(synthetic_set, "s05_fire_yard")
    model = MockVisionModel(faults=["not_json"] * 10)
    c = cfg(strategy="frames", frame_interval_s=2.0, segment_length_s=10.0, max_retries=3, max_model_calls_per_clip=4)
    res = analyze_video(data, VideoMeta.from_key(clip["s3_key"]), c, model)
    assert model.calls == 4 and res["metrics"]["model_calls"] == 4
    assert res["status"] == "fallback"


@pytest.mark.parametrize("bad", [dict(frame_interval_s=0.1), dict(max_retries=10), dict(max_images_per_call=0),
                                 dict(max_model_calls_per_clip=0), dict(frame_width="wide"),
                                 dict(max_images_per_call=2.5), dict(persist_frames="yes")])
def test_config_bounds(bad):
    with pytest.raises(ValueError):
        PipelineConfig(**bad).validate()


def test_negative_control_is_reported_as_failing(synthetic_set):
    from video_events.evaluation import NEGATIVE_CONTROL_MAX_F1, WrongVisionModel

    good, _ = run_suite(str(synthetic_set / "manifest.json"), cfg(strategy="video"), MockVisionModel())
    bad, per_clip = run_suite(str(synthetic_set / "manifest.json"), cfg(strategy="video"),
                              WrongVisionModel(MockVisionModel()))
    assert good["f1"] >= 0.8
    assert bad["f1"] <= NEGATIVE_CONTROL_MAX_F1 and bad["scene_accuracy"] == 0.0
    assert bad["no_event_clean_rate"] == 0.0  # empty clips get a false alarm
    assert all(c["fp"] + c["fn"] > 0 for c in per_clip)


def test_manifest_is_versioned():
    import json
    from pathlib import Path

    m = json.loads((Path(__file__).resolve().parents[1] / "sample-data/eval/manifest.json").read_text())
    assert m["version"] == "1.0" and len(m["clips"]) == 16
    assert all("expected_events" in c for c in m["clips"])
