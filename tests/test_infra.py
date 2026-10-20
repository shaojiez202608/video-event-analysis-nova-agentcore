import json
import zipfile

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template
from cdk_nag import AwsSolutionsChecks
from video_infra.stack import VideoEventStack, foundation_model_id

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bundle")
    (tmp / "bundle").mkdir()
    (tmp / "bundle" / "dispatcher_handler.py").write_text("def handler(e, c):\n    return {}\n")
    with zipfile.ZipFile(tmp / "agent.zip", "w") as z:
        z.writestr("main.py", "print('x')\n")
    params = json.loads((ROOT / "config" / "params.json").read_text())
    deploy = dict(params["deploy"], bundle_dir=str(tmp / "bundle"), agent_zip=str(tmp / "agent.zip"))
    outdir = tmp / "cdk.out"
    app = cdk.App(outdir=str(outdir))
    stack = VideoEventStack(app, "proto-video-test", deploy=deploy, pipeline=params["pipeline"])
    for k, v in deploy["tags"].items():
        cdk.Tags.of(app).add(k, v)
    cdk.Validations.of(app).add_plugins(AwsSolutionsChecks(app))
    app.synth()
    report = json.loads((outdir / "validation-report.json").read_text())
    return stack, Template.from_stack(stack), report


def test_foundation_model_id():
    assert foundation_model_id("us.amazon.nova-2-lite-v1:0") == "amazon.nova-2-lite-v1:0"
    assert foundation_model_id("amazon.nova-lite-v1:0") == "amazon.nova-lite-v1:0"


def test_no_unacknowledged_nag_findings(synth):
    _, _, report = synth
    nag = next(p for p in report["pluginReports"] if p["pluginName"] == "AwsSolutions")
    assert nag["conclusion"] == "success"
    assert nag["violations"] == []
    for sv in nag["suppressedViolations"]:
        assert len(sv["reason"]) > 20  # every acknowledgement has a written reason


def test_lambda_is_arm64(synth):
    _, t, _ = synth
    t.has_resource_properties("AWS::Lambda::Function", {"FunctionName": "proto-video-dispatcher",
                                                         "Architectures": ["arm64"], "Runtime": "python3.13"})


def test_agentcore_runtime_code_config(synth):
    _, t, _ = synth
    t.has_resource_properties("AWS::BedrockAgentCore::Runtime", {
        "AgentRuntimeName": "proto_video_event_agent",
        "AgentRuntimeArtifact": {"CodeConfiguration": {"Runtime": "PYTHON_3_13", "EntryPoint": ["main.py"]}},
        "NetworkConfiguration": {"NetworkMode": "PUBLIC"},
        "Tags": {"Project": "video-event-analysis", "Purpose": "prototype", "Teardown": "after-test"},
    })


def test_buckets_private_encrypted_ssl(synth):
    _, t, _ = synth
    buckets = t.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 2
    for b in buckets.values():
        p = b["Properties"]
        assert p["PublicAccessBlockConfiguration"] == {"BlockPublicAcls": True, "BlockPublicPolicy": True,
                                                       "IgnorePublicAcls": True, "RestrictPublicBuckets": True}
        assert p["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]["ServerSideEncryptionByDefault"][
            "SSEAlgorithm"] == "AES256"
        assert {"Key": "Teardown", "Value": "after-test"} in p["Tags"]
    policies = json.dumps(t.find_resources("AWS::S3::BucketPolicy"))
    assert "aws:SecureTransport" in policies


def test_lifecycle_rules(synth):
    _, t, _ = synth
    t.has_resource_properties("AWS::S3::Bucket", {"LifecycleConfiguration": {"Rules": Match.array_with([
        Match.object_like({"Id": "expire-uploads", "Prefix": "uploads/", "ExpirationInDays": 3}),
        Match.object_like({"Id": "expire-frames", "Prefix": "frames/", "ExpirationInDays": 1}),
    ])}})


def test_bedrock_permission_scoped_to_model(synth):
    _, t, _ = synth
    text = json.dumps(t.find_resources("AWS::IAM::Policy"))
    assert "foundation-model/amazon.nova-2-lite-v1:0" in text
    assert "foundation-model/*" not in text
    assert '"bedrock:*"' not in text and '"s3:*"' not in text


def test_log_group_retention(synth):
    _, t, _ = synth
    t.has_resource_properties("AWS::Logs::LogGroup", {"LogGroupName": "/aws/lambda/proto-video-dispatcher",
                                                      "RetentionInDays": 7})


def test_no_security_group_open_ingress(synth):
    _, t, _ = synth
    assert t.find_resources("AWS::EC2::SecurityGroup") == {}


def test_config_parameter(synth):
    _, t, _ = synth
    t.has_resource_properties("AWS::SSM::Parameter", {"Name": "/proto-video/pipeline-config", "Type": "String"})
