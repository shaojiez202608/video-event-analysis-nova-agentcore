"""Deploy-time validation of the parameter file. Runs before any construct is created, so a bad
value fails `cdk synth` with a list of every problem instead of failing half-way through a deploy.

The pipeline section is validated by the same code the runtime uses (PipelineConfig.from_dict).
"""

from __future__ import annotations

import json
import re
from typing import Any

PREFIX_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
STACK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,127}$")
REGION_RE = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d$")
TAG_RE = re.compile(r"^[A-Za-z0-9 _.:/=+@-]{1,128}$")
ACCOUNT_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")
RETENTION_DAYS = (1, 3, 5, 7, 14, 30, 90)
AGENT_NAME_MAX = 48  # AWS::BedrockAgentCore::Runtime AgentRuntimeName pattern [a-zA-Z][a-zA-Z0-9_]{0,47}
AGENT_SUFFIX = "_event_agent"


class ParamsInvalid(ValueError):
    """The parameter file has one or more invalid values."""


def agent_name_for(prefix: str) -> str:
    return prefix.replace("-", "_") + AGENT_SUFFIX


def _int(d: dict, key: str, lo: int, hi: int, errors: list[str]) -> int | None:
    v = d.get(key)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        errors.append(f"deploy.{key} must be an integer between {lo} and {hi} (got {v!r})")
        return None
    return v


def validate(params: dict[str, Any]) -> None:
    """Raise ParamsInvalid listing every problem in the deploy and pipeline sections."""
    errors: list[str] = []
    if not isinstance(params, dict) or not isinstance(params.get("deploy"), dict) \
            or not isinstance(params.get("pipeline"), dict):
        raise ParamsInvalid("parameter file needs 'deploy' and 'pipeline' objects")
    d = params["deploy"]

    if ACCOUNT_RE.search(json.dumps(params)):
        errors.append("parameter file contains a 12-digit number; pass account ids through the environment "
                      "(CDK_DEFAULT_ACCOUNT) or --context, never in the file")
    if not isinstance(d.get("stack_name"), str) or not STACK_RE.match(d["stack_name"]):
        errors.append("deploy.stack_name must match [A-Za-z][A-Za-z0-9-]{0,127}")
    prefix = d.get("resource_prefix")
    if not isinstance(prefix, str) or not PREFIX_RE.match(prefix):
        errors.append("deploy.resource_prefix must be 3-32 chars of a-z, 0-9 and '-', starting with a letter")
    elif len(agent_name_for(prefix)) > AGENT_NAME_MAX:
        errors.append(f"deploy.resource_prefix is too long: runtime name {agent_name_for(prefix)!r} exceeds "
                      f"{AGENT_NAME_MAX} characters")
    if "region" in d and (not isinstance(d["region"], str) or not REGION_RE.match(d["region"])):
        errors.append("deploy.region must look like us-east-1")
    if d.get("removal_policy") not in ("destroy", "retain"):
        errors.append("deploy.removal_policy must be 'destroy' or 'retain'")
    if d.get("orchestrator", "agentcore") not in ("agentcore", "lambda"):
        errors.append("deploy.orchestrator must be 'agentcore' or 'lambda'")
    video = _int(d, "video_retention_days", 1, 3650, errors)
    frames = _int(d, "frame_retention_days", 1, 3650, errors)
    _int(d, "result_retention_days", 1, 3650, errors)
    if d.get("log_retention_days", 7) not in RETENTION_DAYS:
        errors.append(f"deploy.log_retention_days must be one of {list(RETENTION_DAYS)}")
    timeout = _int(d, "lambda_timeout_s", 60, 900, errors)
    _int(d, "lambda_memory_mb", 512, 10240, errors)
    idle = _int(d, "agent_idle_timeout_s", 60, 28800, errors)
    life = _int(d, "agent_max_lifetime_s", 60, 28800, errors)
    tags = d.get("tags", {})
    if not isinstance(tags, dict) or "Project" not in tags or not all(
            isinstance(k, str) and isinstance(v, str) and TAG_RE.match(k) and TAG_RE.match(v) for k, v in tags.items()):
        errors.append("deploy.tags must be string pairs and include Project")

    # Cross-field rules.
    if idle is not None and life is not None and idle > life:
        errors.append("deploy.agent_idle_timeout_s must not exceed deploy.agent_max_lifetime_s")
    if timeout is not None and life is not None and d.get("orchestrator", "agentcore") == "agentcore" \
            and life < timeout:
        errors.append("deploy.agent_max_lifetime_s must be >= deploy.lambda_timeout_s: the dispatcher waits for "
                      "the runtime session for up to its own timeout")
    if video is not None and frames is not None and frames > video:
        errors.append("deploy.frame_retention_days must not exceed deploy.video_retention_days")
    if params["pipeline"].get("persist_frames") and frames is not None and frames > 7:
        errors.append("pipeline.persist_frames=true requires deploy.frame_retention_days <= 7")

    try:
        from video_events.config import PipelineConfig

        PipelineConfig.from_dict(params["pipeline"])
    except (ValueError, TypeError) as exc:
        errors.append(f"pipeline: {exc}")
    if errors:
        raise ParamsInvalid("invalid parameter file:\n  - " + "\n  - ".join(errors))
