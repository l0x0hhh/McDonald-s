"""Official pricing and durable at-most-once order creation.

An ambiguous mutating call is never retried. The caller may replay its original
key to recover a saved result, but not to issue another upstream mutation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from ..providers.checkout_provider import CheckoutProvider, OFFICIAL_PAYMENT_HOSTS
from ..schemas import OrderIn, OrderOut, PlanOut, Requirements
from .order_repository import OrderRepository

QUOTE_TTL_SECONDS = 5 * 60
_PRICE_FIELDS = ("price", "originalPrice", "discount", "productPrice", "productOriginalPrice",
                 "packingPrice", "packingOriginalPrice", "tablewarePrice", "deliveryPrice", "deliveryOriginalPrice")
_STATUS = {"1": "pending_payment", "2": "preparing", "10": "preparing", "4": "delivering",
           "6": "completed", "8": "completed", "7": "cancelled",
           "待支付": "pending_payment", "已支付": "paid", "配餐中": "preparing",
           "配餐中-已支付": "preparing", "配餐中-餐厅确认配餐中": "preparing",
           "配送中": "delivering", "已完成": "completed", "已评价": "completed", "已取消": "cancelled",
           "订单配餐中": "preparing", "订单配送中": "delivering",
           "订单已完成": "completed", "订单已取消": "cancelled"}
_CHINA_TIME = timezone(timedelta(hours=8))


class CheckoutError(Exception):
    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _fen(value):
    return value if type(value) is int and value >= 0 else None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _order_status(data: dict) -> str:
    # Live orderStatus is a Chinese label, despite the numeric-string schema.
    # The separate `status` field has different semantics (live pending=10),
    # so it must never be interpreted through the orderStatus numeric mapping.
    return _STATUS.get(str(data.get("orderStatus")).strip(), "unknown")


def _payment_expiry(value) -> str | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value.strip()) < 16 or value.strip()[10] not in "T ":
            raise ValueError("invalid payment expiry")
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_CHINA_TIME)
        return parsed.isoformat()
    except (ValueError, TypeError, OverflowError):
        # A malformed upstream expiry is not an absent expiry. This non-date
        # sentinel also fails the browser's expiry check in every timezone.
        return "invalid"


def _over_budget_data(plan: PlanOut, arguments: dict, price: int, budget: int) -> dict:
    return {"total_fen": price, "budget_fen": budget, "over_by_fen": price - budget,
            "basket": {"plan_id": plan.plan_id, **arguments}}


def _business_data(payload) -> dict:
    if not isinstance(payload, dict) or payload.get("success") is False or payload.get("isError"):
        raise ValueError("official tool returned failure")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("official tool returned no data")
    return data


def _payment_url(value) -> str:
    if not isinstance(value, str) or any(ord(c) <= 32 for c in value) or "\\" in value:
        raise ValueError("invalid official payment link")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname not in OFFICIAL_PAYMENT_HOSTS
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443)):
        raise ValueError("unverified official payment host")
    return value


def _arguments(plan: PlanOut, store_code: str) -> dict:
    if not plan.items or not store_code:
        raise CheckoutError("E_QUOTE_FAILED", "购物篮或门店信息不完整，请重新搭配")
    items = []
    for item in plan.items:
        rounds = {}
        for option in item.selected_options:
            rounds.setdefault(option.round_id, []).append({"code": option.option_code, "quantity": option.quantity})
        line = {"productCode": item.code, "quantity": item.quantity}
        if rounds:
            line["roundList"] = [{"round": key, "comboItemList": sorted(values, key=lambda v: (v["code"], v["quantity"]))}
                                 for key, values in sorted(rounds.items())]
        items.append(line)
    return {"storeCode": store_code, "orderType": 1, "beType": 1, "items": items}


def _take_ways(value) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError("official take ways missing")
    result, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("code"), str) or not entry["code"] or entry["code"] in seen:
            raise ValueError("invalid official take way")
        seen.add(entry["code"])
        result.append({"code": entry["code"], "title": str(entry.get("title") or ""), "subtitle": str(entry.get("subtitle") or "")})
    return result


def _expiry(data: dict, now: float) -> float:
    expires = now + QUOTE_TTL_SECONDS
    for key in ("expiresAt", "expires_at", "quoteExpiresAt"):
        if data.get(key) is not None:
            raw = data[key]
            try:
                upstream = float(raw) if isinstance(raw, (int, float)) else datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError, OverflowError):
                raise ValueError("invalid quote expiry")
            expires = min(expires, upstream)
    if expires <= now:
        raise ValueError("official quote expired")
    return expires


class CheckoutService:
    def __init__(self, plans=None, provider=None, repository=None):
        self.plans = plans
        self.provider = provider if provider is not None else CheckoutProvider()
        self.repository = repository if repository is not None else OrderRepository()

    async def quote(self, plan: PlanOut, store_code: str, requirements: Requirements) -> PlanOut:
        return await self._quote(plan, store_code, requirements, allow_over_budget=False)

    async def _quote(self, plan: PlanOut, store_code: str, requirements: Requirements, *, allow_over_budget: bool) -> PlanOut:
        if not plan.plan_id:
            raise CheckoutError("E_QUOTE_FAILED", "方案编号缺失，请重新搭配")
        arguments = _arguments(plan, store_code)
        try:
            data = _business_data(await self.provider.calculate_price(arguments))
            price = _fen(data.get("price"))
            if price is None:
                raise ValueError("price must be integer fen")
            ways = _take_ways(data.get("takeWayList"))
            now = time.time()
            expires = _expiry(data, now)
        except Exception as exc:
            raise CheckoutError("E_QUOTE_FAILED", "暂时没有取得官方报价，请稍后重试") from exc
        if price > requirements.total_budget_fen and not allow_over_budget:
            raise CheckoutError("E_QUOTE_OVER_BUDGET", "官方报价超出预算，请重新搭配",
                                _over_budget_data(plan, arguments, price, requirements.total_budget_fen))
        quoted = plan.model_copy(deep=True)
        quoted.total_fen = price
        # Only reported fields are official facts. Do not derive absent fees.
        quoted.quote_breakdown = {key: _fen(data.get(key)) for key in _PRICE_FIELDS}
        if quoted.quote_breakdown["originalPrice"] is not None:
            quoted.subtotal_fen = quoted.quote_breakdown["originalPrice"]
        if quoted.quote_breakdown["discount"] is not None:
            quoted.discount_fen = quoted.quote_breakdown["discount"]
        quoted.budget_fen = requirements.total_budget_fen
        quoted.remaining_fen = requirements.total_budget_fen - price
        quoted.price_basis = "live_quote"
        quoted.take_way_options = ways
        quoted.quote_id = uuid.uuid4().hex
        quoted.quoted_at = datetime.fromtimestamp(now, timezone.utc).isoformat()
        # Schema productList does not carry a complete option signature. Keep
        # all line amounts as menu reference rather than allocating discounts.
        for item in quoted.items:
            item.line_price_basis = "menu_reference"
        self.repository.save_quote({"quote_id": quoted.quote_id, "plan": quoted.model_dump(),
                                    "requirements": requirements.model_dump(), "store_code": store_code,
                                    "arguments": arguments, "basket_hash": _hash(arguments), "expires_at": expires})
        return quoted

    def _replay(self, record: dict, fingerprint: str, key: str) -> OrderOut:
        if record["fingerprint"] != fingerprint:
            raise CheckoutError("E_IDEMPOTENCY_CONFLICT", "这个请求编号已用于另一份订单，请重新确认")
        if record["result"] is not None:
            return OrderOut.model_validate(record["result"])
        self._mark_unknown(key)
        raise CheckoutError("E_ORDER_SUBMISSION_UNKNOWN", "订单提交结果正在核对，请勿重复下单", {"idempotency_key": key})

    def _mark_unknown(self, key: str) -> None:
        try:
            self.repository.mark_unknown(key)
        except (sqlite3.Error, OSError):
            # The already committed submitting row is itself an at-most-once
            # barrier. An unavailable disk must not turn ambiguity into retry.
            pass

    def _save_updated_plan(self, plan: PlanOut, store: str, requirements: Requirements):
        if self.plans is not None:
            self.plans.save(plan, store, requirements)

    async def create_order(self, payload: OrderIn) -> OrderOut:
        fingerprint = _hash({"plan_id": payload.plan_id, "quote_id": payload.quote_id, "take_way_code": payload.take_way_code})
        # Durable replay takes precedence over chat state, quote TTL and menus.
        existing = self.repository.get_by_key(payload.idempotency_key)
        if existing is not None:
            return self._replay(existing, fingerprint, payload.idempotency_key)
        record = self.repository.get_quote(payload.quote_id)
        if record is None or record["plan"]["plan_id"] != payload.plan_id:
            raise CheckoutError("E_QUOTE_NOT_FOUND", "报价不存在，请重新搭配")
        plan = PlanOut.model_validate(record["plan"])
        requirements = Requirements.model_validate(record["requirements"])
        store_code = record["store_code"]
        if self.plans is not None:
            from .plan_store import PlanExpiredError, PlanNotFoundError
            try:
                current, current_store, current_requirements = self.plans.get(payload.plan_id)
            except (PlanExpiredError, PlanNotFoundError) as exc:
                raise CheckoutError("E_PLAN_NOT_FOUND", "方案已更新，请重新搭配") from exc
            if current_requirements.model_dump() != requirements.model_dump():
                # A new budget, people count or dietary request invalidates
                # the composition validation as well as its quote. Do not
                # silently submit the same basket under a stale requirement.
                raise CheckoutError("E_PLAN_NOT_FOUND", "需求已更新，请重新搭配")
            if current.quote_id != payload.quote_id:
                raise CheckoutError("E_PRICE_CHANGED", "报价已更新，请重新确认", {"updated_plan": current.model_dump()})
            if _hash(_arguments(current, current_store)) != record["basket_hash"]:
                updated = await self.quote(current, current_store, current_requirements)
                self._save_updated_plan(updated, current_store, current_requirements)
                raise CheckoutError("E_PRICE_CHANGED", "购物篮已更新，请重新确认", {"updated_plan": updated.model_dump()})
        if payload.take_way_code not in {entry["code"] for entry in plan.take_way_options}:
            raise CheckoutError("E_TAKE_WAY_INVALID", "请从报价提供的取餐方式中选择")
        refreshed = await self._quote(plan, store_code, requirements, allow_over_budget=True)
        old_ways = sorted(plan.take_way_options, key=lambda v: v["code"])
        new_ways = sorted(refreshed.take_way_options, key=lambda v: v["code"])
        if (record["expires_at"] <= time.time() or refreshed.total_fen != plan.total_fen
                or refreshed.quote_breakdown != plan.quote_breakdown or old_ways != new_ways):
            self._save_updated_plan(refreshed, store_code, requirements)
            raise CheckoutError("E_PRICE_CHANGED", "报价或取餐方式已更新，请重新确认", {"updated_plan": refreshed.model_dump()})
        if refreshed.total_fen > requirements.total_budget_fen:
            raise CheckoutError("E_QUOTE_OVER_BUDGET", "官方报价超出预算，请重新搭配",
                                _over_budget_data(refreshed, record["arguments"], refreshed.total_fen,
                                                  requirements.total_budget_fen))
        won, existing = self.repository.claim_submission(payload.idempotency_key, fingerprint, record)
        if not won:
            return self._replay(existing, fingerprint, payload.idempotency_key)
        try:
            data = _business_data(await self.provider.create_order(dict(record["arguments"], takeWayCode=payload.take_way_code)))
            order_id = data.get("orderId")
            if not isinstance(order_id, str) or not order_id.strip():
                raise ValueError("official order id missing")
            url = _payment_url(data.get("payH5Url"))
            detail = data.get("orderDetail") or {}
            if not isinstance(detail, dict):
                raise ValueError("invalid order detail")
            status = _order_status(detail)
            result = OrderOut(order_id=order_id, plan_id=plan.plan_id, store_code=store_code,
                              items=plan.items, total_fen=plan.total_fen, status=status,
                              payment_url=url if status == "pending_payment" else None,
                              expires_at=_payment_expiry(detail.get("expirePayTime")), created_at=detail.get("createTime") or _utcnow())
            self.repository.save_result(payload.idempotency_key, result.model_dump(), url)
            return result
        except asyncio.CancelledError:
            self._mark_unknown(payload.idempotency_key)
            raise
        except Exception as exc:
            self._mark_unknown(payload.idempotency_key)
            raise CheckoutError("E_ORDER_SUBMISSION_UNKNOWN", "订单提交结果正在核对，请勿重复下单",
                                {"idempotency_key": payload.idempotency_key}) from exc

    async def get_order(self, order_id: str) -> OrderOut:
        record = self.repository.get_by_order_id(order_id)
        if record is None or record["result"] is None:
            raise CheckoutError("E_ORDER_NOT_FOUND", "没有找到这份订单")
        result = OrderOut.model_validate(record["result"])
        try:
            # Existing databases can still have their validated URL solely in
            # the previous pending result. Preserve it on the first query too.
            retained_url = record["record"].get("verified_payment_url") or result.payment_url
            if retained_url is not None:
                retained_url = _payment_url(retained_url)
            data = _business_data(await self.provider.query_order(order_id))
            if data.get("orderId") != order_id:
                raise ValueError("official order identity mismatch")
            result.status = _order_status(data)
            result.payment_url = retained_url if result.status == "pending_payment" else None
            # Missing/null metadata must not erase an already known deadline.
            raw_expiry = data.get("expirePayTime")
            result.expires_at = _payment_expiry(raw_expiry if raw_expiry is not None else result.expires_at)
            self.repository.save_result(record["idempotency_key"], result.model_dump(), retained_url)
            return result
        except Exception as exc:
            raise CheckoutError("E_ORDER_QUERY_FAILED", "暂时没有取得最新订单状态，请稍后再查") from exc

    async def aclose(self) -> None:
        if hasattr(self.provider, "aclose"):
            await self.provider.aclose()
