"""Localised user-facing text, rendered from structured fields with fixed templates.

The model's free-text summary stays in English. Localised headlines are built
from whitelisted values only, so no model text is translated or re-interpreted.
Translations are a starting point and should be reviewed by native speakers.
"""

from __future__ import annotations

SUPPORTED_LOCALES = ("zh-CN", "en-US", "ru-RU")

EVENT_NAMES = {
    "zh-CN": {
        "fire_smoke": "火情/烟雾",
        "violence": "暴力冲突",
        "intrusion": "区域入侵",
        "forced_entry": "撬门/破坏门锁",
        "suspicious_loitering": "可疑徘徊",
        "theft_indication": "疑似盗窃",
        "person_lingering": "人员徘徊",
        "package_delivery": "快递到访",
        "entry_exit": "进出门",
        "vehicle_parking": "车辆停靠",
        "object_carrying": "物品搬运",
        "pet_activity": "宠物活动",
    },
    "en-US": {
        "fire_smoke": "Fire or smoke",
        "violence": "Violence",
        "intrusion": "Intrusion",
        "forced_entry": "Forced entry",
        "suspicious_loitering": "Suspicious loitering",
        "theft_indication": "Possible theft",
        "person_lingering": "Person lingering",
        "package_delivery": "Package delivery",
        "entry_exit": "Entry/exit",
        "vehicle_parking": "Vehicle parking",
        "object_carrying": "Object carrying",
        "pet_activity": "Pet activity",
    },
    "ru-RU": {
        "fire_smoke": "Огонь или дым",
        "violence": "Насилие",
        "intrusion": "Вторжение",
        "forced_entry": "Взлом двери",
        "suspicious_loitering": "Подозрительное нахождение",
        "theft_indication": "Признаки кражи",
        "person_lingering": "Человек задерживается",
        "package_delivery": "Доставка посылки",
        "entry_exit": "Вход/выход",
        "vehicle_parking": "Парковка автомобиля",
        "object_carrying": "Перенос предмета",
        "pet_activity": "Активность питомца",
    },
}

LEVEL_NAMES = {
    "zh-CN": {"emergency": "紧急", "high": "高风险", "attention": "需关注", "low": "低风险", "none": "无"},
    "en-US": {"emergency": "Emergency", "high": "High risk", "attention": "Needs attention", "low": "Low risk",
              "none": "None"},
    "ru-RU": {"emergency": "Экстренно", "high": "Высокий риск", "attention": "Требует внимания",
              "low": "Низкий риск", "none": "Нет"},
}

SCENE_NAMES = {
    "zh-CN": {"home": "看家", "pet": "看宠物", "elderly": "看老人", "child": "看小孩", "store": "看店铺",
              "yard": "看院子", "fish_pond": "看鱼塘", "unknown": "未知场景"},
    "en-US": {"home": "Home", "pet": "Pet", "elderly": "Elderly care", "child": "Child", "store": "Store",
              "yard": "Yard", "fish_pond": "Fish pond", "unknown": "Unknown"},
    "ru-RU": {"home": "Дом", "pet": "Питомец", "elderly": "Пожилой человек", "child": "Ребёнок",
              "store": "Магазин", "yard": "Двор", "fish_pond": "Пруд", "unknown": "Неизвестно"},
}

LOCATION_NAMES = {
    "zh-CN": {"entrance": "门口", "yard": "院子", "indoor": "室内", "storefront": "店铺", "driveway": "车道",
              "pond": "鱼塘", "unknown": "画面中"},
    "en-US": {"entrance": "entrance", "yard": "yard", "indoor": "indoors", "storefront": "storefront",
              "driveway": "driveway", "pond": "pond", "unknown": "in view"},
    "ru-RU": {"entrance": "у входа", "yard": "во дворе", "indoor": "в помещении", "storefront": "у магазина",
              "driveway": "на подъезде", "pond": "у пруда", "unknown": "в кадре"},
}

TEMPLATES = {
    "zh-CN": {
        "headline": "{time} {location}：{event}（{level}）",
        "overview_none": "{scene}：未发现需要关注的事件。",
        "overview": "{scene}：发现 {n} 个事件，最高风险等级：{level}。",
    },
    "en-US": {
        "headline": "{time} at {location}: {event} ({level})",
        "overview_none": "{scene}: no notable events.",
        "overview": "{scene}: {n} event(s), highest risk level: {level}.",
    },
    "ru-RU": {
        "headline": "{time} {location}: {event} ({level})",
        "overview_none": "{scene}: значимых событий не обнаружено.",
        "overview": "{scene}: событий — {n}, наивысший уровень риска: {level}.",
    },
}


def _loc(locale: str) -> str:
    return locale if locale in SUPPORTED_LOCALES else "en-US"


def headline(event: dict, locale: str) -> str:
    lc = _loc(locale)
    return TEMPLATES[lc]["headline"].format(
        time=event.get("local_time", f"+{event['start_s']:.0f}s"),
        location=LOCATION_NAMES[lc].get(event.get("location", "unknown"), LOCATION_NAMES[lc]["unknown"]),
        event=EVENT_NAMES[lc][event["type"]],
        level=LEVEL_NAMES[lc][event["risk_level"]],
    )


def overview(scene_type: str, events: list[dict], overall_level: str, locale: str) -> str:
    lc = _loc(locale)
    scene = SCENE_NAMES[lc].get(scene_type, SCENE_NAMES[lc]["unknown"])
    if not events:
        return TEMPLATES[lc]["overview_none"].format(scene=scene)
    return TEMPLATES[lc]["overview"].format(scene=scene, n=len(events), level=LEVEL_NAMES[lc][overall_level])


def localize(result: dict, locales: list[str]) -> dict[str, dict]:
    out = {}
    for lc in locales:
        if lc not in SUPPORTED_LOCALES:
            continue
        out[lc] = {
            "overview": overview(result["scene"]["type"], result["events"], result["overall_risk_level"], lc),
            "headlines": [headline(e, lc) for e in result["events"]],
        }
    return out
