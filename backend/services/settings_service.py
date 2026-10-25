"""Local-only credential persistence and runtime refresh; no credential echo."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ..config import LOCAL_SETTINGS_PATH, DEEPSEEK_MODEL, read_local_settings, settings


class LocalSettingsStore:
    def __init__(self, path: Path = LOCAL_SETTINGS_PATH):
        self.path = Path(path)

    def _atomic_write(self, payload: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            # mkstemp creates a private file (0600 on POSIX); replacement is atomic.
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, updates: dict[str, str]) -> dict[str, str]:
        current = read_local_settings(self.path)
        current.update({key: value.strip() for key, value in updates.items() if value.strip()})
        self._atomic_write(current)
        return current


local_store = LocalSettingsStore()


def public_settings() -> dict:
    """Configured means present, not that any external request has succeeded."""
    return {"provider": "deepseek", "model": DEEPSEEK_MODEL,
            "deepseek_configured": bool(settings.deepseek_api_key),
            "mcd_mcp_configured": bool(settings.mcd_mcp_token)}


async def save_settings(updates: dict[str, str]) -> dict:
    """Called under the API runtime gate, after all in-flight calls have ended."""
    from . import combo_service, menu_service, plan_service
    from .nutrition_service import nutrition_service
    from ..providers import McpMenuProvider
    from ..providers.checkout_provider import CheckoutProvider

    changed = {key: value.strip() for key, value in updates.items() if value.strip()}
    if not changed:
        return public_settings()
    # Invalidate before committing credentials: failed storage cannot leave a
    # newly persisted key paired with an old live client or a still-valid basket.
    plan_service.store.invalidate_all()
    checkout = plan_service._checkout
    if checkout is not None:
        checkout.repository.invalidate_quotes()
    old_providers = [menu_service._mcp, combo_service._mcp]
    if checkout is not None:
        old_providers.append(checkout.provider)
    values = local_store.save(changed)  # Disk failure leaves the live settings unchanged.
    # Swap every live reference before the first await. A disconnected browser
    # cannot interrupt the request between disk commit and runtime activation.
    settings.deepseek_api_key = values.get("deepseek_api_key", settings.deepseek_api_key)
    settings.mcd_mcp_token = values.get("mcd_mcp_key", settings.mcd_mcp_token)
    menu_service._mcp = McpMenuProvider()
    combo_service._mcp = McpMenuProvider()
    menu_service._menu_cache.clear()
    menu_service._stores_cache.clear()
    combo_service._cache.clear()
    nutrition_service._cache.clear()
    if checkout is not None:
        checkout.provider = CheckoutProvider()
    for provider in old_providers:
        try:
            await provider.aclose()
        except Exception:
            # The old provider has already been detached even if close fails.
            pass
    return public_settings()
