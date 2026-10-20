import copy
import json
from pathlib import Path

import pytest
from video_infra.params import ParamsInvalid, validate

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / "config" / "params.example.json").read_text())


def with_deploy(**kw):
    p = copy.deepcopy(BASE)
    p["deploy"].update(kw)
    return p


@pytest.mark.parametrize("name", ["params.example.json", "params.customer.example.json"])
def test_shipped_param_files_are_valid(name):
    validate(json.loads((ROOT / "config" / name).read_text()))


@pytest.mark.parametrize("params,needle", [
    (with_deploy(resource_prefix="Proto_Video"), "resource_prefix"),
    (with_deploy(resource_prefix="a-very-long-prefix-for-the-runtime"), "resource_prefix"),
    (with_deploy(stack_name="1bad"), "stack_name"),
    (with_deploy(region="us_east_1"), "region"),
    (with_deploy(removal_policy="delete"), "removal_policy"),
    (with_deploy(orchestrator="ecs"), "orchestrator"),
    (with_deploy(log_retention_days=10), "log_retention_days"),
    (with_deploy(lambda_timeout_s=2000), "lambda_timeout_s"),
    (with_deploy(agent_idle_timeout_s=3000, agent_max_lifetime_s=1800), "agent_idle_timeout_s must not exceed"),
    (with_deploy(agent_max_lifetime_s=600), "agent_max_lifetime_s must be >="),
    (with_deploy(frame_retention_days=5, video_retention_days=3), "frame_retention_days"),
    (with_deploy(tags={"Purpose": "x"}), "tags"),
    (with_deploy(stack_name="proto-111122223333"), "12-digit"),
])
def test_invalid_deploy_values_fail(params, needle):
    with pytest.raises(ParamsInvalid, match=needle):
        validate(params)


def test_pipeline_section_uses_runtime_validation():
    p = copy.deepcopy(BASE)
    p["pipeline"]["frame_interval_s"] = 0.05
    with pytest.raises(ParamsInvalid, match="pipeline: frame_interval_s"):
        validate(p)


def test_persist_frames_requires_short_retention():
    p = with_deploy(frame_retention_days=3, video_retention_days=30)
    p["pipeline"]["persist_frames"] = True
    validate(p)
    p["deploy"]["frame_retention_days"] = 14
    with pytest.raises(ParamsInvalid, match="persist_frames"):
        validate(p)


def test_all_problems_reported_together():
    with pytest.raises(ParamsInvalid) as info:
        validate(with_deploy(removal_policy="x", orchestrator="y"))
    assert "removal_policy" in str(info.value) and "orchestrator" in str(info.value)
