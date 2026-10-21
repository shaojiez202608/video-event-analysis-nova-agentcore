# DECISIONS

方案选择记录。原则：在多个可行方案中选更保守、更安全、依赖更少的一个。

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| D1 | 默认模型 `us.amazon.nova-2-lite-v1:0`（美国地理范围的跨区域推理配置文件），可在 `config/params.json` 改 | `global.` 推理配置文件；`amazon.nova-lite-v1:0` 按需调用；Nova Pro/Premier | `aws bedrock list-foundation-models` 显示 Nova 2 Lite 支持 TEXT/IMAGE/VIDEO 输入，但只支持 INFERENCE_PROFILE 调用；`us.` 配置文件把请求留在美国区域内，比 `global.` 更保守。Lite 的成本维度比 Pro/Premier 低。 |
| D2 | 编排是**代码里固定的顺序**（抽帧 → 模型 → 校验 → 合并 → 后处理 → 存储），托管在 AgentCore Runtime 上；视频分析时不让模型自选工具 | 让 LLM 智能体自由调用工具（Strands 等框架） | 视频是不可信输入（画面里可能有文字形式的提示注入）。分析路径上不给模型任何可执行动作，注入最多只能影响输出 JSON，而输出还要经过硬校验。问答路径只开放一个只读工具 `search_events`，摄像头范围由代码注入。 |
| D3 | AgentCore 用**直接代码部署（zip）**，不用容器 | 容器镜像 + ECR | 本机 Docker daemon 未运行；官方文档说明 zip 包可直接部署且要求 arm64 依赖。省掉 ECR 仓库与镜像，残留更少。 |
| D4 | 抽帧用 **PyAV**（wheel 内置 FFmpeg 库），按 `aarch64-manylinux_2_28` 平台用 `uv pip install --only-binary=:all:` 打包 | 静态 ffmpeg 二进制做 Lambda 层；容器内 apt 装 ffmpeg；不抽帧 | PyPI 上 PyAV 19.0.1 提供 `manylinux_2_28_aarch64` wheel，不需要 Docker 也不需要单独下载第三方二进制；许可为 BSD-3（内置的 FFmpeg 库许可见 OPEN-QUESTIONS Q9）。部署后通过 `health` 动作在 AgentCore Runtime 和 Lambda 中实际导入 PyAV 验证。 |
| D5 | 两种策略：`video`（整段视频一次调用，模型自身采样）和 `frames`（本地按间隔抽帧，按分段长度分组，每次调用不超过 `max_images_per_call` 张图）。**部署默认 `frames`、2 s 间隔、10 s 分段** | 默认 `video`；默认 `frames` 4 s 整段（调用最少）；1 s 间隔；再加「把视频切成小段 mp4 分别送」 | 依据 VERIFICATION 4.2 的重复实测（每个配置 3 次，Nova 2 Lite，合成集 16 段）：`video` 3 次都是 16/16 fallback（必填字段缺失），不可用。2 s/10 s：F1 0.518 ± 0.028，51 次调用；4 s 整段：F1 0.536 ± 0.032，33 次调用；1 s/10 s：F1 0.533 ± 0.019，101 次调用。三者 F1 的差异小于运行间波动，**F1 不能用来区分**。旧记录里 4 s 整段单次 F1 0.645 最高、调用最少，但那是单次结果，重复后不成立。选 2 s 的理由：(1) type recall 2 s 为 0.75（3/3 次），4 s 为 0.625（3/3 次），4 s 稳定地少识别 2 个事件类型；平均 tIoU 0.759 对 0.674；(2) 1.5 s 的短火情（`s12`）在 2 s 下 3/3 次被报出（时间偏移，未计为命中），在 4 s 下 3/3 次完全没有报出，而火情是紧急级别；(3) 1 s 能把短事件 recall 提到 0.5，但调用量和输入 token 翻倍，F1 没有提高。代价是比 4 s 多约 55% 调用和 66% 输入 token。注意：每次调用最多 5 张图，2 s 间隔下 10 s 分段与整段的请求完全相同，**分段长度在这个默认值下不起作用**。结论只针对合成集，需要在真实非生产画面上重测。切段重编码会引入编码器依赖与许可问题，不做。 |
| D6 | 输出默认用 Converse **强制工具调用**（`toolChoice` 指定唯一工具，工具入参 schema 即输出契约），可切换为纯文本 JSON（`output_mode=text`）；两种模式都执行同样的硬校验 | 只靠提示词要求 JSON | 实测纯文本模式下模型常返回散文或代码块，工具模式更稳定。但工具模式也没有保证必填字段齐全，所以硬校验不能省。 |
| D7 | 校验失败策略：整体不可解析/顶层 schema 错 → 重试 `max_retries` 次（默认 1）→ 兜底结果 `status=fallback, needs_review=true, events=[]`；单条事件不合法 → 丢弃并记录在 `rejected`，其余保留（`status=partial`） | 失败即整体丢弃；或尽量「修复」模型输出 | 不猜测、不编造：只修正可由白名单推导的字段（category 由 type 决定），时间只在容差内截断。兜底结果显式标记需人工复核，而不是静默返回「无事件」。 |
| D8 | 风险等级最终值 = max(模型给出, 配置的下限)；只升不降 | 完全由模型决定；完全由规则决定 | 安全类场景漏报代价更高。下限表是占位值，需业务确认（OPEN-QUESTIONS Q3）。 |
| D9 | 多语言文案用固定模板 + 白名单字段渲染，不让模型翻译自由文本 | 再调用一次模型翻译摘要 | 不增加调用量；避免把模型自由文本二次加工；译文需母语者复核。 |
| D10 | 自定义关注规则是结构化配置（地点/主体/类型/等级/本地时段），不是自然语言 | 用 LLM 把「只关注门口人员」解析成规则 | 结构化规则可校验、可测试；自然语言到规则的转换放入 backlog。 |
| D11 | 智能问答：mock 模式用关键字解析 + 模板回答；bedrock 模式用 Nova + 单一只读工具，工具入参先过 JSON schema，返回给模型的只有枚举字段与时间，不含自由文本描述 | 把事件描述原文交给模型 | 防止「视频 → 描述 → 问答」链路上的二次提示注入。 |
| D12 | 结果存 S3 JSON（`results/<CAMERA_ID>/<DATE>/<VIDEO_ID>.json`），不建数据库 | DynamoDB / OpenSearch | 原型规模小；少一类资源少一类残留。规模化检索列入 backlog。 |
| D13 | S3 用 SSE-S3（AES256），不建 KMS 客户托管密钥 | KMS CMK | 满足「服务端加密」；KMS 密钥删除有等待期，会在沙箱留下「计划删除」状态的资源。上生产时按数据分类评估 CMK（handover 加固清单）。 |
| D14 | 不暴露任何公网入口（无 API Gateway/函数 URL）；入口只有 S3 上传事件和 IAM SigV4 调用 `InvokeAgentRuntime` | API Gateway + Cognito | 「默认不暴露公网入口」。演示脚本用 AWS CLI/boto3 以 IAM 身份调用。 |
| D15 | AgentCore Runtime 网络模式 `PUBLIC`（出站访问 AWS 服务端点）；入站只接受 IAM 签名请求 | VPC 模式 | 原型不需要访问私有资源；VPC 模式要多建 VPC/子网/端点，残留与成本维度都增加。上生产可评估 VPC 模式。 |
| D16 | 分发 Lambda 的异步重试设为 0，失败事件进入 SQS 失败目的地（`<PREFIX>-dispatch-failures`） | 默认重试 2 次；不设失败目的地 | 避免同一视频被重复分析（重复调用模型）。请求类错误（坏键、非本栈桶）重试也无用，只记日志；内部错误（Runtime 调用异常、S3/SSM 错误）让调用失败，事件进入队列，可人工重放。 |
| D17 | 资源命名：栈 `proto-video-event-analysis`，Lambda `proto-video-dispatcher`，SSM `/proto-video/pipeline-config`；AgentCore Runtime 名称不允许连字符（CloudFormation schema 模式 `[a-zA-Z][a-zA-Z0-9_]{0,47}`），因此用 `proto_video_event_agent` | — | 项目约定资源以 `proto-video-` 开头（栈名、参数名由 `deploy.stack_name`、`deploy.resource_prefix` 配置）；Runtime 名称是唯一例外，原因是服务的命名约束。 |
| D18 | 可删除策略做成参数：`config/params.json`（沙箱）`removal_policy=destroy`；`config/params.customer.example.json`（交付默认）`retain` | 固定一种 | 沙箱需要能干净销毁，交付环境默认保留数据（项目约定）。 |
| D19 | 抽帧默认不落盘（`persist_frames=false`）；若打开，帧写入 `frames/` 前缀并由生命周期规则按 `frame_retention_days` 删除；原始视频与结果也有各自的生命周期 | 帧一律落盘 | FR-7 隐私默认值。S3 生命周期的最小粒度是天，因此「保留时长」以天配置。 |
| D20 | 合成评测视频用 Pillow 绘制色块+文字标签、本机 ffmpeg（libx264）编码；视频文件不提交，提交生成器与标签 | 提交 mp4；用公开数据集 | 可完全复现、无许可问题、无真实画面；仓库保持小。 |
| D21 | mock 模型是「颜色规则检测器」，只看传给它的帧 | 直接返回标注答案 | 这样 mock 下的抽帧策略对比才有意义（稀疏采样会漏掉短事件、分段会切断上下文）。它不代表真实视觉能力。 |
| D22 | CDK 资产（Lambda 代码目录、AgentCore zip）上传到已有的 CDK bootstrap 资产桶 | 自建代码桶 + BucketDeployment | 不修改 CDKToolkit；资产对象的处理见 DEPLOYMENT-LOG。 |
| D23 | 合成集评测时在系统提示后附加「部署上下文」说明合成画面的绘制约定（`prompt_hint`，由运维配置，不来自视频或请求）；交付参数文件里为空 | 不加说明；为合成集专门改提示词主体 | 色块+文字标签是一种示意画法，不说明的话模型会把它当作「静止的图形界面」。说明只解释画法，不提示答案；在 VERIFICATION 中如实标注。 |
| D24 | 模型输出契约把场景拍平为 `scene_type` / `scene_description` 两个顶层字段 | 嵌套的 `scene` 对象 | 实测模型常把 `scene` 填成字符串或空对象；拍平后更少出错（存储结果中仍是 `scene{type,description}`）。 |
| D25 | 模型调用异常（botocore `ClientError` / `BotoCoreError`）按「该段失败」处理，结果照常写入 S3（`partial` / `fallback`，`fallback_reason=model_error:<code>`）；无法解码的上传也写 `fallback` 结果；`safe_handle` 兜底只返回错误类型名 | 让异常向上抛出 | FR-5：下游失败不能变成静默丢弃或 HTTP 500。SDK 自身已有 adaptive 重试（4 次），流水线不再对客户端错误重试。 |
| D26 | 校验错误信息只含字段路径 + JSON schema 关键字（如 `schema: scene_type: enum`） | 沿用 jsonschema 的 `message` | `message` 会嵌入被拒绝的值（例如整段画面描述），而这段字符串会进入日志、`fallback_reason` 和重试提示。FR-7 要求日志不含画面内容。 |
| D27 | 覆盖参数与配置统一做类型与范围校验（间隔 0.5-60 s、分段 0-900 s、重试 ≤ 3、单次图片 1-20），并加单片段模型调用硬上限 `max_model_calls_per_clip`（默认 100，最大 500，含重试） | 只限制覆盖键 | 防止一次请求把单个片段变成上百次调用；超限在任何调用之前拒绝。900 s 片段按默认 2 s 间隔需要 90 次调用，因此默认上限取 100。 |
| D28 | 配置了自定义关注规则时，`high` / `emergency` 的风险事件仍然一定产生通知（`always_high_risk`），不能被关闭；`local_time` 时段按事件区间与时段是否重叠判断 | 自定义规则完全替换默认规则（旧行为） | 只配「门口人员」时，院子里的紧急火情不应沉默。重叠判断避免 21:59:50 开始、持续到 22:00:40 的事件漏掉夜间规则。 |
| D29 | AgentCore Runtime 日志组由服务在创建 Runtime 时自建；栈用 `AwsCustomResource` 在 Runtime 建好后设置保留期、销毁时删除 | 在栈里声明 `AWS::Logs::LogGroup`；部署后手工设置 | 实测声明同名 LogGroup 会因 `AlreadyExists` 部署失败（DEPLOYMENT-LOG）。自定义资源只获得该日志组的 `PutRetentionPolicy` / `DeleteLogGroup` 权限。 |
| D30 | 抽帧在最后一个不完整区间的中点也采一帧（尾段短于 0.1 s 忽略） | `floor(时长/间隔)`（旧行为） | 旧算法在时长不是间隔整数倍时完全不看片段结尾（3.9 s / 2 s 只采 1.0 s 一帧）。 |
| D31 | 每日摘要、检索与 S3 读取上限：结果超过 `max_objects`（500）或命中超过 `limit` 时返回 `truncated=true` 并在回答里提示 | 静默截断（旧行为） | 不完整的答案必须让用户知道。 |
| D32 | LICENSE 为 MIT-0，版权行 `Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.`；git 作者仍为 `shaojiez <shaojiez2026@gmail.com>` | 个人版权行（审查修复轮的做法） | 规范对齐轮的统一决定（aws-samples 惯例）。 |
| D33 | 参数文件以模板入库（`config/params.example.json`、`config/params.customer.example.json`），真实文件 `config/params.json` 被 gitignore，由 `make init` 复制；synth 前校验全部参数与联动规则；账号 ID 只来自凭证环境，运维身份 ARN 只经 `-c operator_principal_arns` 传入 | 直接提交真实参数文件 | 防止真实配置与账号 ID 入库；非法值在创建任何资源之前失败。 |
| D34 | Runtime 资源策略：只允许分发角色与部署时传入的运维身份调用，其余主体与所有 `*ForUser` 动作显式拒绝；媒体桶拒绝其他主体直接读写对象（`aws:ViaAWSService=false` 时） | 只靠身份策略 | 敏感资源再加一层「只允许预期调用方」。服务代表调用方访问（例如 Bedrock 按 S3 URI 读视频）按调用方身份评估，不被误拒。 |
| D35 | Runtime 执行角色信任策略同时带 `aws:SourceAccount` 与收窄到 `runtime/<名称>-*` 的 `aws:SourceArn`；分发 Lambda 角色信任策略保持 Lambda 官方文档的写法（无条件键） | Lambda 角色也加条件键 | AgentCore 官方文档给出这两个条件键（账号级 `...:*`），本项目进一步收窄；Lambda 执行角色指南没有为执行角色给出这些条件键，贸然添加可能导致函数无法承担角色。理由与链接见 SECURITY-NOTES。 |
| D36 | 删除 Runtime 角色的 `GetWorkloadAccessToken*` 权限；补上官方执行角色中的 `logs:PutResourcePolicy`（限本 Runtime 的日志组） | 保留官方示例的全部动作 | 代码从不获取工作负载令牌或做出站 OAuth；2025-10-13 之后创建的 Runtime 由服务关联角色获取自身令牌。部署验证中调用全部成功（VERIFICATION）。 |
| D37 | 会产生费用的命令（真实模型评测、端到端、故障演练）需要 `ALLOW_PAID=1`；`cdk diff` 含删除或替换时需要 `ALLOW_DESTRUCTIVE=1`；预检只读并要求确认账号末 4 位 | 不设开关 | 规范要求；防止误在错误账号部署或误产生费用。 |
| D38 | 评测集加版本号（manifest `version`），并加故意做错的反例模型（`WrongVisionModel`）：反例必须被评测判为失败，否则评测本身有问题 | 只评测正常模型 | 证明评测能报出失败。 |
| D39 | 共享 CDK bootstrap 资产桶中只删除属于本项目的对象：当前构建按 `cdk.out/<栈>.assets.json`，更早的构建按 `infra/bootstrap-asset-keys.txt`（由当时的 assets.json 或对象内容确认），删除所有版本；CDK 通用 provider 包与 CDKToolkit 栈不动，识别不出的不删并列为残留 | 一律不删；按时间删除 | 规范第 10 条。 |
| D40 | 没有 Docker：继续用直接代码部署（arm64 wheel zip）；ASH 中需要 Docker 或未安装的扫描器标为「未运行」交给 CI；`.dockerignore` 不适用 | 安装 Docker 或大量本地扫描器 | 维护者约定本机不装 Docker。 |
| D41 | 不设置 OpenTelemetry `trace_attributes`、不开启 Transaction Search | 引入 ADOT | 没有使用智能体框架，观测靠结构化日志与 EMF；Transaction Search 是账号级设置，交给账号所有者（OPEN-QUESTIONS Q15）。 |
