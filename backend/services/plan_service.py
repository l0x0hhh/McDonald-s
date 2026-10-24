"""方案生成流程串联（A 职责：编排，不写算法）。

与 B 的模块契约（B 接手时实现以下入口即可，A 不改动调用方式）：

    backend/skill/__init__.py 需要暴露：

        def generate_plans(
            items: list[MenuItemOut],          # 该门店全部商品（已含辣度/会员标记）
            requirements: Requirements,         # 完整需求
            nutrition: dict[str, NutritionOut], # 营养表（按商品名索引，可能为空）
        ) -> tuple[list[PlanOut], str | None]
        # 返回 (方案列表, 不可满足原因)；原因非空时列表应为空
        # 同步或 async 均可，本服务会兼容两种形式

B 模块不存在或抛错时，回退到 services/sample_planner.py 的演示样例，
并在响应 suggestions 里明确标注「demo_sample」，不冒充正式算法。
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Optional

from ..schemas import MenuItemOut, NutritionOut, PlanOut, PlanRequest, PlanResponse, Requirements
from .plan_store import PlanStore
from .sample_planner import generate_sample_plans


class PlanService:
    def __init__(self, menus=None, nutrition=None, store=None) -> None:
        # 依赖可注入：默认用全局单例，测试时可替换为独立实例便于模拟超时/降级
        from .menu_service import menu_service as _menu_service
        from .nutrition_service import nutrition_service as _nutrition_service

        self.menus = menus or _menu_service
        self.nutrition = nutrition or _nutrition_service
        self.store = store or PlanStore()
        self._skill_module: Any = None
        self._skill_checked = False

    # ------------------------------------------------------------ B 模块装载

    def _load_skill(self) -> Any:
        if self._skill_checked:
            return self._skill_module
        self._skill_checked = True
        try:
            self._skill_module = importlib.import_module("backend.skill")
        except ImportError:
            self._skill_module = None
        return self._skill_module

    async def _invoke_skill(
        self,
        items: list[MenuItemOut],
        requirements: Requirements,
        nutrition: dict[str, NutritionOut],
    ) -> Optional[tuple[list[PlanOut], Optional[str]]]:
        module = self._load_skill()
        if module is None or not hasattr(module, "generate_plans"):
            return None
        result = module.generate_plans(items, requirements, nutrition)
        if inspect.isawaitable(result):
            result = await result
        return result

    # ------------------------------------------------------------ 主流程

    async def create_plans(self, request: PlanRequest) -> PlanResponse:
        from ..providers import McpError, McpTimeout

        try:
            menu = await self.menus.get_menu(request.store_code)
        except (McpError, McpTimeout):
            # 文档 §7：上游失败时旧方案一律失效，防止用旧价格结算
            self.store.invalidate_all()
            raise

        # auto 模式降级到快照后，旧方案的价格可能已不代表当前数据源，同样失效
        if menu.degraded_reason:
            self.store.invalidate_all()

        nutrition = await self.nutrition.get_nutrition_map()

        skill_result: Optional[tuple[list[PlanOut], Optional[str]]] = None
        skill_error: Optional[str] = None
        try:
            skill_result = await self._invoke_skill(
                menu.items, request.requirements, nutrition
            )
        except Exception as exc:  # noqa: BLE001 — B 模块异常时降级到样例，不让接口挂掉
            skill_error = f"算法模块异常，已回退演示样例：{exc}"

        using_sample = skill_result is None
        if using_sample:
            plans, unsatisfiable = generate_sample_plans(
                menu.items, request.requirements, nutrition
            )
        else:
            plans, unsatisfiable = skill_result  # type: ignore[misc]

        suggestions: list[str] = []
        if using_sample:
            suggestions.append(
                "当前为接口演示样例（demo_sample）：组合与约束校验为简化逻辑，"
                "正式算法由 B 的 backend/skill 模块接入后替换"
            )
        if skill_error:
            suggestions.append(skill_error)
        if menu.degraded_reason:
            suggestions.append(menu.degraded_reason)

        # 服务端保存方案并绑定门店与需求，结算时按此重建金额
        for plan in plans:
            self.store.save(plan, request.store_code, request.requirements)

        # 有方案可用时，切换门店前的旧方案一并失效
        if plans:
            self.store.invalidate_other_stores(request.store_code)

        return PlanResponse(
            plans=plans,
            requirements=request.requirements,
            total_budget_fen=request.requirements.total_budget_fen,
            data_source=menu.data_source,
            fetched_at=menu.fetched_at,
            degraded_reason=menu.degraded_reason,
            unsatisfiable_reason=unsatisfiable,
            suggestions=suggestions,
        )


plan_service = PlanService()
