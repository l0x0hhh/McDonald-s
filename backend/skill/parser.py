"""Validate complete meal requirements against A's shared API model."""
from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from backend.schemas import Requirements, RequirementsDraft


class RequirementError(ValueError):
    """A requirement is missing, unsupported, or inconsistent."""


def parse_requirements(raw: Mapping | Requirements) -> Requirements:
    """Return a complete, strictly validated requirement object."""
    if isinstance(raw, Requirements):
        return raw
    if not isinstance(raw, Mapping):
        raise RequirementError("requirements 必须为对象")
    # A budget without a stated total/per-person basis is not safe to infer.
    missing = [key for key in ("people", "budget_type", "budget_fen") if raw.get(key) is None]
    if missing:
        raise RequirementError("缺少必填需求：" + "、".join(missing))
    if raw.get("dietary_constraints", []) is None:
        raise RequirementError("不辣人数尚未确认")
    try:
        return Requirements.model_validate(dict(raw))
    except ValidationError as exc:
        raise RequirementError("需求字段无效或相互冲突：" + str(exc)) from exc


_NUMBER = r"(?:\d{1,3}|[一二两三四五六七八九十]{1,3})"
_PEOPLE = re.compile(rf"({_NUMBER})\s*(?:个?人|位)")
_NON_SPICY_COUNT = re.compile(
    rf"({_NUMBER})\s*(?:个?人|位)\s*(?:都|里|中)?\s*(?:不吃辣|不能吃辣|不食辣|忌辣|不要辣|不辣)"
)
_NON_SPICY_COUNT_ALT = re.compile(rf"(?:不辣|忌辣)\s*(?:的)?\s*(?:人数|有)?\s*(?:是|为)?\s*({_NUMBER})\s*(?:个?人|位)")
_NON_SPICY_PLAIN = re.compile(r"不吃辣|不能吃辣|不食辣|忌辣|不要辣|不辣")
_DIET_CLEAR = re.compile(r"没有忌口|无忌口|不忌口|取消忌口|不需要忌口|不限制忌口|移除忌口|都能吃辣|可以吃辣|取消不辣|不再忌辣")
_OTHER_DIET = re.compile(r"(?:不吃|不能吃|忌|不要|不含|避开)(?:牛肉|鸡肉|鱼|海鲜|猪肉|乳制品|鸡蛋|花生|坚果)|(?:对)?(?:花生|海鲜|牛奶|鸡蛋|坚果)过敏|乳糖不耐受|素食")
_TOTAL_BUDGET = re.compile(r"(?:总预算|总共|一共|合计|总价|总金额)\s*(?:是|为|约|不超过|控制在)?\s*(\d+(?:\.\d{1,2})?)\s*元?|(?P<after>\d+(?:\.\d{1,2})?)\s*元\s*(?:的)?\s*(?:总预算|总共|合计)")
_PER_BUDGET = re.compile(r"(?:人均|每人|每位)\s*(?:预算|是|为|约|不超过)?\s*(\d+(?:\.\d{1,2})?)\s*元?|(?P<after>\d+(?:\.\d{1,2})?)\s*元\s*(?:每人|每位|人均)")
_BARE_BUDGET = re.compile(r"(?:预算|控制在)\s*(\d+(?:\.\d{1,2})?)\s*元|(?<!\d)(\d+(?:\.\d{1,2})?)\s*元")
_TASTE_WORDS = {
    "beef": r"牛肉|牛堡", "chicken": r"鸡肉|鸡腿|鸡堡", "fish": r"鱼肉|鳕鱼|鱼堡",
    "fried": r"炸物|油炸|炸鸡|薯条", "grilled": r"烤制|烧烤|板烧|烤鸡",
    "sweet": r"甜食|甜点|甜品", "sugar_free": r"无糖|零糖",
}
_PREFERENCE_NEGATIVE = re.compile(r"不喜欢|不想吃|不要|不吃|讨厌|避开")
_FOLLOW_UP = {
    "people": "一共几人用餐（最多 20 人）？",
    "budget": "预算是多少？请说明是总预算还是人均预算。",
    "non_spicy_people": "有几位不吃辣？不辣人数不能超过用餐人数。",
    "dietary_constraints": "目前只能核实不辣餐份。其他忌口无法保证，请确认是否移除该条件。",
    "habit_horizon_months": "希望分析未来几个月的购买习惯？",
}
_FIELD_ORDER = ("people", "budget", "non_spicy_people", "dietary_constraints", "habit_horizon_months")


