from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


@pytest.fixture(scope="session")
def synthetic_set(tmp_path_factory):
    """A small subset of the synthetic evaluation set, generated once per test session."""
    if not _have_ffmpeg():
        pytest.skip("ffmpeg not available")
    import gen_synthetic

    out = tmp_path_factory.mktemp("eval")
    (out / "videos").mkdir()
    wanted = {"s01_empty_home", "s03_delivery", "s05_fire_yard", "s07_multi_yard", "s12_short_fire",
              "s04_loitering_door", "s13_entry_exit"}
    clips = []
    for sc in gen_synthetic.scenarios():
        if sc.sid not in wanted:
            continue
        fname = f"{sc.recorded_at}_{sc.sid}.mp4"
        gen_synthetic.encode(sc, out / "videos" / fname)
        clips.append({"id": sc.sid, "file": f"videos/{fname}", "s3_key": f"uploads/{sc.camera_id}/{fname}",
                      "camera_id": sc.camera_id, "duration_s": sc.duration, "scene": sc.scene, "night": sc.night,
                      "tags": sc.tags, "expected_events": sc.expected, "optional_types": sc.optional})
    (out / "manifest.json").write_text(json.dumps({"clips": clips}), encoding="utf-8")
    return out


def clip_bytes(synthetic_set, clip_id: str) -> tuple[bytes, dict]:
    doc = json.loads((synthetic_set / "manifest.json").read_text())
    clip = next(c for c in doc["clips"] if c["id"] == clip_id)
    return (synthetic_set / clip["file"]).read_bytes(), clip
