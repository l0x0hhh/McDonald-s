# 整合说明：保留 B 的模型配置；能力由接口统一读取显式声明并核对事实。
"""配置与运行模式。

所有凭据只从环境变量或本地 .env 读取，绝不写入代码或仓库。
.env 已在 .gitignore 中排除；仓库只提供 .env.example。
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


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
    """运行时配置。

    data_mode:
        live  —— 只走真实 MCP，失败即报错（用于证明真实集成）
        demo  —— 只走本地快照，全程标注演示数据
        auto  —— 优先真实 MCP，失败自动降级到快照并在响应里标明降级原因
    """

    def __init__(self) -> None:
        self.mcd_mcp_url: str = os.getenv("MCD_MCP_URL", "").strip()
        self.mcd_mcp_token: str = os.getenv("MCD_MCP_TOKEN", "").strip()
        self.mcd_mcp_context: str = os.getenv("MCD_MCP_CONTEXT", "").strip()
        self.data_mode: str = os.getenv("DATA_MODE", "auto").strip().lower()
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

    def describe(self) -> dict:
        """给前端看的安全信息，不含任何凭据。"""
        return {
            "contract_version": "1.1",
            "session_scope": "single_demo",
            "data_limitations": [
                "套餐只使用可核实的默认选配；缺少结构或差价时排除，暂未搜索任意换配组合。",
                "同篮子收银报价、卡费资格与活动读取尚未接通，相应能力仍不可用。",
                "菜单价格为参考估算；演示快照不是实时收银报价。",
            ],
            "data_mode": self.data_mode,
            "mcp_configured": self.has_mcp_credentials,
            "default_store_code": self.default_store_code,
            "default_city": self.default_city,
            "default_store_keyword": self.default_store_keyword,
            "order_type": self.order_type,
            "be_type": self.be_type,
        }


settings = Settings()
