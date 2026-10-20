"""Video probing and frame sampling with PyAV (FFmpeg libraries bundled in the wheel).

PyAV publishes manylinux aarch64 wheels, so the same code runs on arm64 Lambda
and AgentCore Runtime without a separate FFmpeg binary or a container build.
Frames stay in memory; nothing is written to disk.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from fractions import Fraction

import av

THUMB_W, THUMB_H = 80, 45  # tiny RGB thumbnails, used only by the mock model


@dataclass(frozen=True)
class VideoInfo:
    duration_s: float
    fps: float
    width: int
    height: int
    codec: str


@dataclass(frozen=True)
class SampledFrame:
    t: float  # seconds from clip start
    jpeg: bytes  # resized JPEG sent to the model
    thumb_rgb: bytes  # THUMB_W x THUMB_H rgb24, for the mock model


def _open(src: str | bytes):
    return av.open(io.BytesIO(src) if isinstance(src, bytes | bytearray) else src, mode="r")


def probe(src: str | bytes) -> VideoInfo:
    with _open(src) as container:
        if not container.streams.video:
            raise ValueError("no video stream")
        vs = container.streams.video[0]
        if vs.duration is not None and vs.time_base is not None:
            duration = float(vs.duration * vs.time_base)
        elif container.duration is not None:
            duration = container.duration / av.time_base
        else:
            duration = 0.0
        fps = float(vs.average_rate) if vs.average_rate else 0.0
        return VideoInfo(
            duration_s=round(duration, 3),
            fps=fps,
            width=vs.codec_context.width,
            height=vs.codec_context.height,
            codec=vs.codec_context.name,
        )


# A tail shorter than this (container rounding, a few frames) does not get its own sample.
MIN_TAIL_S = 0.1


def sample_times(duration_s: float, interval_s: float) -> list[float]:
    """One sample at the middle of each interval, including a final partial interval.

    The clip is cut into [0, i), [i, 2i), ... The last piece may be shorter than the interval
    (for example 3.9 s at 2 s -> [0, 2) and [2, 3.9)); it is sampled at its own middle, so the
    end of the clip is covered and every sample stays strictly inside the clip. Tails shorter
    than MIN_TAIL_S are folded into the previous interval.
    """
    if duration_s <= 0 or interval_s <= 0:
        return []
    full = math.floor(duration_s / interval_s + 1e-9)
    tail = duration_s - full * interval_s
    n = full + (1 if tail > MIN_TAIL_S or full == 0 else 0)
    out = []
    for i in range(n):
        start, end = i * interval_s, min(duration_s, (i + 1) * interval_s)
        out.append(round((start + end) / 2, 3))
    return out


def _encode_jpeg(frame: av.VideoFrame, width: int) -> bytes:
    height = max(2, int(round(frame.height * width / frame.width / 2)) * 2)
    small = frame.reformat(width=width, height=height, format="yuvj420p")
    ctx = av.CodecContext.create("mjpeg", "w")
    ctx.width, ctx.height, ctx.pix_fmt = width, height, "yuvj420p"
    ctx.time_base = Fraction(1, 25)
    packets = list(ctx.encode(small)) + list(ctx.encode(None))
    return b"".join(bytes(p) for p in packets)


def _thumb(frame: av.VideoFrame) -> bytes:
    rgb = frame.reformat(width=THUMB_W, height=THUMB_H, format="rgb24")
    plane = rgb.planes[0]
    stride, row = plane.line_size, THUMB_W * 3
    buf = bytes(plane)
    return b"".join(buf[y * stride : y * stride + row] for y in range(THUMB_H))


def sample_frames(src: str | bytes, interval_s: float, width: int = 640) -> list[SampledFrame]:
    info = probe(src)
    wanted = sample_times(info.duration_s, interval_s)
    out: list[SampledFrame] = []
    if not wanted:
        return out
    idx = 0
    last = None
    with _open(src) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            last = frame
            while idx < len(wanted) and frame.time + 1e-6 >= wanted[idx]:
                out.append(SampledFrame(wanted[idx], _encode_jpeg(frame, width), _thumb(frame)))
                idx += 1
            if idx >= len(wanted):
                break
    # Container durations can overshoot the last decodable frame slightly.
    while idx < len(wanted) and last is not None:
        out.append(SampledFrame(wanted[idx], _encode_jpeg(last, width), _thumb(last)))
        idx += 1
    return out
