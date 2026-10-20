# DEPLOYMENT-LOG

账号 ID 一律以 `<ACCOUNT_ID>` 代替；本文件不含凭证。

## 基本信息

| 项 | 值 |
|---|---|
| 区域 | us-east-1 |
| 栈名 | `proto-video-event-analysis` |
| 参数文件 | `config/params.json`（`removal_policy=destroy`，`orchestrator=agentcore`） |
| 标签 | `Project=video-event-analysis`、`Purpose=prototype`、`Teardown=after-test` |
| 部署命令 | `./scripts/build_bundle.sh && npx -y aws-cdk@2 deploy -c params=config/params.json --require-approval never` |
| 首次部署 | 2026-10-20，命令开始 11:34:28Z，栈创建 11:35:36Z |
| 更新部署 | 2026-10-20 11:43Z 完成（修复 Lambda `health` 动作读取 SSM 被拒，见下文） |
| 销毁 | 2026-10-20 11:49:39Z 开始 `npx -y aws-cdk@2 destroy -c params=config/params.json --force`；栈 DELETE_COMPLETE 于 11:49:53Z |

## 资源清单（栈内，来自 `list-stack-resources`）

- `AWS::BedrockAgentCore::Runtime` AgentRuntime（名称 `proto_video_event_agent`，直接代码部署，PYTHON_3_13，网络 PUBLIC）
- `AWS::Lambda::Function` ×3：`proto-video-dispatcher`（arm64, python3.13）；CDK 托管的 BucketNotificationsHandler 与 S3AutoDeleteObjects provider
- `AWS::Lambda::EventInvokeConfig`（异步重试 0）、`AWS::Lambda::Permission`
- `AWS::IAM::Role` ×4、`AWS::IAM::Policy` ×3
- `AWS::S3::Bucket` ×2（媒体桶、访问日志桶）、`AWS::S3::BucketPolicy` ×2
- `Custom::S3AutoDeleteObjects` ×2、`Custom::S3BucketNotifications` ×1
- `AWS::Logs::LogGroup`（`/aws/lambda/proto-video-dispatcher`，保留 7 天）
- `AWS::SSM::Parameter`（`/proto-video/pipeline-config`）
- `AWS::CDK::Metadata`

栈外、由服务在运行时创建的：AgentCore Runtime 日志组 `/aws/bedrock-agentcore/runtimes/proto_video_event_agent-<id>-DEFAULT`（保留期为「永不过期」）、AgentCore 工作负载身份 `proto_video_event_agent-<id>`、两个 CDK 托管 Lambda 的日志组、CloudWatch 自定义指标（命名空间 `VideoEventAnalysis`）。

## 验证步骤与结果

1. `scripts/e2e_aws.py --all`（16 段合成视频）：
   - AgentCore Runtime `health`：`machine=aarch64`、`libc=glibc-2.34`、Python 3.13.9、PyAV 19.0.1 可导入 → **arm64 上抽帧依赖可用（已核实）**。
   - Lambda `health`：首次部署时失败（`AccessDeniedException`：`health` 动作先读取 SSM 配置，而 agentcore 模式下 Lambda 角色按最小权限没有 `ssm:GetParameter`）。修复方式：`health` 不读配置也不调用 AWS；重建并更新部署后再测：`machine=aarch64`、`glibc-2.34`、Python 3.13.15、PyAV 19.0.1、mjpeg 编码器可用 → **通过**。没有为此放宽 IAM。
   - 上传 16 个对象到 `uploads/` → S3 事件 → 分发 Lambda → `InvokeAgentRuntime` → Nova 2 Lite（`us.amazon.nova-2-lite-v1:0`，frames 策略 2 s/10 s 分段，强制工具输出）→ `results/`：**16/16 产出结果，全部通过结果 schema 校验，0 个 fallback**。
   - 评分（真实模型、部署环境）：precision 0.476、recall 0.625、F1 0.541、平均 tIoU 0.758、场景准确率 1.0、无事件片段（含提示注入片段 `s14`）误报 0。逐片段结果见 VERIFICATION.md。
   - `ask`（中文「火情发生在几点？」、英文「Did anyone come to the front door today?」）：Nova 调用 1 次只读工具 `search_events`，回答只用工具结果，人员描述为「a person」，没有身份信息。
   - `search`「找宠物出现的片段」：返回 3 条 `pet_activity`。
   - `daily_summary` 2026-10-19：21 个事件，风险概览、异常时段、推荐录像与三语文本均生成。
   - 直接调用 `analyze` 并覆盖策略参数：通过。
   - 越权防护：`analyze` 指定非本栈桶 → `PermissionError`。
