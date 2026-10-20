# DEPLOYMENT-LOG

账号 ID 一律以 `111122223333` 代替；本文件不含凭证。时间均为 UTC。

## 第 1 次记录：审查修复轮（2026-10-20）

### 基本信息

| 项 | 值 |
|---|---|
| 区域 | us-east-1（只用了这一个区域） |
| 栈名 | `proto-video-event-analysis` |
| 代码版本 | 提交「fix(infra): set the runtime log group retention with a custom resource」（审查修复轮最后一个代码提交） |
| 参数文件 | `config/params.json`（`removal_policy=destroy`，`orchestrator=agentcore`，frames 2 s / 10 s，`us.amazon.nova-2-lite-v1:0`） |
| 标签 | `Project=video-event-analysis`、`Purpose=prototype`、`Teardown=after-test` |
| 打包 | `./scripts/build_bundle.sh`（不使用 Docker；`uv pip install --python-platform aarch64-manylinux_2_28 --only-binary=:all:`，命中本机缓存，约 13 s，没有出现下载卡住） |
| 部署命令 | `npx -y aws-cdk@2 deploy -c params=config/params.json --require-approval never` |

### 时间线

| 时间 | 事件 |
|---|---|
| 16:04:50 | 第一次 `cdk deploy` 开始 |
| 约 16:10 | **失败**：`RuntimeLogs`（`AWS::Logs::LogGroup`，名称 `/aws/bedrock-agentcore/runtimes/<runtime id>-DEFAULT`）报 `AlreadyExists`。原因：AgentCore 在创建 Runtime 时自己创建了这个日志组。栈回滚为 `ROLLBACK_COMPLETE` |
| 约 16:13 | `aws cloudformation delete-stack` 删除回滚后的栈；残留核对发现 2 个日志组（服务创建的 Runtime 日志组、CDK 自动删除对象处理函数的日志组），均为本次创建，逐个 `aws logs delete-log-group` 删除 |
| — | 修复：改为 `AwsCustomResource` 在 Runtime 创建后调用 `PutRetentionPolicy`，销毁时 `DeleteLogGroup`（DECISIONS D29） |
| 16:15:50 | 第二次 `cdk deploy` 开始；16:18:55 成功（部署 127 s） |
| 16:19–16:21 | `scripts/e2e_aws.py --all`（报告 `out/v2/e2e_report.json`，未入库） |
| 16:21–16:23 | `scripts/failure_drill_aws.py`（报告 `out/v2/failure_drill.json`，未入库） |
| 16:24:24 | `npx -y aws-cdk@2 destroy -c params=config/params.json --force`；16:26:40 完成 |
| 16:27 | `scripts/verify_cleanup.sh us-east-1`，发现 3 个 CDK 托管处理函数的日志组，逐个删除后复核 |

### 资源清单（栈内，来自 `list-stack-resources`）

- `AWS::BedrockAgentCore::Runtime`（`proto_video_event_agent`，直接代码部署，PYTHON_3_13，网络 PUBLIC）
- `AWS::Lambda::Function` ×4：`proto-video-dispatcher`（arm64，python3.13）；CDK 托管的 BucketNotificationsHandler、S3AutoDeleteObjects provider、AwsCustomResource 处理函数
- `AWS::Lambda::EventInvokeConfig`（异步重试 0，失败目的地 SQS）、`AWS::Lambda::Permission`
- `AWS::SQS::Queue`（`proto-video-dispatch-failures`，SSE-SQS）、`AWS::SQS::QueuePolicy`
- `AWS::IAM::Role` ×5、`AWS::IAM::Policy` ×5
- `AWS::S3::Bucket` ×2（媒体桶、访问日志桶）、`AWS::S3::BucketPolicy` ×2
- `Custom::S3AutoDeleteObjects` ×2、`Custom::S3BucketNotifications` ×1、`Custom::AWS` ×1（`RuntimeLogRetention`）
- `AWS::Logs::LogGroup`（`/aws/lambda/proto-video-dispatcher`，保留 7 天）
- `AWS::SSM::Parameter`（`/proto-video/pipeline-config`）
- `AWS::CDK::Metadata`

栈外、由服务在运行时创建的：Runtime 日志组 `/aws/bedrock-agentcore/runtimes/proto_video_event_agent-<id>-DEFAULT`（部署后实测保留期 7 天，由自定义资源设置）、工作负载身份 `proto_video_event_agent-<id>`、3 个 CDK 托管 Lambda 的日志组（无保留期）、CloudWatch 自定义指标（命名空间 `VideoEventAnalysis`）。

### 验证结果

