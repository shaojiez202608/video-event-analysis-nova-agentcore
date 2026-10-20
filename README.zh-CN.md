# 基于 Amazon Nova 与 Amazon Bedrock AgentCore 的摄像头事件分析

> **Prototype，不用于生产。** 只用于非生产账号中的原型部署，数据仅限合成或已获授权的测试数据。上生产前需要加固的清单见文末。

建议仓库名：`video-event-analysis-nova-agentcore`。[English](README.md)

本项目把家用或小商户摄像头的短视频片段，转成**经过校验的结构化事件**和一段摘要：

- **风险识别**：火情/烟雾、暴力、入侵、撬门、可疑徘徊、疑似盗窃，并给出风险等级（紧急 / 高风险 / 需关注 / 低风险）。
- **行为识别**：人员徘徊、快递到访、进出门、车辆停靠、物品搬运、宠物活动。
- 每个事件都有起止时间、置信度、地点和**非身份**的主体描述（「有人」「快递员模样的人」），不做人脸或身份识别。
- **场景理解**：看家、看宠物、看老人、看小孩、看店铺、看院子、看鱼塘，并附一段摘要。

还包含以下部分：

- **硬校验**：JSON schema、每个字段的白名单、时间范围检查；不合格时重试，仍不合格则显式兜底并标记 `needs_review`。
- **合成评测集**：用 ffmpeg 生成的示意画面，标注精确。覆盖无事件、单事件、多事件、噪声、夜间、短事件和提示注入。
- **抽帧策略对比**：整段视频输入与不同间隔、不同分段的抽帧输入对比，输出调用次数、图片数、token 与 F1、时间 IoU、短事件召回的对照表。
- **后处理**：
  - 多语言文案（中、英、俄）。
  - 结构化的自定义关注规则（「只关注门口人员」「夜间院子有人才提醒」）。
  - 基于已存储事件的智能问答、语义检索和每日摘要。
- **mock 模式**：不依赖 AWS 也能跑通全流程。

## 快速开始（本地，无需 AWS）

```bash
uv sync
uv run python scripts/gen_synthetic.py --out sample-data/eval      # 需要 ffmpeg
uv run pytest
uv run python scripts/run_local.py analyze --all --strategy video
uv run python scripts/run_local.py ask "今天谁到过门口？" --now 2026-10-19T23:59:00Z
uv run python scripts/run_local.py search "找出所有有人在门口停留超过1分钟的视频" --now 2026-10-19T23:59:00Z
uv run python scripts/run_local.py daily --date 2026-10-19
uv run python scripts/compare_strategies.py
```

## 部署（沙箱）

```bash
./scripts/build_bundle.sh                      # 不用 Docker，下载 arm64 wheel
npx -y aws-cdk@2 deploy -c params=config/params.json --require-approval never
uv run python scripts/e2e_aws.py --all
npx -y aws-cdk@2 destroy -c params=config/params.json --force && ./scripts/verify_cleanup.sh
```

参数集中在 `config/params.json`。交付用的 `config/params.customer.example.json` 默认保留数据。详见[部署指南](docs/deployment-guide.md)、[演示脚本](docs/demo-script.md)、[交接说明](docs/handover.md)和[清理指南](docs/cleanup.md)。

## 目前的结果

只在合成集上测过，没有测真实场景的准确率，详见 [VERIFICATION.md](VERIFICATION.md)。

- 93 个单元测试全部通过；cdk-nag（AwsSolutions）没有未确认的发现。
- 已在 us-east-1 真实部署并跑通端到端：S3 → Lambda → AgentCore Runtime → Nova 2 Lite → 结果。Runtime 和 Lambda 中都核实了 arm64 下 PyAV 可用，问答、检索、每日摘要也都调用成功。验证完已销毁，并逐类核对了残留（[DEPLOYMENT-LOG.md](DEPLOYMENT-LOG.md)）。
- 真实模型下，帧 + 时间戳输入的输出稳定。整段视频输入常缺必填字段，被校验层判为兜底。所有策略在无事件片段（含注入文字片段）上都没有误报。

## 上生产前需要加固的清单

- 终端用户认证与按用户的摄像头授权。
- 上传与设备身份绑定。
- DLQ、幂等，以及兜底率告警。
- 限流与预算告警。
- 评估 KMS 和 VPC 模式。
- 按数据驻留要求选择推理配置文件范围。
- 风险等级与阈值经业务确认。
- 在有代表性、已获授权的数据上评测。
- 及时给 PyAV/FFmpeg 打补丁，并审查其许可。
- 使用默认保留数据的参数文件。

完整清单见 [docs/handover.md](docs/handover.md)。

## 许可

MIT-0，见 [LICENSE](LICENSE)。
