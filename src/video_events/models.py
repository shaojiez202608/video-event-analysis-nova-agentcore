"""Model layer. ``MockVisionModel`` and ``BedrockNovaModel`` share one interface,
so the pipeline, the AgentCore entrypoint and the evaluation scripts do not
care which one is used."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Protocol

from . import mock_vision
from .frames import SampledFrame, sample_frames
from .prompts import SYSTEM_PROMPT, frames_prompt, video_prompt
from .schema import tool_input_schema


@dataclass
class ModelRequest:
    kind: str  # "video" | "frames"
    clip_duration_s: float
    seg_start_s: float
    seg_end_s: float
    frames: list[SampledFrame] = field(default_factory=list)
    frame_interval_s: float = 1.0
    video_bytes: bytes | None = None
    video_s3_uri: str | None = None
    video_bucket_owner: str | None = None
    video_format: str = "mp4"
    retry_hint: str | None = None  # set on retries after an invalid response


@dataclass
class ModelResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    images_sent: int = 0


class VisionModel(Protocol):
    name: str

    def analyze(self, req: ModelRequest) -> ModelResponse: ...


class MockVisionModel:
    """Colour-rule model for synthetic clips. ``faults`` lets tests inject bad output:
    each call pops the next fault ("not_json", "bad_schema", "bad_event", "fenced",
    "long_scene", "bad_scene_type", None). The last two put ``LEAK_MARKER`` text in
    fields that fail schema validation, to prove the text never reaches the logs."""

    LEAK_MARKER = "ZEBRA-MARKER red car parked by the gate"

    name = "mock-vision"

    def __init__(self, faults: list[str | None] | None = None, native_fps: float = 1.0):
        self.faults = list(faults or [])
        self.native_fps = native_fps
        self.calls = 0

    def analyze(self, req: ModelRequest) -> ModelResponse:
        self.calls += 1
        t0 = time.perf_counter()
        if req.kind == "video":
            if req.video_bytes is None:
                raise ValueError("mock model needs video bytes")
            # Mirror the documented behaviour of sampling about 1 frame per second.
            frames = sample_frames(req.video_bytes, 1.0 / self.native_fps, width=64)
            interval = 1.0 / self.native_fps
        else:
            frames, interval = req.frames, req.frame_interval_s
        doc = mock_vision.analyze_samples([(f.t, f.thumb_rgb) for f in frames], interval, req.clip_duration_s)
        fault = self.faults.pop(0) if self.faults else None
        text = self._apply_fault(doc, fault)
        return ModelResponse(
            text=text,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            images_sent=0 if req.kind == "video" else len(req.frames),
        )

    @staticmethod
    def _apply_fault(doc: dict, fault: str | None) -> str:
        if fault == "not_json":
            return "Sure! Here is what I saw: a door."
        if fault == "bad_schema":
            return json.dumps({"events": "none"})
        if fault == "bad_event":
            doc = json.loads(json.dumps(doc))
            doc["events"] += [
                {**_template_event(), "type": "face_match"},
                {**_template_event(), "start_s": 50.0, "end_s": 10.0},
                {**_template_event(), "start_s": -30.0, "end_s": 9999.0},
            ]
        if fault == "long_scene":
            doc = dict(doc, scene_description=(MockVisionModel.LEAK_MARKER + " ") * 20)
        if fault == "bad_scene_type":
            doc = dict(doc, scene_type=MockVisionModel.LEAK_MARKER)
        if fault == "fenced":
            return "```json\n" + json.dumps(doc) + "\n```"
        return json.dumps(doc, ensure_ascii=False)


def _template_event() -> dict:
    return {
        "category": "risk",
        "type": "intrusion",
        "start_s": 1.0,
        "end_s": 2.0,
        "risk_level": "high",
        "confidence": "low",
        "location": "yard",
        "subject": "person",
        "description": "injected",
    }


class BedrockNovaModel:
    """Amazon Nova through the Bedrock Runtime Converse API."""

    TOOL_NAME = "report_camera_events"

    def __init__(self, model_id: str, max_tokens: int = 2048, temperature: float = 0.0, client=None, region=None,
                 output_mode: str = "tool", prompt_hint: str = ""):
        import boto3
        from botocore.config import Config

        self.name = model_id
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.output_mode = output_mode
        self.system_prompt = SYSTEM_PROMPT + (f"\nDeployment context: {prompt_hint}" if prompt_hint else "")
        self.client = client or boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(read_timeout=300, retries={"max_attempts": 4, "mode": "adaptive"}),
        )

    def build_content(self, req: ModelRequest) -> list[dict]:
        content: list[dict] = []
        if req.kind == "video":
            if req.video_bytes is not None:
                source = {"bytes": req.video_bytes}
            elif req.video_s3_uri:
                loc = {"uri": req.video_s3_uri}
                if req.video_bucket_owner:
                    loc["bucketOwner"] = req.video_bucket_owner
                source = {"s3Location": loc}
            else:
                raise ValueError("video request needs bytes or an S3 URI")
            content.append({"video": {"format": req.video_format, "source": source}})
            content.append({"text": video_prompt(req.clip_duration_s, req.retry_hint)})
        else:
            for f in req.frames:
                content.append({"text": f"Frame at t={f.t:.1f}s"})
                content.append({"image": {"format": "jpeg", "source": {"bytes": f.jpeg}}})
            content.append(
                {"text": frames_prompt(req.clip_duration_s, req.seg_start_s, req.seg_end_s,
                                       [f.t for f in req.frames], req.retry_hint)}
            )
        return content

    def analyze(self, req: ModelRequest) -> ModelResponse:
        t0 = time.perf_counter()
        kwargs = dict(
            modelId=self.model_id,
            system=[{"text": self.system_prompt}],
            messages=[{"role": "user", "content": self.build_content(req)}],
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": self.temperature},
        )
        if self.output_mode == "tool":
            kwargs["toolConfig"] = {
                "tools": [{"toolSpec": {
                    "name": self.TOOL_NAME,
                    "description": "Report the camera events, scene and summary for this clip.",
                    "inputSchema": {"json": tool_input_schema()},
                }}],
                "toolChoice": {"tool": {"name": self.TOOL_NAME}},
            }
        resp = self.client.converse(**kwargs)
        content = resp["output"]["message"]["content"]
        tool_inputs = [c["toolUse"].get("input") for c in content
                       if "toolUse" in c and c["toolUse"].get("name") == self.TOOL_NAME]
        if tool_inputs:
            text = json.dumps(tool_inputs[0], ensure_ascii=False)
        else:
            text = "".join(c.get("text", "") for c in content)
        usage = resp.get("usage", {})
        return ModelResponse(
            text=text,
            input_tokens=int(usage.get("inputTokens", 0)),
            output_tokens=int(usage.get("outputTokens", 0)),
            latency_ms=int(resp.get("metrics", {}).get("latencyMs", (time.perf_counter() - t0) * 1000)),
            images_sent=len(req.frames),
        )


def make_model(cfg) -> VisionModel:
    if cfg.model_mode == "bedrock":
        return BedrockNovaModel(cfg.model_id, cfg.max_tokens, cfg.temperature, output_mode=cfg.output_mode,
                                prompt_hint=cfg.prompt_hint)
    return MockVisionModel()
