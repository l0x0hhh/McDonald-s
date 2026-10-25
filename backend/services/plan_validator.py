# 整合说明：A 独立重算 B 的组成、餐份和金额，已核实优惠另行核对。
"""A 的事实校验边界；不通过的方案直接丢弃并说明原因。"""

from __future__ import annotations

from ..schemas import ComboDetailOut, MenuItemOut, PlanOut, PlanningContext, Requirements
from ..meal_policy import confirmed_mild, extra_portions, portion_targets
from .meal_facts import resolve_meal_facts


def validate_plan(
    plan: PlanOut,
    requirements: Requirements,
    menu_map: dict[str, MenuItemOut],
    combo_details: dict[str, ComboDetailOut],
    *, context: PlanningContext | None = None, strict_details: bool = False,
) -> list[str]:
    """返回问题列表；空列表代表通过。"""
    if not plan.items:
        return ["方案没有包含任何商品"]
    problems: list[str] = []
    if len({item.code for item in plan.items}) != len(plan.items):
        problems.append("同一商品应合并数量，不能重复列项")
    servings = non_spicy = total = subtotal = 0
    sides = 0
    for item in plan.items:
        source = menu_map.get(item.code)
        if source is None:
            problems.append(f"商品 {item.code} 不在该门店菜单中")
            continue
        if item.quantity < 1:
            problems.append(f"{source.name} 数量必须为正整数")
            continue
        if item.name != source.name:
            problems.append(f"商品 {item.code} 名称与菜单不符")
        if source.requires_membership:
            problems.append(f"{source.name} 需要额外会员或身份资格，当前尚未核验")
        facts, errors = resolve_meal_facts(source, combo_details.get(item.code), item.selected_options)
        if facts is None:
            problems.extend(f"{source.name}：{error}" for error in errors)
            continue

        # 差价按每份套餐内的选项数量计算，再乘整份套餐购买数量。
        unit = source.price_fen + facts.extra_price_fen
        original_unit = source.original_price_fen + facts.extra_price_fen
        if min(source.price_fen, source.original_price_fen, unit, original_unit) < 0:
            problems.append(f"{source.name} 价格不能为负数")
        if item.unit_price_fen != unit or item.original_unit_price_fen != original_unit:
            problems.append(f"{source.name} 单价或原价与菜单及已核实选配差价不符")
        if item.line_total_fen != unit * item.quantity:
            problems.append(f"{source.name} 小计与核实单价×数量不符")
        if item.servings != facts.servings:
            problems.append(f"{source.name} 餐份自报 {item.servings}，实际主食餐份为 {facts.servings}")
        if item.spicy_level != facts.spicy_level:
            problems.append(f"{source.name} 辣度与实际餐品或选配不符")
        if strict_details:
            if (item.food_role != source.food_role
                    or item.beverage_status != facts.beverage_status
                    or item.non_spicy_servings != facts.non_spicy_servings
                    or item.side_servings != facts.side_servings
                    or item.components != facts.components):
                problems.append(f"{source.name} 组成、角色或主食/小食餐份与事实不符")
            if source.is_combo and item.selected_options != facts.selected_options:
                problems.append(f"{source.name} 选配缺失或与事实不符")
        if requirements.exclude_beverage and facts.beverage_status != "none":
            problems.append(f"{source.name} 实际选配含饮品或含饮料情况未知，不能满足不带饮料")
        if requirements.non_spicy_people == requirements.people and not confirmed_mild(facts.spicy_level, facts.components):
            problems.append(f"{source.name} 含辣味或未知辣度餐品/选配，不满足全员不辣")
        servings += facts.servings * item.quantity
        non_spicy += facts.non_spicy_servings * item.quantity
        sides += facts.side_servings * item.quantity
        total += unit * item.quantity
        subtotal += original_unit * item.quantity

    # 优惠只能使用服务端注入的已核实事实，客户端不能填优惠金额。
    if context is not None and context.promotions:
        from ..skill.promotions import apply_promotions
        total, applied = apply_promotions(
            {item.code: item.quantity for item in plan.items}, total,
            context.promotions, context.store_code,
        )
        if plan.applied_promotions != applied:
            problems.append("优惠明细与服务端已核实规则不符")
    elif plan.applied_promotions:
        problems.append("本次没有可核实优惠规则，不能自报额外优惠")
    if strict_details:
        main_target, safe_target, extra_target, _ = portion_targets(requirements)
        if servings < main_target or non_spicy < safe_target:
            problems.append("实际主食或不辣主食份数不足本次饭量要求")
        if extra_portions(servings, sides, main_target) < extra_target:
            problems.append("正餐加餐不足；甜品不计小食份数")
        if plan.side_servings_total != sides:
            problems.append("总小食餐份与实际组成不符")
        if context is not None and plan.price_basis != context.price_basis:
            problems.append("报价依据与本次数据来源不符")
        if not plan.summary.strip() or not plan.checks or not all(check.satisfied for check in plan.checks):
            problems.append("方案摘要或约束检查不完整")

    # 总额、原价、优惠、预算余额均由相同事实重建，防止伪造折扣或够吃人数。
    for label, actual, expected in (
        ("总额", plan.total_fen, total),
        ("原价合计", plan.subtotal_fen, subtotal),
        ("优惠金额", plan.discount_fen, subtotal - total),
        ("预算", plan.budget_fen, requirements.total_budget_fen),
        ("预算余额", plan.remaining_fen, requirements.total_budget_fen - total),
        ("总餐份", plan.servings_total, servings),
        ("不辣餐份", plan.non_spicy_servings, non_spicy),
    ):
        if actual != expected:
            problems.append(f"{label}自报 {actual}，核实值为 {expected}")
    if total > requirements.total_budget_fen:
        problems.append(f"应付 {total / 100:.2f} 元超过预算 {requirements.total_budget_fen / 100:.2f} 元")
    if servings < requirements.people:
        problems.append(f"实际只有 {servings} 主食餐份，不足 {requirements.people} 人")
    if non_spicy < requirements.non_spicy_people:
        problems.append(f"实际不辣餐份 {non_spicy}，不足需要的 {requirements.non_spicy_people} 份")
    return problems


def validate_plans(
    plans: list[PlanOut],
    requirements: Requirements,
    menu_items: list[MenuItemOut],
    combo_details: dict[str, ComboDetailOut],
    *, context: PlanningContext | None = None, strict_details: bool = False,
) -> tuple[list[PlanOut], list[str]]:
    """返回通过的方案及被丢弃方案的说明。"""
    menu_map = {item.code: item for item in menu_items}
    kept: list[PlanOut] = []
    dropped: list[str] = []
    for plan in plans:
        problems = validate_plan(
            plan, requirements, menu_map, combo_details,
            context=context, strict_details=strict_details,
        )
        if problems:
            dropped.append(f"「{plan.title or plan.type}」未通过校验：" + "；".join(problems))
        else:
            kept.append(plan)
    return kept, dropped
