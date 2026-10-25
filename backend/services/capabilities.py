# 整合说明：B 的声明作为决策依据，门店事实和独立校验决定实际可用性。
"""能力标记（A 职责）。

规则（扩展草案 §二）：某个能力只有在**输入、数据与决策全链路**都支持时才能标 true。
只有字段存在、或只有 A 这一侧支持，都不算数。

- 静态能力（GET /api/v1/config）：代码与模块接线层面是否支持，与门店无关。
- 运行时能力（POST /api/v1/plans 响应）：叠加本次实际取到的数据后是否仍然可用。
  例如「不带饮料」需要拿到套餐结构才能判断某份套餐是否含饮料，
  拿不到结构时本次请求就不可用，必须明确提示，不能忽略约束硬凑普通套餐。
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Optional

CAPABILITY_KEYS = (
    "chat_draft",
    "combo_details",
    "exclude_beverage",
    "appetite_planning",
    "taste_matching",
    "membership_comparison",
    "habit_estimate",
    "nutrition_summary",
    "campaigns",
)

# 硬约束类能力：不可用时必须明确拒绝，不能忽略约束后返回普通方案
HARD_CONSTRAINTS = ("exclude_beverage",)


class CapabilityUnavailable(Exception):
    """收到了已明确但当前全链路不支持的硬约束。"""

    def __init__(self, capability: str, reason: str) -> None:
        super().__init__(reason)
        self.capability = capability
        self.reason = reason


def load_skill_module() -> Optional[Any]:
    """探测 B 的 skill 模块是否存在。不存在返回 None，不抛异常。"""
    try:
        return importlib.import_module("backend.skill")
    except ImportError:
        return None


def skill_supports_context(module: Any) -> bool:
    # 签名只检查参数能否传入，不再推断算法具备哪些能力。
    """B 的 generate_plans 是否接受三参数加 context 关键字的调用。"""
    func = getattr(module, "generate_plans", None)
    if not callable(func):
        return False
    try:
        inspect.signature(func).bind(None, None, None, context=None)
    except (TypeError, ValueError):
        return False
    return True


def static_capabilities(*, skill_module: Optional[Any]) -> dict[str, bool]:
    """与门店无关的能力：代码接线层面是否支持。"""
    # B 显式导出 CAPABILITIES；未声明、非布尔值或没有算法入口都不能宣称支持。
    has_skill = skill_module is not None and callable(getattr(skill_module, "generate_plans", None))
    declared = getattr(skill_module, "CAPABILITIES", {}) if has_skill else {}
    if not isinstance(declared, dict):
        declared = {}
    decision_exclude_beverage = declared.get("exclude_beverage") is True if has_skill else True

    return {
        # A 已实现多轮草稿合并与回传
        "chat_draft": True,
        # A 已接入 query-meal-detail；能否真的取到要看运行时
        "combo_details": True,
        # 输入与事实由 A 提供，决策由实际模块显式声明；保存前仍逐项校验。
        "exclude_beverage": decision_exclude_beverage,
        # 饭量和口味由 B 显式声明；字段或 context 参数本身不能启用能力。
        "appetite_planning": has_skill and declared.get("appetite_planning") is True,
        # B 已有独立口味词表；MCP 的营销 tags 不参与口味判断。
        "taste_matching": has_skill and declared.get("taste_matching") is True,
        # 开卡对比需 A 提供同篮子真实报价，本版尚未接入
        "membership_comparison": False,
        # 习惯分析依赖开卡对比与真实卡费/有效期
        "habit_estimate": False,
        # 整餐营养汇总待营养匹配修好后提供
        "nutrition_summary": False,
        # 活动与新品待核实后接入
        "campaigns": False,
    }


def runtime_capabilities(
    static: dict[str, bool],
    *,
    has_combo_details: bool,
    beverage_data_ready: bool,
    data_source: str,
) -> dict[str, bool]:
    """叠加本次请求的数据可用性。

    - combo_details：有没有真的取到套餐选配结构
    - exclude_beverage：有可确认的主食单品，或 B 已声明支持且收到可评估的套餐选配数据。
      这里只表示能处理该约束，不保证给定预算与人数一定存在可行解。
    """
    caps = dict(static)
    caps["combo_details"] = static.get("combo_details", False) and has_combo_details
    caps["exclude_beverage"] = (
        static.get("exclude_beverage", False) and beverage_data_ready
    )
    return caps


def describe_missing(
    capability: str, *, has_combo_details: bool, beverage_data_ready: bool = True
) -> str:
    """给前端与用户看的缺能力说明。"""
    if capability == "exclude_beverage":
        if not beverage_data_ready:
            return (
                "当前决策模块缺少可核实的无饮料主食单品或套餐选配数据，"
                "无法在确证的前提下满足「整单不含饮料」。"
            )
        return "当前决策模块不支持「不带饮料」约束，已按硬约束拒绝，未返回普通方案。"
    return f"能力 {capability} 当前不可用"


def planner_capabilities(*, ready: bool, online: bool = True) -> dict[str, bool]:
    """LLM path readiness; store-specific facts are checked again for each basket."""
    return {
        "chat_draft": ready, "combo_details": online,
        "exclude_beverage": ready and online, "appetite_planning": ready and online,
        "taste_matching": ready and online, "membership_comparison": False,
        "habit_estimate": False, "nutrition_summary": False, "campaigns": False,
    }
