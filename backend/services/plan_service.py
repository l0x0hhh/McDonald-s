"""Online facts -> model baskets -> official quotes -> saved server baskets."""
from __future__ import annotations

import asyncio
import inspect

from ..providers import McpError
from ..schemas import ComboDetailOut, ConstraintCheck, PlanRequest, PlanResponse, PlanningContext
from .capabilities import planner_capabilities
from .llm_planner import LLMPlanner, PlanningError
from .plan_store import PlanStore

PLANNING_TIMEOUT_SECONDS = 75


def _basket_signature(plan):
    """Identify an already proposed basket, without selecting or ranking products."""
    return tuple(sorted((line.code, line.quantity, tuple(sorted(
        (option.round_id, option.option_code, option.quantity) for option in line.selected_options
    ))) for line in plan.items))


def _quote_feedback(plan, requirements, index, official_total=None):
    total = official_total if type(official_total) is int and official_total >= 0 else None
    budget = requirements.total_budget_fen
    return {
        "candidate_index": index, "reason": "official_over_budget",
        "candidate": {
            "title": plan.title, "summary": plan.summary, "reasons": plan.reasons,
            "items": [{"code": line.code, "quantity": line.quantity,
                       "selected_options": [{"round_id": option.round_id,
                                             "option_code": option.option_code,
                                             "quantity": option.quantity}
                                            for option in line.selected_options]}
                      for line in plan.items],
        },
        "reference_total_fen": sum(line.line_total_fen for line in plan.items),
        "total_fen": total, "budget_fen": budget,
        "over_by_fen": max(0, total - budget) if total is not None else None,
    }


