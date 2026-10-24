"""Summarize only nutrition tied to final, known food components and portions.

``items`` are the final PlanItemOut entries. For a combo, ``components`` must
list each final food with code/name and quantity per combo; the combo's own
name is never looked up as a food. A component may carry ``serving_size`` or
``serving_ratio``. If the nutrition row states a serving_size, it must match
the component size unless an explicit ratio converts it. Missing values remain
``None``; known zero is distinct from missing. Metric-level provenance is
preserved, including third-party estimates.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
import json
from math import isfinite
from typing import Any


METRICS = (
    "energy_kcal", "protein_g", "fat_g", "carbohydrate_g",
    "sodium_mg", "calcium_mg", "fiber_g",
)


def _data(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value):
        return asdict(value)
    return {}


def _positive_number(value: Any) -> bool:
    return type(value) in (int, float) and isfinite(value) and value > 0


def _metric_number(value: Any) -> bool:
    return type(value) in (int, float) and isfinite(value) and value >= 0


def _row(food: Mapping[str, Any], nutrition: Mapping[str, Any], *, direct: bool) -> Mapping[str, Any]:
    code, name = food.get("code"), food.get("name")
    for key in (code, name):
        if key is not None and key in nutrition:
            return _data(nutrition[key])
    # A may have attached a verified per-item row. Never use a combo parent row
    # as nutrition evidence for its components.
    return _data(food.get("nutrition")) if direct else {}


def _portion_ratio(food: Mapping[str, Any], row: Mapping[str, Any]) -> float | None:
    explicit = food.get("serving_ratio", food.get("portion_ratio"))
    if explicit is not None:
        return float(explicit) if _positive_number(explicit) else None
    actual_size = food.get("serving_size")
    row_size = row.get("serving_size")
    if row_size is not None and (actual_size is None or str(actual_size).strip() != str(row_size).strip()):
        return None
    return 1.0


def summarize_nutrition(items: Any, nutrition: Any, context: Any = None) -> dict[str, Any]:
    """Aggregate known metrics and report coverage separately for each one.

    ``nutrition`` is A's lookup by food code or name. The reserved ``context``
    parameter does not supply recommendations: the planner builds and checks
    an alternative basket before calling ``build_nutrition_suggestion``.
    """
    menu_nutrition = _data(nutrition)
    entries = items if isinstance(items, Sequence) and not isinstance(items, (str, bytes)) else []
    totals = {metric: 0.0 for metric in METRICS}
    known = {metric: 0 for metric in METRICS}
    missing_metrics: dict[str, list[dict[str, Any]]] = {metric: [] for metric in METRICS}
    missing_items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    total_units = 0
    total_units_known = True
    unknown_combo_units = 0

    def add_food(food: Mapping[str, Any], quantity: int, *, direct: bool) -> None:
        nonlocal total_units
        total_units += quantity
        label = {"code": food.get("code"), "name": food.get("name"), "quantity": quantity}
        row = _row(food, menu_nutrition, direct=direct)
        ratio = _portion_ratio(food, row) if row else None
        if not row or row.get("matched") is False or ratio is None:
            reason = "缺少匹配的营养资料" if not row or row.get("matched") is False else "营养规格或份量无法匹配"
            missing_items.append({**label, "reason": reason})
            for metric in METRICS:
                missing_metrics[metric].append(label)
            return
        for metric in METRICS:
            value = row.get(metric)
            if not _metric_number(value):
                missing_metrics[metric].append(label)
                continue
            totals[metric] += float(value) * ratio * quantity
            known[metric] += quantity
            metric_source = _data(_data(row.get("metric_sources")).get(metric))
            if not metric_source:
                raw_source = _data(row.get("metric_sources")).get(metric)
                if isinstance(raw_source, str):
                    metric_source = {"source": raw_source}
            sources.append({
                **label,
                "metric": metric,
                "source": metric_source.get("source", row.get("source")),
                "data_source": metric_source.get("data_source", row.get("data_source")),
                "estimated": metric_source.get("estimated", row.get("estimated", False)),
                "serving_size": metric_source.get("serving_size", row.get("serving_size")),
                "fetched_at": metric_source.get("fetched_at", row.get("fetched_at")),
            })

    for raw_item in entries:
        item = _data(raw_item)
        quantity = item.get("quantity")
        if type(quantity) is not int or quantity < 1:
            missing_items.append({"code": item.get("code"), "name": item.get("name"), "reason": "商品数量未知"})
            continue
        role = item.get("food_role")
        if role == "combo" or item.get("is_combo") is True:
            components = item.get("components")
            if not isinstance(components, Sequence) or isinstance(components, (str, bytes)) or not components:
                total_units_known = False
                unknown_combo_units += quantity
                label = {"code": item.get("code"), "name": item.get("name"), "quantity": quantity}
                missing_items.append({**label, "reason": "套餐最终组成未知，不能按套餐名称匹配营养"})
                for metric in METRICS:
                    missing_metrics[metric].append(label)
                continue
            for raw_component in components:
                component = _data(raw_component)
                count = component.get("quantity")
                if type(count) is not int or count < 1 or not (component.get("code") or component.get("name")):
                    total_units_known = False
                    unknown_combo_units += quantity
                    label = {"code": item.get("code"), "name": item.get("name"), "quantity": quantity}
                    missing_items.append({**label, "reason": "套餐成分或份数未核实"})
                    for metric in METRICS:
                        missing_metrics[metric].append(label)
                else:
                    add_food(component, quantity * count, direct=False)
        elif role in ("main", "side", "beverage", "dessert"):
            add_food(item, quantity, direct=True)
        else:
            total_units += quantity
            label = {"code": item.get("code"), "name": item.get("name"), "quantity": quantity}
            missing_items.append({**label, "reason": "食物类别未知，不能证明单品营养对应关系"})
            for metric in METRICS:
                missing_metrics[metric].append(label)

    coverage = {
        metric: {
            "known_units": known[metric],
            "total_units": total_units,
            "total_units_known": total_units_known,
            "unknown_combo_units": unknown_combo_units,
            "complete": total_units > 0 and total_units_known and known[metric] == total_units,
        }
        for metric in METRICS
    }
    visible_totals = {metric: totals[metric] if known[metric] else None for metric in METRICS}
    if total_units == 0 or not any(known.values()):
        status = "unavailable"
    elif all(coverage[metric]["complete"] for metric in METRICS):
        status = "complete"
    else:
        status = "partial"
    return {
        "status": status,
        "totals": visible_totals,
        "coverage": coverage,
        "total_units_known": total_units_known,
        "unknown_combo_units": unknown_combo_units,
        "missing_items": missing_items,
        "missing_metrics": missing_metrics,
        "sources": sources,
        "suggestions": [],
        "note": "仅汇总已匹配规格的已知部分；未知指标不按零计算。" if status != "complete" else "最终餐品营养指标均已匹配。",
    }


def build_nutrition_suggestion(current: Any, alternative: Any) -> dict[str, Any] | None:
    """Describe a validated replacement with fully sourced protein evidence.

    The planner must build both baskets from its menu facts and call
    ``validate_plan`` on the alternative before this helper. This function
    also checks the final budget, distinct item/option list, satisfied plan
    checks, full metric coverage and provenance. It makes no general health
    claim and never infers fiber status from missing data.
    """
    before, after = _data(current), _data(alternative)
    before_total, after_total = before.get("total_fen"), after.get("total_fen")
    before_budget, after_budget = before.get("budget_fen"), after.get("budget_fen")
    if not all(type(value) is int and value >= 0 for value in (before_total, after_total, before_budget, after_budget)):
        return None
    if before_budget != after_budget or before_total > before_budget or after_total > after_budget:
        return None
    before_items, after_items = before.get("items"), after.get("items")
    if not isinstance(before_items, list) or not isinstance(after_items, list) or not before_items or not after_items:
        return None
    def fingerprint(rows: list[Any]) -> tuple[str, ...] | None:
        try:
            return tuple(sorted(json.dumps({
                "code": _data(row).get("code"),
                "quantity": _data(row).get("quantity"),
                "selected_options": _data(row).get("selected_options"),
            }, ensure_ascii=False, sort_keys=True) for row in rows))
        except (TypeError, ValueError):
            return None
    before_key, after_key = fingerprint(before_items), fingerprint(after_items)
    if before_key is None or after_key is None or before_key == after_key:
        return None
    checks = after.get("checks")
    if not isinstance(checks, list) or not checks or any(_data(check).get("satisfied") is not True for check in checks):
        return None
    old_summary, new_summary = _data(before.get("nutrition_summary")), _data(after.get("nutrition_summary"))
    metric = "protein_g"
    old_coverage = _data(_data(old_summary.get("coverage")).get(metric))
    new_coverage = _data(_data(new_summary.get("coverage")).get(metric))
    old_value = _data(old_summary.get("totals")).get(metric)
    new_value = _data(new_summary.get("totals")).get(metric)
    if (old_coverage.get("complete") is not True or new_coverage.get("complete") is not True
            or not _metric_number(old_value) or not _metric_number(new_value)
            or new_value <= old_value):
        return None
    old_sources = [source for source in (old_summary.get("sources") or []) if _data(source).get("metric") == metric]
    new_sources = [source for source in (new_summary.get("sources") or []) if _data(source).get("metric") == metric]
    if not old_sources or not new_sources or any(not _data(source).get("source") for source in old_sources + new_sources):
        return None
    display_items = lambda rows: [{"code": _data(row).get("code"), "name": _data(row).get("name"),
                                    "quantity": _data(row).get("quantity"),
                                    "selected_options": _data(row).get("selected_options")}
                                   for row in rows]
    return {
        "kind": "verified_replacement",
        "metric": metric,
        "from_value": old_value,
        "to_value": new_value,
        "from_items": display_items(before_items),
        "to_items": display_items(after_items),
        "from_total_fen": before_total,
        "total_fen": after_total,
        "budget_fen": after_budget,
        "sources": {"before": old_sources, "after": new_sources},
        "reason": f"若想增加已知蛋白质，可选此已核实替换：{old_value:g} 克变为 {new_value:g} 克；总价 {after_total} 分，仍在预算内。",
    }
