# 修订说明：价格与选配差价解析遇到非有限数值时返回未知，不泄漏转换异常。
"""麦当劳 MCP 在线适配（A 职责）。

实现一个不依赖官方 SDK 的最小 MCP 客户端（JSON-RPC over Streamable HTTP），
原因：依赖越少，评委本机复现越容易；MCP 交互只用到 initialize + tools/call 两个方法。

本模块只做数据获取与结构映射，不做任何业务决策。
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx

from ..config import settings
from .base import ProviderResult

_MCP_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")


class McpError(Exception):
    """MCP 调用失败（非超时）。"""


class McpTimeout(McpError):
    """MCP 调用超时。"""


def extract_json(text: str) -> dict:
    """从工具返回文本中提取业务 JSON。

    MCP 工具返回的 text 可能包含说明文字 + JSON 混排，
    用 raw_decode 逐个位置尝试，优先返回带业务标记（success/data/code）的对象。
    """
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    candidates: list[dict] = []
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            candidates.append(obj)
    if not candidates:
        raise McpError("无法从 MCP 工具返回中解析出 JSON")
    for obj in candidates:
        if "success" in obj or "data" in obj or "code" in obj:
            return obj
    return candidates[-1]


class McpHttpClient:
    """最小 MCP Streamable HTTP 客户端。"""

    def __init__(
        self,
        url: str,
        token: str,
        timeout: float = 25.0,
        context_header: Optional[str] = None,
    ) -> None:
        self._url = url
        self._token = token
        self._context = context_header
        self._client = httpx.AsyncClient(timeout=timeout)
        self._session_id: Optional[str] = None
        self._next_id = 1
        self._initialized = False

    # ------------------------------------------------------------ 底层

    async def _post(self, payload: dict, *, expect_body: bool = True) -> httpx.Response:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        # 某些 MCP 网关（如本地代理桥）需要额外的上下文头，可选
        if getattr(self, "_context", None):
            headers["X-WorkBuddy-MCP-Context"] = self._context
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            resp = await self._client.post(self._url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise McpTimeout(f"MCP 请求超时: {exc}") from exc
        except httpx.HTTPError as exc:
            raise McpError(f"MCP 网络错误: {exc}") from exc

        session_id = resp.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        if resp.status_code >= 400:
            raise McpError(f"MCP HTTP {resp.status_code}: {resp.text[:300]}")
        if not expect_body:
            return resp
        return resp

    def _decode_body(self, resp: httpx.Response) -> Any:
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            obj: Any = None
            for line in resp.text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    try:
                        obj = json.loads(line[len("data:"):].strip())
                    except json.JSONDecodeError:
                        continue
            if obj is None:
                raise McpError("SSE 响应中未找到 JSON 数据")
            return obj
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise McpError(f"MCP 返回非 JSON 内容: {resp.text[:200]}") from exc

    async def _request(self, method: str, params: Optional[dict] = None) -> Any:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        self._next_id += 1
        if params is not None:
            payload["params"] = params
        resp = await self._post(payload)
        data = self._decode_body(resp)
        if not isinstance(data, dict):
            raise McpError("MCP 响应格式异常")
        if "error" in data and data["error"]:
            raise McpError(f"MCP 错误: {data['error']}")
        return data.get("result")

    # ------------------------------------------------------------ 协议

    async def initialize(self) -> None:
        if self._initialized:
            return
        last_error: Optional[Exception] = None
        for version in _MCP_PROTOCOL_VERSIONS:
            try:
                await self._request(
                    "initialize",
                    {
                        "protocolVersion": version,
                        "capabilities": {},
                        "clientInfo": {"name": "mcd-backend", "version": "0.1.0"},
                    },
                )
            except McpError as exc:
                last_error = exc
                continue
            try:
                # initialized 通知：服务器一般回 202 空体，不解析内容
                await self._post(
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    expect_body=False,
                )
            except McpError:
                pass  # 个别服务器对该通知返回异常体，可忽略
            self._initialized = True
            return
        raise McpError(f"MCP initialize 失败: {last_error}")

    async def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict:
        await self.initialize()
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        if not isinstance(result, dict):
            raise McpError(f"工具 {name} 返回结构异常")
        if result.get("isError"):
            raise McpError(f"工具 {name} 执行失败: {str(result)[:300]}")
        content = result.get("content") or []
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        if not texts:
            raise McpError(f"工具 {name} 未返回文本内容")
        return extract_json("\n".join(texts))

    async def aclose(self) -> None:
        await self._client.aclose()


def parse_price_fen(value: Any) -> Optional[int]:
    """把 MCP 的价格字段（元，字符串或数字）换算成整数分。"""
    if value is None:
        return None
    try:
        return int((Decimal(str(value)) * 100).to_integral_value())
    # Infinity 等异常数值也属于未知价格，不能让转换异常中断菜单读取。
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def parse_diff_price_fen(value: Any) -> Optional[int]:
    """套餐选项差价，如 "+ ¥1"、"+ ¥0"、"- ¥2"。

    解析失败返回 None（记未知），绝不返回 0 —— 0 会被当成「已确认不加价」，
    与真实的「+ ¥0」无法区分。
    """
    if value is None:
        return None
    try:
        text = str(value).replace("¥", "").replace("+", "").replace(" ", "")
        return int((Decimal(text) * 100).to_integral_value())
    # 与主价格保持一致：非有限差价返回未知，绝不当作零差价。
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


class McpMenuProvider:
    """用 MCP 工具拉取门店 / 菜单 / 营养数据。"""

    def __init__(self) -> None:
        self._client: Optional[McpHttpClient] = None

    @property
    def client(self) -> McpHttpClient:
        if self._client is None:
            if not settings.has_mcp_credentials:
                raise McpError("未配置 MCD_MCP_URL / MCD_MCP_TOKEN")
            self._client = McpHttpClient(
                settings.mcd_mcp_url,
                settings.mcd_mcp_token,
                timeout=settings.mcp_timeout,
                context_header=settings.mcd_mcp_context or None,
            )
        return self._client

    async def get_stores(self, city: str, keyword: str) -> ProviderResult:
        payload = await self.client.call_tool(
            "query-nearby-stores",
            {"beType": settings.be_type, "searchType": 2, "city": city, "keyword": keyword},
        )
        return ProviderResult(
            data=payload.get("data") or [],
            data_source="live_mcp",
            fetched_at=payload.get("datetime", ""),
            trace_id=payload.get("traceId"),
            tool_name="query-nearby-stores",
        )

    async def get_menu(self, store_code: str) -> ProviderResult:
        arguments: dict[str, Any] = {
            "storeCode": store_code,
            "orderType": settings.order_type,
            "beType": settings.be_type,
        }
        payload = await self.client.call_tool("query-meals", arguments)
        return ProviderResult(
            data=payload.get("data"),
            data_source="live_mcp",
            fetched_at=payload.get("datetime", ""),
            trace_id=payload.get("traceId"),
            tool_name="query-meals",
        )

    async def get_meal_detail(self, store_code: str, meal_code: str) -> ProviderResult:
        """套餐选配结构。入参实测为 code（不是 mealCode），到店自取不传 beCode。"""
        payload = await self.client.call_tool(
            "query-meal-detail",
            {
                "storeCode": store_code,
                "code": str(meal_code),
                "orderType": settings.order_type,
                "beType": settings.be_type,
            },
        )
        return ProviderResult(
            data=payload,
            data_source="live_mcp",
            fetched_at=payload.get("datetime", ""),
            trace_id=payload.get("traceId"),
            tool_name="query-meal-detail",
        )

    async def get_nutrition(self) -> ProviderResult:
        payload = await self.client.call_tool("list-nutrition-foods", {})
        return ProviderResult(
            data=payload.get("data"),
            data_source="live_mcp",
            fetched_at=payload.get("datetime", ""),
            trace_id=payload.get("traceId"),
            tool_name="list-nutrition-foods",
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
