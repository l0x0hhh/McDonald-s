# 在线需求提取与配餐共用 Responses 请求边界；保留子组与否定校验。
"""Extract meal requirements while keeping every explicit hard constraint visible."""
from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from backend import config as _configuration  # 统一读取本地环境配置；不记录凭据。
from backend.schemas import Requirements, RequirementsDraft
from backend.skill.parser import _TASTE_WORDS, _current_data, _finish, _number, _total_people_matches, rule_extract


class ExtractionError(RuntimeError):
    """The online model did not return a trustworthy extraction."""
    code = "E_LLM_REQUEST_FAILED"
    message = "需求理解服务请求失败，请重试或直接填写人数和预算。"
    data = None


def _evidence_supports(field: str, value: Any, quote: str) -> bool:
    """Check that numbers and critical qualifiers appear in the cited words."""
    counts = [_number(word) for word in re.findall(r"\d+|[一二两三四五六七八九十]+", quote)]
    if field == "people":
        return type(value) is int and value in counts and bool(re.search(r"人|位", quote))
    if field == "budget_fen":
        try:
            # 中文金额可由模型补充，但证据必须是带货币单位的真实金额，不能引用人数充数。
            money_words = re.findall(r"(\d+(?:\.\d{1,2})?|[一二两三四五六七八九十]+)\s*(?:元|块)", quote)
            quoted_fen = [int(Decimal(word) * 100) if re.fullmatch(r"\d+(?:\.\d{1,2})?", word)
                          else (_number(word) * 100 if _number(word) is not None else None)
                          for word in money_words]
            return type(value) is int and value in quoted_fen
        except InvalidOperation:
            return False
    if field == "budget_type":
        return (value == "total" and bool(re.search(r"总预算|总共|合计|总价", quote))) or (
            value == "per_person" and bool(re.search(r"人均|每人|每位", quote)))
    if field == "dietary_constraints":
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
            return False
        return (bool(re.search(r"不吃辣|不能吃辣|不辣|忌辣|不要辣", quote)) and value[0].get("kind") == "non_spicy"
                and value[0].get("people") in counts)
    if field == "exclude_beverage":
        if value is True:
            return bool(re.search(r"不带(?:任何)?饮|不要(?:任何)?饮|不喝(?:任何)?饮|不需要饮|无饮|去掉饮|排除饮", quote)) and not bool(re.search(r"不是不|取消无|不排除", quote))
        return value is False and bool(re.search(r"可以|允许|需要|要喝|加饮|饮料不限|不是不|取消无|不排除", quote))
    if field == "membership_status":
        return (value == "active" and bool(re.search(r"有卡|已有|会员|开卡", quote))
                and not bool(re.search(r"没|未|不", quote))) or (
                    value == "none" and bool(re.search(r"没|未|无|不是", quote)))
    if field in ("meal_frequency_per_month", "habit_horizon_months"):
        unit = "次" if field == "meal_frequency_per_month" else "月"
        return type(value) is int and value in counts and unit in quote
    if field == "appetite":
        cues = {"light": r"轻饭量|饭量小|胃口小|吃得少", "normal": r"正常饭量|普通饭量", "heavy": r"大饭量|饭量大|胃口大|吃得多"}
        return value in cues and bool(re.search(cues[value], quote)) and not bool(re.search(r"不是|不要", quote))
    if field in ("preferred_tags", "habitual_tags"):
        return (isinstance(value, list) and bool(value)
                and all(tag in _TASTE_WORDS and re.search(_TASTE_WORDS[tag], quote) for tag in value)
                and (field != "habitual_tags" or bool(re.search(r"平时|通常|常买|经常买", quote))))
    return bool(quote)


def _validated_result(model_data: Mapping[str, Any], message: str, current: Any) -> dict[str, Any]:
    """Accept model additions only with literal evidence from this message."""
    if not isinstance(model_data, Mapping):
        raise ExtractionError("模型必须返回 JSON 对象")
    updates = model_data.get("updates")
    evidence = model_data.get("evidence")
    if not isinstance(updates, Mapping) or not isinstance(evidence, Mapping):
        raise ExtractionError("模型输出缺少可核查的结构")
    allowed = set(Requirements.model_fields)
    if any(key not in allowed for key in updates):
        raise ExtractionError("模型输出了未支持的需求字段")
    rule = rule_extract(message, current)
    data = rule["draft"].model_dump()
    previous = _current_data(current)
    for field, value in updates.items():
        quote = evidence.get(field)
        if not isinstance(quote, str) or not quote or quote not in message:
            continue
        if not _evidence_supports(field, value, quote):
            continue
        # 引文中的“2人”可能属于子组；必须回到完整原句核实总人数。
        if field == "people" and not any(
            _number(match.group(1)) == value
            for match in _total_people_matches(message, previous.get("people"))
        ):
            continue
        if field == "exclude_beverage" and "beverage_scope" in data.get("pending_fields", []):
            continue
        # 规则已处理否定与更正，模型不能通过截短引文恢复被否定的饭量或口味。
        if field == "appetite" and value != data.get("appetite"):
            continue
        if field in ("preferred_tags", "habitual_tags") and not set(value).issubset(data.get(field) or []):
            continue
        if data.get(field) != previous.get(field):
            continue  # The explicit rule result wins over model interpretation.
        if field == "exclude_beverage" and previous.get(field) is True and value is False:
            continue
        if field == "dietary_constraints" and "dietary_constraints" in data.get("pending_fields", []):
            continue
        candidate = dict(data)
        candidate[field] = value
        try:
            RequirementsDraft.model_validate(candidate)
        except ValidationError:
            continue
        data = candidate
    return _finish(data, "llm")


