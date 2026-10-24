# 修复说明：在线模型沿用子组/否定校验，DeepSeek 使用 JSON 非思考输出并保留真实失败原因。
"""Extract meal requirements while keeping every explicit hard constraint visible."""
from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from backend import config as _configuration  # 统一读取本地环境配置；不记录凭据。
from backend.schemas import Requirements, RequirementsDraft
from backend.skill.parser import _TASTE_WORDS, _current_data, _finish, _number, _total_people_matches, rule_extract


class ExtractionError(RuntimeError):
    """The online model did not return a trustworthy extraction."""


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
    key = os.getenv("LLM_API_KEY", "").strip()
    if not key:
        raise ExtractionError("未配置 LLM_API_KEY；在线提取不可用")
    base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
    model = os.getenv("LLM_MODEL", "").strip()
    if not base_url.startswith("https://") or not model:
        raise ExtractionError("请配置 HTTPS 的 LLM_BASE_URL 和 LLM_MODEL")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                "只提取这一句用户明确表达的点餐需求。只返回 JSON 对象，键为 updates、evidence。"
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
                "正确处理否定和更正，例如不是大饭量、不喜欢牛肉改成鸡肉。"
            )},
            {"role": "user", "content": json.dumps({"message": message, "current": _current_data(current)}, ensure_ascii=False)},
        ],
    }
    # DeepSeek 默认开启思考；短需求提取显式关闭思考并要求 JSON，避免超时或解释文本。
    if urlsplit(base_url).hostname == "api.deepseek.com":
        payload.update({"thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
                        "stream": False, "max_tokens": 1200})
    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
        if body["choices"][0].get("finish_reason") not in (None, "stop"):
            raise ExtractionError("模型输出未完整结束，请重试")
        content = body["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("content 不是文本")
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        parsed = json.loads(content)
        if not isinstance(parsed, Mapping):
            raise ValueError("JSON 不是对象")
        return parsed
    except urllib.error.HTTPError as exc:
        raise ExtractionError(f"模型服务请求失败（HTTP {exc.code}），请检查 API Key、模型名和额度") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ExtractionError("模型服务请求失败或超时，请稍后重试") from exc
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ExtractionError("模型返回格式错误，请重试") from exc


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
    if demo_mode:
        result = rule_extract(message, current)
        result["parse_source"] = "demo_template"
        return result
    model_data = await asyncio.to_thread(_request_model, message, current, timeout)
    return _validated_result(model_data, message, current)
