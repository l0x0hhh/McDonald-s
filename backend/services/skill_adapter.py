# 整合说明：将 A 的实际默认选配转换为 B 的规划事实，价格仍为菜单参考价。
"""仅转换已核实事实；不执行组合搜索，不生成会员/活动资格。"""

from __future__ import annotations

from ..schemas import ComboDetailOut, MenuItemOut, PlanningContext
from .meal_facts import resolve_meal_facts


def adapt_skill_input(
    items: list[MenuItemOut], context: PlanningContext,
) -> tuple[list[MenuItemOut], dict]:
    """B 获得可用默认套餐；A 保留原菜单与完整轮次，独立核验最终结果。"""
    adapted: list[MenuItemOut] = []
    combos: dict[str, dict] = {}
    for item in items:
        detail = context.combo_details.get(item.code)
        # 生产链路只接受 A 的结构模型；独立 B 测试可直接使用其事实字典。
        if detail is not None and not isinstance(detail, ComboDetailOut):
            try:
                detail = ComboDetailOut.model_validate(detail)
            except (ValueError, TypeError):
                detail = None
        facts, _ = resolve_meal_facts(item, detail)
        if facts is None or min(
            item.price_fen + facts.extra_price_fen,
            item.original_price_fen + facts.extra_price_fen,
        ) < 0:
            adapted.append(item.model_copy(update={"available": False}))
            continue
        adapted.append(item.model_copy(update={
            "servings": facts.servings,
            "servings_verified": item.food_role in ("main", "side", "beverage", "dessert"),
            "non_spicy_servings": facts.non_spicy_servings,
            "side_servings": facts.side_servings,
            "spicy_level": facts.spicy_level,
            # B 的原价汇总与 A 的规格差价核算使用同一口径。
            "original_price_fen": item.original_price_fen + facts.extra_price_fen,
        }))
        if not item.is_combo or detail is None:
            continue
        selected = [
            {**option.model_dump(), "code": option.option_code}
            for option in facts.selected_options
        ]
        combos[item.code] = {
            "verified": True,
            "components": facts.components,
            "selected_options": selected,
            "selection_components_verified": True,
            "price_fen": item.price_fen + facts.extra_price_fen,
            "price_verified": True,
            "price_basis": context.price_basis,
            "spec": "；".join(
                f"{option.round_name}：{option.option_name}×{option.quantity}"
                for option in facts.selected_options
            ),
            "rounds": [
                {
                    "id": round_.round_id,
                    "min_select": round_.min_quantity,
                    "max_select": round_.max_quantity,
                    "options": [option.model_dump() for option in round_.options],
                }
                for round_ in detail.rounds
            ],
        }
    payload = context.model_dump(exclude={"combo_details"})
    payload["combo_details"] = combos
    return adapted, payload