2. 更新部署后，复跑 `e2e_aws.py --clips s05_fire_yard s14_injection_text s03_delivery`：3/3 产出结果，F1 1.0。
3. 日志检查：分发 Lambda 与 Runtime 日志里只有 id、计数、枚举值和耗时（`dispatch_done`、`video_analyzed`、EMF 行）；用 `filter-log-events` 搜索 `visible`、`summary`、`description`、`question` 均 0 条 → FR-7「日志不含画面内容」**已在真实环境核实**。

## 销毁与残留核对

`scripts/verify_cleanup.sh us-east-1`（只读）逐类检查：

| 类别 | 第一次核对（destroy 后） | 处理 | 最终 |
|---|---|---|---|
| CloudFormation 栈 | 不存在（DELETE_COMPLETE） | — | 无 |
| S3 桶（含对象与版本） | 无 `proto-video-*` 桶（auto-delete 已清空并删除；未开版本控制） | — | 无 |
| 日志组 | 残留 3 个：两个 CDK 托管 Lambda 的 `/aws/lambda/proto-video-event-analysis-…` 日志组，以及 `/aws/bedrock-agentcore/runtimes/proto_video_event_agent-<id>-DEFAULT` | 逐个 `aws logs delete-log-group` 删除（均为本项目运行时产生） | 无 |
| KMS 密钥 | 本栈未创建；按标签查询无结果（无计划删除状态的密钥） | — | 无 |
| ECR 镜像/仓库 | 未使用容器；查询无结果 | — | 无 |
| IAM 角色与策略 | 无 `proto-video-*` 角色/客户托管策略 | — | 无 |
| Lambda | 无 `proto-video-*` 函数 | — | 无 |
| API Gateway（REST/HTTP） | 未创建；查询无结果 | — | 无 |
| AgentCore Runtime / 工作负载身份 | 无 `proto_video*` runtime；工作负载身份已随 runtime 删除 | — | 无 |
| SSM 参数 | 无 `/proto-video/*` | — | 无 |
| 按标签 `Project=video-event-analysis` / `Teardown=after-test` | 无 | — | 无 |

无法删除或按规则未删除的项目（如实记录）：

- **CDK bootstrap 资产桶中的部署资产对象**：两次 `cdk deploy` 各上传了 AgentCore 代码 zip、Lambda 代码包和模板（最多 6 个对象）到共享的 CDK bootstrap 资产桶。这个桶属于 CDKToolkit，按要求不修改 CDKToolkit。第一次部署的资产对象键已无法从本地准确还原，而共享桶里还有其他栈的资产，误删风险高，所以未删除。
- **CloudWatch 自定义指标**：EMF 产生了 8 个 `VideoEventAnalysis` 命名空间下的指标。CloudWatch 指标无法删除，没有新数据时会自动过期（[CloudWatch 指标概念](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html)）。它们不是可删除的资源，也不是长期运行的计算资源。

结论：本项目创建的栈及栈外运行时资源已清理，无残留；仍存在的只有上面两项，即共享 CDK bootstrap 桶中的资产对象和不可删除、会自动过期的 CloudWatch 指标。
