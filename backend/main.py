"""应用入口（A 职责）。

启动：backend 目录下执行
    ../.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8000

真实 MCP 调用需要 backend/.env 提供 MCD_MCP_URL 与 MCD_MCP_TOKEN
（仓库不提交，参考 .env.example）。没有凭据时用 DATA_MODE=demo 走本地快照。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api.routes import router, unhandled_handler, validation_handler
from .services import menu_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await menu_service.aclose()


app = FastAPI(
    title="麦门 AI 点餐助手 API",
    version="0.1.0",
    description="多人聚餐点餐方案服务；模拟结算，不产生真实交易",
    lifespan=lifespan,
)

# 开发阶段前后端分进程，放开跨域；封板前按部署方式收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.add_exception_handler(RequestValidationError, validation_handler)
app.add_exception_handler(Exception, unhandled_handler)


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    """本地演示入口；需求和方案仍由 API 计算。"""
    return FileResponse(Path(__file__).parent / "web" / "index.html")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
