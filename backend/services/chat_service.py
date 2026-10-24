"""需求解析服务（A 职责：编排与兜底，不是正式 NLP）。

与 B 的模块契约（B 接手 backend/llm.py 时实现以下入口即可）：

    async def extract_requirements(
        message: str,
        current: RequirementsDraft | None,
    ) -> dict
    # 返回 {
    #   "draft": RequirementsDraft,          # 本轮合并后的部分需求（推荐）
    #   "requirements": Requirements | None, # 必填项齐全时的完整需求
    #   "missing_fields": list[str],         # 仍缺失的字段名
    #   "follow_up": str | None,             # 需要追问时给用户的话术
    #   "parse_source": "llm",
    #   "notes": list[str],
    # }

B 模块不存在或异常时，回退到本文件内置的规则解析（parse_source=rule）。
规则解析只认固定句式，不能声称具备语义理解；解析不出的字段进入
missing_fields 并触发 follow_up。

多轮状态：current 允许缺字段（草稿）。已收集的信息跨轮保留，
新一句里提到的字段才覆盖旧值；出现矛盾（如不吃辣人数超过总人数）
必须追问更正，不能静默收敛。
"""

from __future__ import annotations

import importlib
import re
from typing import Any, Optional

from ..schemas import ChatIn, ChatOut, DietaryConstraint, Requirements, RequirementsDraft

_CN_NUM = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

_PEOPLE_PATTERNS = [
    re.compile(r"(\d{1,3})\s*(?:个\s*)?人"),
    re.compile(r"([一二两三四五六七八九十]{1,3})\s*(?:个\s*)?人"),
]
# 更正句式：「不是 6 个人，是 4 个人」「改成 4 人」。优先于句中先出现的旧值
_PEOPLE_CORRECTION = re.compile(
    r"(?:不是|改为|改成|换成|其实是|应该是|是)\s*(\d{1,3})\s*(?:个\s*)?人"
)
_PER_PERSON_PATTERN = re.compile(r"(?:人均|每人|一个人?均)\s*(\d+(?:\.\d+)?)\s*元?")
_TOTAL_BUDGET_PATTERN = re.compile(
    r"(?:总预算|预算|一共|总共|合计|以内|封顶)\s*(\d+(?:\.\d+)?)\s*元"
)
_BUDGET_CORRECTION = re.compile(
    r"(?:不是|改为|改成|换成|其实是|应该是)\s*(\d+(?:\.\d+)?)\s*元"
)
_NEGATION_BUDGET = re.compile(r"(?:不是|改为|改成|换成)\s*\d+(?:\.\d+)?\s*元")
_NEGATION_PEOPLE = re.compile(r"(?:不是|改为|改成|换成)\s*\d{1,3}\s*(?:个\s*)?人")
_NON_SPICY_WITH_COUNT = [
    re.compile(r"(\d{1,2})\s*(?:个\s*)?人\s*(?:不|不能|不吃|不要)\s*吃?\s*辣"),
    re.compile(r"([一二两三四五六七八九十]{1,3})\s*(?:个\s*)?人\s*(?:不|不能|不吃|不要)\s*吃?\s*辣"),
]
_NON_SPICY_PLAIN = re.compile(r"(?:不|不能|不吃|不要)\s*吃?\s*辣")
_HEAVY_HINT = re.compile(r"能吃|胃口大|大胃|吃得多|饭量大")
_LIGHT_HINT = re.compile(r"吃得少|胃口小|饭量小|吃不多")
# 不带饮料：整单不含饮品
_NO_DRINK = re.compile(
    r"不(?:带|要|喝|点|加)?\s*(?:饮料|饮品|喝的|可乐|奶茶|咖啡)"
    r"|不喝(?:饮料|东西|可乐)"
    r"|不要饮料|不要饮品|去饮料|含饮料.*不"
)
_ACTIVE_CARD = re.compile(r"(?:已经?|已)?\s*(?:开了?|有|办了?|买了?)\s*(?:麦金卡|会员卡|卡)")
_NO_CARD = re.compile(r"(?:没有|没办|没买|不是)\s*(?:麦金卡|会员卡|会员)|没有卡")

# 偏好标签：只认第一批约定的七个值，中文说法映射到同一套标签
_TAG_KEYWORDS = {
    "beef": ("牛肉", "牛堡", "安格斯"),
    "chicken": ("鸡肉", "鸡腿", "鸡排", "板烧", "脆汁鸡"),
    "fish": ("鱼", "鳕鱼"),
    "fried": ("炸", "脆", "薯条"),
    "grilled": ("烤", "板烧", "煎"),
    "sweet": ("甜", "派", "圆筒", "麦旋风", "新地"),
    "sugar_free": ("无糖", "不加糖", "零卡"),
}


