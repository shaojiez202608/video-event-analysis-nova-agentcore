"""CDK app entry. All parameters come from one file (default config/params.json, created from
config/params.example.json by `make init`). The account comes from the environment
(CDK_DEFAULT_ACCOUNT), never from the file. Use `make deploy` (see README) rather than calling
cdk directly, so that the preflight and the destructive-change guard run first.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "src"))
from video_infra import params as params_check  # noqa: E402
from video_infra.stack import VideoEventStack  # noqa: E402

app = cdk.App()
params_path = ROOT / (app.node.try_get_context("params") or "config/params.json")
if not params_path.is_file():
    sys.exit(f"{params_path} not found. Run `make init` (copies config/params.example.json) or pass -c params=<file>.")
params = json.loads(params_path.read_text(encoding="utf-8"))
params_check.validate(params)  # fails synth before any construct is created
deploy, pipeline = params["deploy"], params["pipeline"]

stack = VideoEventStack(
    app,
    deploy["stack_name"],
    deploy=deploy,
    pipeline=pipeline,
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
                        region=deploy.get("region") or os.environ.get("CDK_DEFAULT_REGION")),
    description="Prototype: camera clip event analysis with Amazon Nova and Amazon Bedrock AgentCore",
)
for k, v in deploy.get("tags", {}).items():
    cdk.Tags.of(app).add(k, v)
cdk.Validations.of(app).add_plugins(AwsSolutionsChecks(app))
app.synth()