class PlanService:
    def __init__(self, menus=None, nutrition=None, store=None, combos=None, context_provider=None,
                 checkout=None, planner=None):
        from .combo_service import combo_service
        from .menu_service import menu_service
        from .nutrition_service import nutrition_service
        self.menus = menus if menus is not None else menu_service
        self.nutrition = nutrition if nutrition is not None else nutrition_service
        self.combos = combos if combos is not None else combo_service
        self.store = store if store is not None else PlanStore()
        self.context_provider = context_provider
        self.planner = planner if planner is not None else LLMPlanner()
        self._checkout = checkout

    @property
    def checkout(self):
        if self._checkout is None:
            from .checkout_service import CheckoutService
            self._checkout = CheckoutService(plans=self.store)
        return self._checkout

    async def get_context(self, menu):
        if self.context_provider is not None:
            context = self.context_provider(menu.store_code, menu)
        elif hasattr(self.menus, "get_planning_context"):
            context = self.menus.get_planning_context(menu.store_code, menu)
        else:
            context = PlanningContext(store_code=menu.store_code, data_source=menu.data_source,
                                      fetched_at=menu.fetched_at)
        if inspect.isawaitable(context):
            context = await context
        context = PlanningContext.model_validate(context)
        if context.store_code != menu.store_code or context.data_source != "live_mcp":
            raise McpError("本次没有匹配当前门店的在线事实，请重新查看菜单。")
        for code, detail in list(context.combo_details.items()):
            context.combo_details[code] = ComboDetailOut.model_validate(detail)
        return context

    async def create_plans(self, request: PlanRequest) -> PlanResponse:
        deadline = asyncio.get_running_loop().time() + PLANNING_TIMEOUT_SECONDS
        try:
            return await self._create_plans(request, deadline)
        except asyncio.TimeoutError as exc:
            raise PlanningError("E_PLANNING_TIMEOUT", "本次搭配核对超时，请重试。") from exc

    async def _create_plans(self, request: PlanRequest, deadline: float) -> PlanResponse:
        async def within_deadline(awaitable):
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            return await asyncio.wait_for(awaitable, timeout=remaining)

        self.store.invalidate_all()
        menu = await within_deadline(self.menus.get_menu(request.store_code))
        if menu.data_source != "live_mcp" or menu.store_code != request.store_code:
            raise McpError("本次菜单不是当前门店的在线数据，请重新查看菜单。")
        context = await within_deadline(self.get_context(menu))
        # Keep repair facts request-local, even if a provider reuses its context object.
        context = context.model_copy(deep=True)
        context.quote_feedback = []
        codes = [item.code for item in menu.items if item.is_combo and item.available
                 and not item.is_merchandise and (not item.requires_membership or item.eligibility_verified)]
        if codes:
            details = await within_deadline(self.combos.get_details(request.store_code, codes))
            context.combo_details.update({code: detail for code, detail in details.details.items()
                                          if detail.source == "live_mcp"})
            if details.missing_codes:
                context.notes.append(f"{len(details.missing_codes)} 份套餐暂缺完整选配，相关候选将排除。")
        caps = planner_capabilities(ready=True, online=True)
        caps["combo_details"] = bool(context.combo_details)
        context.capabilities = caps
        # Optional online enhancement never substitutes snapshot facts or invents nutrition.
        try:
            await within_deadline(self.nutrition.get_nutrition_map())
        except asyncio.TimeoutError:
            raise
        except Exception:
            context.notes.append("营养数据暂不可用，未知营养不计为零。")
        quoted, seen = [], set()
        for round_index in range(2):
            plans = await within_deadline(self.planner.generate(menu.items, request.requirements, context))
            if not plans:
                break
            over_budget, quote_error = [], None
            for index, candidate in enumerate(plans):
                signature = _basket_signature(candidate)
                if signature in seen:
                    continue
                seen.add(signature)
                try:
                    plan = await within_deadline(self.checkout.quote(
                        candidate, request.store_code, request.requirements))
                    if plan.price_basis != "live_quote" or not plan.quote_id:
                        raise PlanningError("E_QUOTE_FAILED", "官方报价未完成，请重试。")
                except asyncio.TimeoutError:
                    if not quoted:
                        raise
                    context.notes.append("其余候选核对超时，已保留完成官方报价的搭配。")
                    break
                except Exception as exc:
                    if getattr(exc, "code", None) == "E_QUOTE_OVER_BUDGET":
                        data = getattr(exc, "data", None)
                        total = data.get("total_fen") if isinstance(data, dict) else None
                        over_budget.append(_quote_feedback(candidate, request.requirements, index, total))
                        context.notes.append(f"「{candidate.title}」官方应付超过预算，本次未展示。")
                    else:
                        quote_error = exc
                        context.notes.append(f"「{candidate.title}」本次未完成官方报价，已排除。")
                    continue
                if plan.total_fen > request.requirements.total_budget_fen:
                    # A reference amount never overrides the independent official total.
                    over_budget.append(_quote_feedback(candidate, request.requirements, index, plan.total_fen))
                    context.notes.append(f"「{plan.title}」官方应付超过预算，本次未展示。")
                    continue
                plan.remaining_fen = request.requirements.total_budget_fen - plan.total_fen
                plan.checks.append(ConstraintCheck(name="预算", satisfied=True, detail="官方应付未超过预算"))
                quoted.append(plan)
            # Preserve successful candidates in model order; do not search or price-sort.
            if quoted:
                break
            if quote_error is not None:
                raise quote_error
            if round_index == 1 or not over_budget:
                break
            context.quote_feedback.extend(over_budget)
        for index, plan in enumerate(quoted):
            plan.type = "recommended" if index == 0 else "alternative"
            self.store.save(plan, request.store_code, request.requirements)
        return PlanResponse(
            plans=quoted, requirements=request.requirements, total_budget_fen=request.requirements.total_budget_fen,
            data_source="live_mcp", fetched_at=menu.fetched_at, suggestions=context.notes,
            unsatisfiable_reason=None if quoted else "本次暂未找到通过核对且符合预算的搭配，请调整需求后重试。",
            capabilities=caps, algorithm_source="llm_skill", skill_version=self.planner.skill_version,
            skill_hash=self.planner.skill_hash,
        )


plan_service = PlanService()
