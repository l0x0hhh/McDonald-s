"""数据提供方接口定义与共享数据结构。

ProviderResult 保存 MCP 原始 payload 与采集元信息（时间 / traceId / 来源）。
快照文件也按这个结构落盘，演示模式读取后与在线模式走完全相同的映射。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from ..schemas import DataSource


@dataclass
class ProviderResult:
    data: Any
    data_source: DataSource
    fetched_at: str
    trace_id: Optional[str] = None
    tool_name: Optional[str] = None


@dataclass
class MenuSnapshot:
    """一次完整采集的结果：门店 + 菜单 + 营养表。

    stores / menu / nutrition 均为 MCP 原始返回中的 data 部分，
    未经过任何删改，保证证据可回溯。
    """

    store_code: str
    fetched_at: str
    stores: Optional[Any] = None
    menu: Optional[Any] = None
    nutrition: Optional[Any] = None
    trace_ids: dict[str, str] = field(default_factory=dict)
    data_source: DataSource = "live_mcp"


class MenuProvider(Protocol):
    """数据提供方需要实现的三个能力。"""

    async def get_stores(self, city: str, keyword: str) -> ProviderResult: ...

    async def get_menu(self, store_code: str) -> ProviderResult: ...

    async def get_nutrition(self) -> ProviderResult: ...
