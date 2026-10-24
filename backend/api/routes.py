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

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import settings
from ..providers import McpError, McpTimeout
from ..schemas import ChatIn, ChatOut, MenuOut, OrderIn, OrderOut, PlanRequest, PlanResponse, StoreListOut
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
    "E_UPSTREAM": 502,
    "E_UPSTREAM_TIMEOUT": 504,
    "E_PLAN_NOT_FOUND": 404,
    "E_PLAN_EXPIRED": 409,
    # 收到了已明确但当前全链路不支持的硬约束：不能忽略约束后返回普通方案
    "E_CAPABILITY_UNAVAILABLE": 409,
    "E_INTERNAL": 500,
}


def fail(error_code: str, msg: str, data=None) -> JSONResponse:
    status = ERROR_STATUS.get(error_code, 500)
    return JSONResponse(
        {"code": error_code, "msg": msg, "data": data}, status_code=status
    )


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
    from ..services.capabilities import load_skill_module, static_capabilities

    payload = settings.describe()
    # 能力标记反映「输入 + 数据 + 决策」全链路；这里给的是与门店无关的部分
    payload["contract_version"] = "1.1"
    payload["capabilities"] = static_capabilities(skill_module=load_skill_module())
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
    try:
        result: ChatOut = await chat_service.parse(payload)
    except Exception as exc:  # noqa: BLE001
        return fail("E_INTERNAL", f"需求解析失败：{exc}")
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
        return fail("E_INTERNAL", f"方案生成失败：{exc}")
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
