import json
import zipfile

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template
from cdk_nag import AwsSolutionsChecks
from video_infra.stack import VideoEventStack, foundation_model_id

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


def _synth(tmp, mutate=None):
    (tmp / "bundle").mkdir()
    (tmp / "bundle" / "dispatcher_handler.py").write_text("def handler(e, c):\n    return {}\n")
    with zipfile.ZipFile(tmp / "agent.zip", "w") as z:
        z.writestr("main.py", "print('x')\n")
    params = json.loads((ROOT / "config" / "params.json").read_text())
    if mutate:
        mutate(params)
    deploy = dict(params["deploy"], bundle_dir=str(tmp / "bundle"), agent_zip=str(tmp / "agent.zip"))
    outdir = tmp / "cdk.out"
    app = cdk.App(outdir=str(outdir))
    stack = VideoEventStack(app, deploy["stack_name"], deploy=deploy, pipeline=params["pipeline"])
    for k, v in deploy["tags"].items():
        cdk.Tags.of(app).add(k, v)
    cdk.Validations.of(app).add_plugins(AwsSolutionsChecks(app))
    app.synth()
    report = json.loads((outdir / "validation-report.json").read_text())
    return stack, Template.from_stack(stack), report


def _nag(report):
    return next(p for p in report["pluginReports"] if p["pluginName"] == "AwsSolutions")


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    return _synth(tmp_path_factory.mktemp("bundle"))


def test_foundation_model_id():
    assert foundation_model_id("us.amazon.nova-2-lite-v1:0") == "amazon.nova-2-lite-v1:0"
    assert foundation_model_id("amazon.nova-lite-v1:0") == "amazon.nova-lite-v1:0"


def test_no_unacknowledged_nag_findings(synth):
    _, _, report = synth
    nag = _nag(report)
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


def test_dispatcher_failures_go_to_queue(synth):
    _, t, _ = synth
    t.has_resource_properties("AWS::SQS::Queue", {"QueueName": Match.string_like_regexp("-dispatch-failures$"),
                                                  "SqsManagedSseEnabled": True})
    t.has_resource_properties("AWS::Lambda::EventInvokeConfig", {
        "MaximumRetryAttempts": 0,
        "DestinationConfig": {"OnFailure": {"Destination": Match.any_value()}}})


def _prefix_and_model(params, prefix="acme-cam", model="eu.amazon.nova-pro-v1:0", orchestrator=None):
    params["deploy"]["resource_prefix"] = prefix
    params["deploy"]["stack_name"] = f"{prefix}-stack"
    params["pipeline"]["model_id"] = model
    if orchestrator:
        params["deploy"]["orchestrator"] = orchestrator


@pytest.mark.parametrize("model,orchestrator", [
    ("eu.amazon.nova-pro-v1:0", None),          # other prefix + other inference profile
    ("amazon.nova-lite-v1:0", None),            # on-demand model id (no profile)
    ("global.amazon.nova-2-lite-v1:0", "lambda"),  # lambda orchestrator adds Bedrock/S3 to the Lambda role
])
def test_nag_clean_with_other_prefix_and_model(tmp_path, model, orchestrator):
    _, t, report = _synth(tmp_path, lambda p: _prefix_and_model(p, model=model, orchestrator=orchestrator))
    nag = _nag(report)
    assert nag["conclusion"] == "success", nag["violations"]
    t.has_resource_properties("AWS::BedrockAgentCore::Runtime", {"AgentRuntimeName": "acme_cam_event_agent"})
    t.has_resource_properties("AWS::Lambda::Function", {"FunctionName": "acme-cam-dispatcher"})
    text = json.dumps(t.find_resources("AWS::IAM::Policy"))
    assert f"foundation-model/{foundation_model_id(model)}" in text
    assert "nova-2-lite" not in text or "nova-2-lite" in model
    acknowledged = {sv["ruleName"] for sv in nag["suppressedViolations"]}
    assert any("acme_cam_event_agent-*" in r for r in acknowledged)


def test_acknowledgements_are_scoped_to_constructs(synth):
    _, _, report = synth
    for sv in _nag(report)["suppressedViolations"]:
        paths = [c["constructPath"] for c in sv["violatingConstructs"]]
        at = sv.get("acknowledgedAt", "")
        assert at not in ("", "proto-video-event-analysis"), (sv["ruleName"], at)  # never the whole stack
        assert all(p.startswith(at + "/") or p == at for p in paths), (sv["ruleName"], at, paths)


def test_on_demand_model_is_region_scoped(tmp_path):
    _, t, _ = _synth(tmp_path, lambda p: _prefix_and_model(p, model="amazon.nova-lite-v1:0"))
    text = json.dumps(t.find_resources("AWS::IAM::Policy"))
    assert "arn:aws:bedrock:*::" not in text and "inference-profile" not in text


def test_runtime_log_group_has_retention(synth):
    _, t, _ = synth
    groups = t.find_resources("AWS::Logs::LogGroup")
    runtime = [g for g in groups.values() if "bedrock-agentcore/runtimes" in json.dumps(g["Properties"]["LogGroupName"])]
    assert len(runtime) == 1
    g = runtime[0]
    assert g["Properties"]["RetentionInDays"] == 7
    assert g["DeletionPolicy"] == "Delete"
    name = json.dumps(g["Properties"]["LogGroupName"])
    assert "AgentRuntimeId" in name and "-DEFAULT" in name
