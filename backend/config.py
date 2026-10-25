# 整合说明：保留 B 的模型配置；能力由接口统一读取显式声明并核对事实。
"""配置与运行模式。

凭据从服务端环境变量、本地 .env 或本机 settings.local.json 读取。
本地凭据文件均被 .gitignore 排除；API 只公开是否已配置。
"""

from __future__ import annotations

import os
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOCAL_SETTINGS_PATH = DATA_DIR / "settings.local.json"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"


def read_local_settings(path: Path) -> dict[str, str]:
    """Read only supported secret fields; malformed local files expose no data."""
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: value[key].strip() for key in ("deepseek_api_key", "mcd_mcp_key")
            if isinstance(value.get(key), str) and value[key].strip()}


def _load_dotenv(path: Path) -> None:
    """极简 .env 加载器，避免为一行配置引入额外依赖。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        # 已存在的真实环境变量优先，便于命令行临时覆盖
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")
# 兼容既有 B 本地配置；系统与 backend/.env 的显式设置优先。
_load_dotenv(BASE_DIR.parent / ".env")


class Settings:
    """Online-only application settings; tests inject their own providers."""

    def __init__(self, local_path: Path = LOCAL_SETTINGS_PATH) -> None:
        local = read_local_settings(local_path)
        self.mcd_mcp_url: str = os.getenv("MCD_MCP_URL", "").strip() or "https://mcp.mcd.cn"
        self.mcd_mcp_token: str = local.get("mcd_mcp_key", os.getenv("MCD_MCP_TOKEN", "").strip())
        self.deepseek_api_key: str = local.get("deepseek_api_key", os.getenv("DEEPSEEK_API_KEY", "").strip())
        self.mcd_mcp_context: str = os.getenv("MCD_MCP_CONTEXT", "").strip()
        self.data_mode: str = "live"
        self.llm_provider = "deepseek"
        self.codex_model = os.getenv("CODEX_MODEL", "").strip()
        self.llm_base_url = DEEPSEEK_BASE_URL
        self.llm_model = DEEPSEEK_MODEL
        self.default_store_code: str = os.getenv("DEFAULT_STORE_CODE", "1400011").strip()
        self.default_city: str = os.getenv("DEFAULT_CITY", "广州").strip()
        self.default_store_keyword: str = os.getenv(
            "DEFAULT_STORE_KEYWORD", "天河区体育西路"
        ).strip()
        self.mcp_timeout: float = float(os.getenv("MCP_TIMEOUT", "25"))
        self.snapshot_path = DATA_DIR / "snapshot_1400011.json"

        # 本阶段固定到店自取：orderType=1 且不传 beCode
        self.order_type: int = 1
        self.be_type: int = 1

    @property
    def has_mcp_credentials(self) -> bool:
        return bool(self.mcd_mcp_url and self.mcd_mcp_token)

    @property
    def llm_configured(self) -> bool:
        return bool(self.deepseek_api_key)

    def describe(self) -> dict:
        """Online readiness only; never expose credentials."""
        from .services.capabilities import planner_capabilities
        from .services.llm_planner import SKILL_PATH
        ready = self.llm_configured and SKILL_PATH.is_file()
        return {
            "contract_version": "1.2", "session_scope": "single_user",
            "data_limitations": ["商品行价为菜单参考；总应付以官方报价为准。", "未知营养不计为零。"],
            "data_mode": "live", "mcp_configured": self.has_mcp_credentials,
            "llm_provider": self.llm_provider, "llm_configured": self.llm_configured, "planner_ready": ready,
            "ordering_available": ready and self.has_mcp_credentials,
            "default_store_code": self.default_store_code, "default_city": self.default_city,
            "default_store_keyword": self.default_store_keyword, "order_type": self.order_type,
            "be_type": self.be_type,
            "capabilities": planner_capabilities(ready=ready, online=self.has_mcp_credentials),
        }


settings = Settings()
