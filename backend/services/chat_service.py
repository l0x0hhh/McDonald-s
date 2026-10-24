"""需求解析服务（A 职责：编排与兜底，不是正式 NLP）。

与 B 的模块契约（B 接手 backend/llm.py 时实现以下入口即可）：

    async def extract_requirements(
        message: str,
        current: Requirements | None,
    ) -> dict
    # 返回 {
    #   "requirements": Requirements,       # 合并 current 之后的完整需求
    #   "missing_fields": list[str],        # 仍缺失的字段名
    #   "follow_up": str | None,            # 需要追问时给用户的话术
    #   "parse_source": "llm",
    # }

B 模块不存在或异常时，回退到本文件内置的规则解析（parse_source=rule）。
规则解析只认固定句式，不能声称具备语义理解；解析不出的字段进入
missing_fields 并触发 follow_up。
"""

from __future__ import annotations

import importlib
import re
from typing import Any, Optional

from ..schemas import ChatIn, ChatOut, DietaryConstraint, Requirements

_CN_NUM = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

_PEOPLE_PATTERNS = [
    re.compile(r"(\d{1,3})\s*(?:个\s*)?人"),
    re.compile(r"([一二两三四五六七八九十]{1,3})\s*(?:个\s*)?人"),
]
_PER_PERSON_PATTERN = re.compile(r"(?:人均|每人|一个人?均)\s*(\d+(?:\.\d+)?)\s*元?")
_TOTAL_BUDGET_PATTERN = re.compile(
    r"(?:总预算|预算|一共|总共|合计|以内|封顶)\s*(\d+(?:\.\d+)?)\s*元"
)
_NON_SPICY_WITH_COUNT = [
    re.compile(r"(\d{1,2})\s*(?:个\s*)?人\s*(?:不|不能|不吃|不要)\s*吃?\s*辣"),
    re.compile(r"([一二两三四五六七八九十]{1,3})\s*(?:个\s*)?人\s*(?:不|不能|不吃|不要)\s*吃?\s*辣"),
]
_NON_SPICY_PLAIN = re.compile(r"(?:不|不能|不吃|不要)\s*吃?\s*辣")
_HEAVY_HINT = re.compile(r"能吃|胃口大|大胃|吃得多|饭量大")
_LIGHT_HINT = re.compile(r"吃得少|胃口小|饭量小|吃不多")


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


def rule_extract(message: str, current: Optional[Requirements]) -> dict:
    """规则解析。只处理固定句式，解析不出的字段交给追问。

    未收集到的字段用 None 占位（不能给 people 填 0 占位，会触发模型校验失败），
    只有人数与预算都齐时才返回 Requirements。
    """
    people: Optional[int] = current.people if current else None
    budget_type = current.budget_type if current else "total"
    budget_fen: Optional[int] = current.budget_fen if current else None
    appetite = current.appetite if current else "normal"
    preferred_tags = list(current.preferred_tags) if current else []
    dietary = list(current.dietary_constraints) if current else []
    notes: list[str] = []

    for pattern in _PEOPLE_PATTERNS:
        match = pattern.search(message)
        if match:
            value = _cn_to_int(match.group(1))
            if value and 0 < value <= 20:
                people = value
            break

    per_person = _PER_PERSON_PATTERN.search(message)
    total_budget = _TOTAL_BUDGET_PATTERN.search(message)
    if per_person:
        budget_type = "per_person"
        budget_fen = _yuan_to_fen(per_person.group(1))
    elif total_budget:
        budget_type = "total"
        budget_fen = _yuan_to_fen(total_budget.group(1))
    else:
        bare = re.search(r"(\d+(?:\.\d+)?)\s*元", message)
        if bare:
            budget_type = "total"
            budget_fen = _yuan_to_fen(bare.group(1))
            notes.append("金额未说明口径，默认按总预算处理；人均预算请说「人均 X 元」")

    spicy_count: Optional[int] = None
    for pattern in _NON_SPICY_WITH_COUNT:
        match = pattern.search(message)
        if match:
            value = _cn_to_int(match.group(1))
            if value is not None and 0 <= value <= 20:
                spicy_count = value
            break
    spicy_unknown = spicy_count is None and bool(_NON_SPICY_PLAIN.search(message))

    if spicy_count is not None:
        dietary = [DietaryConstraint(kind="non_spicy", people=spicy_count)]
    elif spicy_unknown and not dietary:
        spicy_unknown = True  # 未给人数，需要追问
    elif not _NON_SPICY_PLAIN.search(message):
        dietary = [] if current is None else dietary

    if _HEAVY_HINT.search(message):
        appetite = "heavy"
    elif _LIGHT_HINT.search(message):
        appetite = "light"

    missing: list[str] = []
    if people is None:
        missing.append("people")
    if not budget_fen:
        missing.append("budget")
    if spicy_unknown and not dietary:
        missing.append("non_spicy_people")

    follow_up: Optional[str] = None
    if missing:
        parts = []
        if "people" in missing:
            parts.append("一共几个人用餐？")
        if "budget" in missing:
            parts.append("预算是多少（总预算还是人均）？")
        if "non_spicy_people" in missing:
            parts.append("有几位不吃辣？")
        follow_up = "还想确认：" + "".join(parts)

    output: Optional[Requirements] = None
    if people is not None and budget_fen:
        output = Requirements(
            people=people,
            budget_type=budget_type,
            budget_fen=budget_fen,
            dietary_constraints=dietary,
            appetite=appetite,
            preferred_tags=preferred_tags,
        )

    return {
        "requirements": output,
        "missing_fields": missing,
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

    async def parse(self, payload: ChatIn) -> ChatOut:
        module = self._load_llm()
        if module is not None and hasattr(module, "extract_requirements"):
            try:
                result = await module.extract_requirements(payload.message, payload.current)
                return ChatOut(
                    requirements=result.get("requirements"),
                    missing_fields=result.get("missing_fields", []),
                    follow_up=result.get("follow_up"),
                    ready=not result.get("missing_fields"),
                    parse_source="llm",
                    notes=result.get("notes", []),
                )
            except Exception:  # noqa: BLE001 — LLM 超时/输出不完整时走规则兜底
                rule_result = rule_extract(payload.message, payload.current)
                # 明示已从 LLM 回退，不伪装成模型实时理解
                rule_result["notes"] = [
                    "语义理解服务暂时不可用，已切换到固定句式识别，请尽量用「N个人、预算X元」的表述"
                ] + rule_result["notes"]
                return ChatOut(
                    requirements=rule_result["requirements"],
                    missing_fields=rule_result["missing_fields"],
                    follow_up=rule_result["follow_up"],
                    ready=not rule_result["missing_fields"],
                    parse_source=rule_result["parse_source"],
                    notes=rule_result["notes"],
                )

        result = rule_extract(payload.message, payload.current)
        return ChatOut(
            requirements=result["requirements"],
            missing_fields=result["missing_fields"],
            follow_up=result["follow_up"],
            ready=not result["missing_fields"],
            parse_source=result["parse_source"],
            notes=result["notes"],
        )


chat_service = ChatService()
