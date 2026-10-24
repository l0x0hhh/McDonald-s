# 修订说明：保存前按菜单和完整选配重算金额与餐份，不信任算法自报汇总。
"""A 的事实校验边界；不通过的方案直接丢弃并说明原因。"""

from __future__ import annotations

from ..schemas import ComboDetailOut, MenuItemOut, PlanOut, Requirements
from .meal_facts import resolve_meal_facts


def validate_plan(
    plan: PlanOut,
    requirements: Requirements,
    menu_map: dict[str, MenuItemOut],
    combo_details: dict[str, ComboDetailOut],
) -> list[str]:
    """返回问题列表；空列表代表通过。"""
    if not plan.items:
        return ["方案没有包含任何商品"]
    problems: list[str] = []
    servings = non_spicy = total = subtotal = 0
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
        if requirements.exclude_beverage and facts.beverage_status != "none":
            problems.append(f"{source.name} 实际选配含饮品或含饮料情况未知，不能满足不带饮料")
        servings += facts.servings * item.quantity
        non_spicy += facts.non_spicy_servings * item.quantity
        total += unit * item.quantity
        subtotal += original_unit * item.quantity

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
) -> tuple[list[PlanOut], list[str]]:
    """返回通过的方案及被丢弃方案的说明。"""
    menu_map = {item.code: item for item in menu_items}
    kept: list[PlanOut] = []
    dropped: list[str] = []
    for plan in plans:
        problems = validate_plan(plan, requirements, menu_map, combo_details)
        if problems:
            dropped.append(f"「{plan.title or plan.type}」未通过校验：" + "；".join(problems))
        else:
            kept.append(plan)
    return kept, dropped
