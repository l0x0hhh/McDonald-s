"""Two write-only key fields, fixed provider/model, bounded validation errors."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .routes import fail, ok
from ..services import settings_service

router = APIRouter(prefix="/api/v1")


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    deepseek_api_key: str = Field(default="", max_length=4096)
    mcd_mcp_key: str = Field(default="", max_length=4096)

    @field_validator("deepseek_api_key", "mcd_mcp_key")
    @classmethod
    def credential_value(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid credential")
        return value.strip()


@router.get("/settings")
async def get_settings():
    return ok(settings_service.public_settings())


@router.post("/settings")
async def post_settings(request: Request):
    # Fetch Metadata carries the browser's original relation through Vite proxy.
    relation = request.headers.get("sec-fetch-site", "").lower()
    origin = request.headers.get("origin")
    if relation and relation not in ("same-origin", "none"):
        return fail("E_SETTINGS_ORIGIN", "设置仅接受同源页面提交。")
    if origin and relation != "same-origin":
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin != expected:
            return fail("E_SETTINGS_ORIGIN", "设置仅接受同源页面提交。")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return fail("E_SETTINGS_CONTENT_TYPE", "请使用 JSON 提交设置。")
    try:
        raw = await request.body()
        if len(raw) > 16384:
            return fail("E_PARAM", "设置参数有误。")
        payload = SettingsUpdate.model_validate_json(raw)
    except (ValidationError, ValueError):
        return fail("E_PARAM", "设置参数有误，请检查输入。")
    try:
        return JSONResponse({"code": 0, "data": await settings_service.save_settings(payload.model_dump()), "msg": "已保存"})
    except Exception:
        return fail("E_INTERNAL", "设置保存失败，请重试。")
