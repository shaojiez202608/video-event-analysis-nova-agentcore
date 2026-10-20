"""Deterministic stand-in for the vision model, for tests and offline demos.

The synthetic evaluation clips (scripts/gen_synthetic.py) draw every entity as a
flat colour block from ``PALETTE``. This module finds those colours in tiny
thumbnails of the sampled frames and applies simple temporal rules to produce
the same JSON contract a real model returns. It only "sees" the frames it is
given, so it reacts to sampling density and segment boundaries the same way a
real model would: short events can be missed and context can be split.

It is not a computer-vision model and is only meaningful on the synthetic set.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .frames import THUMB_H, THUMB_W

PALETTE: dict[str, tuple[int, int, int]] = {
    "person_a": (0, 0, 255),
    "person_b": (0, 255, 255),
    "parcel": (255, 255, 0),
    "box": (255, 128, 192),
    "vehicle": (90, 60, 200),
    "pet": (0, 200, 0),
    "fire": (255, 130, 0),
    "tool": (255, 0, 255),
    "door": (128, 64, 0),
    "restricted": (128, 0, 128),
    "pond": (0, 90, 160),
}
FIRE_FLICKER = ((255, 105, 0), (255, 155, 0))  # both classify as "fire"

# Background colour per scene (day). Night variants are the same hue, darkened.
SCENE_BACKGROUNDS: dict[str, tuple[int, int, int]] = {
    "home": (200, 190, 170),
    "yard": (70, 110, 70),
    "store": (240, 160, 100),
    "pet": (240, 220, 110),
    "elderly": (210, 170, 210),
    "child": (160, 210, 240),
    "fish_pond": (120, 120, 160),
}
NIGHT_FACTOR = 0.25

SCENE_DEFAULT_LOCATION = {
    "home": "entrance",
    "yard": "yard",
    "store": "storefront",
    "pet": "indoor",
    "elderly": "indoor",
    "child": "indoor",
    "fish_pond": "pond",
    "unknown": "unknown",
}

MIN_PIXELS = 3
# Large static shapes need many matching pixels, so anti-aliased edges of other
# shapes or text (which can blend into similar colours) are not mistaken for them.
MIN_PIXELS_BY_CLASS = {"vehicle": 60, "door": 40, "restricted": 40, "pond": 40, "fire": 8, "box": 10}
LINGER_S = 20.0
PARK_MIN_S = 5.0

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 normalised


def _references() -> list[tuple[str | None, tuple[int, int, int]]]:
    refs: list[tuple[str | None, tuple[int, int, int]]] = [(k, v) for k, v in PALETTE.items()]
    refs += [("fire", c) for c in FIRE_FLICKER]
    for bg in SCENE_BACKGROUNDS.values():
        refs.append((None, bg))
        refs.append((None, tuple(int(c * NIGHT_FACTOR) for c in bg)))
    refs += [(None, (0, 0, 0)), (None, (255, 255, 255))]
    return refs


_REFS = _references()
MAX_DIST2 = 80**2


def _classify_px(px: tuple[int, int, int]) -> str | None:
    """Nearest reference colour (entities, backgrounds, text ink); None if background or too far."""
    best, best_d = None, MAX_DIST2
    for name, ref in _REFS:
        d = (px[0] - ref[0]) ** 2 + (px[1] - ref[1]) ** 2 + (px[2] - ref[2]) ** 2
        if d < best_d:
            best, best_d = name, d
    return best


def detect(thumb_rgb: bytes) -> dict[str, Box]:
    """Return {class: bbox} for palette colours present in a thumbnail."""
    hits: dict[str, list[int]] = {}
    counts: dict[str, int] = {}
    for i in range(0, min(len(thumb_rgb), THUMB_W * THUMB_H * 3), 3):
        cls = _classify_px((thumb_rgb[i], thumb_rgb[i + 1], thumb_rgb[i + 2]))
        if cls is None:
            continue
        p = i // 3
        x, y = p % THUMB_W, p // THUMB_W
        b = hits.setdefault(cls, [x, y, x, y])
        b[0], b[1], b[2], b[3] = min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y)
        counts[cls] = counts.get(cls, 0) + 1
    return {
        c: (b[0] / THUMB_W, b[1] / THUMB_H, (b[2] + 1) / THUMB_W, (b[3] + 1) / THUMB_H)
        for c, b in hits.items()
        if counts[c] >= MIN_PIXELS_BY_CLASS.get(c, MIN_PIXELS)
    }


def classify_scene(thumb_rgb: bytes) -> tuple[str, bool]:
    """Scene from the background colour of the top-right corner; also night flag."""
    xs, ys = range(THUMB_W - 10, THUMB_W - 2), range(1, 5)
    acc = [0, 0, 0]
    n = 0
    for y in ys:
        for x in xs:
            i = (y * THUMB_W + x) * 3
            acc[0] += thumb_rgb[i]
            acc[1] += thumb_rgb[i + 1]
            acc[2] += thumb_rgb[i + 2]
            n += 1
    avg = tuple(v / n for v in acc)
    best, best_d, night = "unknown", 1e9, False
    for scene, ref in SCENE_BACKGROUNDS.items():
        for is_night, f in ((False, 1.0), (True, NIGHT_FACTOR)):
            d = sum((avg[k] - ref[k] * f) ** 2 for k in range(3))
            if d < best_d:
                best, best_d, night = scene, d, is_night
    if best_d > 60**2 * 3:
        return "unknown", False
    return best, night


def _overlap(a: Box | None, b: Box | None, pad: float = 0.02) -> bool:
    if not a or not b:
        return False
    return a[0] - pad < b[2] and b[0] - pad < a[2] and a[1] - pad < b[3] and b[1] - pad < a[3]


@dataclass
class _Sample:
    t: float
    det: dict[str, Box]

    @property
    def person(self) -> Box | None:
        return self.det.get("person_a") or self.det.get("person_b")


def _runs(samples: list[_Sample], pred, half: float, duration: float) -> list[tuple[float, float]]:
    runs: list[tuple[float, float]] = []
    start = prev = None
    for s in samples:
        if pred(s):
            if start is None:
                start = s.t
            prev = s.t
        elif start is not None:
            runs.append((start, prev))
            start = None
    if start is not None:
        runs.append((start, prev))
    return [(round(max(0.0, a - half), 2), round(min(duration, b + half), 2)) for a, b in runs]


def _event(etype, cat, level, start, end, location, subject, desc):
    return {
        "category": cat,
        "type": etype,
        "start_s": start,
        "end_s": end,
        "risk_level": level,
        "confidence": "medium",
        "location": location,
        "subject": subject,
        "description": desc,
    }


def analyze_samples(
    times_and_thumbs: list[tuple[float, bytes]], interval_s: float, clip_duration_s: float
) -> dict:
    """Produce a model-contract JSON document from sampled thumbnails."""
    if not times_and_thumbs:
        return {"scene_type": "unknown", "scene_description": "no frames", "events": [], "summary": "No frames."}
    samples = [_Sample(t, detect(th)) for t, th in times_and_thumbs]
    votes = Counter(classify_scene(th) for _, th in times_and_thumbs)
    scene, night = votes.most_common(1)[0][0]
    loc = SCENE_DEFAULT_LOCATION.get(scene, "unknown")
    half = interval_s / 2
    dur = clip_duration_s
    door = next((s.det["door"] for s in samples if "door" in s.det), None)
    restricted = next((s.det["restricted"] for s in samples if "restricted" in s.det), None)
    ev: list[dict] = []

    def add_runs(pred, *args):
        for a, b in _runs(samples, pred, half, dur):
            ev.append(_event(args[0], args[1], args[2], a, b, *args[3:]))

    add_runs(lambda s: "fire" in s.det, "fire_smoke", "risk", "emergency", loc, "fire_smoke",
             "Flickering fire-like region visible.")
    add_runs(lambda s: "person_a" in s.det and "person_b" in s.det and _overlap(s.det["person_a"], s.det["person_b"]),
             "violence", "risk", "emergency", loc, "multiple_people", "Two people in close physical contact.")
    add_runs(lambda s: "tool" in s.det and _overlap(s.det["tool"], door), "forced_entry", "risk", "high",
             "entrance", "person", "A person works at the door with a tool.")
    add_runs(lambda s: _overlap(s.person, restricted), "intrusion", "risk", "high", loc, "person",
             "A person crosses into the restricted area.")
    add_runs(lambda s: "pet" in s.det, "pet_activity", "behavior", "low", loc, "pet", "An animal moves around.")
    add_runs(lambda s: "box" in s.det and _overlap(s.det["box"], s.person), "object_carrying", "behavior", "low",
             loc, "person", "A person carries a box.")
    for a, b in _runs(samples, lambda s: "vehicle" in s.det, half, dur):
        if b - a >= PARK_MIN_S:
            ev.append(_event("vehicle_parking", "behavior", "low", a, b, "driveway" if scene == "yard" else loc,
                             "vehicle", "A vehicle arrives and stops."))

    person_runs = _runs(samples, lambda s: s.person is not None, half, dur)
    first_parcel, last_parcel = "parcel" in samples[0].det, "parcel" in samples[-1].det
    parcel_ts = [s.t for s in samples if "parcel" in s.det]
    for a, b in person_runs:
        if not first_parcel and last_parcel and parcel_ts and a <= parcel_ts[0] + half:
            ev.append(_event("package_delivery", "behavior", "low", a, b, loc, "courier_like_person",
                             "A courier-like person leaves a parcel."))
        if first_parcel and not last_parcel and parcel_ts and a <= parcel_ts[-1]:
            ev.append(_event("theft_indication", "risk", "high", a, min(b, parcel_ts[-1] + half), loc, "person",
                             "A parcel that was present is taken away."))
        in_run = [s for s in samples if a - half <= s.t <= b + half and s.person is not None]
        if in_run and (_overlap(in_run[0].person, door) or _overlap(in_run[-1].person, door)):
            ev.append(_event("entry_exit", "behavior", "low", a, b, "entrance", "person",
                             "A person passes through the door."))
        if b - a >= LINGER_S:
            ev.append(_event("person_lingering", "behavior", "low", a, b, loc, "person",
                             "A person stays in view for a long time."))
            touches, was = 0, False
            for s in in_run:
                now = _overlap(s.person, door)
                touches += int(now and not was)
                was = now
            if touches >= 2:
                ev.append(_event("suspicious_loitering", "risk", "attention", a, b, "entrance", "person",
                                 "A person lingers at the door and touches it repeatedly."))
    ev.sort(key=lambda e: (e["start_s"], e["type"]))
    summary = (
        "No notable activity." if not ev else "Detected: " + ", ".join(sorted({e["type"] for e in ev})) + "."
    )
    return {
        "scene_type": scene,
        "scene_description": f"Synthetic {scene} camera view{' at night' if night else ''}.",
        "events": ev,
        "summary": summary,
    }
