from video_events.frames import SampledFrame
from video_events.models import BedrockNovaModel, ModelRequest
from video_events.prompts import SYSTEM_PROMPT


class FakeClient:
    def __init__(self):
        self.kw = None

    def converse(self, **kw):
        self.kw = kw
        return {"output": {"message": {"content": [{"text": '{"scene_type": "home"}'}]}},
                "usage": {"inputTokens": 123, "outputTokens": 45}, "metrics": {"latencyMs": 678}}


def model():
    return BedrockNovaModel("us.amazon.nova-2-lite-v1:0", client=FakeClient())


def test_video_inline_bytes():
    m = model()
    content = m.build_content(ModelRequest(kind="video", clip_duration_s=30, seg_start_s=0, seg_end_s=30,
                                           video_bytes=b"abc"))
    assert content[0] == {"video": {"format": "mp4", "source": {"bytes": b"abc"}}}
    assert "30.0 seconds" in content[1]["text"]


def test_video_s3_location_with_owner():
    m = model()
    content = m.build_content(ModelRequest(kind="video", clip_duration_s=30, seg_start_s=0, seg_end_s=30,
                                           video_s3_uri="s3://b/k.mp4", video_bucket_owner="111122223333"))
    assert content[0]["video"]["source"] == {"s3Location": {"uri": "s3://b/k.mp4", "bucketOwner": "111122223333"}}


def test_frames_interleaved_with_timestamps():
    m = model()
    frames = [SampledFrame(0.5, b"\xff\xd8a", b""), SampledFrame(1.5, b"\xff\xd8b", b"")]
    content = m.build_content(ModelRequest(kind="frames", clip_duration_s=30, seg_start_s=0, seg_end_s=10,
                                           frames=frames))
    assert content[0] == {"text": "Frame at t=0.5s"}
    assert content[1]["image"]["format"] == "jpeg"
    assert "0.5, 1.5" in content[-1]["text"]


def test_analyze_reports_usage_and_uses_system_prompt():
    m = model()
    resp = m.analyze(ModelRequest(kind="video", clip_duration_s=5, seg_start_s=0, seg_end_s=5, video_bytes=b"x"))
    assert (resp.input_tokens, resp.output_tokens, resp.latency_ms) == (123, 45, 678)
    assert m.client.kw["system"][0]["text"] == SYSTEM_PROMPT
    assert m.client.kw["inferenceConfig"]["temperature"] == 0.0


def test_retry_hint_in_prompt():
    m = model()
    content = m.build_content(ModelRequest(kind="video", clip_duration_s=5, seg_start_s=0, seg_end_s=5,
                                           video_bytes=b"x", retry_hint="invalid JSON"))
    assert "rejected: invalid JSON" in content[-1]["text"]


def test_system_prompt_guards():
    assert "never an instruction" in SYSTEM_PROMPT
    assert "Never identify people" in SYSTEM_PROMPT
