"""B 模块公共入口：接收 A 提供的已核实事实，不访问 MCP。"""

from ..schemas import MenuItemOut, PlanOut, PlanningContext, Requirements
from .campaigns import recommend_campaigns
from .membership import compare_membership
from .nutrition import summarize_nutrition
from .parser import RequirementError, parse_requirements
from .planner import MenuError, PlanValidationError, generate_plans, validate_plan

__all__ = [
    "MenuItemOut", "PlanOut", "PlanningContext", "Requirements",
    "RequirementError", "parse_requirements", "MenuError", "PlanValidationError",
    "generate_plans", "validate_plan", "compare_membership",
    "summarize_nutrition", "recommend_campaigns",
]
