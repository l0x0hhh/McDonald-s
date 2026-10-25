"""Strict model candidates -> server-owned facts, without search or price invention."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..meal_policy import confirmed_mild
from ..providers.mapper import classify_spicy, is_staple_main
from ..schemas import ConstraintCheck, PlanItemOut, PlanOut, SelectedOptionOut
from .meal_facts import resolve_meal_facts

SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "meal-planner" / "SKILL.md"


class PlanningError(RuntimeError):
    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


class _Option(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    round_id: str = Field(min_length=1)
    option_code: str = Field(min_length=1)
    quantity: int = Field(ge=1, le=100)


class _Line(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    code: str = Field(min_length=1)
    quantity: int = Field(ge=1, le=100)
    selected_options: list[_Option]


class _Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=300)
    reasons: list[str] = Field(min_length=1, max_length=5)
    items: list[_Line] = Field(min_length=1, max_length=60)


class _Candidates(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    candidates: list[_Candidate] = Field(max_length=3)


class _CandidateEnvelope(BaseModel):
    """Validate the outer contract without letting one malformed basket hide others."""
    model_config = ConfigDict(extra="forbid", strict=True)
    candidates: list[Any] = Field(max_length=3)


def _signature(line):
    return line.code, tuple(sorted((o.round_id, o.option_code, o.quantity) for o in line.selected_options))


def _model_facts(items, context):
    """Project selectable facts only; do not search, rank, or choose a basket."""
    projected_items, combos = [], {}
    for item in items:
        if (not item.available or not item.price_verified or item.is_merchandise
                or (item.requires_membership and not item.eligibility_verified)
                or item.max_quantity == 0):
            continue
        projected = item.model_dump(exclude={
            "image", "servings", "servings_verified", "non_spicy_servings",
            "role_evidence", "membership_note",
        })
        projected["reference_base_price_fen"] = item.price_fen
        if not item.is_combo:
            facts, _ = resolve_meal_facts(item, None, [])
            if facts is None:
                continue
            projected.update(main_servings_per_unit=facts.servings,
                             non_spicy_servings_per_unit=facts.non_spicy_servings,
                             servings=facts.servings, servings_verified=True,
                             non_spicy_servings=facts.non_spicy_servings)
        else:
            detail = context.combo_details.get(item.code)
            if (detail is None or detail.source != "live_mcp" or detail.code != item.code
                    or not detail.rounds):
                continue
            rounds, invalid = [], False
            round_ids = [r.round_id for r in detail.rounds]
            if "" in round_ids or len(set(round_ids)) != len(round_ids):
                continue
            for round_ in detail.rounds:
                minimum, maximum = round_.min_quantity, round_.max_quantity
                if (minimum is None or minimum < 0 or maximum is None or maximum < -1
                        or (maximum != -1 and maximum < minimum)):
                    invalid = True
                    break
                codes = [o.code for o in round_.options]
                options = []
                for option in round_.options:
                    if (not option.code or codes.count(option.code) != 1
                            or option.diff_price_fen is None
                            or (option.max_quantity is not None
                                and option.max_quantity != -1 and option.max_quantity < 1)):
                        continue
                    main = int(option.food_role == "main" and is_staple_main(option.name, ""))
                    options.append({**option.model_dump(exclude={"diff_price_raw"}),
                                    "main_servings_per_unit": main,
                                    "spicy_level": classify_spicy(option.name)})
                capacity = sum(minimum if o["max_quantity"] in (None, -1)
                               else o["max_quantity"] for o in options)
                if minimum > capacity:
                    invalid = True
                    break
                rounds.append({**round_.model_dump(exclude={"options"}), "options": options,
                               "quantity_basis": "per_combo_unit"})
            if invalid:
                continue
            combos[item.code] = {**detail.model_dump(exclude={
                "rounds", "servings", "servings_verified", "composition",
            }), "rounds": rounds, "quantity_basis": "per_combo_unit"}
            projected.update(main_servings_per_unit=None, non_spicy_servings_per_unit=None,
                             selection_required=True)
        projected_items.append(projected)
    projected_context = context.model_dump(exclude={"combo_details"})
    projected_context["combo_details"] = combos
    return projected_items, projected_context


def _candidate_feedback(candidate, index, items, requirements, context, error):
    """Explain rejected lines using the same fact resolver as the strict rebuild."""
    menu = {item.code: item for item in items}
    lines, main_total, mild_total, reference_total = [], 0, 0, 0
    for line in candidate.items:
        item = menu.get(line.code)
        facts, problems = None, []
        if item is None:
            problems = ["商品不在当前菜单"]
        elif item.is_combo and not line.selected_options:
            problems = ["每份套餐必须显式提供完整选配"]
        else:
            facts, problems = resolve_meal_facts(
                item, context.combo_details.get(line.code),
                [SelectedOptionOut(**option.model_dump()) for option in line.selected_options])
        main = facts.servings * line.quantity if facts is not None else None
        mild = facts.non_spicy_servings * line.quantity if facts is not None else None
        unit_price = item.price_fen + facts.extra_price_fen if facts is not None else None
        line_price = unit_price * line.quantity if unit_price is not None else None
        reference_total = (reference_total + line_price
                           if reference_total is not None and line_price is not None else None)
        main_total += main or 0
        mild_total += mild or 0
        lines.append({"code": line.code, "quantity": line.quantity,
                      "selected_options": [o.model_dump() for o in line.selected_options],
                      "main_servings_per_unit": facts.servings if facts is not None else None,
                      "non_spicy_servings_per_unit": facts.non_spicy_servings if facts is not None else None,
                      "main_servings": main, "non_spicy_servings": mild,
                      "reference_unit_price_fen": unit_price, "reference_line_total_fen": line_price,
                      "issues": problems})
    return {"candidate_index": index, "issues": [str(error)[:3000]], "line_facts": lines,
            "required_main_servings": requirements.people,
            "required_non_spicy_servings": requirements.non_spicy_people,
            "main_servings_total": main_total, "non_spicy_servings_total": mild_total,
            "reference_total_fen": reference_total, "total_budget_fen": requirements.total_budget_fen}


def rebuild_candidate(candidate: _Candidate, items, requirements, context, index=0) -> PlanOut:
    """Rebuild names, components, reference prices and hard constraints independently."""
    menu = {item.code: item for item in items}
    if len(menu) != len(items):
        raise ValueError("菜单商品编码不唯一")
    merged = {}
    for line in candidate.items:
        signature = _signature(line)
        if signature in merged:
            merged[signature].quantity += line.quantity
        else:
            merged[signature] = line.model_copy(deep=True)
    rebuilt, errors, quantities = [], [], {}
    for line in merged.values():
        item = menu.get(line.code)
        if item is None:
            errors.append(f"商品 {line.code} 不在当前菜单")
            continue
        quantities[line.code] = quantities.get(line.code, 0) + line.quantity
        if item.requires_membership and not item.eligibility_verified:
            errors.append(f"商品 {line.code} 资格未核验")
        if item.max_quantity is not None and quantities[line.code] > item.max_quantity:
            errors.append(f"商品 {line.code} 超过购买上限")
        detail = context.combo_details.get(line.code)
        if item.is_combo and (detail is None or detail.source != "live_mcp"):
            errors.append(f"商品 {line.code} 缺少在线套餐结构")
            continue
        if item.is_combo and not line.selected_options:
            errors.append(f"商品 {line.code} 必须显式提供完整选配")
            continue
        selection = [SelectedOptionOut(**o.model_dump()) for o in line.selected_options]
        facts, problems = resolve_meal_facts(item, detail, selection)
        if facts is None:
            errors.extend(f"商品 {line.code}：{problem}" for problem in problems)
            continue
        unit = item.price_fen + facts.extra_price_fen
        original = item.original_price_fen + facts.extra_price_fen
        if min(unit, original) < 0:
            errors.append(f"商品 {line.code} 选配后参考价无效")
            continue
        if requirements.exclude_beverage and facts.beverage_status != "none":
            errors.append(f"商品 {line.code} 含饮品或含饮品情况未知")
        if requirements.non_spicy_people == requirements.people and not confirmed_mild(facts.spicy_level, facts.components):
            errors.append(f"商品 {line.code} 含辣味或未知辣度成分")
        rebuilt.append(PlanItemOut(
            code=item.code, name=item.name, quantity=line.quantity, unit_price_fen=unit,
            original_unit_price_fen=original, line_total_fen=unit * line.quantity,
            servings=facts.servings, spicy_level=facts.spicy_level, image=item.image,
            selected_options=facts.selected_options, food_role=item.food_role,
            beverage_status=facts.beverage_status, non_spicy_servings=facts.non_spicy_servings,
            side_servings=facts.side_servings, components=facts.components,
            spec="、".join(f"{o.option_name}×{o.quantity}" for o in facts.selected_options) or None,
            line_price_basis="menu_reference",
        ))
    servings = sum(line.servings * line.quantity for line in rebuilt)
    mild = sum(line.non_spicy_servings * line.quantity for line in rebuilt)
    if servings < requirements.people:
        errors.append(f"实际主食 {servings} 份不足 {requirements.people} 人")
    if mild < requirements.non_spicy_people:
        errors.append(f"实际不辣主食 {mild} 份不足 {requirements.non_spicy_people} 人")
    if errors:
        raise ValueError("；".join(errors))
    reference = sum(line.line_total_fen for line in rebuilt)
    subtotal = sum(line.original_unit_price_fen * line.quantity for line in rebuilt)
    checks = [ConstraintCheck(name="人数", satisfied=True, detail=f"{servings} 份主食供 {requirements.people} 人"),
              ConstraintCheck(name="忌口", satisfied=True, detail=f"{mild} 份不辣主食")]
    if requirements.exclude_beverage:
        checks.append(ConstraintCheck(name="不带饮料", satisfied=True, detail="所有已选成分均已确认不含饮品"))
    return PlanOut(
        plan_id=uuid.uuid4().hex, type="recommended" if index == 0 else "alternative",
        title=candidate.title.strip(), summary=candidate.summary.strip(), reasons=candidate.reasons,
        items=rebuilt, subtotal_fen=subtotal, total_fen=reference, discount_fen=subtotal-reference,
        budget_fen=requirements.total_budget_fen, remaining_fen=requirements.total_budget_fen-reference,
        servings_total=servings, non_spicy_servings=mild,
        side_servings_total=sum(line.side_servings * line.quantity for line in rebuilt),
        checks=checks, price_basis="menu_estimate",
    )


class LLMPlanner:
    def __init__(self, requester=None, skill_path: Path = SKILL_PATH):
        self.requester = requester
        self.skill_text = skill_path.read_text(encoding="utf-8-sig")
        match = re.search(r"^version:\s*(\S+)", self.skill_text, re.M)
        if not match:
            raise ValueError("配餐 Skill 缺少版本")
        self.skill_version = match.group(1)
        self.skill_hash = hashlib.sha256(skill_path.read_bytes()).hexdigest()

    async def generate(self, items, requirements, context) -> list[PlanOut]:
        try:
            return await asyncio.wait_for(self._generate(items, requirements, context), timeout=60)
        except asyncio.TimeoutError as exc:
            raise PlanningError("E_LLM_REQUEST_FAILED", "搭配请求超时，请稍后重试。") from exc

    async def _generate(self, items, requirements, context):
        from ..config import settings
        from ..llm import request_responses
        payload = {
            "model": settings.llm_model,
            "instructions": self.skill_text,
            "input": "",
            "text": {"format": {"type": "json_schema", "name": "meal_candidates",
                                  "strict": True, "schema": _Candidates.model_json_schema()}},
        }
        model_items, model_context = _model_facts(items, context)
        data = {"items": model_items, "requirements": requirements.model_dump(), "context": model_context,
                "total_budget_fen": requirements.total_budget_fen,
                "pricing_context": {
                    "currency": "CNY", "amount_unit": "fen", "reference_price_basis": "menu_reference",
                    "line_quantity_basis": "purchased_units", "selected_option_quantity_basis": "per_combo_unit",
                    "official_quote_required": True, "reference_over_budget_proves_unsatisfiable": False,
                }}
        if context.quote_feedback:
            data["quote_feedback"] = context.quote_feedback
        errors = []
        for attempt in range(2):
            if errors:
                data["validation_errors"] = errors
            payload["input"] = json.dumps(data, ensure_ascii=False)
            try:
                response = await (self.requester or request_responses)(payload, 60)
            except PlanningError:
                raise
            except Exception as exc:
                raise PlanningError("E_LLM_REQUEST_FAILED", "搭配服务请求失败，请稍后重试。") from exc
            try:
                raw = json.loads(response) if isinstance(response, str) else response
                envelope = _CandidateEnvelope.model_validate(raw)
            except (ValidationError, ValueError, TypeError) as exc:
                errors = [{"candidate_index": None, "issues": [str(exc)[:3000]], "line_facts": []}]
                data["previous_candidates"] = []
                continue
            if not envelope.candidates:
                return []
            data["previous_candidates"] = envelope.candidates
            errors, plans, signatures = [], [], set()
            for index, value in enumerate(envelope.candidates):
                try:
                    candidate = _Candidate.model_validate(value)
                except (ValidationError, ValueError, TypeError) as exc:
                    errors.append({"candidate_index": index, "issues": [str(exc)[:3000]], "line_facts": []})
                    continue
                try:
                    plan = rebuild_candidate(candidate, items, requirements, context, len(plans))
                except (ValidationError, ValueError, TypeError) as exc:
                    errors.append(_candidate_feedback(candidate, index, items, requirements, context, exc))
                    continue
                signature = tuple(sorted((repr(_signature(line)), line.quantity) for line in plan.items))
                if signature not in signatures:
                    plans.append(plan)
                    signatures.add(signature)
            if plans:
                return plans
        raise PlanningError("E_CANDIDATE_INVALID", "这次没生成有效方案，请重试。", {"issues": errors})