def _number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if "十" in text:
        left, right = text.split("十", 1)
        if (not left or left in digits) and (not right or right in digits):
            return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(text)


def _fen(text: str) -> int:
    return int(Decimal(text) * 100)


def _current_data(current: RequirementsDraft | Requirements | Mapping[str, Any] | None) -> dict[str, Any]:
    if current is None:
        return RequirementsDraft().model_dump()
    if isinstance(current, (Requirements, RequirementsDraft)):
        return RequirementsDraft.model_validate(current.model_dump()).model_dump()
    if isinstance(current, Mapping):
        return RequirementsDraft.model_validate(dict(current)).model_dump()
    raise ValueError("current 必须是需求草稿或完整需求")


def _set_pending(data: dict[str, Any], name: str) -> None:
    pending = data.setdefault("pending_fields", [])
    if name not in pending:
        pending.append(name)


def _clear_pending(data: dict[str, Any], name: str) -> None:
    data["pending_fields"] = [field for field in data.get("pending_fields", []) if field != name]


def _finish(data: dict[str, Any], source: str) -> dict[str, Any]:
    if data.get("people") is None:
        _set_pending(data, "people")
    else:
        _clear_pending(data, "people")
    if data.get("budget_type") is None or data.get("budget_fen") is None:
        _set_pending(data, "budget")
    else:
        _clear_pending(data, "budget")
    if data.get("dietary_constraints") is None:
        _set_pending(data, "non_spicy_people")
    elif data.get("people") is not None:
        count = sum(row.get("people", 0) for row in data["dietary_constraints"])
        if count > data["people"]:
            _set_pending(data, "non_spicy_people")
        else:
            _clear_pending(data, "non_spicy_people")
    pending = list(dict.fromkeys(data.get("pending_fields", [])))
    pending.sort(key=lambda field: (_FIELD_ORDER.index(field) if field in _FIELD_ORDER else len(_FIELD_ORDER), field))
    data["pending_fields"] = pending
    draft = RequirementsDraft.model_validate(data)
    requirements = None
    if not pending:
        complete = {field: getattr(draft, field) for field in Requirements.model_fields}
        try:
            requirements = parse_requirements(complete)
        except RequirementError as exc:
            raise RequirementError(str(exc)) from exc
    prompts = [_FOLLOW_UP.get(field, f"请确认 {field}。") for field in pending]
    return {
        "requirements": requirements,
        "draft": draft,
        "missing_fields": pending,
        "follow_up": " ".join(prompts) if prompts else None,
        "parse_source": source,
        "notes": list(draft.notes),
    }


def _extract_people(message: str, data: dict[str, Any]) -> None:
    matches = []
    for match in _PEOPLE.finditer(message):
        before, after = message[max(0, match.start() - 3):match.start()], message[match.end():]
        if re.search(r"不是\s*$", before):
            continue
        if re.match(r"\s*(?:中|里)?\s*(?:不吃辣|不能吃辣|忌辣|不辣)", after):
            continue
        matches.append(match)
    if not matches:
        return
    value = _number(matches[-1].group(1))
    if value is None or not 1 <= value <= 20:
        data["people"] = None
        _set_pending(data, "people")
        data["notes"].append("用餐人数只支持 1～20 人，请重新确认。")
    else:
        data["people"] = value
        _clear_pending(data, "people")


