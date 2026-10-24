# 修订说明：样例使用已核实的实际餐份与默认选配，不带饮料仅走已确认的主食单品。
"""A 的接口演示兜底；不执行 B 的正式最优搜索、饭量、口味或优惠算法。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from ..schemas import (
    ConstraintCheck, MenuItemOut, NutritionOut, PlanItemOut, PlanOut,
    PlanningContext, Requirements,
)
from .meal_facts import MealFacts, resolve_meal_facts


@dataclass
class _Candidate:
    item: MenuItemOut
    facts: MealFacts

    @property
    def price_fen(self) -> int:
        return self.item.price_fen + self.facts.extra_price_fen


# 每份套餐可能有多个主食；按核实餐份扣减，不能把一份小食算一人。
def _fill_servings(
    candidates: list[_Candidate], needed: int, max_repeat: int,
) -> list[tuple[_Candidate, int]]:
    selected: list[tuple[_Candidate, int]] = []
    remaining = needed
    for candidate in candidates:
        if remaining <= 0:
            break
        servings = candidate.facts.servings
        quantity = min(max_repeat, (remaining + servings - 1) // servings)
        selected.append((candidate, quantity))
        remaining -= quantity * servings
    return selected


def _make_item(candidate: _Candidate, quantity: int, nutrition: dict[str, NutritionOut]) -> PlanItemOut:
    item, facts = candidate.item, candidate.facts
    return PlanItemOut(
        code=item.code, name=item.name, quantity=quantity,
        unit_price_fen=candidate.price_fen,
        original_unit_price_fen=item.original_price_fen + facts.extra_price_fen,
        line_total_fen=candidate.price_fen * quantity,
        servings=facts.servings, spicy_level=facts.spicy_level, image=item.image,
        selected_options=[option.model_copy(deep=True) for option in facts.selected_options],
        spec=("；".join(f"{option.round_name}：{option.option_name}×{option.quantity}"
                       for option in facts.selected_options) or None),
        nutrition=nutrition.get(item.name) or NutritionOut(matched=False),
    )


def _build_plan(
    plan_type: str, selection: list[tuple[_Candidate, int]],
    requirements: Requirements, nutrition: dict[str, NutritionOut],
) -> PlanOut:
    # 合并重复商品，避免分别给忌口组和其他人配餐后出现两行相同规格。
    merged: dict[str, tuple[_Candidate, int]] = {}
    for candidate, quantity in selection:
        previous = merged.get(candidate.item.code)
        merged[candidate.item.code] = (candidate, quantity + (previous[1] if previous else 0))
    items = [_make_item(candidate, quantity, nutrition) for candidate, quantity in merged.values()]
    total = sum(item.line_total_fen for item in items)
    subtotal = sum(item.original_unit_price_fen * item.quantity for item in items)
    servings = sum(item.servings * item.quantity for item in items)
    non_spicy = sum(candidate.facts.non_spicy_servings * quantity
                    for candidate, quantity in merged.values())
    checks = [
        ConstraintCheck(name="预算", satisfied=total <= requirements.total_budget_fen,
                        detail=f"应付 {total / 100:.2f} 元 / 预算 {requirements.total_budget_fen / 100:.2f} 元"),
        ConstraintCheck(name="人数", satisfied=servings >= requirements.people,
                        detail=f"已核实主食 {servings} 份 / 共 {requirements.people} 人"),
        ConstraintCheck(name="忌口", satisfied=non_spicy >= requirements.non_spicy_people,
                        detail=f"已核实不辣餐份 {non_spicy} / 需要 {requirements.non_spicy_people}"),
        ConstraintCheck(name="无需开卡", satisfied=True, detail="已排除需额外会员或身份资格的商品"),
    ]
    reasons = [
        f"按实际主食选配计 {servings} 餐份，其中可确认不辣 {non_spicy} 份",
        "按菜单参考价与已知选配差价估算，尚未接入收银实时算价",
        "演示样例按每主食餐份参考价排序，不保证全局最优；饭量与口味规则待正式算法接入",
    ]
    if requirements.exclude_beverage:
        checks.append(ConstraintCheck(
            name="不含饮料", satisfied=True,
            detail="仅使用已确认不含饮品的主食单品",
        ))
        reasons.append("样例暂未搜索套餐替换选配，仅用无饮料单品；不能据此判断所有套餐都必须带饮料")
    if plan_type == "balanced":
        reasons.append("搭配多样：降低同一商品重复次数，不代表营养均衡")
    return PlanOut(
        plan_id=uuid.uuid4().hex[:12], type=plan_type,
        title="最省样例方案" if plan_type == "cheapest" else "搭配多样方案",
        summary="", items=items, subtotal_fen=subtotal, total_fen=total,
        discount_fen=subtotal - total, budget_fen=requirements.total_budget_fen,
        remaining_fen=requirements.total_budget_fen - total,
        servings_total=servings, non_spicy_servings=non_spicy,
        reasons=reasons, checks=checks,
    )


def generate_sample_plans(
    items: list[MenuItemOut], requirements: Requirements,
    nutrition: dict[str, NutritionOut], context: Optional[PlanningContext] = None,
) -> tuple[list[PlanOut], Optional[str]]:
    """返回核实过事实的演示候选；搜索不充分时如实说明，不宣称数学无解。"""
    details = context.combo_details if context else {}
    candidates: list[_Candidate] = []
    for item in items:
        if item.requires_membership or item.is_merchandise:
            continue
        if requirements.exclude_beverage and item.is_combo:
            continue
        facts, _ = resolve_meal_facts(item, details.get(item.code))
        if facts is None or facts.servings < 1:
            continue
        if requirements.exclude_beverage and facts.beverage_status != "none":
            continue
        candidate = _Candidate(item, facts)
        if min(candidate.price_fen, item.original_price_fen + facts.extra_price_fen) < 0:
            continue
        candidates.append(candidate)
    if not candidates:
        return [], "没有可核实主食餐份与价格的候选，当前演示样例无法生成方案"

    candidates.sort(key=lambda c: (Fraction(c.price_fen, c.facts.servings), c.price_fen, c.item.code))
    mild = [candidate for candidate in candidates
            if candidate.facts.non_spicy_servings == candidate.facts.servings]
    if requirements.non_spicy_people and not mild:
        return [], "缺少实际选配可确认不辣的主食，当前样例无法满足忌口要求"

    # 其余人也能吃不辣餐，不应因分组而被迫买更贵的辣味商品。
    plans: list[PlanOut] = []
    seen: set[tuple] = set()
    for plan_type, max_repeat in (("cheapest", requirements.people), ("balanced", 2)):
        selection = _fill_servings(mild, requirements.non_spicy_people, max_repeat)
        covered = sum(candidate.facts.servings * quantity for candidate, quantity in selection)
        selection += _fill_servings(candidates, max(0, requirements.people - covered), max_repeat)
        plan = _build_plan(plan_type, selection, requirements, nutrition)
        if not all(check.satisfied for check in plan.checks):
            continue
        key = tuple(sorted((item.code, item.quantity) for item in plan.items))
        if key not in seen:
            plans.append(plan)
            seen.add(key)
    if not plans:
        return [], "当前演示样例未找到同时满足预算、人数与忌口的方案，可提高预算或等待正式算法进一步搜索"
    if len(plans) == 1:
        plans[0].reasons.append("当前仅有一套不同且通过约束的样例方案，不凑数")
    return plans, None
