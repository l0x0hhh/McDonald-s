"""在线配餐与官方订单 API。

启动：backend 目录下执行
    ../.venv/Scripts/python.exe -m uvicorn backend.main:app --port 18000

真实 MCP 调用需要 backend/.env 提供 MCD_MCP_URL 与 MCD_MCP_TOKEN
（仓库不提交，参考 .env.example）。模型配餐还需服务端模型 API 配置。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .api.routes import router, unhandled_handler, validation_handler
from .api.settings_routes import router as settings_router
from .api.runtime_gate import RuntimeGateMiddleware
from .services import combo_service, menu_service, plan_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        try:
            await combo_service.aclose()
        finally:
            try:
                await menu_service.aclose()
            finally:
                if plan_service._checkout is not None:
                    await plan_service._checkout.aclose()


app = FastAPI(
    title="麦门 AI 点餐助手 API",
    version="1.2.0",
    description="模型配餐、官方核价与待支付订单；付款在官方页面完成",
    lifespan=lifespan,
)

# Only the known local frontend may initiate browser requests using this account.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(settings_router)
app.add_middleware(RuntimeGateMiddleware)
app.add_exception_handler(RequestValidationError, validation_handler)
app.add_exception_handler(Exception, unhandled_handler)


@app.get("/", include_in_schema=False)
async def home() -> RedirectResponse:
    """Keep one product entry; the obsolete sample page is not a runtime UI."""
    return RedirectResponse("http://127.0.0.1:5173/", status_code=307)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
