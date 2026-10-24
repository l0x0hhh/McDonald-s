# 修订说明：从菜单与实际选配重建价格、饮料和餐份事实，供样例与保存前校验共用。
"""只核实已选餐品，不承担 B 的组合搜索或优惠决策。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..providers.mapper import classify_spicy, is_staple_main
from ..schemas import (
    BeverageStatus, ComboDetailOut, ComboOptionOut, MenuItemOut,
    SelectedOptionOut, SpicyLevel,
)


@dataclass
class MealFacts:
    servings: int
    non_spicy_servings: int
    spicy_level: SpicyLevel
    beverage_status: BeverageStatus
    extra_price_fen: int = 0
    selected_options: list[SelectedOptionOut] = field(default_factory=list)


# 空选配只代表沿用默认；默认轮次或数量不明确时要求 B 显式选配。
def default_selection(detail: ComboDetailOut) -> tuple[list[SelectedOptionOut], list[str]]:
    selected: list[SelectedOptionOut] = []
    problems: list[str] = []
    for round_ in detail.rounds:
        quantity = round_.default_quantity
        if quantity is None and round_.min_quantity == round_.max_quantity:
            quantity = round_.min_quantity
        if quantity is None or quantity < 0:
            problems.append(f"轮次 {round_.name} 默认数量未知，须明确选配")
            continue
        if quantity == 0:
            continue
        defaults = [option for option in round_.options if option.is_default]
        if len(defaults) != 1:
            problems.append(f"轮次 {round_.name} 默认选项不唯一，须明确选配")
            continue
        option = defaults[0]
        selected.append(SelectedOptionOut(
            round_id=round_.round_id, round_name=round_.name,
            option_code=option.code, option_name=option.name,
            quantity=quantity, diff_price_fen=option.diff_price_fen,
        ))
    return selected, problems


# 显式选配必须是整份套餐的完整清单，遗漏必选轮次不能自动补回默认饮料。
def resolve_meal_facts(
    menu_item: MenuItemOut,
    detail: Optional[ComboDetailOut] = None,
    selected_options: Optional[list[SelectedOptionOut]] = None,
) -> tuple[Optional[MealFacts], list[str]]:
    if menu_item.is_merchandise:
        return None, ["周边或导航项不能计入餐食"]
    if not menu_item.is_combo:
        if selected_options:
            return None, ["单品不能携带套餐选配"]
        servings = int(menu_item.food_role == "main" and is_staple_main(
            menu_item.name, menu_item.category
        ))
        return MealFacts(
            servings=servings,
            non_spicy_servings=servings if menu_item.spicy_level == "mild" else 0,
            spicy_level=menu_item.spicy_level,
            beverage_status=menu_item.beverage_status,
        ), []
    if detail is None or detail.code != menu_item.code or not detail.rounds:
        return None, ["缺少匹配的套餐结构，无法核实实际餐份与规格"]

    problems: list[str] = []
    selected = selected_options
    if not selected:
        selected, default_problems = default_selection(detail)
        problems.extend(default_problems)

    rounds = {round_.round_id: round_ for round_ in detail.rounds}
    if len(rounds) != len(detail.rounds) or "" in rounds:
        problems.append("套餐轮次标识缺失或重复")
    counts = {round_id: 0 for round_id in rounds}
    seen: set[tuple[str, str]] = set()
    resolved: list[tuple[ComboOptionOut, int]] = []
    canonical: list[SelectedOptionOut] = []
    extra_price = 0
    for selection in selected:
        round_ = rounds.get(selection.round_id)
        if round_ is None:
            problems.append(f"选中了不存在的轮次 {selection.round_id}")
            continue
        matches = [option for option in round_.options if option.code == selection.option_code]
        if len(matches) != 1:
            problems.append(f"轮次 {round_.name} 的选项 {selection.option_code} 不存在或不唯一")
            continue
        option = matches[0]
        # 展示名也要与选项一致，避免选的是饮料却显示成小食。
        if selection.option_name is not None and selection.option_name != option.name:
            problems.append(f"选项 {option.code} 名称与套餐数据不符")
        if selection.round_name is not None and selection.round_name != round_.name:
            problems.append(f"轮次 {round_.round_id} 名称与套餐数据不符")
        key = (selection.round_id, selection.option_code)
        if key in seen:
            problems.append(f"轮次 {round_.name} 重复选择了 {option.name}，应合并数量")
        seen.add(key)
        if selection.quantity < 1:
            problems.append(f"选项 {option.name} 数量必须为正整数")
            continue
        counts[selection.round_id] += selection.quantity
        if option.max_quantity is not None and option.max_quantity != -1:
            if selection.quantity > option.max_quantity or option.max_quantity < 0:
                problems.append(f"选项 {option.name} 数量超过可选上限")
        if option.diff_price_fen is None:
            problems.append(f"选项 {option.name} 差价未知，不能按零差价计价")
        elif selection.diff_price_fen is not None and selection.diff_price_fen != option.diff_price_fen:
            problems.append(f"选项 {option.name} 差价与套餐数据不符")
        if option.diff_price_fen is not None:
            extra_price += option.diff_price_fen * selection.quantity
        resolved.append((option, selection.quantity))
        canonical.append(SelectedOptionOut(
            round_id=round_.round_id, round_name=round_.name,
            option_code=option.code, option_name=option.name,
            quantity=selection.quantity, diff_price_fen=option.diff_price_fen,
        ))

    for round_ in detail.rounds:
        count = counts.get(round_.round_id, 0)
        minimum, maximum = round_.min_quantity, round_.max_quantity
        if minimum is None or minimum < 0 or maximum is None or maximum < -1:
            problems.append(f"轮次 {round_.name} 数量边界未知或无效")
            continue
        if count < minimum or (maximum != -1 and count > maximum):
            problems.append(f"轮次 {round_.name} 已选 {count} 份，不满足数量边界 {minimum}/{maximum}")
    if problems:
        return None, problems

    # 小食和饮品不增加餐份；混合套餐只有全部食物均可确认不辣才计入不辣餐份。
    servings = sum(quantity for option, quantity in resolved
                   if option.food_role == "main" and is_staple_main(option.name, ""))
    food_levels = [classify_spicy(option.name) for option, _ in resolved
                   if option.food_role != "beverage"]
    spicy: SpicyLevel = "unknown"
    if "spicy" in food_levels:
        spicy = "spicy"
    elif food_levels and all(level == "mild" for level in food_levels):
        spicy = "mild"
    beverage: BeverageStatus = "unknown"
    if any(option.beverage_status == "contains" for option, _ in resolved):
        beverage = "contains"
    elif resolved and all(option.beverage_status == "none" for option, _ in resolved):
        beverage = "none"
    return MealFacts(
        servings=servings, non_spicy_servings=servings if spicy == "mild" else 0,
        spicy_level=spicy, beverage_status=beverage,
        extra_price_fen=extra_price,
        selected_options=canonical,
    ), []
