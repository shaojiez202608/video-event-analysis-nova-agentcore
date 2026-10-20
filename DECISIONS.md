# DECISIONS

方案选择记录。原则：在多个可行方案中选更保守、更安全、依赖更少的一个。

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| D1 | 默认模型 `us.amazon.nova-2-lite-v1:0`（美国地理范围的跨区域推理配置文件），可在 `config/params.json` 改 | `global.` 推理配置文件；`amazon.nova-lite-v1:0` 按需调用；Nova Pro/Premier | `aws bedrock list-foundation-models` 显示 Nova 2 Lite 支持 TEXT/IMAGE/VIDEO 输入，但只支持 INFERENCE_PROFILE 调用；`us.` 配置文件把请求留在美国区域内，比 `global.` 更保守。Lite 的成本维度比 Pro/Premier 低。 |
| D2 | 编排是**代码里固定的顺序**（抽帧 → 模型 → 校验 → 合并 → 后处理 → 存储），托管在 AgentCore Runtime 上；视频分析时不让模型自选工具 | 让 LLM 智能体自由调用工具（Strands 等框架） | 视频是不可信输入（画面里可能有文字形式的提示注入）。分析路径上不给模型任何可执行动作，注入最多只能影响输出 JSON，而输出还要经过硬校验。问答路径只开放一个只读工具 `search_events`，摄像头范围由代码注入。 |
| D3 | AgentCore 用**直接代码部署（zip）**，不用容器 | 容器镜像 + ECR | 本机 Docker daemon 未运行；官方文档说明 zip 包可直接部署且要求 arm64 依赖。省掉 ECR 仓库与镜像，残留更少。 |
| D4 | 抽帧用 **PyAV**（wheel 内置 FFmpeg 库），按 `aarch64-manylinux_2_28` 平台用 `uv pip install --only-binary=:all:` 打包 | 静态 ffmpeg 二进制做 Lambda 层；容器内 apt 装 ffmpeg；不抽帧 | PyPI 上 PyAV 19.0.1 提供 `manylinux_2_28_aarch64` wheel，不需要 Docker 也不需要单独下载第三方二进制；许可为 BSD-3（内置的 FFmpeg 库许可见 OPEN-QUESTIONS Q9）。部署后通过 `health` 动作在 AgentCore Runtime 和 Lambda 中实际导入 PyAV 验证。 |
| D5 | 两种策略：`video`（整段视频一次调用，模型自身采样）和 `frames`（本地按间隔抽帧，按分段长度分组，每次调用不超过 `max_images_per_call` 张图）。**部署默认 `frames`、2 s 间隔、10 s 分段** | 再加「把视频切成小段 mp4 分别送」；默认 `video` | 切段重编码会引入编码器依赖与许可问题。默认值来自实测（VERIFICATION 4.2）：在合成集上，整段视频输入常出现必填字段缺失并走兜底，帧 + 时间戳输入稳定。这个结论只针对合成集，需要在真实非生产画面上重测。 |
| D6 | 输出默认用 Converse **强制工具调用**（`toolChoice` 指定唯一工具，工具入参 schema 即输出契约），可切换为纯文本 JSON（`output_mode=text`）；两种模式都执行同样的硬校验 | 只靠提示词要求 JSON | 实测纯文本模式下模型常返回散文或代码块，工具模式更稳定。但工具模式也没有保证必填字段齐全，所以硬校验不能省。 |
| D7 | 校验失败策略：整体不可解析/顶层 schema 错 → 重试 `max_retries` 次（默认 1）→ 兜底结果 `status=fallback, needs_review=true, events=[]`；单条事件不合法 → 丢弃并记录在 `rejected`，其余保留（`status=partial`） | 失败即整体丢弃；或尽量「修复」模型输出 | 不猜测、不编造：只修正可由白名单推导的字段（category 由 type 决定），时间只在容差内截断。兜底结果显式标记需人工复核，而不是静默返回「无事件」。 |
| D8 | 风险等级最终值 = max(模型给出, 配置的下限)；只升不降 | 完全由模型决定；完全由规则决定 | 安全类场景漏报代价更高。下限表是占位值，需业务确认（OPEN-QUESTIONS Q3）。 |
| D9 | 多语言文案用固定模板 + 白名单字段渲染，不让模型翻译自由文本 | 再调用一次模型翻译摘要 | 不增加调用量；避免把模型自由文本二次加工；译文需母语者复核。 |
| D10 | 自定义关注规则是结构化配置（地点/主体/类型/等级/本地时段），不是自然语言 | 用 LLM 把「只关注门口人员」解析成规则 | 结构化规则可校验、可测试；自然语言到规则的转换放入 backlog。 |
| D11 | 智能问答：mock 模式用关键字解析 + 模板回答；bedrock 模式用 Nova + 单一只读工具，工具入参先过 JSON schema，返回给模型的只有枚举字段与时间，不含自由文本描述 | 把事件描述原文交给模型 | 防止「视频 → 描述 → 问答」链路上的二次提示注入。 |
| D12 | 结果存 S3 JSON（`results/<camera>/<date>/<video>.json`），不建数据库 | DynamoDB / OpenSearch | 原型规模小；少一类资源少一类残留。规模化检索列入 backlog。 |
| D13 | S3 用 SSE-S3（AES256），不建 KMS 客户托管密钥 | KMS CMK | 满足「服务端加密」；KMS 密钥删除有等待期，会在沙箱留下「计划删除」状态的资源。上生产时按数据分类评估 CMK（handover 加固清单）。 |
| D14 | 不暴露任何公网入口（无 API Gateway/函数 URL）；入口只有 S3 上传事件和 IAM SigV4 调用 `InvokeAgentRuntime` | API Gateway + Cognito | 「默认不暴露公网入口」。演示脚本用 AWS CLI/boto3 以 IAM 身份调用。 |
| D15 | AgentCore Runtime 网络模式 `PUBLIC`（出站访问 AWS 服务端点）；入站只接受 IAM 签名请求 | VPC 模式 | 原型不需要访问私有资源；VPC 模式要多建 VPC/子网/端点，残留与成本维度都增加。上生产可评估 VPC 模式。 |
| D16 | 分发 Lambda 的异步重试设为 0 | 默认重试 | 避免同一视频被重复分析（重复调用模型）；失败在日志中可见。上生产需加 DLQ/失败目的地（handover）。 |
| D17 | 资源命名：栈 `proto-video-event-analysis`，Lambda `proto-video-dispatcher`，SSM `/proto-video/pipeline-config`；AgentCore Runtime 名称不允许连字符（CloudFormation schema 模式 `[a-zA-Z][a-zA-Z0-9_]{0,47}`），因此用 `proto_video_event_agent` | — | 项目约定资源以 `proto-video-` 开头；Runtime 名称是唯一例外，原因是服务的命名约束。 |
| D18 | 可删除策略做成参数：`config/params.json`（沙箱）`removal_policy=destroy`；`config/params.customer.example.json`（交付默认）`retain` | 固定一种 | 通用要求第 5 节。 |
| D19 | 抽帧默认不落盘（`persist_frames=false`）；若打开，帧写入 `frames/` 前缀并由生命周期规则按 `frame_retention_days` 删除；原始视频与结果也有各自的生命周期 | 帧一律落盘 | FR-7 隐私默认值。S3 生命周期的最小粒度是天，因此「保留时长」以天配置。 |
| D20 | 合成评测视频用 Pillow 绘制色块+文字标签、本机 ffmpeg（libx264）编码；视频文件不提交，提交生成器与标签 | 提交 mp4；用公开数据集 | 可完全复现、无许可问题、无真实画面；仓库保持小。 |
| D21 | mock 模型是「颜色规则检测器」，只看传给它的帧 | 直接返回标注答案 | 这样 mock 下的抽帧策略对比才有意义（稀疏采样会漏掉短事件、分段会切断上下文）。它不代表真实视觉能力。 |
| D23 | 合成集评测时在系统提示后附加「部署上下文」说明合成画面的绘制约定（`prompt_hint`，由运维配置，不来自视频或请求）；交付参数文件里为空 | 不加说明；为合成集专门改提示词主体 | 色块+文字标签是一种示意画法，不说明的话模型会把它当作「静止的图形界面」。说明只解释画法，不提示答案；在 VERIFICATION 中如实标注。 |
| D24 | 模型输出契约把场景拍平为 `scene_type` / `scene_description` 两个顶层字段 | 嵌套的 `scene` 对象 | 实测模型常把 `scene` 填成字符串或空对象；拍平后更少出错（存储结果中仍是 `scene{type,description}`）。 |
| D22 | CDK 资产（Lambda 代码目录、AgentCore zip）上传到已有的 CDK bootstrap 资产桶 | 自建代码桶 + BucketDeployment | 不修改 CDKToolkit；资产对象的处理见 DEPLOYMENT-LOG。 |
