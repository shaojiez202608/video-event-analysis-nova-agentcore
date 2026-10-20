# Demo script

About 15 minutes. Part A needs no AWS account. Part B needs a deployed stack.

## Part A: local, mock mode (no AWS)

1. **Show the input.** Generate the synthetic set and open two clips, for example
   `s03_delivery` and `s14_injection_text`:
   ```bash
   uv run python scripts/gen_synthetic.py --out sample-data/eval
   open sample-data/eval/videos/*_s03_delivery.mp4
   ```
   Say: "These are coloured shapes with labels. The repository contains no real footage. The
   ground truth is in `sample-data/eval/manifest.json`."

2. **Run the pipeline** on every clip in mock mode:
   ```bash
   uv run python scripts/run_local.py analyze --all --strategy video
   ```
   Point out the structured events with times and risk levels, the scene type, and the
   Chinese overview line. Open one result in `out/local/results/...json` and show
   `events[]`, `localized` (zh-CN / en-US / ru-RU), `notifications` (watch rules) and `metrics`.

3. **Hard validation.** Run `uv run pytest tests/test_validation.py -q`, then show
   `test_bad_events_dropped_status_partial` and `test_fallback_after_retries_exhausted` in
   `tests/test_pipeline.py`. Unknown types, reversed or out-of-clip times, and identity-like text
   are rejected or redacted. When the model output cannot be used, the result is a
   `fallback` with `needs_review: true` rather than a silent "no events".

4. **Strategy trade-off.**
   ```bash
   uv run python scripts/compare_strategies.py
   ```
   Explain the table: model calls and images sent against F1, temporal IoU and short-event recall.
   Each call carries at most `max_images_per_call` (5) frames, so even "whole clip" is split into
   5-frame calls and no call sees the whole clip. That image cap, not the segment length, is why
   events that need the whole clip (delivery, theft, loitering) are missed or cut into pieces; at
   2 s spacing the 10 s and whole-clip rows are identical requests. Sparse sampling (every 4 s)
   misses the 1.5 s fire in `s12_short_fire`.

5. **Q&A, search and daily summary over the stored events:**
   ```bash
   uv run python scripts/run_local.py ask "今天谁到过门口？" --now 2026-10-19T23:59:00Z
   uv run python scripts/run_local.py ask "火情发生在几点？" --now 2026-10-19T23:59:00Z
   uv run python scripts/run_local.py search "找出所有有人在门口停留超过1分钟的视频" --now 2026-10-19T23:59:00Z   # empty: no event in one clip lasts > 1 min
   uv run python scripts/run_local.py search "找宠物出现的片段" --now 2026-10-19T23:59:00Z
   uv run python scripts/run_local.py daily --date 2026-10-19
   ```
   Note that answers describe people only as "有人" or "快递员模样的人" and never identify anyone.

## Part B: deployed (AWS, real Nova)

1. Deploy (README "Deploy"), then run (calls Bedrock, so it needs `ALLOW_PAID=1`):
   ```bash
   ALLOW_PAID=1 uv run python scripts/e2e_aws.py --clips s01_empty_home s03_delivery s05_fire_yard s14_injection_text
   ```
2. Walk through the output:
   - The `health` actions report an arm64 machine and the PyAV version inside AgentCore Runtime and Lambda.
   - Uploading to S3 starts processing. The results appear under `results/`.
   - Results are scored against the labels. Show `metrics` (model calls and tokens).
   - `ask` uses Nova with one read-only `search_events` tool. Its camera scope is fixed by code.
   - The guard: `analyze` on a bucket that is not the configured one returns `PermissionError`.
3. Show CloudWatch Logs for the dispatcher. The lines hold only ids, counts and timings, with no descriptions or summaries.
4. Run the real-model evaluation:
   ```bash
   ALLOW_PAID=1 uv run python scripts/evaluate.py --mode bedrock
   ALLOW_PAID=1 make eval-bedrock        # 3 runs per configuration, mean and spread
   ```
5. Show the failure drill (`ALLOW_PAID=1 uv run python scripts/failure_drill_aws.py`) and the negative
   control (`uv run python scripts/evaluate.py --mode mock --negative-control`).
6. Clean up (README "Clean up").