def _cn_to_int(text: str) -> Optional[int]:
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return None
    if text == "十":
        return 10
    if len(text) == 2 and text[0] == "十":
        return 10 + _CN_NUM.get(text[1], 0)
    if len(text) == 2 and text[1] == "十":
        return _CN_NUM.get(text[0], 1) * 10
    if len(text) == 3 and text[1] == "十":
        return _CN_NUM.get(text[0], 1) * 10 + _CN_NUM.get(text[2], 0)
    if len(text) == 1:
        return _CN_NUM.get(text)
    return None


def _yuan_to_fen(text: str) -> int:
    return int(round(float(text) * 100))


def _strip_span(message: str, span: Optional[tuple[int, int]]) -> str:
    """把已匹配掉的片段挖空，避免同一个数字被后面的模式重复解释。

    典型坑：「其中 2 个人不吃辣」里的 2 是不吃辣的人数，不是总人数。
    """
    if not span:
        return message
    start, end = span
    return message[:start] + " " * (end - start) + message[end:]


def _extract_people(message: str) -> Optional[int]:
    """先更正句式、再普通句式；「不是6个人，是4个人」要取 4。

    关键：更正模式不能从头搜，否则会先匹配到被否定的那个旧值
    （「不是6个人」里的 6）。只在被否定片段之后找新值。
    """
    negation = _NEGATION_PEOPLE.search(message)
    if negation:
        tail = message[negation.end():]
        for pattern in _PEOPLE_PATTERNS:
            match = pattern.search(tail)
            if match:
                value = _cn_to_int(match.group(1))
                if value and 0 < value <= 20:
                    return value
                break
        return None  # 只说了「不是6个人」没给新值，保持原值不动
    for pattern in _PEOPLE_PATTERNS:
        match = pattern.search(message)
        if match:
            value = _cn_to_int(match.group(1))
            if value and 0 < value <= 20:
                return value
            break
    return None


def _extract_budget(message: str) -> tuple[Optional[str], Optional[int]]:
    """返回 (budget_type, budget_fen)。金额未说明口径时默认总预算并记 note。"""
    negation = _NEGATION_BUDGET.search(message)
    if negation:
        # 只在被否定片段之后找新金额，口径沿用上一次的
        tail = message[negation.end():]
        match = re.search(r"(\d+(?:\.\d+)?)\s*元", tail)
        if match:
            return None, _yuan_to_fen(match.group(1))
        return None, None
    per_person = _PER_PERSON_PATTERN.search(message)
    total_budget = _TOTAL_BUDGET_PATTERN.search(message)
    if per_person:
        return "per_person", _yuan_to_fen(per_person.group(1))
    if total_budget:
        return "total", _yuan_to_fen(total_budget.group(1))
    bare = re.search(r"(\d+(?:\.\d+)?)\s*元", message)
    if bare:
        return "total", _yuan_to_fen(bare.group(1))
    return None, None


def _extract_tags(message: str) -> list[str]:
    tags: list[str] = []
    for tag, keywords in _TAG_KEYWORDS.items():
        if any(kw in message for kw in keywords):
            tags.append(tag)
    return tags


def rule_extract(message: str, current: Optional[RequirementsDraft]) -> dict:
    """规则解析：把本句话里提到的字段解析成补丁，再与已有草稿合并。

    只处理固定句式，解析不出的字段不覆盖旧值、也不造假值。
    """
    notes: list[str] = []
    patch_data: dict[str, Any] = {}

    # 忌口先解析：它的数字（「2 个人不吃辣」）必须从总人数候选里挖掉，
    # 否则一句话里的人数会被误当成用餐总人数
    spicy_span: Optional[tuple[int, int]] = None
    spicy_count: Optional[int] = None
    for pattern in _NON_SPICY_WITH_COUNT:
        match = pattern.search(message)
        if match:
            spicy_span = match.span()
            value = _cn_to_int(match.group(1))
            if value is not None and 0 <= value <= 20:
                spicy_count = value
            break

    residual = _strip_span(message, spicy_span)

    people = _extract_people(residual)
    if people is not None:
        patch_data["people"] = people

    budget_type, budget_fen = _extract_budget(residual)
    if budget_fen is not None:
        patch_data["budget_fen"] = budget_fen
        if budget_type is not None:
            patch_data["budget_type"] = budget_type
        else:
            notes.append("金额未说明口径，沿用了上一次的预算口径")

    # 忌口：给了人数就记录；只说「不吃辣」没给人数时记为 0，触发追问
    if spicy_count is not None:
        patch_data["dietary_constraints"] = [
            DietaryConstraint(kind="non_spicy", people=spicy_count)
        ]
    elif _NON_SPICY_PLAIN.search(message):
        if current is None or not current.dietary_constraints:
            patch_data["dietary_constraints"] = [
                DietaryConstraint(kind="non_spicy", people=0)
            ]

    if _HEAVY_HINT.search(message):
        patch_data["appetite"] = "heavy"
    elif _LIGHT_HINT.search(message):
        patch_data["appetite"] = "light"

    if _NO_DRINK.search(message):
        patch_data["exclude_beverage"] = True

    if _ACTIVE_CARD.search(message):
        patch_data["membership_status"] = "active"
    elif _NO_CARD.search(message):
        patch_data["membership_status"] = "none"

    tags = _extract_tags(message)
    if tags:
        patch_data["preferred_tags"] = tags

    base = current or RequirementsDraft()
    draft = base.merge(RequirementsDraft(**patch_data))

    # 预算口径矛盾时确认，不擅自改口径
    if budget_fen is not None and budget_type is None and draft.budget_type is None:
        draft = draft.merge(RequirementsDraft(budget_type="total"))
        notes.append("金额未说明口径，默认按总预算处理；人均预算请说「人均 X 元」")

    missing = draft.missing_fields()
    problems = draft.contradictions()

    follow_up: Optional[str] = None
    if problems:
        follow_up = "需要更正：" + "；".join(problems)
    elif missing:
        parts = []
        if "people" in missing:
            parts.append("一共几个人用餐？")
        if "budget" in missing:
            parts.append("预算是多少（总预算还是人均）？")
        if "non_spicy_people" in missing:
            parts.append("有几位不吃辣？")
        follow_up = "还想确认：" + "".join(parts)

    requirements: Optional[Requirements] = None
    if draft.is_complete():
        requirements = draft.to_requirements()

    return {
        "draft": draft,
        "requirements": requirements,
        "missing_fields": missing + (["contradiction"] if problems else []),
        "follow_up": follow_up,
        "parse_source": "rule",
        "notes": notes,
    }