1. `health`：Runtime 与 Lambda 均为 `aarch64`、glibc 2.34、PyAV 19.0.1，mjpeg 编码器可用，`sample_times(4, 2) = [1.0, 3.0]`。
2. 端到端：16/16 产出结果，全部通过结果 schema 校验，0 个 fallback；F1 0.541（逐片段见 VERIFICATION 4.2）。`ask`、`search`、`daily_summary`、直接 `analyze`、越权防护（非本栈桶 → `PermissionError`）都成功。
3. 故障演练（VERIFICATION 4.3）：不合格输出与模型调用异常都写入了 `fallback` 结果；内部错误进入 SQS 失败队列；SSM 参数已恢复。
4. 日志检查：在分发 Lambda 与 Runtime 日志组中搜索演练标记与所有结果文档中 29 段描述/摘要文本，**0 条命中**；`model_output_invalid` 日志行只有 `reason: "schema: scene_type: enum"` 这类字段路径 + 关键字。这次是在真实环境中按失败路径核实了 FR-7「日志不含画面内容」；它不能证明其他未演练的失败类型下的日志内容。

### 销毁与残留核对

`scripts/verify_cleanup.sh us-east-1`（只读）逐类检查，第一次在 destroy 之后，第二次在删除日志组之后：

| 类别 | destroy 后 | 处理 | 最终 |
|---|---|---|---|
| CloudFormation 栈 | 不存在 | — | 无 |
| S3 桶（含对象版本与删除标记） | 无 `proto-video-event-analysis*` 桶（auto-delete 清空后删除；未开版本控制） | — | 无 |
| 日志组 | 3 个 CDK 托管处理函数的日志组（`/aws/lambda/proto-video-event-analysi-AWS679f…`、`-BucketNotificationsHandl…`、`-CustomS3AutoDeleteObject…`）；Runtime 日志组已被自定义资源删除 | 逐个 `aws logs delete-log-group` | 无 |
| SQS | 无 `proto-video-*` 队列 | — | 无 |
| KMS | 本栈未创建；别名与按标签查询均无结果 | — | 无 |
| ECR | 未使用容器；查询无结果 | — | 无 |
| IAM 角色与客户托管策略 | 无 | — | 无 |
| Lambda | 无 | — | 无 |
| API Gateway（REST/HTTP） | 未创建；查询无结果 | — | 无 |
| AgentCore Runtime / 工作负载身份 | 无 `proto_video*` | — | 无 |
| SSM 参数 | 无 `/proto-video/*` | — | 无 |
| 按标签 `Project=video-event-analysis` | 无 | — | 无 |

**CDK bootstrap 资产桶**（`cdk-hnb659fds-assets-111122223333-us-east-1`，属于 CDKToolkit，本轮按要求不修改，只记录）。按 `cdk.out/proto-video-event-analysis.assets.json` 与两次部署的模板哈希，本轮上传或使用的对象键如下（`head-object` 确认存在）：

| 对象键 | 内容 | 归属 |
|---|---|---|
| `9e165fd8434034f1e0b0a7ef8647a6fefd6c79ae03d11c01efd66617a1a701b6.zip` | AgentCore 代码包（本轮上传，16:05:35） | 本项目 |
| `d0c49c605c0a5d6c85a22ab81e6634e4d786def127802b5f9bcc12af8dd51516.zip` | 分发 Lambda 代码包（本轮上传，16:05:38） | 本项目 |
| `4b7286ab973ec7ea280114ad998cfd02748f32fad9434cff0b95b138f573b1c5.json` | 第一次（失败的）部署的模板 | 本项目 |
| `a2fab645bb0362cff3f1eedca1a8ce48f53e3f9906bad66fbf3a529a98a3a1de.json` | 第二次部署的模板 | 本项目 |
| `faa95a81ae7d7373f3e1f242268f904eb748d8d0fdd306e8a6fe515a1905a7d6.zip` | CDK 通用 S3AutoDeleteObjects provider 包（2025-11-17 已存在，其他栈共用） | CDK 通用，不属于本项目 |
| `9b8fce7ae7f25ef82fdbaf6b72523b99d0875c0c9c826642819fb51f11d9b125.zip` | CDK 通用 AwsCustomResource 处理函数包（本轮之前已存在） | CDK 通用，不属于本项目 |

本项目更早一次部署（本仓库的早期版本）留下的对象，按当时的 `cdk.out/*.assets.json` 识别：`4b31e16937773d871c9df7014f4fe014567e9c722ccd9c3ba665fd81050c4724.zip`、`f38fd6b616f5baab474effead3dca04d5d40b3901d04ac66b75a419c5afb6584.zip`、`14ba07f11619485220cd3418d08c04dacce1981ca565424421167c2913ebebf0.json`，以及更早一次构建的代码包 `36fb1e9eae3d1bc2e722fb4581fa96d6a0c9072e994ee372131be710fcdf94bf.zip`、`3d048c4d5ae72ffd29d0edb0c798bed37849bd42ebdde5627b2c7b4f899f2bbd.zip`（`cdk.out` 中仍有对应的资产目录）。

**CloudWatch 自定义指标**：`VideoEventAnalysis` 命名空间下的指标无法删除，没有新数据时自动过期（[CloudWatch 指标概念](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html)）。

**结论：仍有残留：** CDK bootstrap 资产桶中属于本项目的 4 个本轮对象与 5 个早期对象（上表与上段所列，原因：本轮按要求不修改 bootstrap 资产桶，只记录）；以及不可删除、会自动过期的 CloudWatch 指标。除此之外，本轮创建的栈及栈外运行时资源（日志组、工作负载身份）已全部清理。
