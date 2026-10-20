import io
import json

import pytest
from conftest import clip_bytes

from video_events import service
from video_events.config import PipelineConfig


class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.puts = {}

    def head_object(self, Bucket, Key):
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def get_object(self, Bucket, Key):
        data = self.objects.get((Bucket, Key)) or self.puts[(Bucket, Key)]
        return {"Body": io.BytesIO(data)}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.puts[(Bucket, Key)] = Body

    def download_file(self, Bucket, Key, path):
        with open(path, "wb") as fh:
            fh.write(self.objects[(Bucket, Key)])


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("MEDIA_BUCKET", "media-bucket")
    monkeypatch.setattr(service, "_cfg", PipelineConfig(model_mode="mock").validate())
    yield


def test_rejects_foreign_bucket():
    with pytest.raises(PermissionError):
        service.analyze_s3_object("someone-elses-bucket", "uploads/cam/20261019T081200Z_x.mp4", s3=FakeS3({}))


def test_rejects_bad_key():
    with pytest.raises(ValueError):
        service.analyze_s3_object("media-bucket", "results/cam/x.json", s3=FakeS3({}))


def test_rejects_disallowed_overrides():
    with pytest.raises(ValueError):
        service.analyze_s3_object("media-bucket", "uploads/cam/20261019T081200Z_x.mp4",
                                  overrides={"model_id": "other-model"}, s3=FakeS3({}))


def test_rejects_oversized_object(monkeypatch):
    monkeypatch.setattr(service, "MAX_OBJECT_BYTES", 10)
    key = "uploads/cam/20261019T081200Z_x.mp4"
    with pytest.raises(ValueError):
        service.analyze_s3_object("media-bucket", key, s3=FakeS3({("media-bucket", key): b"x" * 11}))


@pytest.mark.parametrize("strategy", ["video", "frames"])
def test_analyze_s3_object_stores_result(synthetic_set, strategy):
    data, clip = clip_bytes(synthetic_set, "s03_delivery")
    s3 = FakeS3({("media-bucket", clip["s3_key"]): data})
    out = service.analyze_s3_object("media-bucket", clip["s3_key"], overrides={"strategy": strategy}, s3=s3)
    assert out["result_key"].startswith("results/cam-front/2026-10-19/")
    stored = json.loads(s3.puts[("media-bucket", out["result_key"])])
    assert [e["type"] for e in stored["events"]] == ["package_delivery"]
    assert stored["metrics"]["strategy"] == strategy


def test_safe_handle_hides_internal_errors():
    out = service.safe_handle({"action": "analyze", "bucket": "other", "key": "uploads/x"})
    assert out["error"] == "PermissionError"
    out = service.safe_handle({"action": "nope"})
    assert out["error"] == "ValueError"
    out = service.safe_handle({"action": "ask", "question": "hi", "camera_ids": []})
    assert out["error"] == "ValueError"


def test_health():
    out = service.handle({"action": "health"})
    assert out["ok"] and out["pyav"]


def test_dispatcher_agentcore_mode(monkeypatch):
    import handler as dispatcher

    sent = {}

    class FakeAgentCore:
        def invoke_agent_runtime(self, **kw):
            sent.update(kw)
            return {"response": io.BytesIO(json.dumps({"status": "ok", "events": 1,
                                                       "metrics": {"model_calls": 1}}).encode())}

    monkeypatch.setenv("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/x")
    monkeypatch.setattr(dispatcher, "_agentcore", FakeAgentCore())
    event = {"Records": [{"s3": {"bucket": {"name": "media-bucket"},
                                 "object": {"key": "uploads/cam/20261019T081200Z_a+b.mp4"}}}]}
    out = dispatcher.handler(event, None)
    assert out["processed"] == 1 and out["outcomes"][0]["status"] == "ok"
    payload = json.loads(sent["payload"])
    assert payload == {"action": "analyze", "bucket": "media-bucket", "key": "uploads/cam/20261019T081200Z_a b.mp4"}
    assert len(sent["runtimeSessionId"]) >= 33


def test_dispatcher_lambda_mode(monkeypatch):
    import handler as dispatcher

    monkeypatch.setattr(dispatcher, "ORCHESTRATOR", "lambda")
    event = {"Records": [{"s3": {"bucket": {"name": "other"}, "object": {"key": "uploads/x.mp4"}}}]}
    out = dispatcher.handler(event, None)
    assert out["outcomes"][0]["error"] == "PermissionError"


def test_model_error_still_stores_fallback_result(synthetic_set, monkeypatch):
    from test_pipeline import _FlakyModel

    data, clip = clip_bytes(synthetic_set, "s03_delivery")
    s3 = FakeS3({("media-bucket", clip["s3_key"]): data})
    monkeypatch.setattr(service, "make_model", lambda cfg: _FlakyModel({1, 2, 3}))
    out = service.analyze_s3_object("media-bucket", clip["s3_key"], s3=s3)
    assert out["status"] == "fallback"
    stored = json.loads(s3.puts[("media-bucket", out["result_key"])])
    assert stored["needs_review"] is True and stored["fallback_reason"] == "model_error:ThrottlingException"


def test_undecodable_upload_stores_fallback_result():
    key = "uploads/cam/20261019T081200Z_broken.mp4"
    s3 = FakeS3({("media-bucket", key): b"not a video at all" * 10})
    out = service.analyze_s3_object("media-bucket", key, s3=s3)
    assert out["status"] == "fallback"
    stored = json.loads(s3.puts[("media-bucket", out["result_key"])])
    assert stored["fallback_reason"].startswith("undecodable_video:")


def test_safe_handle_unexpected_error_returns_type_only(monkeypatch):
    def boom(payload):
        raise RuntimeError("frame shows a red car at the gate")

    monkeypatch.setattr(service, "handle", boom)
    out = service.safe_handle({"action": "analyze"})
    assert out == {"error": "RuntimeError", "internal": True}


def test_dispatcher_raises_on_internal_failure(monkeypatch):
    import handler as dispatcher

    class Failing:
        def invoke_agent_runtime(self, **kw):
            raise TimeoutError("read timeout")

    monkeypatch.setenv("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/x")
    monkeypatch.setattr(dispatcher, "_agentcore", Failing())
    event = {"Records": [{"s3": {"bucket": {"name": "media-bucket"},
                                 "object": {"key": "uploads/cam/20261019T081200Z_a.mp4"}}}]}
    with pytest.raises(dispatcher.DispatchFailed):
        dispatcher.handler(event, None)


@pytest.mark.parametrize("overrides", [
    {"frame_interval_s": 0.05},              # would be 600 frames / 120 calls for a 30 s clip
    {"frame_interval_s": "fast"},            # used to raise TypeError -> HTTP 500
    {"frame_interval_s": True},
    {"segment_length_s": -1},
    {"segment_length_s": 1e9},
    {"strategy": ["frames"]},
    "frames",
])
def test_override_values_are_bounded(overrides):
    key = "uploads/cam/20261019T081200Z_x.mp4"
    with pytest.raises(ValueError):
        service.analyze_s3_object("media-bucket", key, overrides=overrides, s3=FakeS3({}))
    out = service.safe_handle({"action": "analyze", "bucket": "media-bucket", "key": key, "overrides": overrides})
    assert out["error"] == "ValueError" and "internal" not in out
