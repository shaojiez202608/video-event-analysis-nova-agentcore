"""Result storage: one JSON document per clip.

Key layout (same for local directory and S3):
    results/<CAMERA_ID>/<YYYY-MM-DD (UTC)>/<VIDEO_ID>.json
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def _check(part: str) -> str:
    if not part or not set(part) <= _SAFE or part.startswith("."):
        raise ValueError("unsafe key component")
    return part


def result_key(result: dict) -> str:
    v = result["video"]
    day = v["recorded_at"][:10]
    return f"results/{_check(v['camera_id'])}/{day}/{_check(v['video_id'])}.json"


def _days(start: datetime, end: datetime) -> list[str]:
    d, out = start.astimezone(UTC).date(), []
    while d <= end.astimezone(UTC).date():
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class ResultList(list):
    """A list of result documents with a ``truncated`` flag (True when a read cap was hit)."""

    truncated: bool = False

    def __init__(self, items=(), truncated: bool = False):
        super().__init__(items)
        self.truncated = truncated


class EventStore(Protocol):
    def put(self, result: dict) -> str: ...

    def list_results(self, camera_ids: list[str], start: datetime, end: datetime) -> list[dict]: ...


class LocalStore:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    def put(self, result: dict) -> str:
        key = result_key(result)
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return key

    def list_results(self, camera_ids: list[str], start: datetime, end: datetime) -> list[dict]:
        out = ResultList()
        cams = camera_ids or [p.name for p in (self.root / "results").glob("*") if p.is_dir()]
        for cam in cams:
            for day in _days(start, end):
                for p in sorted((self.root / "results" / _check(cam) / day).glob("*.json")):
                    out.append(json.loads(p.read_text(encoding="utf-8")))
        return out


class S3Store:
    def __init__(self, bucket: str, client=None, max_objects: int = 500):
        import boto3

        self.bucket = bucket
        self.s3 = client or boto3.client("s3")
        self.max_objects = max_objects

    def put(self, result: dict) -> str:
        key = result_key(result)
        self.s3.put_object(
            Bucket=self.bucket, Key=key, Body=json.dumps(result, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
        return key

    def list_results(self, camera_ids: list[str], start: datetime, end: datetime) -> list[dict]:
        """At most ``max_objects`` documents; ``.truncated`` is set when more exist."""
        out = ResultList()
        if not camera_ids:
            raise ValueError("camera_ids is required for S3 queries")
        paginator = self.s3.get_paginator("list_objects_v2")
        for cam in camera_ids:
            for day in _days(start, end):
                prefix = f"results/{_check(cam)}/{day}/"
                for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        if len(out) >= self.max_objects:
                            out.truncated = True
                            return out
                        body = self.s3.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read()
                        out.append(json.loads(body))
        return out
