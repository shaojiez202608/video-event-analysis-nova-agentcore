"""CDK app entry. All parameters come from one file (default config/params.json):

    npx -y aws-cdk@2 deploy -c params=config/params.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_infra.stack import VideoEventStack  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

app = cdk.App()
params_path = ROOT / (app.node.try_get_context("params") or "config/params.json")
params = json.loads(params_path.read_text(encoding="utf-8"))
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
