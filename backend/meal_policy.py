"""Shared dining rules; dessert is not a meal-sized extra or a shared portion."""
from collections.abc import Sequence
from math import ceil

from .schemas import Requirements


def portion_targets(requirements: Requirements) -> tuple[int, int, int, str]:
    people = requirements.people
    if requirements.appetite == "light":
        return people, requirements.non_spicy_people, 0, "轻饭量按每人 1 份主食配置；甜品不计正餐份量"
    multiplier = 2 if requirements.appetite == "heavy" else 1
    label = "大饭量" if multiplier == 2 else "正常饭量"
    assumption = (f"{label}按每人 {multiplier} 份主食、每 2 人至少 1 份正餐小食或额外主食配置；"
                  "甜品不抵加餐，小食份数不代表可供所有人分享")
    return people * multiplier, requirements.non_spicy_people * multiplier, ceil(people / 2), assumption


def extra_portions(main: int, side: int, main_target: int) -> int:
    return side + max(0, main - main_target)


def confirmed_mild(spicy_level: str, components: Sequence[dict] = ()) -> bool:
    """Every selected component, including drinks, needs explicit mild evidence."""
    if components:
        return all(component.get("spicy_level") == "mild" for component in components)
    return spicy_level == "mild"
