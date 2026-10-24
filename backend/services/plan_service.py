"""调用 B 的正式决策、校验后保存；新硬约束不可降级成旧样例。"""
from __future__ import annotations

import importlib
import inspect
from typing import Any

from ..schemas import PlanningContext, PlanOut, PlanRequest, PlanResponse
from .plan_store import PlanStore


class CapabilityUnavailableError(RuntimeError):
    """当前决策模块不能安全执行请求。"""


class PlanService:
    def __init__(self, menus=None, nutrition=None, store=None, context_provider=None) -> None:
        from .menu_service import menu_service
        from .nutrition_service import nutrition_service
        self.menus = menus or menu_service
        self.nutrition = nutrition or nutrition_service
        self.store = store or PlanStore()
        self.context_provider = context_provider
        self._skill_module: Any = None
        self._skill_checked = False

    def _load_skill(self) -> Any:
        if not self._skill_checked:
            self._skill_checked = True
            try:
                self._skill_module = importlib.import_module("backend.skill")
            except ImportError:
                self._skill_module = None
        return self._skill_module

    async def get_context(self, menu) -> PlanningContext:
        # 价格/资格事实来自服务端 provider，不接受 HTTP 客户端填入。
        if self.context_provider:
            context = self.context_provider(menu.store_code, menu)
        elif hasattr(self.menus, "get_planning_context"):
            context = self.menus.get_planning_context(menu.store_code, menu)
        else:
            context = PlanningContext(
                store_code=menu.store_code, data_source=menu.data_source,
                fetched_at=menu.fetched_at,
                price_basis="demo_snapshot" if menu.data_source == "demo_snapshot" else "menu_estimate",
            )
        if inspect.isawaitable(context):
            context = await context
        context = PlanningContext.model_validate(context)
        if context.store_code != menu.store_code:
            raise ValueError("套餐与报价上下文不属于当前门店")
        return context

    async def _invoke_skill(self, items, requirements, nutrition, context):
        module = self._load_skill()
        if module is None or not hasattr(module, "generate_plans"):
            raise CapabilityUnavailableError("正式决策模块不可用，请重启服务后重试。")
        parameters = inspect.signature(module.generate_plans).parameters
        if "context" not in parameters:
            raise CapabilityUnavailableError("当前决策模块版本不支持新需求，请更新并重启服务。")
        result = module.generate_plans(items, requirements, nutrition, context=context)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, tuple) or len(result) != 2:
            raise ValueError("Skill 返回值不符合方案契约")
        plans, reason = result
        if not isinstance(plans, list) or len(plans) > 3 or (plans and reason):
            raise ValueError("方案数量或无解原因不符合契约")
        if not plans and not reason:
            raise ValueError("无解必须提供原因")
        validator = getattr(module, "validate_plan", None)
        if validator is None:
            raise CapabilityUnavailableError("正式方案校验器不可用，请更新并重启服务。")
        validated, seen = [], set()
        for raw in plans:
            plan = PlanOut.model_validate(raw)
            validator(plan, items, requirements, context=context)
            signature = tuple(sorted((i.code, i.quantity, str(i.selected_options)) for i in plan.items))
            if signature in seen:
                raise ValueError("重复方案不能保存")
            seen.add(signature)
            validated.append(plan)
        return validated, reason

    async def create_plans(self, request: PlanRequest) -> PlanResponse:
        # 当前为单会话演示。任何重新生成（含无解/失败）都会撤销旧价格。
        self.store.invalidate_all()
        menu = await self.menus.get_menu(request.store_code)
        suggestions = []
        try:
            nutrition = await self.nutrition.get_nutrition_map()
        except Exception:
            nutrition = {}
            suggestions.append("营养数据暂不可用，点餐主流程继续；未知营养不会计为零。")
        context = await self.get_context(menu)
        try:
            plans, reason = await self._invoke_skill(menu.items, request.requirements, nutrition, context)
        except CapabilityUnavailableError:
            raise
        except Exception as exc:
            raise CapabilityUnavailableError("正式方案未通过约束校验，请稍后重试；本次未生成可结算方案。") from exc
        for plan in plans:
            self.store.save(plan, request.store_code, request.requirements)
        if menu.degraded_reason:
            suggestions.append(menu.degraded_reason)
        if not context.membership_facts:
            suggestions.append("缺少已核实的同篮子报价与会员卡费，开卡比较暂不可计算。")
        from ..skill.campaigns import recommend_campaigns
        campaigns = recommend_campaigns(context.campaigns, request.requirements, request.store_code)
        return PlanResponse(
            plans=plans, requirements=request.requirements,
            total_budget_fen=request.requirements.total_budget_fen,
            data_source=menu.data_source, fetched_at=menu.fetched_at,
            degraded_reason=menu.degraded_reason, unsatisfiable_reason=reason,
            suggestions=suggestions, algorithm_source="skill", campaigns=campaigns,
        )


plan_service = PlanService()
