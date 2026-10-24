"""API 路由与统一错误处理（A 职责）。

响应约定（与方案 5.1 一致）：
- 成功：{"code": 0, "data": ..., "msg": "ok"}
- 失败：HTTP 状态码 + 稳定错误码（见 ERROR_STATUS）
  E_PARAM 参数有误；E_UPSTREAM 上游不可用；E_UPSTREAM_TIMEOUT 上游超时；
  E_PLAN_NOT_FOUND / E_PLAN_EXPIRED 方案不存在或过期；E_INTERNAL 内部错误
"""

from __future__ import annotations

import time
import uuid

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
    menu_service,
    plan_service,
)
from ..services.plan_service import CapabilityUnavailableError

router = APIRouter(prefix="/api/v1")


def ok(data) -> JSONResponse:
    return JSONResponse({"code": 0, "data": data, "msg": "ok"})


ERROR_STATUS = {
    "E_PARAM": 422,
    "E_UPSTREAM": 502,
    "E_UPSTREAM_TIMEOUT": 504,
    "E_PLAN_NOT_FOUND": 404,
    "E_PLAN_EXPIRED": 409,
    "E_INTERNAL": 500,
    "E_CAPABILITY_UNAVAILABLE": 409,
}


def fail(error_code: str, msg: str) -> JSONResponse:
    status = ERROR_STATUS.get(error_code, 500)
    return JSONResponse({"code": error_code, "msg": msg, "data": None}, status_code=status)


# ---------------------------------------------------------------- 配置


class ConfigOut(BaseModel):
    data_mode: str
    mcp_configured: bool
    default_store_code: str
    default_city: str
    order_type: int
    be_type: int


@router.get("/config")
async def get_config() -> JSONResponse:
    return ok(settings.describe())


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
    try:
        result: ChatOut = await chat_service.parse(payload)
    except Exception as exc:  # noqa: BLE001
        return fail("E_INTERNAL", f"需求解析失败：{exc}")
    return ok(result.model_dump())


# ---------------------------------------------------------------- 方案


@router.post("/plans")
async def create_plans(payload: PlanRequest) -> JSONResponse:
    try:
        result: PlanResponse = await plan_service.create_plans(payload)
    except McpTimeout as exc:
        return fail("E_UPSTREAM_TIMEOUT", str(exc))
    except McpError as exc:
        return fail("E_UPSTREAM", str(exc))
    except CapabilityUnavailableError as exc:
        return fail("E_CAPABILITY_UNAVAILABLE", str(exc))
    except Exception as exc:  # noqa: BLE001
        return fail("E_INTERNAL", f"方案生成失败：{exc}")
    return ok(result.model_dump())


@router.get("/campaigns")
async def get_campaigns(store_code: str, preferred_tags: list[str] = Query(default=[])) -> JSONResponse:
    """只读发现；上游尚无可核实活动时返回明确不可用状态。"""
    from ..skill.campaigns import recommend_campaigns
    try:
        menu = await menu_service.get_menu(store_code)
        context = await plan_service.get_context(menu)
    except (McpError, McpTimeout):
        return ok({"items": [], "status": "unavailable", "reason": "活动数据暂不可用"})
    requirements = Requirements(people=1, budget_fen=0, preferred_tags=preferred_tags)
    items = recommend_campaigns(context.campaigns, requirements, store_code)
    return ok({
        "items": items,
        "status": "available" if context.campaigns else "unavailable",
        "reason": None if context.campaigns else "尚无已核实的活动读取数据",
        "data_source": menu.data_source, "fetched_at": menu.fetched_at,
        "degraded_reason": menu.degraded_reason,
    })


# ---------------------------------------------------------------- 模拟结算


@router.post("/orders")
async def create_order(payload: OrderIn) -> JSONResponse:
    try:
        plan, store_code, _requirements = plan_service.store.get(payload.plan_id)
    except PlanNotFoundError as exc:
        return fail("E_PLAN_NOT_FOUND", str(exc))
    except PlanExpiredError as exc:
        return fail("E_PLAN_EXPIRED", str(exc))

    # 金额从服务端保存的方案重建，不信任请求里的任何金额字段
    order = OrderOut(
        order_id=uuid.uuid4().hex[:12],
        plan_id=plan.plan_id,
        store_code=store_code,
        items=plan.items,
        total_fen=plan.total_fen,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
    )
    return ok(order.model_dump())


# ---------------------------------------------------------------- 异常兜底


async def validation_handler(request: Request, exc: RequestValidationError):
    return fail("E_PARAM", f"参数有误：{exc.errors()[:3]}")


async def unhandled_handler(request: Request, exc: Exception):
    return fail("E_INTERNAL", f"内部错误：{exc}")
