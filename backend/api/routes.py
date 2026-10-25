# 整合说明：合并套餐与活动接口，单会话聊天变更撤销旧方案。
"""API 路由与统一错误处理（A 职责）。

响应约定（与方案 5.1 一致）：
- 成功：{"code": 0, "data": ..., "msg": "ok"}
- 失败：HTTP 状态码 + 稳定错误码（见 ERROR_STATUS）
  E_PARAM 参数有误；E_UPSTREAM 上游不可用；E_UPSTREAM_TIMEOUT 上游超时；
  E_PLAN_NOT_FOUND / E_PLAN_EXPIRED 方案不存在或过期；E_INTERNAL 内部错误
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import settings
from ..providers import McpError, McpTimeout
from ..schemas import ChatIn, ChatOut, MenuOut, OrderIn, OrderOut, PlanRequest, PlanResponse, Requirements, StoreListOut
from ..services import (
    PlanExpiredError,
    PlanNotFoundError,
    chat_service,
    combo_service,
    menu_service,
    plan_service,
)

router = APIRouter(prefix="/api/v1")


def ok(data) -> JSONResponse:
    return JSONResponse({"code": 0, "data": data, "msg": "ok"})


ERROR_STATUS = {
    "E_PARAM": 422,
    "E_SETTINGS_ORIGIN": 403,
    "E_SETTINGS_CONTENT_TYPE": 415,
    "E_UPSTREAM": 502,
    "E_UPSTREAM_TIMEOUT": 504,
    "E_PLAN_NOT_FOUND": 404,
    "E_PLAN_EXPIRED": 409,
    # 收到了已明确但当前全链路不支持的硬约束：不能忽略约束后返回普通方案
    "E_CAPABILITY_UNAVAILABLE": 409,
    "E_INTERNAL": 500,
    "E_LLM_NOT_CONFIGURED": 503,
    "E_LLM_REQUEST_FAILED": 502,
    "E_PLANNING_TIMEOUT": 504,
    "E_LLM_QUOTA": 503,
    "E_CANDIDATE_INVALID": 502,
    "E_ONLINE_REQUIRED": 503,
    "E_QUOTE_FAILED": 502,
    "E_QUOTE_NOT_FOUND": 404,
    "E_QUOTE_EXPIRED": 409,
    "E_PRICE_CHANGED": 409,
    "E_BUDGET_EXCEEDED": 409,
    "E_QUOTE_OVER_BUDGET": 409,
    "E_TAKE_WAY_REQUIRED": 422,
    "E_TAKE_WAY_INVALID": 422,
    "E_IDEMPOTENCY_CONFLICT": 409,
    "E_ORDER_IN_PROGRESS": 409,
    "E_ORDER_SUBMISSION_UNKNOWN": 409,
    "E_ORDER_NOT_FOUND": 404,
    "E_ORDER_QUERY_FAILED": 502,
    "E_INVALID_PAYMENT_URL": 502,
}


def fail(error_code: str, msg: str, data=None) -> JSONResponse:
    status = ERROR_STATUS.get(error_code, 500)
    return JSONResponse(
        {"code": error_code, "msg": msg, "data": data}, status_code=status
    )


def service_failure(exc: Exception, message: str) -> JSONResponse:
    """Expose bounded domain errors, not transport bodies or credential-bearing URLs."""
    code = getattr(exc, "code", None)
    if code in ERROR_STATUS:
        return fail(code, getattr(exc, "message", message), getattr(exc, "data", None))
    return fail("E_INTERNAL", message)


# ---------------------------------------------------------------- 配置


class ConfigOut(BaseModel):
    data_mode: str
    mcp_configured: bool
    default_store_code: str
    default_city: str
    order_type: int
    be_type: int
    contract_version: str
    capabilities: dict


@router.get("/config")
async def get_config() -> JSONResponse:
    payload = settings.describe()
    payload["contract_version"] = "1.2"
    return ok(payload)


# ---------------------------------------------------------------- 门店


@router.get("/stores")
async def get_stores(city: str | None = None, keyword: str | None = None) -> JSONResponse:
    try:
        result: StoreListOut = await menu_service.get_stores(city, keyword)
    except McpTimeout as exc:
        return fail("E_UPSTREAM_TIMEOUT", str(exc))
    except McpError as exc:
        return fail("E_UPSTREAM", str(exc))
    return ok(result.model_dump())


# ---------------------------------------------------------------- 菜单（前端展示用可选接口）


@router.get("/menu/{store_code}")
async def get_menu(store_code: str) -> JSONResponse:
    try:
        result: MenuOut = await menu_service.get_menu(store_code)
    except McpTimeout as exc:
        return fail("E_UPSTREAM_TIMEOUT", str(exc))
    except McpError as exc:
        return fail("E_UPSTREAM", str(exc))
    return ok(result.model_dump())


# ---------------------------------------------------------------- 需求对话


@router.post("/chat")
async def chat(payload: ChatIn) -> JSONResponse:
    # config 已声明 single_demo；新输入使旧需求对应的价格失效。
    plan_service.store.invalidate_all()
    try:
        result: ChatOut = await chat_service.parse(payload)
    except Exception as exc:  # noqa: BLE001
        return service_failure(exc, "这次没识别出来，请重试或直接填写人数和预算。")
    return ok(result.model_dump())


# ---------------------------------------------------------------- 方案


@router.post("/plans")
async def create_plans(payload: PlanRequest) -> JSONResponse:
    from ..services.capabilities import CapabilityUnavailable

    try:
        result: PlanResponse = await plan_service.create_plans(payload)
    except CapabilityUnavailable as exc:
        # 硬约束无法满足：明确拒绝，不退回含饮料的普通方案
        return fail(
            "E_CAPABILITY_UNAVAILABLE", str(exc), {"capability": exc.capability}
        )
    except McpTimeout as exc:
        return fail("E_UPSTREAM_TIMEOUT", str(exc))
    except McpError as exc:
        return fail("E_UPSTREAM", str(exc))
    except Exception as exc:  # noqa: BLE001
        return service_failure(exc, "这次没配好，请稍后重试。")
    return ok(result.model_dump())


# ---------------------------------------------------------------- 套餐选配结构（供 C 展示规格与差价）


@router.get("/menu/{store_code}/combos")
async def get_combo_details(store_code: str, codes: str | None = None) -> JSONResponse:
    """返回套餐的轮次、选项与差价。

    codes 为逗号分隔的商品 code；不传时取该门店全部套餐（有数量上限）。
    取不到的套餐逐条说明原因，不给空的轮次冒充「不含饮料」。
    """
    from ..services.combo_service import MAX_COMBOS_PER_REQUEST

    try:
        menu = await menu_service.get_menu(store_code)
    except McpTimeout as exc:
        return fail("E_UPSTREAM_TIMEOUT", str(exc))
    except McpError as exc:
        return fail("E_UPSTREAM", str(exc))

    if codes:
        wanted = [c.strip() for c in codes.split(",") if c.strip()]
    else:
        wanted = [i.code for i in menu.items if i.is_combo and not i.is_merchandise]

    result = await combo_service.get_details(store_code, wanted)
    return ok(
        {
            "store_code": store_code,
            "combos": [c.model_dump() for c in result.details.values()],
            "missing_codes": result.missing_codes,
            "limit": MAX_COMBOS_PER_REQUEST,
            "data_source": result.data_source,
            "fetched_at": result.fetched_at,
            "degraded_reason": result.degraded_reason,
        }
    )


# ---------------------------------------------------------------- 活动发现（只读服务端事实）


@router.get("/campaigns")
async def get_campaigns(store_code: str, preferred_tags: list[str] = Query(default=[])) -> JSONResponse:
    from ..skill.campaigns import recommend_campaigns
    try:
        menu = await menu_service.get_menu(store_code)
        context = await plan_service.get_context(menu)
    except (McpError, McpTimeout):
        return ok({"items": [], "status": "unavailable", "reason": "活动数据暂不可用"})
    requirements = Requirements(people=1, budget_fen=0, preferred_tags=preferred_tags)
    items = recommend_campaigns(context.campaigns, requirements, store_code)
    return ok({
        "items": items, "status": "available" if context.campaigns else "unavailable",
        "reason": None if context.campaigns else "尚无已核实的活动读取数据",
        "data_source": menu.data_source, "fetched_at": menu.fetched_at,
        "degraded_reason": menu.degraded_reason,
    })


# ---------------------------------------------------------------- 官方订单与支付


@router.post("/orders")
async def create_order(payload: OrderIn) -> JSONResponse:
    try:
        order: OrderOut = await plan_service.checkout.create_order(payload)
    except PlanNotFoundError as exc:
        return fail("E_PLAN_NOT_FOUND", str(exc))
    except PlanExpiredError as exc:
        return fail("E_PLAN_EXPIRED", str(exc))
    except Exception as exc:
        return service_failure(exc, "订单结果暂未确认，请先查询订单，不要重复提交。")
    return ok(order.model_dump())


@router.get("/orders/{order_id}")
async def get_order(order_id: str) -> JSONResponse:
    try:
        order: OrderOut = await plan_service.checkout.get_order(order_id)
    except Exception as exc:
        return service_failure(exc, "暂时没查到订单状态，请稍后再查。")
    return ok(order.model_dump())


# ---------------------------------------------------------------- 异常兜底


async def validation_handler(request: Request, exc: RequestValidationError):
    return fail("E_PARAM", f"参数有误：{exc.errors()[:3]}")


async def unhandled_handler(request: Request, exc: Exception):
    return fail("E_INTERNAL", "服务暂时出了点问题，请稍后重试。")