def _extract_budget(message: str, data: dict[str, Any]) -> None:
    mentions = []
    for kind, pattern in (("per_person", _PER_BUDGET), ("total", _TOTAL_BUDGET)):
        for match in pattern.finditer(message):
            before = message[max(0, match.start() - 4):match.start()]
            if re.search(r"(?:不是|并非|不按)\s*$", before):
                continue
            mentions.append((match.start(), match.end(), kind, _fen(match.group(1) or match.group("after"))))
    mentions.sort()
    # 同句“不是 150，是 120”与“改成 120”以最后明确值为准。
    correcting = bool(re.search(r"不是|并非|改成|更正|调整为|哦不|应该是", message))
    if len({kind for _, _, kind, _ in mentions}) > 1 and not correcting:
        data["budget_type"] = data["budget_fen"] = None
        _set_pending(data, "budget")
        data["notes"].append("同时出现人均和总预算，请确认本次采用哪一种口径及金额。")
        return
    if len({amount for _, _, _, amount in mentions}) > 1 and not correcting:
        data["budget_type"] = data["budget_fen"] = None
        _set_pending(data, "budget")
        data["notes"].append("本句出现不同预算金额，请确认最终金额。")
        return
    if mentions:
        _, _, kind, amount = mentions[-1]
        data["budget_type"] = kind
        data["budget_fen"] = amount
        _clear_pending(data, "budget")
        return
    if re.search(r"人均|每人|每位", message) and not re.search(r"总预算|总共", message):
        data["budget_type"] = "per_person"
    elif re.search(r"总预算|总共|总价", message):
        data["budget_type"] = "total"
    bare = _BARE_BUDGET.search(message)
    if bare:
        data["budget_fen"] = _fen(bare.group(1) or bare.group(2))
        if data.get("budget_type") is None:
            _set_pending(data, "budget")
            data["notes"].append("金额已记下，请确认是总预算还是人均预算。")
        else:
            _clear_pending(data, "budget")
    elif data.get("budget_type") is not None and data.get("budget_fen") is not None:
        _clear_pending(data, "budget")


def _extract_diet(message: str, data: dict[str, Any]) -> None:
    if _DIET_CLEAR.search(message):
        data["dietary_constraints"] = []
        _clear_pending(data, "non_spicy_people")
        _clear_pending(data, "dietary_constraints")
    matches = []
    for pattern in (_NON_SPICY_COUNT, _NON_SPICY_COUNT_ALT):
        for match in pattern.finditer(message):
            if not re.search(r"不是\s*$", message[max(0, match.start() - 3):match.start()]):
                matches.append(match)
    if matches:
        match = max(matches, key=lambda found: found.end())
        count = _number(match.group(1))
        if count is None or not 0 <= count <= 20:
            data["dietary_constraints"] = None
            _set_pending(data, "non_spicy_people")
            data["notes"].append("不辣人数超出可支持范围，请重新确认。")
        else:
            data["dietary_constraints"] = [{"kind": "non_spicy", "people": count}] if count else []
            _clear_pending(data, "non_spicy_people")
    elif _NON_SPICY_PLAIN.search(message) and not _DIET_CLEAR.search(message):
        if data.get("dietary_constraints") in ([], None):
            data["dietary_constraints"] = None
            _set_pending(data, "non_spicy_people")
    elif "non_spicy_people" in data.get("pending_fields", []):
        # A bare "2 人" answers the previous non-spicy follow-up, not total people.
        bare = re.fullmatch(r"\s*([\d一二两三四五六七八九十]+)\s*(?:个|位)?人?\s*", message)
        if bare:
            count = _number(bare.group(1))
            if count is not None and 0 <= count <= 20:
                data["dietary_constraints"] = [{"kind": "non_spicy", "people": count}] if count else []
                _clear_pending(data, "non_spicy_people")
    if _OTHER_DIET.search(message):
        _set_pending(data, "dietary_constraints")
        data["notes"].append("检测到目前无法核实的忌口，不能保证该条件已满足。")


