# 整合说明：统一使用 B 的需求解析，A 再检查草稿缺项、矛盾和来源标记。
"""正式模型或演示规则解析；异常时保留已确认需求并明确降级。"""

from __future__ import annotations

import importlib
from typing import Any

from ..config import settings
from ..schemas import ChatIn, ChatOut, Requirements, RequirementsDraft


def rule_extract(message, current=None) -> dict:
    from ..skill.parser import rule_extract as parse_rule
    result = parse_rule(message, current)
    problems = result["draft"].contradictions()
    if problems:
        result["missing_fields"] = list(dict.fromkeys(result["missing_fields"] + ["contradiction"]))
        result["follow_up"] = "需要更正：" + "；".join(problems)
    return result


class ChatService:
    def __init__(self) -> None:
        self._llm_module: Any = None
        self._llm_checked = False

    def _load_llm(self) -> Any:
        if not self._llm_checked:
            self._llm_checked = True
            try:
                self._llm_module = importlib.import_module("backend.llm")
            except ImportError:
                self._llm_module = None
        return self._llm_module

    @staticmethod
    def _build_out(result: dict, notes: list[str]) -> ChatOut:
        if not isinstance(result, dict):
            raise TypeError("需求提取必须返回字典")
        raw = result.get("requirements")
        requirements = Requirements.model_validate(raw) if raw is not None else None
        draft = RequirementsDraft.model_validate(
            result.get("draft") or (requirements.to_draft() if requirements else RequirementsDraft())
        )
        missing = list(dict.fromkeys(
            list(result.get("missing_fields") or []) + draft.missing_fields()
        ))
        problems = draft.contradictions()
        follow_up = result.get("follow_up")
        if problems:
            missing = list(dict.fromkeys(missing + ["contradiction"]))
            follow_up = "需要更正：" + "；".join(problems)
        return ChatOut(
            requirements=requirements, draft=draft,
            missing_fields=missing, follow_up=follow_up,
            ready=requirements is not None and not missing and not problems,
            parse_source=result.get("parse_source", "rule"),
            notes=notes + list(result.get("notes") or []),
        )

    async def parse(self, payload: ChatIn) -> ChatOut:
        from .llm_planner import PlanningError
        module = self._load_llm()
        if module is None or not callable(getattr(module, "extract_requirements", None)):
            raise PlanningError("E_LLM_NOT_CONFIGURED", "需求理解服务尚未就绪，请直接填写人数和预算。")
        result = await module.extract_requirements(payload.message, payload.current, demo_mode=False)
        return self._build_out(result, [])


chat_service = ChatService()
