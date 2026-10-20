"""CDK stack: S3 upload -> dispatcher Lambda (arm64) -> AgentCore Runtime (direct code, arm64) -> Nova."""

from __future__ import annotations

import json
from pathlib import Path

from aws_cdk import (
    Acknowledgment,
    Aws,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Validations,
)
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_destinations as lambda_dest
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_assets as s3_assets
from aws_cdk import aws_s3_notifications as s3n
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_ssm as ssm
from aws_cdk import custom_resources as cr
from constructs import Construct

from .params import agent_name_for

ROOT = Path(__file__).resolve().parents[2]
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm")


def foundation_model_id(model_id: str) -> str:
    """'us.amazon.nova-2-lite-v1:0' -> 'amazon.nova-2-lite-v1:0' (strip a geo inference-profile prefix)."""
    head, _, rest = model_id.partition(".")
    return rest if head in ("us", "eu", "apac", "global", "jp", "au", "ca") else model_id


class VideoEventStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, deploy: dict, pipeline: dict,
                 operator_arns: list[str] | None = None, **kwargs) -> None:
        """``operator_arns``: IAM user/role ARNs (besides the stack's own roles) that may invoke the runtime
        and read/write media objects, typically the deploying principal. They come from
        ``-c operator_principal_arns=...`` at deploy time, never from the committed parameter file."""
        super().__init__(scope, cid, **kwargs)
        operator_arns = list(operator_arns or [])
        prefix = deploy["resource_prefix"]
        destroy = deploy.get("removal_policy", "retain") == "destroy"
        removal = RemovalPolicy.DESTROY if destroy else RemovalPolicy.RETAIN
        log_days, log_days_n = _retention(deploy.get("log_retention_days", 7))

        bundle_dir = Path(deploy.get("bundle_dir") or ROOT / "build" / "bundle")
        agent_zip = Path(deploy.get("agent_zip") or ROOT / "build" / "agent.zip")
        if not bundle_dir.is_dir() or not agent_zip.is_file():
            raise RuntimeError("run scripts/build_bundle.sh before cdk synth/deploy")

        # ------------------------------------------------------------------ storage
        access_logs = s3.Bucket(
            self, "AccessLogs",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(30))],
            removal_policy=removal,
            auto_delete_objects=destroy,
        )
        media = s3.Bucket(
            self, "Media",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            minimum_tls_version=1.2,
            server_access_logs_bucket=access_logs,
            server_access_logs_prefix="media/",
            lifecycle_rules=[
                s3.LifecycleRule(id="expire-uploads", prefix="uploads/",
                                 expiration=Duration.days(deploy["video_retention_days"])),
                s3.LifecycleRule(id="expire-frames", prefix="frames/",
                                 expiration=Duration.days(deploy["frame_retention_days"])),
                s3.LifecycleRule(id="expire-results", prefix="results/",
                                 expiration=Duration.days(deploy["result_retention_days"])),
                s3.LifecycleRule(id="abort-mpu", abort_incomplete_multipart_upload_after=Duration.days(1)),
            ],
            removal_policy=removal,
            auto_delete_objects=destroy,
        )

        config_param = ssm.StringParameter(
            self, "PipelineConfig",
            parameter_name=f"/{prefix}/pipeline-config",
            string_value=json.dumps(pipeline, separators=(",", ":")),
            description="Video event analysis pipeline configuration (JSON)",
        )

        # ------------------------------------------------------------------ AgentCore Runtime
        agent_name = agent_name_for(prefix)  # runtime names allow [a-zA-Z0-9_]
        code_asset = s3_assets.Asset(self, "AgentCode", path=str(agent_zip))

        model_id = pipeline["model_id"]
        fm_id = foundation_model_id(model_id)
        if fm_id != model_id:  # cross-region inference profile: the model may run in any region of the profile
            model_resources = [
                f"arn:aws:bedrock:*::foundation-model/{fm_id}",
                f"arn:aws:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:inference-profile/{model_id}",
            ]
        else:  # on-demand model id: this region only
            model_resources = [f"arn:aws:bedrock:{Aws.REGION}::foundation-model/{fm_id}"]

        agent_role = iam.Role(
            self, "AgentRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                    # Narrowed from the documented account-wide "...:*" to this runtime's ARNs.
                    "ArnLike": {"aws:SourceArn":
                                f"arn:aws:bedrock-agentcore:{Aws.REGION}:{Aws.ACCOUNT_ID}:runtime/{agent_name}-*"},
                },
            ),
            description="Execution role for the video event AgentCore runtime",
        )
        runtime_logs = f"arn:aws:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*"
        agent_policy = iam.Policy(self, "AgentPolicy", statements=[
            iam.PolicyStatement(actions=["logs:CreateLogGroup", "logs:DescribeLogStreams"], resources=[runtime_logs]),
            iam.PolicyStatement(actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                                resources=[f"{runtime_logs}:log-stream:*"]),
            iam.PolicyStatement(actions=["logs:DescribeLogGroups"],
                                resources=[f"arn:aws:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:log-group:*"]),
            iam.PolicyStatement(actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords",
                                         "xray:GetSamplingRules", "xray:GetSamplingTargets"], resources=["*"]),
            iam.PolicyStatement(actions=["cloudwatch:PutMetricData"], resources=["*"],
                                conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}),
            # The documented logs:PutResourcePolicy, scoped to this runtime's log groups. No workload-identity
            # (GetWorkloadAccessToken*) permissions: the code never requests workload tokens or outbound OAuth.
            iam.PolicyStatement(actions=["logs:PutResourcePolicy"], resources=[
                f"arn:aws:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/{agent_name}-*"]),
            iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=model_resources),
            iam.PolicyStatement(actions=["s3:GetObject"], resources=[media.arn_for_objects("uploads/*"),
                                                                     media.arn_for_objects("results/*")]),
            iam.PolicyStatement(actions=["s3:PutObject"], resources=[media.arn_for_objects("results/*"),
                                                                     media.arn_for_objects("frames/*")]),
            iam.PolicyStatement(actions=["s3:ListBucket"], resources=[media.bucket_arn],
                                conditions={"StringLike": {"s3:prefix": ["results/*"]}}),
            iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[config_param.parameter_arn]),
        ])
        # Read of the single code package object (no bucket-wide wildcard).
        agent_policy.add_statements(iam.PolicyStatement(
            actions=["s3:GetObject"],
            resources=[f"arn:aws:s3:::{code_asset.s3_bucket_name}/{code_asset.s3_object_key}"]))
        agent_policy.attach_to_role(agent_role)

        runtime = agentcore.CfnRuntime(
            self, "AgentRuntime",
            agent_runtime_name=agent_name,
            description="Camera clip analysis: sampling, Nova call, validation, post-processing",
            role_arn=agent_role.role_arn,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                code_configuration=agentcore.CfnRuntime.CodeConfigurationProperty(
                    code=agentcore.CfnRuntime.CodeProperty(
                        s3=agentcore.CfnRuntime.S3LocationProperty(
                            bucket=code_asset.s3_bucket_name, prefix=code_asset.s3_object_key),
                    ),
                    runtime="PYTHON_3_13",
                    entry_point=["main.py"],
                ),
            ),
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(network_mode="PUBLIC"),
            lifecycle_configuration=agentcore.CfnRuntime.LifecycleConfigurationProperty(
                idle_runtime_session_timeout=deploy["agent_idle_timeout_s"],
                max_lifetime=deploy["agent_max_lifetime_s"],
            ),
            environment_variables={
                "MEDIA_BUCKET": media.bucket_name,
                "MEDIA_BUCKET_OWNER": Aws.ACCOUNT_ID,
                "PIPELINE_CONFIG_PARAM": config_param.parameter_name,
                "METRICS_NAMESPACE": "VideoEventAnalysis",
            },
            tags=dict(deploy.get("tags", {})),
        )
        runtime.node.add_dependency(agent_policy)
        # The service creates /aws/bedrock-agentcore/runtimes/<runtime id>-DEFAULT itself while the runtime is
        # created (an AWS::Logs::LogGroup with that name fails with AlreadyExists), with "never expire".
        # A custom resource sets the retention right after the runtime exists and deletes the group on destroy.
        runtime_log_group_name = f"/aws/bedrock-agentcore/runtimes/{runtime.attr_agent_runtime_id}-DEFAULT"
        runtime_log_group_arn = f"arn:aws:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:log-group:{runtime_log_group_name}"
        retention_call = cr.AwsSdkCall(
            service="CloudWatchLogs", action="putRetentionPolicy",
            parameters={"logGroupName": runtime_log_group_name, "retentionInDays": log_days_n},
            physical_resource_id=cr.PhysicalResourceId.of(runtime_log_group_name),
        )
        runtime_log_retention = cr.AwsCustomResource(
            self, "RuntimeLogRetention",
            on_create=retention_call,
            on_update=retention_call,
            on_delete=cr.AwsSdkCall(
                service="CloudWatchLogs", action="deleteLogGroup",
                parameters={"logGroupName": runtime_log_group_name},
                ignore_error_codes_matching="ResourceNotFoundException",
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([iam.PolicyStatement(
                actions=["logs:PutRetentionPolicy", "logs:DeleteLogGroup"],
                resources=[runtime_log_group_arn, f"{runtime_log_group_arn}:*"])]),
            install_latest_aws_sdk=False,
        )
        runtime_log_retention.node.add_dependency(runtime)

        # ------------------------------------------------------------------ dispatcher Lambda
        orchestrator = deploy.get("orchestrator", "agentcore")
        fn_name = f"{prefix}-dispatcher"
        fn_logs = logs.LogGroup(self, "DispatcherLogs", log_group_name=f"/aws/lambda/{fn_name}",
                                retention=log_days, removal_policy=RemovalPolicy.DESTROY)
        fn_role = iam.Role(self, "DispatcherRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
                           description="Dispatcher Lambda role")
        fn_statements = [
            iam.PolicyStatement(actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                                resources=[f"{fn_logs.log_group_arn}"]),
            iam.PolicyStatement(actions=["bedrock-agentcore:InvokeAgentRuntime"],
                                resources=[runtime.attr_agent_runtime_arn, f"{runtime.attr_agent_runtime_arn}/*"]),
        ]
        if orchestrator == "lambda":
            fn_statements += [
                iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=model_resources),
                iam.PolicyStatement(actions=["s3:GetObject"], resources=[media.arn_for_objects("uploads/*")]),
                iam.PolicyStatement(actions=["s3:PutObject"], resources=[media.arn_for_objects("results/*"),
                                                                         media.arn_for_objects("frames/*")]),
                iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[config_param.parameter_arn]),
            ]
        # Failed asynchronous invocations (internal errors, not request rejections) land here.
        failures = sqs.Queue(
            self, "DispatchFailures",
            queue_name=f"{prefix}-dispatch-failures",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            retention_period=Duration.days(14),
            removal_policy=RemovalPolicy.DESTROY,
        )
        fn_policy = iam.Policy(self, "DispatcherPolicy", statements=fn_statements)
        fn_policy.attach_to_role(fn_role)
        dispatcher = lambda_.Function(
            self, "Dispatcher",
            function_name=fn_name,
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="dispatcher_handler.handler",
            code=lambda_.Code.from_asset(str(bundle_dir)),
            timeout=Duration.seconds(deploy["lambda_timeout_s"]),
            memory_size=deploy["lambda_memory_mb"],
            role=fn_role,
            log_group=fn_logs,
            retry_attempts=0,
            on_failure=lambda_dest.SqsDestination(failures),
            environment={
                "ORCHESTRATOR": orchestrator,
                "AGENT_RUNTIME_ARN": runtime.attr_agent_runtime_arn,
                "MEDIA_BUCKET": media.bucket_name,
                "MEDIA_BUCKET_OWNER": Aws.ACCOUNT_ID,
                "PIPELINE_CONFIG_PARAM": config_param.parameter_name,
            },
            description="S3 upload -> AgentCore runtime dispatcher",
        )
        dispatcher.node.add_dependency(fn_policy)

        # ------------------------------------------------------------------ resource policies
        # Runtime: only the dispatcher role and the named operators may invoke it; everyone else is
        # explicitly denied, including other principals of this account that have IAM permissions.
        invokers = [fn_role.role_arn, *operator_arns]
        invoke_actions = ["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeAgentRuntimeCommand",
                          "bedrock-agentcore:InvokeAgentRuntimeCommandShell",
                          "bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream",
                          "bedrock-agentcore:StopRuntimeSession"]
        user_actions = ["bedrock-agentcore:InvokeAgentRuntimeForUser",
                        "bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStreamForUser"]
        runtime_policy = agentcore.CfnResourcePolicy(
            self, "RuntimeResourcePolicy",
            resource_arn=runtime.attr_agent_runtime_arn,
            policy=Stack.of(self).to_json_string({
                "Version": "2012-10-17",
                "Statement": [
                    {"Sid": "AllowExpectedCallers", "Effect": "Allow", "Principal": {"AWS": invokers},
                     "Action": "bedrock-agentcore:InvokeAgentRuntime", "Resource": runtime.attr_agent_runtime_arn},
                    {"Sid": "DenyEveryoneElse", "Effect": "Deny", "Principal": "*", "Action": invoke_actions,
                     "Resource": runtime.attr_agent_runtime_arn,
                     "Condition": {"ArnNotEquals": {"aws:PrincipalArn": invokers}}},
                    # runtimeUserId is not used: nobody may pass a caller-chosen user id.
                    {"Sid": "DenyUserIdInvocations", "Effect": "Deny", "Principal": "*", "Action": user_actions,
                     "Resource": runtime.attr_agent_runtime_arn},
                ],
            }),
        )
        runtime_policy.node.add_dependency(fn_role)

        # Media bucket: object reads/writes only by this stack's roles and the named operators. AWS services
        # acting on a caller's behalf (for example Bedrock reading a clip by S3 URI) are evaluated as the caller.
        media_writers = [agent_role.role_arn, fn_role.role_arn, *operator_arns]
        auto_delete = self.node.try_find_child("Custom::S3AutoDeleteObjectsCustomResourceProvider")
        if auto_delete is not None:
            media_writers.append(auto_delete.role_arn)
        media.add_to_resource_policy(iam.PolicyStatement(
            sid="DenyObjectAccessExceptExpectedPrincipals", effect=iam.Effect.DENY,
            principals=[iam.AnyPrincipal()],
            actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetObjectVersion",
                     "s3:DeleteObjectVersion"],
            resources=[media.arn_for_objects("*")],
            conditions={"ArnNotEquals": {"aws:PrincipalArn": media_writers},
                        "Bool": {"aws:ViaAWSService": "false"}},
        ))
        for suffix in VIDEO_SUFFIXES:
            media.add_event_notification(s3.EventType.OBJECT_CREATED, s3n.LambdaDestination(dispatcher),
                                         s3.NotificationKeyFilter(prefix="uploads/", suffix=suffix))

        CfnOutput(self, "MediaBucketName", value=media.bucket_name)
        CfnOutput(self, "AgentRuntimeArn", value=runtime.attr_agent_runtime_arn)
        CfnOutput(self, "AgentRuntimeId", value=runtime.attr_agent_runtime_id)
        CfnOutput(self, "RuntimeLogGroupName", value=runtime_log_group_name)
        CfnOutput(self, "DispatcherFunctionName", value=dispatcher.function_name)
        CfnOutput(self, "PipelineConfigParam", value=config_param.parameter_name)
        CfnOutput(self, "DispatchFailureQueueUrl", value=failures.queue_url)

        self._acknowledge(access_logs=access_logs, failures=failures, agent_policy=agent_policy,
                          fn_policy=fn_policy, dispatcher=dispatcher, agent_name=agent_name, model_id=model_id,
                          fm_id=fm_id, media=media, lambda_mode=orchestrator == "lambda",
                          log_retention=runtime_log_retention)

    def _acknowledge(self, *, access_logs: s3.Bucket, failures: sqs.Queue, agent_policy: iam.Policy,
                     fn_policy: iam.Policy, dispatcher: lambda_.Function, agent_name: str, model_id: str,
                     fm_id: str, media: s3.Bucket, lambda_mode: bool, log_retention: cr.AwsCustomResource) -> None:
        """cdk-nag v3 acknowledgements, each attached to the construct that triggers it.

        Finding ids are built from the same values as the policies (runtime name, model id), so a
        different resource_prefix or model_id still synthesises cleanly. Each one is listed with
        its reason in SECURITY-NOTES.md."""

        def ack(construct, rule: str, reason: str, finding: str | None = None) -> None:
            rid = f"AwsSolutions::AwsSolutions-{rule}" + (f"[{finding}]" if finding else "")
            Validations.of(construct).acknowledge(Acknowledgment(id=rid, reason=reason))

        ack(access_logs, "S1", "This bucket is the server-access-log destination; logging it to itself would loop.")
        ack(failures, "SQS3", "This queue is itself the on-failure destination of the dispatcher Lambda; a further "
                              "dead-letter queue behind it would only move the same messages again.")

        media_ref = f"<{self.get_logical_id(media.node.default_child)}.Arn>"
        object_prefixes = {
            f"Resource::{media_ref}/{p}/*": "Object prefix in this stack's own media bucket."
            for p in ("uploads", "results", "frames")
        }
        model_finding = {
            f"Resource::arn:aws:bedrock:*::foundation-model/{fm_id}":
                f"The inference profile {model_id} routes to the same foundation model in several regions of "
                "its geography, so the foundation-model ARN needs a region wildcard; the model id is fixed.",
        } if fm_id != model_id else {}
        runtime_logs = "arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*"
        agent_findings = {
            f"Resource::{runtime_logs}":
                "AgentCore Runtime writes to a log group named after its generated runtime id; the scope is the "
                "runtime log-group family from the documented execution role.",
            f"Resource::{runtime_logs}:log-stream:*":
                "Log streams inside the AgentCore runtime log groups (documented execution role).",
            "Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:*":
                "logs:DescribeLogGroups is a list action without resource-level scoping (documented role).",
            "Resource::*":
                "X-Ray PutTraceSegments/PutTelemetryRecords/GetSampling* do not support resource-level "
                "permissions; cloudwatch:PutMetricData is limited by the cloudwatch:namespace condition.",
            f"Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/"
            f"{agent_name}-*":
                f"logs:PutResourcePolicy on the log groups of runtime {agent_name} only (generated id suffix), as in "
                "the documented execution role.",
            **model_finding,
            **object_prefixes,
        }
        for finding, reason in agent_findings.items():
            ack(agent_policy, "IAM5", reason, finding)

        fn_findings = {"Resource::<AgentRuntime.AgentRuntimeArn>/*": "Endpoints (qualifiers) of this one runtime."}
        if lambda_mode:
            fn_findings.update(model_finding)
            fn_findings.update(object_prefixes)
        for finding, reason in fn_findings.items():
            ack(fn_policy, "IAM5", reason, finding)

        ack(dispatcher, "L1",
            "The project's own dispatcher uses Python 3.13 on purpose: it shares one arm64 wheel bundle with the "
            "AgentCore Runtime, whose newest direct-code runtime is PYTHON_3_13. Move both together (handover).")

        ack(log_retention, "IAM5",
            "logs:PutRetentionPolicy / logs:DeleteLogGroup on this runtime's own log group; the ':*' form of the "
            "log-group ARN is the one CloudWatch Logs evaluates for log-group actions.",
            "Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/"
            "<AgentRuntime.AgentRuntimeId>-DEFAULT:*")

        basic = "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
        singletons = {
            "BucketNotificationsHandler": "CDK-managed bucket-notifications handler",
            "AWS679f53fac002430cb0da5b7982bd2287": "CDK-managed AwsCustomResource handler (runtime log retention)",
        }
        for child in self.node.children:
            for id_prefix, what in singletons.items():
                if child.node.id.startswith(id_prefix):
                    ack(child, "IAM4", f"{what} runs at deploy/destroy time only and uses AWSLambdaBasicExecutionRole; "
                                       "the project's own roles use inline least-privilege policies.", basic)


def _retention(days: int) -> tuple[logs.RetentionDays, int]:
    """Nearest supported retention period, as the CDK enum and as a number of days."""
    table = {1: logs.RetentionDays.ONE_DAY, 3: logs.RetentionDays.THREE_DAYS, 5: logs.RetentionDays.FIVE_DAYS,
             7: logs.RetentionDays.ONE_WEEK, 14: logs.RetentionDays.TWO_WEEKS, 30: logs.RetentionDays.ONE_MONTH,
             90: logs.RetentionDays.THREE_MONTHS}
    n = min(table, key=lambda d: abs(d - days))
    return table[n], n
