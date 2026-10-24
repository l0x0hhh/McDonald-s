"""providers 包：MCP 在线适配与本地快照演示适配。

设计约定：
- 两种 Provider 都返回「MCP 原始形状」的数据，再统一经过 mapper 转换。
  这样演示模式和真实模式输出的字段结构永远一致，前端无需区分处理。
- 任何一层都不持有业务决策逻辑（那是 B 的 skill/ 的职责）。
"""

from __future__ import annotations

from .base import MenuProvider, MenuSnapshot, ProviderResult
from .mcp_provider import McpMenuProvider, McpError, McpTimeout
from .snapshot_provider import SnapshotMenuProvider, save_snapshot

__all__ = [
    "MenuProvider",
    "MenuSnapshot",
    "ProviderResult",
    "McpMenuProvider",
    "McpError",
    "McpTimeout",
    "SnapshotMenuProvider",
    "save_snapshot",
]
