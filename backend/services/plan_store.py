"""方案存取（A 职责：快照与方案保存）。

方案由服务端保存并绑定门店、需求与生成时间；
结算时根据服务端保存的方案重建金额，不信任前端自报总价。

切换门店后旧方案继续保留但可被标记失效；方案超过 TTL 视为过期，
防止用旧价格结算。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from ..config import DATA_DIR
from ..schemas import PlanOut, Requirements

PLAN_TTL_SECONDS = 30 * 60
# 配餐硬约束变更时递增；旧方案须重新生成，不能跨策略版本继续结算。
PLAN_POLICY_VERSION = 3
_STORE_PATH = DATA_DIR / "plans.json"


class PlanNotFoundError(Exception):
    pass


class PlanExpiredError(Exception):
    pass


class _PlanRecord:
    def __init__(self, plan: PlanOut, store_code: str, requirements: Requirements) -> None:
        self.plan = plan
        self.store_code = store_code
        self.requirements = requirements
        self.created_at = time.time()


class PlanStore:
    """进程内缓存 + JSON 文件持久化（重启后仍可结算）。"""

    def __init__(self, path: Path = _STORE_PATH) -> None:
        self._path = path
        self._records: dict[str, _PlanRecord] = {}
        self._load()

    # ------------------------------------------------------------ 持久化

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for plan_id, rec in raw.items():
            try:
                if rec.get("policy_version") != PLAN_POLICY_VERSION:
                    continue
                self._records[plan_id] = _PlanRecord(
                    plan=PlanOut.model_validate(rec["plan"]),
                    store_code=rec["store_code"],
                    requirements=Requirements.model_validate(rec["requirements"]),
                )
                self._records[plan_id].created_at = rec.get("created_at", time.time())
            except Exception:  # noqa: BLE001 — 旧记录损坏时跳过，不让服务起不来
                continue

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            plan_id: {
                "policy_version": PLAN_POLICY_VERSION,
                "plan": rec.plan.model_dump(),
                "store_code": rec.store_code,
                "requirements": rec.requirements.model_dump(),
                "created_at": rec.created_at,
            }
            for plan_id, rec in self._records.items()
        }
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------ 对外

    def save(self, plan: PlanOut, store_code: str, requirements: Requirements) -> str:
        plan_id = plan.plan_id or uuid.uuid4().hex[:12]
        plan.plan_id = plan_id
        self._records[plan_id] = _PlanRecord(plan, store_code, requirements)
        # 只保留最近 50 个方案，避免文件无限增长
        if len(self._records) > 50:
            for plan_id_old in sorted(
                self._records, key=lambda k: self._records[k].created_at
            )[:-50]:
                self._records.pop(plan_id_old, None)
        self._flush()
        return plan_id

    def get(self, plan_id: str) -> tuple[PlanOut, str, Requirements]:
        record = self._records.get(plan_id)
        if record is None:
            raise PlanNotFoundError(f"方案不存在或已重启清理：{plan_id}")
        if time.time() - record.created_at > PLAN_TTL_SECONDS:
            raise PlanExpiredError(f"方案已过期（超过 {PLAN_TTL_SECONDS // 60} 分钟），请重新生成")
        return record.plan, record.store_code, record.requirements

    def invalidate_other_stores(self, store_code: str) -> int:
        """只保留指定门店的方案：切换门店后，其他门店的旧价格一律失效。"""
        removed = 0
        for plan_id in [
            k for k, v in self._records.items() if v.store_code != store_code
        ]:
            self._records.pop(plan_id, None)
            removed += 1
        if removed:
            self._flush()
        return removed

    def invalidate_all(self) -> int:
        """清空全部方案。用于上游超时或数据降级后，防止用旧价格结算。"""
        count = len(self._records)
        if count:
            self._records.clear()
            self._flush()
        return count
