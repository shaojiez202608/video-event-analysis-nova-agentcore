"""JSON schemas for the model output contract and the final stored result."""

from __future__ import annotations

from .taxonomy import (
    ALL_EVENT_TYPES,
    CONFIDENCE_LEVELS,
    LOCATIONS,
    RISK_LEVELS,
    SCENE_TYPES,
    SUBJECTS,
)

SCHEMA_VERSION = "1.0"

TEXT_MAX = 300  # characters; keeps free text short and bounded

MODEL_EVENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "category",
        "type",
        "start_s",
        "end_s",
        "risk_level",
        "confidence",
        "location",
        "subject",
        "description",
    ],
    "properties": {
        "category": {"type": "string", "enum": ["risk", "behavior"]},
        "type": {"type": "string", "enum": list(ALL_EVENT_TYPES)},
        "start_s": {"type": "number"},
        "end_s": {"type": "number"},
        "risk_level": {"type": "string", "enum": list(RISK_LEVELS)},
        "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
        "location": {"type": "string", "enum": list(LOCATIONS)},
        "subject": {"type": "string", "enum": list(SUBJECTS)},
        "description": {"type": "string", "maxLength": TEXT_MAX},
    },
}

MODEL_OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["scene_type", "scene_description", "events", "summary"],
    "properties": {
        "scene_type": {"type": "string", "enum": list(SCENE_TYPES)},
        "scene_description": {"type": "string", "maxLength": TEXT_MAX},
        # Items are validated one by one in validation.py so that a single bad
        # event is rejected without discarding the whole response.
        "events": {"type": "array", "maxItems": 50},
        "summary": {"type": "string", "maxLength": TEXT_MAX * 2},
    },
}

RESULT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "schema_version",
        "video",
        "status",
        "scene",
        "events",
        "overall_risk_level",
        "summary",
        "rejected",
        "metrics",
    ],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "status": {"enum": ["ok", "partial", "fallback"]},
        "overall_risk_level": {"enum": ["none", *RISK_LEVELS]},
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["event_id", "category", "type", "start_s", "end_s", "risk_level"],
            },
        },
    },
}


def tool_input_schema() -> dict:
    """The output contract as a Converse tool input schema (events items inlined)."""
    import copy

    schema = copy.deepcopy(MODEL_OUTPUT_SCHEMA)
    schema.pop("$schema", None)
    schema["properties"]["events"] = {"type": "array", "items": copy.deepcopy(MODEL_EVENT_SCHEMA)}
    return schema
