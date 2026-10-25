"""services 包：流程串联、数据缓存、快照保存与方案存取（A 职责）。"""

from .capabilities import CapabilityUnavailable, static_capabilities
from .chat_service import ChatService, chat_service
from .combo_service import ComboService, combo_service
from .menu_service import MenuService, menu_service
from .nutrition_service import NutritionService, nutrition_service
from .plan_service import PlanService, plan_service
from .plan_store import PlanExpiredError, PlanNotFoundError
from .plan_validator import validate_plans

__all__ = [
    "ChatService",
    "chat_service",
    "ComboService",
    "combo_service",
    "MenuService",
    "menu_service",
    "NutritionService",
    "nutrition_service",
    "PlanService",
    "plan_service",
    "PlanNotFoundError",
    "PlanExpiredError",
    "CapabilityUnavailable",
    "static_capabilities",
    "validate_plans",
]