def _extract_beverage(message: str, data: dict[str, Any]) -> None:
    events: list[tuple[int, int, bool]] = []
    patterns = [
        (True, r"不带(?:任何)?(?:饮料|饮品)|不要(?:任何)?(?:饮料|饮品)|不喝(?:任何)?(?:饮料|饮品)|不需要(?:饮料|饮品)|去掉(?:饮料|饮品)|排除(?:饮料|饮品)|无(?:饮料|饮品)|不要喝的"),
        (False, r"不是不(?:要|喝|带|需要)(?:饮料|饮品)|可以(?:喝|带)?(?:饮料|饮品)|允许(?:饮料|饮品)|需要(?:饮料|饮品)|要喝?(?:饮料|饮品)|加(?:饮料|饮品)|带(?:饮料|饮品)|饮料不限|不排除(?:饮料|饮品)|取消无(?:饮料|饮品)"),
    ]
    for value, pattern in patterns:
        for match in re.finditer(pattern, message):
            events.append((match.end(), len(match.group()), value))
    if events:
        data["exclude_beverage"] = max(events)[2]


def _extract_appetite(message: str, data: dict[str, Any]) -> None:
    patterns = {"light": r"轻饭量|饭量轻|饭量小|胃口小|吃得少|吃不多",
                "normal": r"正常饭量|饭量正常|普通饭量",
                "heavy": r"大饭量|饭量大|胃口大|大胃王|吃得多|很能吃"}
    events = [(m.end(), value) for value, pattern in patterns.items() for m in re.finditer(pattern, message)]
    if events:
        data["appetite"] = max(events)[1]


def _extract_tags(message: str, data: dict[str, Any]) -> None:
    tags = list(data.get("preferred_tags") or [])
    habitual = list(data.get("habitual_tags") or [])
    positive: list[str] = []
    negative: list[str] = []
    for tag, pattern in _TASTE_WORDS.items():
        for match in re.finditer(pattern, message):
            before = message[max(0, match.start() - 9):match.start()]
            after = message[match.end():match.end() + 3]
            if re.search(r"(?:平时|通常|常买|经常买).{0,5}$", before):
                if tag not in habitual:
                    habitual.append(tag)
            elif _PREFERENCE_NEGATIVE.search(before) or re.match(r"\s*改成", after):
                negative.append(tag)
            else:
                positive.append(tag)
    if re.search(r"改成|换成", message) and positive:
        tags = []
    for tag in negative:
        tags = [item for item in tags if item != tag]
    for tag in positive:
        if tag not in tags:
            tags.append(tag)
    data["preferred_tags"] = tags
    data["habitual_tags"] = habitual


def _extract_habits(message: str, data: dict[str, Any]) -> None:
    status_patterns = {
        "none": r"没(?:有)?(?:会员)?卡|没(?:有)?开卡|未开卡|不是会员|无会员卡|无卡",
        "active": r"已有(?:会员)?卡|已经开卡|有(?:会员)?卡|我是会员|会员卡有效",
    }
    events = [(m.end(), status) for status, pattern in status_patterns.items() for m in re.finditer(pattern, message)]
    if events:
        data["membership_status"] = max(events)[1]
    frequency = re.search(rf"(?:每月|每个月|一个月|月均)\s*({_NUMBER})\s*次", message)
    if frequency:
        data["meal_frequency_per_month"] = _number(frequency.group(1))
    horizon = re.search(rf"(?:未来|接下来|分析|看)\s*({_NUMBER})\s*个?月", message)
    if horizon:
        value = _number(horizon.group(1))
        if value is not None and value >= 1:
            data["habit_horizon_months"] = value
            _clear_pending(data, "habit_horizon_months")
        else:
            data["habit_horizon_months"] = None
            _set_pending(data, "habit_horizon_months")


def rule_extract(message: str, current: RequirementsDraft | Requirements | Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Merge fixed Chinese phrases into a draft; ask instead of guessing."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("请输入点餐需求")
    data = _current_data(current)
    data["notes"] = []
    _extract_diet(message, data)
    # A bare count can answer a pending non-spicy question, so parse it before total people.
    answered_non_spicy = bool(re.fullmatch(r"\s*[\d一二两三四五六七八九十]+\s*(?:个|位)?人?\s*", message)
                              and current is not None and "non_spicy_people" in _current_data(current).get("pending_fields", []))
    if not answered_non_spicy:
        _extract_people(message, data)
    _extract_budget(message, data)
    _extract_beverage(message, data)
    _extract_appetite(message, data)
    _extract_tags(message, data)
    _extract_habits(message, data)
    return _finish(data, "rule")
