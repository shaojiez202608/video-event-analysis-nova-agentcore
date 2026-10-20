"""Pipeline configuration.

All tunables live in one JSON document (``config/params.json`` -> ``pipeline``).
In AWS the same document is stored in SSM Parameter Store by the CDK stack and
read once per cold start.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from .taxonomy import DEFAULT_RISK_FLOORS, RISK_LEVELS

STRATEGIES = ("video", "frames")


@dataclass
class PipelineConfig:
    # "video": send the whole clip to the model in one call (the model samples frames itself).
    # "frames": sample frames locally at frame_interval_s, group them into segments of
    #           segment_length_s (0 = whole clip), one model call per segment batch.
    strategy: str = "video"
    frame_interval_s: float = 1.0
    segment_length_s: float = 10.0
    # Per-request image cap for embedded images. Check the current value on the
    # Amazon Nova user guide ("Supported content type by modality").
    max_images_per_call: int = 5
    frame_width: int = 640
    # Clips larger than this are passed to the model by S3 URI instead of bytes.
    # Must stay below the documented inline payload limit (see the Nova user guide).
    inline_video_max_bytes: int = 20_000_000

    model_mode: str = "mock"  # "mock" | "bedrock"
    model_id: str = "us.amazon.nova-2-lite-v1:0"
    max_tokens: int = 2048
    temperature: float = 0.0
    max_retries: int = 1  # extra attempts after an unparsable / schema-invalid response
    # "tool": force a single tool call whose input schema is the output contract
    #         (Nova constrained decoding); "text": ask for a bare JSON object.
    # Validation runs the same way in both modes.
    output_mode: str = "tool"
    # Operator-supplied deployment context appended to the system prompt (never taken
    # from the video or the request). The synthetic evaluation sets a hint that explains
    # its rendering convention; see scripts/evaluate.py.
    prompt_hint: str = ""

    time_tolerance_s: float = 1.0  # how far outside [0, duration] an event may end before rejection
    merge_gap_s: float = 2.0  # same-type events closer than this are merged across segments

    risk_floors: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_RISK_FLOORS))
    locales: list[str] = field(default_factory=lambda: ["zh-CN", "en-US", "ru-RU"])
    watch_rules: list[dict[str, Any]] = field(default_factory=list)
    # Minutes east of UTC used to turn clip timestamps into local clock time for
    # night-time rules and daily summaries. Business value unknown (OPEN-QUESTIONS.md).
    utc_offset_minutes: int = 0

    # Privacy: frames are kept in memory only. When true, sampled frames are also
    # written under frames/ in the media bucket, where an S3 lifecycle rule deletes
    # them after frame_retention_days (set in the deploy section).
    persist_frames: bool = False

    def validate(self) -> PipelineConfig:
        if self.strategy not in STRATEGIES:
            raise ValueError(f"strategy must be one of {STRATEGIES}")
        if self.model_mode not in ("mock", "bedrock"):
            raise ValueError("model_mode must be 'mock' or 'bedrock'")
        if self.output_mode not in ("tool", "text"):
            raise ValueError("output_mode must be 'tool' or 'text'")
        if len(self.prompt_hint) > 1000:
            raise ValueError("prompt_hint is too long")
        if self.frame_interval_s <= 0:
            raise ValueError("frame_interval_s must be > 0")
        if self.segment_length_s < 0:
            raise ValueError("segment_length_s must be >= 0")
        if self.max_images_per_call < 1:
            raise ValueError("max_images_per_call must be >= 1")
        for k, v in self.risk_floors.items():
            if v not in RISK_LEVELS:
                raise ValueError(f"risk floor for {k} is not a valid level: {v}")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PipelineConfig:
        data = dict(data or {})
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown pipeline config keys: {sorted(unknown)}")
        floors = dict(DEFAULT_RISK_FLOORS)
        floors.update(data.pop("risk_floors", {}) or {})
        return cls(risk_floors=floors, **data).validate()

    def with_overrides(self, **overrides: Any) -> PipelineConfig:
        d = self.to_dict()
        d.update({k: v for k, v in overrides.items() if v is not None})
        return PipelineConfig.from_dict(d)


def load_config(path: str | None = None) -> PipelineConfig:
    """Load from a params file, the PIPELINE_CONFIG env var (JSON), or SSM."""
    if path:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        return PipelineConfig.from_dict(doc.get("pipeline", doc))
    raw = os.environ.get("PIPELINE_CONFIG")
    if raw:
        return PipelineConfig.from_dict(json.loads(raw))
    param = os.environ.get("PIPELINE_CONFIG_PARAM")
    if param:
        import boto3

        value = boto3.client("ssm").get_parameter(Name=param)["Parameter"]["Value"]
        return PipelineConfig.from_dict(json.loads(value))
    return PipelineConfig().validate()
