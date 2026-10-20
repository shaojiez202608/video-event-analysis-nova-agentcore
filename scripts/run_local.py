"""Local mock-mode demo: analyse clips, store results in out/, then search / ask / daily summary.

    uv run python scripts/run_local.py analyze --all
    uv run python scripts/run_local.py ask "今天谁到过门口？" --now 2026-10-19T23:59:00Z
    uv run python scripts/run_local.py search "找宠物出现的片段" --now 2026-10-19T23:59:00Z
    uv run python scripts/run_local.py daily --date 2026-10-19
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from video_events import query  # noqa: E402
from video_events.config import load_config  # noqa: E402
from video_events.models import make_model  # noqa: E402
from video_events.pipeline import VideoMeta, analyze_video  # noqa: E402
from video_events.store import LocalStore  # noqa: E402

CAMS = ["cam-front", "cam-yard", "cam-store", "cam-living", "cam-bedroom", "cam-pond"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["analyze", "ask", "search", "daily"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("--manifest", default="sample-data/eval/manifest.json")
    ap.add_argument("--config", default="config/params.json")
    ap.add_argument("--mode", choices=["mock", "bedrock"], default="mock")
    ap.add_argument("--strategy", choices=["video", "frames"])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="out/local")
    ap.add_argument("--now")
    ap.add_argument("--date", default="2026-10-19")
    ap.add_argument("--cameras", nargs="*", default=CAMS)
    args = ap.parse_args()
    cfg = load_config(args.config).with_overrides(model_mode=args.mode, strategy=args.strategy)
    store = LocalStore(args.out)
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(UTC)

    if args.cmd == "analyze":
        root = Path(args.manifest).parent
        clips = json.loads(Path(args.manifest).read_text())["clips"]
        if args.only:
            clips = [c for c in clips if c["id"] in args.only]
        model = make_model(cfg)
        for c in clips:
            res = analyze_video((root / c["file"]).read_bytes(), VideoMeta.from_key(c["s3_key"]), cfg, model)
            key = store.put(res)
            ev = ", ".join(f"{e['type']}[{e['start_s']:.0f}-{e['end_s']:.0f}s,{e['risk_level']}]" for e in res["events"])
            print(f"{c['id']:<26} {res['status']:<8} scene={res['scene']['type']:<9} {ev or '-'}")
            print(f"{'':<26} zh: {res['localized']['zh-CN']['overview']}  -> {key}")
    elif args.cmd == "ask":
        out = query.ask(args.text or "", store, args.cameras, now, mode=args.mode, model_id=cfg.model_id,
                        utc_offset_minutes=cfg.utc_offset_minutes)
        print(out["answer"])
    elif args.cmd == "search":
        flt = query.parse_query_rules(args.text or "", now, cfg.utc_offset_minutes)
        hits = query.search(store, args.cameras, flt)
        if not hits:
            print("(no matching events)")
        for h in hits:
            print(f"{h['local_time']} {h['camera_id']:<11} {h['type']:<22} {h['duration_s']:>5.0f}s  {h['source_key']}")
    else:
        print(query.dumps(query.daily_summary(store, args.cameras, args.date, cfg.utc_offset_minutes, cfg.locales)))


if __name__ == "__main__":
    main()
