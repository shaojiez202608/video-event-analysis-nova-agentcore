"""Frame-sampling / segmentation strategy comparison (FR-2, acceptance 3).

Runs the evaluation set under a grid of strategies and prints one table with
call volume (model calls, images sent, tokens) next to result quality.

    uv run python scripts/compare_strategies.py                  # mock model, full grid
    uv run python scripts/compare_strategies.py --mode bedrock --grid small   # real Nova calls
    uv run python scripts/compare_strategies.py --mode bedrock --grid d5 --repeat 3   # mean / spread

With the default max_images_per_call=5, "frames every 2s" sends exactly the same requests for
10 s segments and for the whole clip (each 10 s holds 5 frames, and a whole-clip batch is split
every 5 frames anyway), so those two rows differ only by run-to-run variation.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_events.config import load_config  # noqa: E402
from video_events.evaluation import run_suite  # noqa: E402
from video_events.models import MockVisionModel, make_model  # noqa: E402
from video_events.prompts import SYNTHETIC_EVAL_HINT  # noqa: E402

GRIDS = {
    "full": [("video", None, None)] + [("frames", i, s) for i in (0.5, 1.0, 2.0, 4.0) for s in (5.0, 10.0, 0.0)],
    "small": [("video", None, None), ("frames", 1.0, 10.0), ("frames", 2.0, 0.0), ("frames", 4.0, 0.0)],
    # Configurations behind the deployed default (DECISIONS D5).
    "d5": [("frames", 2.0, 10.0), ("frames", 4.0, 0.0), ("frames", 1.0, 10.0), ("video", None, None)],
}
QUALITY = ("f1", "recall", "precision", "mean_tiou", "short_event_recall", "no_event_clean_rate", "scene_accuracy")


def spread(runs: list[dict]) -> dict:
    """Mean, sample standard deviation, min and max of the quality metrics over repeated runs."""
    out: dict = {"runs": len(runs)}
    for k in QUALITY + ("fallback_clips",):
        vals = [r[k] for r in runs]
        out[k] = round(statistics.fmean(vals), 3)
        out[f"{k}_sd"] = round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0
        out[f"{k}_min"], out[f"{k}_max"] = min(vals), max(vals)
    for k in ("model_calls", "images_sent", "input_tokens", "output_tokens"):
        out[k] = round(statistics.fmean(r[k] for r in runs))
    out["clips"] = runs[0]["clips"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="sample-data/eval/manifest.json")
    ap.add_argument("--config", default="config/params.json")
    ap.add_argument("--mode", choices=["mock", "bedrock"], default="mock")
    ap.add_argument("--grid", choices=list(GRIDS), default="full")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-hint", action="store_true", help="do not add the synthetic-set rendering hint")
    ap.add_argument("--output-mode", choices=["tool", "text"])
    ap.add_argument("--repeat", type=int, default=1, help="run each configuration N times (model variance)")
    ap.add_argument("--out", help="write JSON rows here")
    args = ap.parse_args()
    base = load_config(args.config).with_overrides(
        model_mode=args.mode, output_mode=args.output_mode, prompt_hint="" if args.no_hint else SYNTHETIC_EVAL_HINT)
    rows = []
    for strategy, interval, segment in GRIDS[args.grid]:
        cfg = base.with_overrides(strategy=strategy, frame_interval_s=interval, segment_length_s=segment)
        label = "video (whole clip)" if strategy == "video" else (
            f"frames every {interval:g}s, " + ("whole clip" if not segment else f"{segment:g}s segments"))
        runs = []
        for i in range(args.repeat):
            model = make_model(cfg) if args.mode == "bedrock" else MockVisionModel()
            summary, _ = run_suite(args.manifest, cfg, model, args.only)
            runs.append(summary)
            print(f"done: {label} run {i + 1}/{args.repeat} f1={summary['f1']}", file=sys.stderr)
        row = {"strategy": label, **(spread(runs) if args.repeat > 1 else runs[0])}
        if args.repeat > 1:
            row["per_run"] = runs
        rows.append(row)
    cols = ["model_calls", "images_sent", "input_tokens", "output_tokens", "f1", "recall", "precision",
            "mean_tiou", "short_event_recall", "no_event_clean_rate", "scene_accuracy", "fallback_clips"]
    print(f"Mode: {args.mode}. Clips: {rows[0]['clips'] if rows else 0}.")
    print("| strategy | " + " | ".join(cols) + " |")
    print("|---|" + "---|" * len(cols))
    for r in rows:
        def cell(r, c):
            return f"{r[c]} ± {r[c + '_sd']}" if f"{c}_sd" in r and c in QUALITY else str(r[c])
        print(f"| {r['strategy']} | " + " | ".join(cell(r, c) for c in cols) + " |")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"mode": args.mode, "rows": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
