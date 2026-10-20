"""Run the evaluation set with one strategy (FR-6, acceptance 2).

    uv run python scripts/evaluate.py --mode mock
    uv run python scripts/evaluate.py --mode bedrock --strategy video     # real Nova calls
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_events.config import load_config  # noqa: E402
from video_events.evaluation import run_suite  # noqa: E402
from video_events.models import MockVisionModel, make_model  # noqa: E402
from video_events.prompts import SYNTHETIC_EVAL_HINT  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="sample-data/eval/manifest.json")
    ap.add_argument("--config", default="config/params.json")
    ap.add_argument("--mode", choices=["mock", "bedrock"], default="mock")
    ap.add_argument("--strategy", choices=["video", "frames"])
    ap.add_argument("--interval", type=float)
    ap.add_argument("--segment", type=float)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-hint", action="store_true", help="do not add the synthetic-set rendering hint")
    ap.add_argument("--output-mode", choices=["tool", "text"])
    ap.add_argument("--results-dir", help="also write result JSON documents here")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args()
    cfg = load_config(args.config).with_overrides(
        model_mode=args.mode, strategy=args.strategy, output_mode=args.output_mode,
        prompt_hint="" if args.no_hint else SYNTHETIC_EVAL_HINT, frame_interval_s=args.interval, segment_length_s=args.segment)
    model = make_model(cfg) if args.mode == "bedrock" else MockVisionModel()
    summary, per_clip = run_suite(args.manifest, cfg, model, args.only, args.results_dir)
    report = {"mode": args.mode, "model": model.name, "strategy": cfg.strategy,
              "frame_interval_s": cfg.frame_interval_s, "segment_length_s": cfg.segment_length_s,
              "summary": summary, "clips": per_clip}
    print("| clip | TP | FP | FN | scene ok | status | calls | missed | spurious |")
    print("|---|---|---|---|---|---|---|---|---|")
    for c in per_clip:
        print(f"| {c['id']} | {c['tp']} | {c['fp']} | {c['fn']} | {c['scene_ok']} | {c['status']} | "
              f"{c['model_calls']} | {','.join(c['missed']) or '-'} | {','.join(c['spurious']) or '-'} |")
    print()
    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