class ChatService:
    def __init__(self) -> None:
        self._llm_module: Any = None
        self._llm_checked = False

    def _load_llm(self) -> Any:
        if self._llm_checked:
            return self._llm_module
        self._llm_checked = True
        try:
            self._llm_module = importlib.import_module("backend.llm")
        except ImportError:
            self._llm_module = None
        return self._llm_module

    # ------------------------------------------------------------ 统一出口

    @staticmethod
    def _coerce_draft(value: Any, requirements: Optional[Requirements]) -> RequirementsDraft:
        """把 B 返回的 draft / requirements 统一收敛成草稿对象。"""
        if isinstance(value, RequirementsDraft):
            return value
        if isinstance(value, Requirements):
            return value.to_draft()
        if isinstance(value, dict):
            try:
                return RequirementsDraft(**value)
            except Exception:  # noqa: BLE001 — 字段不合法时退回从 requirements 推导
                pass
        if requirements is not None:
            return requirements.to_draft()
        return RequirementsDraft()

    def _build_out(self, result: dict, source: str, notes_prefix: list[str]) -> ChatOut:
        requirements = result.get("requirements")
        if requirements is not None and not isinstance(requirements, Requirements):
            try:
                requirements = Requirements.model_validate(requirements)
            except Exception:  # noqa: BLE001 — B 返回了不合法需求时按未完成处理
                requirements = None

        draft = self._coerce_draft(result.get("draft"), requirements)
        if requirements is not None:
            # 完整需求优先：避免 B 返回了 requirements 却漏带新字段
            draft = draft.merge(requirements.to_draft())

        missing = list(result.get("missing_fields") or [])
        if not missing:
            missing = draft.missing_fields()

        problems = draft.contradictions()
        follow_up = result.get("follow_up")
        if problems:
            # 矛盾必须让用户更正，不能被 B 的 ready 盖过去
            follow_up = "需要更正：" + "；".join(problems)
            if "contradiction" not in missing:
                missing.append("contradiction")

        ready = requirements is not None and not missing and not problems
        return ChatOut(
            requirements=requirements,
            draft=draft,
            missing_fields=missing,
            follow_up=follow_up,
            ready=ready,
            parse_source=result.get("parse_source") or source,
            notes=notes_prefix + list(result.get("notes") or []),
        )

    async def parse(self, payload: ChatIn) -> ChatOut:
        module = self._load_llm()
        if module is not None and hasattr(module, "extract_requirements"):
            try:
                result = await module.extract_requirements(payload.message, payload.current)
                return self._build_out(result, "llm", [])
            except Exception:  # noqa: BLE001 — LLM 超时/输出不完整时走规则兜底
                # 明示已从 LLM 回退，不伪装成模型实时理解
                result = rule_extract(payload.message, payload.current)
                return self._build_out(
                    result,
                    "rule",
                    [
                        "语义理解服务暂时不可用，已切换到固定句式识别，"
                        "请尽量用「N个人、预算X元」的表述"
                    ],
                )

        result = rule_extract(payload.message, payload.current)
        return self._build_out(result, "rule", [])


chat_service = ChatService()
