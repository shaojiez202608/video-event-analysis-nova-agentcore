"""Structured logs and CloudWatch Embedded Metric Format (EMF) lines (FR-7, FR-8).

Log policy: only identifiers, counts, enumerated values and timings. Never log
frames, model text, descriptions, summaries or questions.
"""

from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger("video_events")

_ALLOWED_TYPES = (int, float, bool, type(None))
METRIC_KEYS = ("model_calls", "retries", "validation_failures", "input_tokens", "output_tokens", "images_sent",
               "rejected_events", "total_ms")


def log_event(name: str, **fields) -> None:
    safe = {}
    for k, v in fields.items():
        if isinstance(v, _ALLOWED_TYPES) or (isinstance(v, str) and len(v) <= 200):
            safe[k] = v
    log.info(json.dumps({"event": name, **safe}, ensure_ascii=False))


def emf_line(metrics: dict, namespace: str | None = None, strategy: str = "") -> str:
    ns = namespace or os.environ.get("METRICS_NAMESPACE", "VideoEventAnalysis")
    doc = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": ns,
                    "Dimensions": [["Strategy"]],
                    "Metrics": [{"Name": k, "Unit": "Milliseconds" if k.endswith("_ms") else "Count"}
                                for k in METRIC_KEYS],
                }
            ],
        },
        "Strategy": strategy or str(metrics.get("strategy", "")),
        **{k: metrics.get(k, 0) for k in METRIC_KEYS},
    }
    return json.dumps(doc)
