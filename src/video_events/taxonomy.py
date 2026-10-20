"""Closed vocabularies used by the model output contract and by validation.

Every value the model may emit for an enumerated field is listed here. Anything
outside these lists is rejected by ``validation.py``. The defaults below are
placeholders for a generic smart-camera product; the business owner must
confirm them (see OPEN-QUESTIONS.md).
"""

from __future__ import annotations

# Risk events (risk identification).
RISK_EVENT_TYPES: tuple[str, ...] = (
    "fire_smoke",            # visible flames or smoke
    "violence",              # physical altercation between people
    "intrusion",             # a person entering a restricted / fenced area
    "forced_entry",          # tampering with or prying at a door/window/lock
    "suspicious_loitering",  # lingering at an entrance while repeatedly touching the door
    "theft_indication",      # an object that was present is taken away by a person
)

# Behaviour events (behaviour recognition).
BEHAVIOR_EVENT_TYPES: tuple[str, ...] = (
    "person_lingering",   # a person stays in view for a prolonged time
    "package_delivery",   # a courier-like person drops off a parcel
    "entry_exit",         # a person goes in or out through a door
    "vehicle_parking",    # a vehicle arrives and stops
    "object_carrying",    # a person carries a box or other object through the scene
    "pet_activity",       # an animal moves in view
)

EVENT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "risk": RISK_EVENT_TYPES,
    "behavior": BEHAVIOR_EVENT_TYPES,
}

ALL_EVENT_TYPES: tuple[str, ...] = RISK_EVENT_TYPES + BEHAVIOR_EVENT_TYPES


def category_of(event_type: str) -> str | None:
    for cat, types in EVENT_CATEGORIES.items():
        if event_type in types:
            return cat
    return None


# Ordered from lowest to highest.
RISK_LEVELS: tuple[str, ...] = ("low", "attention", "high", "emergency")


def risk_rank(level: str) -> int:
    return RISK_LEVELS.index(level)


def max_level(*levels: str) -> str:
    return max(levels, key=risk_rank)


# Viewing purpose of the camera (scene understanding).
SCENE_TYPES: tuple[str, ...] = (
    "home",        # general home watch (entrance, hallway, living room)
    "pet",         # watching pets
    "elderly",     # watching an elderly family member
    "child",       # watching a child
    "store",       # small shop / storefront
    "yard",        # yard / garden / driveway
    "fish_pond",   # pond or water feature
    "unknown",
)

# Where in the frame the event happens. Used by watch rules ("door only").
LOCATIONS: tuple[str, ...] = ("entrance", "yard", "indoor", "storefront", "driveway", "pond", "unknown")

# Non-identity subject descriptors. There is deliberately no field that can carry a
# name, face description or identity. "courier_like_person" describes appearance only.
SUBJECTS: tuple[str, ...] = (
    "person",
    "courier_like_person",
    "multiple_people",
    "vehicle",
    "pet",
    "object",
    "fire_smoke",
    "unknown",
)

CONFIDENCE_LEVELS: tuple[str, ...] = ("low", "medium", "high")

# Default minimum risk level per event type. The final level is
# max(model_level, floor): code can escalate a model's level but never lowers it.
# PLACEHOLDER mapping, to be confirmed by the business owner (OPEN-QUESTIONS.md Q3).
DEFAULT_RISK_FLOORS: dict[str, str] = {
    "fire_smoke": "emergency",
    "violence": "emergency",
    "forced_entry": "high",
    "intrusion": "high",
    "theft_indication": "high",
    "suspicious_loitering": "attention",
    "person_lingering": "low",
    "package_delivery": "low",
    "entry_exit": "low",
    "vehicle_parking": "low",
    "object_carrying": "low",
    "pet_activity": "low",
}
