# Prior art

Search date: 2026-10-20. Sources: aws-samples, awslabs and aws-solutions-library-samples
GitHub organisations, AWS documentation, AWS blogs. Every link was opened on the search date.
Named customer case studies were excluded on purpose.

## Closest assets

| Asset | Covers | Does not cover |
|---|---|---|
| [Bedrock Video Understanding tool](https://github.com/aws-samples/sample-bedrock-video-understanding) and its IP-camera event detection lab notebook ([notebook](https://github.com/aws-samples/sample-bedrock-video-understanding/blob/main/source/analytics/sample/chinese_notebooks/lab1-step2-video-understanding-ip-camera-event-detection.ipynb)) | A CDK-deployed tool with frame-based, shot/clip-based and embedding workflows. Compares models and settings, with token usage and cost estimates per video. An AgentCore chatbot recommends workflows. The notebook gets Nova 2 Lite to return a JSON schema with an `event_type` enum (package theft / delivery / no event), a time range and evidence. | No accuracy measurement against ground truth, so no calls-vs-accuracy experiment. No labelled synthetic evaluation set. No validation after the model responds (time bounds, rejection, fallback). No risk level or scene type. No Q&A or summaries over accumulated events. AgentCore is used only for the advisor chatbot. **This is the closest asset.** |
| [Camera edge data intelligence with Bedrock](https://github.com/aws-samples/sample-camera-edge-data-intelligence-transformation-with-bedrock) | A full camera platform: RTSP/RTMP/KVS/S3 ingest, a YOLO pre-filter, and Bedrock detectors that tag images and clips against registered tag groups. Adds notifications, OpenSearch search, timelines and reports. | No structured event schema with risk level, start/end and scene. No strict validation or fallback. No strategy comparison or evaluation. No AgentCore Runtime. It is a heavy stack (ECS, KVS, OpenSearch). |
| [AI driver safety helper](https://github.com/aws-samples/sample-ai-driver-safety-helper) | Multi-camera vehicle clips sent to Nova Pro as synchronised frames. JSON output with severity and summary. Synthetic clips generated with a video model. | No type whitelist or time ranges. No validation. No strategy or accuracy evaluation. No AgentCore. |
| [Nova video classify and tag](https://github.com/aws-samples/sample-amazon-nova-video-classify-tag) | Nova classifies video into a fixed taxonomy plus tags, calibrated with embeddings, with built-in accuracy evaluation. | Not camera or security events. No temporal localisation, risk level, sampling strategy study or AgentCore. |
| [Guidance for media extraction and dynamic content policy](https://github.com/aws-solutions-library-samples/guidance-for-media-extraction-and-dynamic-content-policy-framework-on-aws) | Configurable frame sampling, removal of similar frames, shot analysis, and an LLM evaluation sandbox for moderation and classification. | Not about security events, with no event schema, validation or accuracy-vs-calls study. |
| [Guidance for Media2Cloud](https://github.com/aws-solutions-library-samples/guidance-for-media2cloud-on-aws) | Media ingest and analysis with dynamic frame selection, scene detection and search. | Not about security events. No Nova, AgentCore, whitelist validation or synthetic evaluation. |
| [Video security analysis for privileged access management](https://aws.amazon.com/blogs/machine-learning/video-security-analysis-for-privileged-access-management-using-generative-ai-and-amazon-bedrock/) ([repo](https://github.com/aws-samples/video-security-analysis-for-pam-using-generative-ai)) | Screen recordings sampled into fixed batches, then a risk narrative is aggregated across batches. | Free-text output with no schema or whitelist, and no physical cameras, Nova or evaluation. |
| [Media video agent on AgentCore](https://github.com/aws-samples/sample-media-video-agent-amazon-bedrock-agentcore) | An agent on AgentCore Runtime with Memory, ffmpeg in a container, and chat Q&A over a video library. | Not Nova. No structured security events, validation or evaluation. |
| [Nova 2 user guide: video understanding](https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-multimodal-models.html), [Nova samples: temporal reasoning](https://github.com/aws-samples/amazon-nova-samples/tree/main/multimodal-understanding/repeatable-patterns/25-temporal-reasoning-premier), [Bedrock Data Automation video output](https://docs.aws.amazon.com/bedrock/latest/userguide/bda-ouput-video.html) | Baselines: the model's own sampling behaviour, temporal prompts, and managed video summaries and chapters. | No security-event taxonomy, validation layer or strategy evaluation. |

## Assessment

Overlap is **moderate, not high**. The closest asset (the video understanding tool and its
IP-camera notebook) already shows "Nova + JSON schema with an event-type enum + time range". The
camera platform sample already covers "camera to Bedrock tags to search and notify". This project
does not claim "video to events with Nova" as new.

## Differentiators of this project

1. **A reproducible calls-vs-accuracy experiment.** `scripts/compare_strategies.py` measures event
   F1, temporal IoU, short-event recall and no-event cleanliness next to model calls, images and
   tokens, across whole-clip video input and a grid of frame intervals and segment lengths.
   Existing assets report cost or configuration only.
2. **A synthetic, labelled, licence-free evaluation set.** `scripts/gen_synthetic.py` renders
   schematic clips of coloured shapes with text labels and exact ground truth. The set covers
   no-event, single-event, multi-event, noise, night, short-event and prompt-injection clips.
3. **Hard validation as its own layer.** The output passes JSON schema, a closed vocabulary for
   every field, time-range checks with a tolerance, category derived from type, identity-text
   redaction, per-event rejection with reasons, retry and then an explicit `fallback` /
   `needs_review` result. Validation outcomes are counted in the metrics.
4. **A camera-event schema** that holds risk and behaviour type, risk level with floors that can
   only raise the level, scene type, location, non-identity subject, start and end.
5. **AgentCore Runtime hosts the extraction pipeline itself**, with code-defined orchestration
   because the video is untrusted, plus a mock and a Lambda fallback orchestrator.
6. **Lightweight post-processing over the stored events**: localised text from templates, structured
   watch rules, Q&A (mock or Nova with one read-only tool), search and daily summary. All of it
   runs in a small repository without a database or search cluster.

Conclusion: the differentiators are clear and fall within the directions the project brief
lists, so development continued.
