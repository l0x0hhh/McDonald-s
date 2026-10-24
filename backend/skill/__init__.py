"""B 模块公共入口：接收 A 提供的已核实事实，不访问 MCP。"""

from ..schemas import MenuItemOut, PlanOut, PlanningContext, Requirements
from .campaigns import recommend_campaigns
from .membership import compare_membership
from .nutrition import summarize_nutrition
from .parser import RequirementError, parse_requirements
from .planner import MenuError, PlanValidationError, generate_plans, validate_plan

# B 只声明已由决策规则处理的能力；A 仍需按门店事实计算运行时可用性。
CAPABILITIES = {
    "exclude_beverage": True,
    "appetite_planning": True,
    "taste_matching": True,
}

__all__ = [
    "CAPABILITIES",
    "MenuItemOut", "PlanOut", "PlanningContext", "Requirements",
    "RequirementError", "parse_requirements", "MenuError", "PlanValidationError",
    "generate_plans", "validate_plan", "compare_membership",
    "summarize_nutrition", "recommend_campaigns",
]