def _request_model(message: str, current: Any, timeout: float) -> Mapping[str, Any]:
    """Extract on the same Responses transport; optional fields use explicit null."""
    schema = Requirements.model_json_schema()
    properties = {}
    for name, field in schema["properties"].items():
        field = dict(field)
        field.pop("default", None)
        properties[name] = {"anyOf": [field, {"type": "null"}]}
    extraction_schema = {
        "type": "object", "additionalProperties": False, "$defs": schema.get("$defs", {}),
        "properties": {
            "updates": {"type": "object", "additionalProperties": False,
                        "properties": properties, "required": list(properties)},
            "evidence": {"type": "object", "additionalProperties": False,
                         "properties": {name: {"type": ["string", "null"]} for name in properties},
                         "required": list(properties)},
        }, "required": ["updates", "evidence"],
    }
    payload = {
        "model": _configuration.settings.llm_model,
        "instructions": ("只提取这一句用户明确表达的点餐需求。只返回 JSON 对象，键为 updates、evidence。"
                "updates 只能包含字段 people、budget_type、budget_fen、dietary_constraints、"
                "exclude_beverage、appetite、preferred_tags、membership_status、meal_frequency_per_month、"
                "habit_horizon_months、habitual_tags。evidence 的每个同名键必须是原句逐字短引文。"
                "budget_type 仅 total/per_person；预算是人民币整数分；dietary_constraints 只用"
                "[{\"kind\":\"non_spicy\",\"people\":整数}]；exclude_beverage 是布尔值；"
                "appetite 仅 light/normal/heavy；口味仅 beef/chicken/fish/fried/grilled/sweet/sugar_free；"
                "membership_status 仅 unknown/none/active，用户自报 active 不代表资格已核验。"
                "未明确的信息不要填，不能猜预算口径、忌口人数或会员资格。"
                "其他过敏或忌口不要擅自承诺支持。不要计算餐品价格。"
                "总人数与其中/另外几人的偏好人数必须区分，不要用子组覆盖总人数。"
                "部分人不要饮料不等于整单不要；遇到按人分配饮料时不修改整单饮料字段。"
                "正确处理否定和更正，例如不是大饭量、不喜欢牛肉改成鸡肉。"),
        "input": json.dumps({"message": message, "current": _current_data(current)}, ensure_ascii=False),
        "text": {"format": {"type": "json_schema", "name": "requirement_updates", "strict": True,
                              "schema": extraction_schema}},
    }
    response = _responses_request(payload, timeout)
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError as exc:
            raise ExtractionError("模型返回格式错误，请重试") from exc
    if not isinstance(response, Mapping):
        raise ExtractionError("模型返回格式错误，请重试")
    return {key: {k: v for k, v in response.get(key, {}).items() if v is not None}
            for key in ("updates", "evidence")}


async def extract_requirements(
    message: str,
    current: RequirementsDraft | Requirements | Mapping[str, Any] | None = None,
    *,
    demo_mode: bool = False,
    timeout: float = 12,
) -> dict[str, Any]:
    """Return the shared chat contract; never conceal online failures as model success."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("请输入点餐需求")
    model_data = await asyncio.to_thread(_request_model, message, current, timeout)
    return _validated_result(model_data, message, current)


def _responses_request(payload: dict, timeout: float):
    """The production boundary is fixed to DeepSeek; no subscription/API fallback."""
    return _api_responses_request(payload, timeout)


def _api_responses_request(payload: dict, timeout: float):
    """Adapt the internal Responses contract to DeepSeek JSON chat completion."""
    from .services.llm_planner import PlanningError
    settings = _configuration.settings
    key = settings.deepseek_api_key
    if not key:
        raise PlanningError("E_LLM_NOT_CONFIGURED", "请先在设置中保存 DeepSeek API Key。")
    schema = payload.get("text", {}).get("format", {}).get("schema")
    instructions = str(payload.get("instructions") or "")
    instructions += "\n只返回一个符合以下 schema 的有效 JSON 对象，不要 Markdown 或额外说明。"
    if schema is not None:
        instructions += "\nJSON schema: " + json.dumps(schema, ensure_ascii=False)
    model_input = payload.get("input", "")
    if not isinstance(model_input, str):
        model_input = json.dumps(model_input, ensure_ascii=False)
    body = {"model": _configuration.DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": instructions},
                         {"role": "user", "content": model_input}],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"}, "stream": False}
    request = urllib.request.Request(
        _configuration.DEEPSEEK_BASE_URL + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        choices = result.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("missing structured response")
        choice = choices[0]
        if choice.get("finish_reason") not in (None, "stop"):
            raise ValueError("incomplete response")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("missing structured response")
        return content
    except urllib.error.HTTPError as exc:
        # Never surface upstream text, URLs or credential-bearing exception strings.
        if exc.code in (402, 429):
            try:
                error = json.loads(exc.read(8192)).get("error", {})
            except (ValueError, TypeError, AttributeError):
                error = {}
            if (exc.code == 402 or error.get("code") in ("credit_balance_exhausted", "insufficient_quota")
                    or error.get("type") == "insufficient_quota"):
                raise PlanningError("E_LLM_QUOTA", "DeepSeek API 余额或额度不足，请处理后重试。") from None
        raise PlanningError("E_LLM_REQUEST_FAILED", f"搭配服务请求失败（HTTP {exc.code}），请稍后重试。") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PlanningError("E_LLM_REQUEST_FAILED", "搭配服务请求失败或返回不完整，请稍后重试。") from None


async def request_responses(payload: dict, timeout: float = 60):
    return await asyncio.to_thread(_responses_request, payload, timeout)
