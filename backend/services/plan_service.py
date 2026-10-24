# 整合说明：A 注入并复核事实，B 负责决策；正式算法失败时不冒充演示成功。
"""取数、适配、正式规划、独立校验与模拟结算前保存。"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

from ..schemas import ComboDetailOut, PlanOut, PlanRequest, PlanResponse, PlanningContext
from .capabilities import (
    CapabilityUnavailable, describe_missing, runtime_capabilities,
    skill_supports_context, static_capabilities,
)
from .plan_store import PlanStore
from .plan_validator import validate_plans
from .sample_planner import generate_sample_plans
from .skill_adapter import adapt_skill_input


class PlanService:
    def __init__(self, menus=None, nutrition=None, store=None, combos=None, context_provider=None) -> None:
        from .combo_service import combo_service
        from .menu_service import menu_service
        from .nutrition_service import nutrition_service
        self.menus = menus or menu_service
        self.nutrition = nutrition or nutrition_service
        self.combos = combos or combo_service
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
        if not callable(getattr(self._skill_module, "generate_plans", None)):
            self._skill_module = None
        return self._skill_module

    @staticmethod
    def _skill_accepts_context(module: Any) -> bool:
        return skill_supports_context(module)

    async def get_context(self, menu) -> PlanningContext:
        # 上下文只来自服务端；HTTP 请求模型不接受价格、会员或优惠事实。
        if self.context_provider is not None:
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
            raise ValueError("规划上下文不属于当前门店")
        for code, detail in list(context.combo_details.items()):
            if isinstance(detail, dict):
                try:
                    context.combo_details[code] = ComboDetailOut.model_validate(detail)
                except (ValueError, TypeError):
                    context.notes.append(f"套餐 {code} 的外部结构无法核实，相关候选将排除")
        return context

    async def _invoke_skill(self, items, requirements, nutrition, context):
        module = self._load_skill()
        if module is None:
            return None
        adapted_items, payload = adapt_skill_input(items, context)
        supports_context = self._skill_accepts_context(module)
        if supports_context:
            result = module.generate_plans(adapted_items, requirements, nutrition, context=payload)
        else:
            result = module.generate_plans(adapted_items, requirements, nutrition)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, tuple) or len(result) != 2:
            raise ValueError("Skill 返回值不符合方案契约")
        plans, reason = result
        if (not isinstance(plans, list) or len(plans) > 3 or (plans and reason)
                or (reason is not None and not isinstance(reason, str))):
            raise ValueError("方案列表或无解原因不符合契约")
        validator = getattr(module, "validate_plan", None)
        normalized, seen = [], set()
        for raw in plans:
            plan = PlanOut.model_validate(raw)
            if callable(validator):
                validator(plan, adapted_items, requirements, context=payload)
            signature = tuple(sorted(
                (line.code, line.quantity, str([option.model_dump() for option in line.selected_options]))
                for line in plan.items
            ))
            if signature in seen:
                raise ValueError("同一组合不能重复保存为多张卡片")
            seen.add(signature)
            normalized.append(plan)
        return normalized, reason

    async def create_plans(self, request: PlanRequest) -> PlanResponse:
        # 当前单会话演示：更改需求、无解或上游失败时均撤销旧价格。
        self.store.invalidate_all()
        module = self._load_skill()
        static = static_capabilities(skill_module=module)
        if request.requirements.exclude_beverage and not static["exclude_beverage"]:
            raise CapabilityUnavailable(
                "exclude_beverage",
                describe_missing("exclude_beverage", has_combo_details=False),
            )
        menu = await self.menus.get_menu(request.store_code)
        context = await self.get_context(menu)

        # 保留 A 的完整轮次；适配器只将通过事实核验的默认选配提供给 B。
        codes = [item.code for item in menu.items
                 if item.is_combo and not item.is_merchandise and not item.requires_membership]
        combo_result = await self.combos.get_details(request.store_code, codes) if codes else None
        if combo_result is not None:
            context.combo_details.update(combo_result.details)
            if menu.data_source == "live_mcp" and any(
                detail.source == "demo_snapshot" for detail in combo_result.details.values()
            ):
                context.notes.append("部分套餐组成来自历史快照；菜单来源在线不表示组成或价格已实时收银核验。")
            if combo_result.missing_codes:
                context.notes.append(
                    f"有 {len(combo_result.missing_codes)} 份套餐缺少结构，无法核实的套餐不参与决策"
                )
            if combo_result.degraded_reason:
                context.notes.append(combo_result.degraded_reason)

        adapted_items, skill_context = adapt_skill_input(menu.items, context)
        beverage_ready = any(
            item.available and not item.requires_membership and (
                (not item.is_combo and item.food_role == "main" and item.servings > 0
                 and item.beverage_status == "none")
                or (item.is_combo and item.code in skill_context["combo_details"]
                    and all(c["beverage_status"] == "none"
                            for c in skill_context["combo_details"][item.code]["components"]))
            )
            for item in adapted_items
        )
        caps = runtime_capabilities(
            static, has_combo_details=bool(context.combo_details),
            beverage_data_ready=beverage_ready, data_source=menu.data_source,
        )
        context.capabilities = caps
        if request.requirements.exclude_beverage and not caps["exclude_beverage"]:
            raise CapabilityUnavailable(
                "exclude_beverage",
                describe_missing("exclude_beverage", has_combo_details=bool(context.combo_details),
                                 beverage_data_ready=False),
            )
        suggestions = list(context.notes)
        try:
            nutrition = await self.nutrition.get_nutrition_map()
        except Exception:
            nutrition = {}
            suggestions.append("营养数据暂不可用，点餐主流程继续；未知营养不计为零。")

        using_sample = module is None
        if using_sample:
            if request.requirements.appetite != "normal" or request.requirements.preferred_tags:
                raise CapabilityUnavailable("planning", "演示样例不支持饭量变化或口味决策，请接入正式 Skill。")
            plans, reason = generate_sample_plans(menu.items, request.requirements, nutrition, context)
            for plan in plans:
                plan.price_basis = context.price_basis
            suggestions.append("当前为接口演示样例（demo_sample），正式 Skill 未装载。")
        else:
            try:
                plans, reason = await self._invoke_skill(menu.items, request.requirements, nutrition, context)
            except Exception as exc:
                raise CapabilityUnavailable(
                    "planning", "正式方案生成或约束校验失败，本次没有可结算方案，请稍后重试。"
                ) from exc
        if not plans and not reason:
            reason = "当前候选未产生可核实方案，请调整需求后重试。"

        # B 复核决策规则之后，A 再按原菜单和完整轮次重算事实，不信任自报合计。
        combo_details = {
            code: detail for code, detail in context.combo_details.items()
            if isinstance(detail, ComboDetailOut)
        }
        kept, dropped = validate_plans(
            plans, request.requirements, menu.items, combo_details,
            context=context, strict_details=not using_sample and self._skill_accepts_context(module),
        )
        suggestions.extend(dropped)
        if dropped and not kept:
            raise CapabilityUnavailable("planning", "候选未通过 A 的事实校验：" + "；".join(dropped))
        if menu.degraded_reason:
            suggestions.append(menu.degraded_reason)
        if not context.membership_facts:
            suggestions.append("缺少已核实的同篮子报价与卡费，开卡比较暂不可计算。")
        for plan in kept:
            self.store.save(plan, request.store_code, request.requirements)

        campaigns = []
        if not using_sample:
            from ..skill.campaigns import recommend_campaigns
            campaigns = recommend_campaigns(context.campaigns, request.requirements, request.store_code)
        return PlanResponse(
            plans=kept, requirements=request.requirements,
            total_budget_fen=request.requirements.total_budget_fen,
            data_source=menu.data_source, fetched_at=menu.fetched_at,
            degraded_reason=menu.degraded_reason,
            unsatisfiable_reason=None if kept else reason, suggestions=suggestions,
            capabilities=caps, algorithm_source="demo_sample" if using_sample else "skill",
            campaigns=campaigns,
        )


plan_service = PlanService()
