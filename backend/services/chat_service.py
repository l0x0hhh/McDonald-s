"""编排正式需求提取；失败时保留草稿并明确标注规则解析来源。"""
from __future__ import annotations

import importlib
from typing import Any

from ..config import settings
from ..schemas import ChatIn, ChatOut


def rule_extract(message, current=None) -> dict:
    # 所有入口共用 B 的约束解析，避免兜底时丢掉无饮料等硬约束。
    from ..skill.parser import rule_extract as parse_rule
    return parse_rule(message, current)


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

    async def parse(self, payload: ChatIn) -> ChatOut:
        module = self._load_llm()
        if module is None:
            result = rule_extract(payload.message, payload.current)
            result.setdefault("notes", []).append("语义理解模块不可用，当前使用固定规则解析。")
        else:
            try:
                result = await module.extract_requirements(
                    payload.message, payload.current,
                    demo_mode=settings.data_mode == "demo",
                )
                if not isinstance(result, dict):
                    raise TypeError("需求提取必须返回字典")
            except Exception:
                result = rule_extract(payload.message, payload.current)
                result.setdefault("notes", []).append("语义理解服务暂不可用，已切换固定规则；已确认的需求仍保留。")
        missing = list(result.get("missing_fields", []))
        requirements = result.get("requirements")
        return ChatOut(
            requirements=requirements,
            draft=result.get("draft"),
            missing_fields=missing,
            follow_up=result.get("follow_up"),
            ready=requirements is not None and not missing,
            parse_source=result.get("parse_source", "rule"),
            notes=result.get("notes", []),
        )


chat_service = ChatService()
