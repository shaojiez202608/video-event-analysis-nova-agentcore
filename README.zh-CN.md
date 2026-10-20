# 基于 Amazon Nova 与 Amazon Bedrock AgentCore 的摄像头事件分析

> **免责声明。** 本项目是只用于演示和学习的原型，**不用于生产**。使用方对用它部署的任何内容、使用方式及其后果负责。在接触任何真实数据之前，请与你自己的安全和法务团队评估，并遵循 [AWS 责任共担模型](https://aws.amazon.com/compliance/shared-responsibility-model/)。请使用非生产（最好是可随时丢弃的）账号，数据仅限合成或已获授权的测试数据。上生产前请先看[加固清单](docs/handover.md#hardening-checklist-before-production)。

[English](README.md) · 版本 0.1.0（[CHANGELOG](CHANGELOG.md)）

本项目把家用或小商户摄像头的短视频片段，转成**经过校验的结构化事件**和一段摘要：

- **风险识别**：火情/烟雾、暴力、入侵、撬门、可疑徘徊、疑似盗窃，并给出风险等级（紧急 / 高风险 / 需关注 / 低风险）。
- **行为识别**：人员徘徊、快递到访、进出门、车辆停靠、物品搬运、宠物活动。
- 每个事件都有起止时间、置信度、地点和**非身份**的主体描述（「有人」「快递员模样的人」），不做人脸或身份识别。
- **场景理解**：看家、看宠物、看老人、看小孩、看店铺、看院子、看鱼塘，并附一段摘要。

另外包含：带重试和显式 `needs_review` 兜底的硬校验；带反例对照的合成评测集；抽帧策略对比（整段视频与按间隔抽帧）；多语言文案、结构化关注规则、基于已存储事件的问答、关键字检索和每日摘要；不依赖 AWS 的 mock 模式。

## 架构

```
S3 uploads/ ──事件──> Lambda (arm64) ──InvokeAgentRuntime──> AgentCore Runtime (arm64, zip)
     │                   └─ 失败 ─> SQS 队列                    │  PyAV 抽帧
     │                                                         │  Nova 2 Lite (Converse)
     │                                                         │  校验 → 合并 → 后处理
     └──────────────────────────────── results/<CAMERA_ID>/<DATE>/<CLIP>.json <─┘
运维 ──InvokeAgentRuntime（SigV4，需在 Runtime 资源策略允许范围内）──> ask / search / daily_summary
```

组件、信任边界、部署拓扑、取舍和带日期的事实核对见 [docs/architecture.md](docs/architecture.md)（其中的 mermaid 图是事实来源）。

## 前置条件和成本

- 一个非生产 AWS 账号和区域，其中 Amazon Bedrock AgentCore Runtime 与所配置的 Nova 推理配置文件可用（`make preflight` 会检查），且已完成 CDK bootstrap。
- Python 3.13 与 [uv](https://docs.astral.sh/uv/)、Node.js（用于 `npx -y aws-cdk@2`）、AWS CLI v2、`ffmpeg`（只用于生成合成集）。**不需要 Docker。**
- 一个能部署带 IAM 角色的 CloudFormation 栈的 IAM 身份。

成本：按实际用量计费，不预设免费额度。计费维度：Amazon Bedrock 输入与输出 token（视频帧和图片计为输入 token）、AgentCore Runtime（会话的计算与内存）、Lambda 请求与时长、S3 存储与请求、SQS 请求、CloudWatch Logs 写入与存储及自定义指标。见 [Amazon Bedrock 定价](https://aws.amazon.com/bedrock/pricing/)、[Amazon Bedrock AgentCore 定价](https://aws.amazon.com/bedrock/agentcore/pricing/)、[AWS Lambda 定价](https://aws.amazon.com/lambda/pricing/)、[Amazon S3 定价](https://aws.amazon.com/s3/pricing/)、[Amazon CloudWatch 定价](https://aws.amazon.com/cloudwatch/pricing/)。用量参考：默认配置跑一遍 16 段合成集，模型调用 51 次，输入 token 约 164,000（VERIFICATION.md）。建议给账号配置 [AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create.html) 预算告警。

## 部署

所有参数集中在一个文件里，从模板生成：

```bash
make init                                   # uv sync；把 config/params.example.json 复制为 config/params.json
./scripts/build_bundle.sh                   # 下载 arm64 wheel 并打包代码，不用 Docker
CONFIRM_ACCOUNT=<LAST_4_DIGITS> make deploy # 预检，拒绝删除/替换，然后 cdk deploy
```

`make deploy` 会先跑只读预检（确认账号末 4 位和区域、工具链、服务可用性），校验参数文件（任何非法值都会让 synth 失败），显示 `cdk diff`，发现删除或替换时默认拒绝（需显式 `ALLOW_DESTRUCTIVE=1`），并把部署者的 IAM 身份作为资源策略允许的运维身份传入。账号 ID 永远不写进文件。`config/params.customer.example.json` 是交付用的参数文件（默认保留数据）。每个参数的含义见 [docs/deployment-guide.md](docs/deployment-guide.md)。

## 使用和验证

```bash
uv run python scripts/gen_synthetic.py --out sample-data/eval   # 16 段合成视频
ALLOW_PAID=1 make verify                                         # 端到端（全部片段）+ 故障演练
```

`make verify` 会在 Runtime 和 Lambda 中执行 `health`（arm64 下的 PyAV），把片段上传到 `uploads/<CAMERA_ID>/<YYYYMMDDTHHMMSSZ>_<NAME>.mp4`，等待 `results/…json`，校验并打分，调用 `ask`、`search`、`daily_summary`，检查非本栈桶的越权防护，最后跑故障演练（不合格模型输出、模型调用异常、失败队列、日志扫描）。演示脚本见 [docs/demo-script.md](docs/demo-script.md)。

目前的结果（只在合成集上测过，没有测真实场景准确率；详见 [VERIFICATION.md](VERIFICATION.md)）：

- 真实 Nova 2 Lite（每个配置 3 次）：每 2 秒抽帧 F1 0.518 ± 0.028，51 次调用；每 4 秒 F1 0.536 ± 0.032，33 次调用，但识别出的事件类型更少，且从未报出 1.5 秒的短火情。整段视频输入的输出全部不可用（`fallback`）。所有配置在无事件片段（含注入文字片段）上都没有误报。
- 反例对照（故意做错的模型）F1 0.04，被评测正确判为失败。

## 故障排查

| 现象 | 原因与处理 |
|---|---|
| `config/params.json not found` | 运行 `make init`。 |
| synth 时报 `invalid parameter file: ...` | 列出的值违反 `infra/video_infra/params.py` 中的规则，按提示修改。 |
| 预检停在账号确认 | 把 `CONFIRM_ACCOUNT` 设为目标账号末 4 位；如果账号不对，就停止。 |
| `make deploy` 提示 "deletes or replaces resources" | 先看 diff；确有需要才用 `ALLOW_DESTRUCTIVE=1` 重跑。 |
| 调用 `InvokeAgentRuntime` 报 `AccessDeniedException` | 调用者不是分发 Lambda，也不在 Runtime 资源策略的运维身份里。用该身份执行 `make deploy`（或传 `-c operator_principal_arns=...`）重新部署。 |
| 上传后没有结果 | 对象键不符合 `uploads/<CAMERA_ID>/<YYYYMMDDTHHMMSSZ>_<NAME>.<mp4\|mov\|mkv\|webm>`（日志里是 `request_rejected`），或发生内部错误：查看 `<PREFIX>-dispatch-failures` 队列。 |
| 结果 `status: fallback` | 模型输出未通过校验或模型调用失败；`fallback_reason` 里是字段路径或错误码。 |
| `... calls paid AWS services` | 调用 Amazon Bedrock 的命令需要 `ALLOW_PAID=1`。 |

## 安全与局限

安全控制、IAM 范围、资源策略和每条 cdk-nag 确认见 [SECURITY.md](SECURITY.md) 与 [SECURITY-NOTES.md](SECURITY-NOTES.md)；威胁与残余风险见 [THREAT_MODEL.md](THREAT_MODEL.md)（草稿）。补充功能的覆盖边界：

- **检索**是关键字解析器（中、英、俄），把问题转成对已存储事件的结构化过滤条件。它不是语义检索或向量检索；`search` 动作在 `model_mode=bedrock` 下也不调用模型。只有 bedrock 模式下的 `ask` 会调用 Nova，并且只开放一个只读工具 `search_events`。
- **每日摘要**是对已存储事件的确定性汇总，不由模型撰写。
- **多语言**（zh-CN、en-US、ru-RU）只覆盖模板化的标题、概览行和每日摘要文本；模型输出的自由文本摘要和描述保持英文。
- **关注规则**是流水线配置里的一份全局结构化列表（地点、主体、类型、等级、本地时段），不按用户区分，也不支持自然语言。`high` / `emergency` 风险事件无论规则如何都会通知。
- **事件不跨片段合并**。「有人在门口停留超过 1 分钟」只能匹配单个片段内持续这么久的事件；合成片段只有 30–45 秒，所以这条演示查询返回空。
- 结果触达读取上限时返回 `truncated: true`，回答里也会提示。
- 没有终端用户认证，调用者是 IAM 身份；画面里的文字注入可能导致漏检。见[加固清单](docs/handover.md#hardening-checklist-before-production)。

## 清理

```bash
make destroy         # 先确认栈名，删除数据或 bootstrap 资产对象前再确认一次
make verify-cleanup  # 只读；发现本项目任何残留就以非零退出
```

不要以为命令跑完就删干净了，请务必运行残留核对。`cdk destroy` **不会**自动删除的资源及处理方式：

| 资源 | 处理 |
|---|---|
| CDK 自定义资源 Lambda 的日志组（`/aws/lambda/<STACK>-…`） | `make destroy` 删除 |
| AgentCore Runtime 日志组 `/aws/bedrock-agentcore/runtimes/<RUNTIME_ID>-DEFAULT`（由服务创建） | 销毁时由 `RuntimeLogRetention` 自定义资源删除；若仍存在，`make destroy` 删除 |
| 以 Runtime 命名的工作负载身份（由服务创建） | 通常随 Runtime 删除；若仍存在，`make destroy` 删除 |
| `removal_policy=retain`（交付参数文件）时的媒体桶与访问日志桶 | 连同数据保留；`make destroy` 在第二次确认后才删除 |
| CDK bootstrap 资产桶中本项目的代码包和模板 | `make destroy` 按资产哈希删除（CDK 通用 provider 包和 CDKToolkit 栈不动） |
| CDK bootstrap 栈（CDKToolkit） | 永不改动 |
| CloudWatch 自定义指标（`VideoEventAnalysis`） | 无法删除，没有新数据后自动过期 |

详见 [docs/cleanup.md](docs/cleanup.md)。

## 目录结构

| 路径 | 内容 |
|---|---|
| `src/video_events/` | 核心库：事件清单、schema、校验、抽帧、模型、流水线、后处理、存储、查询 |
| `src/agent/` | AgentCore Runtime 入口 |
| `src/functions/dispatcher/` | S3 触发的分发 Lambda |
| `infra/` | CDK（Python）应用、栈、参数校验、bootstrap 资产键历史 |
| `config/` | 参数模板（`params.example.json` 沙箱，`params.customer.example.json` 交付） |
| `scripts/` | 合成集生成、评测、策略对比、打包、预检、部署、端到端、故障演练、清理、残留核对、密钥扫描金丝雀 |
| `tests/` | 单元测试与基础设施测试 |
| `sample-data/eval/manifest.json` | 带版本的合成集标注（视频由脚本生成） |
| `docs/` | 架构、部署指南、演示、清理、交接、prior art、规范 review 表 |
| `.github/` | CI、手动部署验证 workflow、Dependabot、issue 与 PR 模板、CODEOWNERS |

治理文档：[DECISIONS.md](DECISIONS.md)、[OPEN-QUESTIONS.md](OPEN-QUESTIONS.md)、[THREAT_MODEL.md](THREAT_MODEL.md)、[SECURITY-NOTES.md](SECURITY-NOTES.md)、[VERIFICATION.md](VERIFICATION.md)、[DEPLOYMENT-LOG.md](DEPLOYMENT-LOG.md)、[THIRD-PARTY-LICENSES](THIRD-PARTY-LICENSES)、[AGENTS.md](AGENTS.md)。

## 本地测试（无需 AWS）

```bash
make init
uv run python scripts/gen_synthetic.py --out sample-data/eval      # 需要 ffmpeg
make lint test                                                     # ruff + 单元测试（mock 模型）
make eval-mock                                                     # 评测、反例对照、策略网格
uv run python scripts/run_local.py analyze --all --strategy video
uv run python scripts/run_local.py ask "今天谁到过门口？" --now 2026-10-19T23:59:00Z
uv run python scripts/run_local.py search "找出所有有人在门口停留超过1分钟的视频" --now 2026-10-19T23:59:00Z
uv run python scripts/run_local.py daily --date 2026-10-19
```

## Contributing

见 [CONTRIBUTING](CONTRIBUTING.md)。

## Security

见 [SECURITY](SECURITY.md)。

## License

本项目使用 MIT-0 许可，见 [LICENSE](LICENSE) 文件。
