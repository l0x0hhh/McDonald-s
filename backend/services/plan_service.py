# 修订说明：套餐需取结构后校验；按显式声明报告能力，回退样例时同步降级。
"""方案生成流程串联（A 职责：编排，不写算法）。

与 B 的模块契约（B 接手时实现以下入口即可，A 不改动调用方式）：

    backend/skill/__init__.py 需要暴露：

        def generate_plans(
            items: list[MenuItemOut],          # 该门店全部商品（已含辣度/会员/角色标记）
            requirements: Requirements,         # 完整需求
            nutrition: dict[str, NutritionOut], # 营养表（按商品名索引，可能为空）
            *, context: PlanningContext | None = None,   # 可选：套餐结构等事实
        ) -> tuple[list[PlanOut], str | None]
        # 返回 (方案列表, 不可满足原因)；原因非空时列表应为空
        # 同步或 async 均可，本服务会兼容两种形式
        # 旧的三参数签名仍然有效，此时 A 不会传 context。
        # 新能力需通过模块 CAPABILITIES 显式声明，不能由签名推断。

B 模块不存在或抛错时，回退到 services/sample_planner.py 的演示样例，
并在响应 suggestions 里明确标注「demo_sample」，不冒充正式算法。

硬约束：收到 exclude_beverage 等硬约束但全链路不支持时，直接抛
CapabilityUnavailable（HTTP 409），绝不忽略约束返回普通套餐。
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Optional

from ..schemas import (
    ComboDetailOut,
    MenuItemOut,
    NutritionOut,
    PlanOut,
    PlanRequest,
    PlanResponse,
    PlanningContext,
    Requirements,
)
from .plan_store import PlanStore
from .plan_validator import validate_plans
from .sample_planner import generate_sample_plans


class PlanService:
    def __init__(self, menus=None, nutrition=None, store=None, combos=None) -> None:
        # 依赖可注入：默认用全局单例，测试时可替换为独立实例便于模拟超时/降级
        from .combo_service import combo_service as _combo_service
        from .menu_service import menu_service as _menu_service
        from .nutrition_service import nutrition_service as _nutrition_service

        self.menus = menus or _menu_service
        self.nutrition = nutrition or _nutrition_service
        self.combos = combos or _combo_service
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
        # 缺少可调用入口时与未安装 B 模块一致，避免配置与实际兜底能力不一致。
        if not callable(getattr(self._skill_module, "generate_plans", None)):
            self._skill_module = None
        return self._skill_module

    async def _invoke_skill(
        self,
        items: list[MenuItemOut],
        requirements: Requirements,
        nutrition: dict[str, NutritionOut],
        context: Optional[PlanningContext],
    ) -> Optional[tuple[list[PlanOut], Optional[str]]]:
        module = self._load_skill()
        if module is None or not hasattr(module, "generate_plans"):
            return None
        # 按签名决定是否提供 context，不靠 try/except TypeError 试探
        if context is not None and self._skill_accepts_context(module):
            result = module.generate_plans(items, requirements, nutrition, context=context)
        else:
            result = module.generate_plans(items, requirements, nutrition)
        if inspect.isawaitable(result):
            result = await result
        return result

    @staticmethod
    def _skill_accepts_context(module: Any) -> bool:
        from .capabilities import skill_supports_context

        return skill_supports_context(module)

    # ------------------------------------------------------------ 主流程

    async def create_plans(self, request: PlanRequest) -> PlanResponse:
        from ..providers import McpError, McpTimeout
        from .capabilities import (
            CapabilityUnavailable,
            describe_missing,
            runtime_capabilities,
            static_capabilities,
        )

        skill_module = self._load_skill()
        static_caps = static_capabilities(skill_module=skill_module)

        try:
            menu = await self.menus.get_menu(request.store_code)
        except (McpError, McpTimeout):
            # 文档 §7：上游失败时旧方案一律失效，防止用旧价格结算
            self.store.invalidate_all()
            raise

        # auto 模式降级到快照后，旧方案的价格可能已不代表当前数据源，同样失效
        if menu.degraded_reason:
            self.store.invalidate_all()

        # ---- 先检查决策声明；数据可用性在读取套餐结构后检查，避免提前排除可选配套餐。
        # 不能「忽略约束返回普通套餐」，那是把硬约束当软偏好处理
        if request.requirements.exclude_beverage:
            if not static_caps.get("exclude_beverage"):
                raise CapabilityUnavailable(
                    "exclude_beverage",
                    describe_missing(
                        "exclude_beverage",
                        has_combo_details=False,
                        beverage_data_ready=True,
                    ),
                )

        # ---- 套餐结构：只在真正需要时取，避免每次点餐都打几十次上游
        # 普通样例也必须读取默认选配来核实餐份；无饮料样例仅用单品，不依赖套餐。
        need_combo_details = not request.requirements.exclude_beverage or (
            skill_module is not None and self._skill_accepts_context(skill_module)
        )
        combo_details: dict[str, ComboDetailOut] = {}
        combo_missing: list[str] = []
        combo_degraded: Optional[str] = None
        if need_combo_details:
            combo_codes = [
                i.code for i in menu.items
                if i.is_combo and not i.is_merchandise and not i.requires_membership
            ]
            combo_result = await self.combos.get_details(request.store_code, combo_codes)
            combo_details = combo_result.details
            combo_missing = combo_result.missing_codes
            combo_degraded = combo_result.degraded_reason

        # B 能收到结构时允许尝试无饮料选配；样例与旧入口只使用已知单品事实。
        beverage_data_ready = self._beverage_data_ready(menu.items, combo_details, skill_module)
        caps = runtime_capabilities(
            static_caps,
            has_combo_details=bool(combo_details),
            beverage_data_ready=beverage_data_ready,
            data_source=menu.data_source,
        )
        if request.requirements.exclude_beverage and not caps["exclude_beverage"]:
            raise CapabilityUnavailable(
                "exclude_beverage",
                describe_missing("exclude_beverage", has_combo_details=bool(combo_details),
                                 beverage_data_ready=False),
            )

        # 营养属于可选信息，取不到不能阻塞点餐
        try:
            nutrition = await self.nutrition.get_nutrition_map()
        except McpError:
            nutrition = {}

        context = PlanningContext(
            store_code=request.store_code,
            combo_details=combo_details,
            data_source=menu.data_source,
            fetched_at=menu.fetched_at,
            price_basis=(
                "demo_snapshot" if menu.data_source == "demo_snapshot" else "menu_estimate"
            ),
            capabilities=caps,
        )
        if combo_missing:
            context.notes.append(
                f"有 {len(combo_missing)} 份套餐未取到选配结构，"
                "这些套餐不参与需要结构信息的判断"
            )
        if combo_degraded:
            context.notes.append(combo_degraded)

        # ---- 调决策模块（B 优先，缺则回退 A 的样例）
        skill_result: Optional[tuple[list[PlanOut], Optional[str]]] = None
        skill_error: Optional[str] = None
        try:
            skill_result = await self._invoke_skill(
                menu.items, request.requirements, nutrition, context
            )
        except Exception as exc:  # noqa: BLE001 — B 模块异常时降级到样例，不让接口挂掉
            skill_error = f"算法模块异常，已回退演示样例：{exc}"

        using_sample = skill_result is None
        if using_sample:
            # B 出错回退后必须同步降级能力，不能仍显示饭量/口味算法已生效。
            caps = runtime_capabilities(
                static_capabilities(skill_module=None),
                has_combo_details=bool(combo_details),
                beverage_data_ready=bool(self._beverage_free_candidates(menu.items)),
                data_source=menu.data_source,
            )
            context.capabilities = caps
            if request.requirements.exclude_beverage and not caps["exclude_beverage"]:
                raise CapabilityUnavailable(
                    "exclude_beverage",
                    (skill_error or "已回退演示样例") + "；样例缺少可确认的无饮料主食单品，无法继续生成方案",
                )
            plans, unsatisfiable = generate_sample_plans(
                menu.items, request.requirements, nutrition, context
            )
        else:
            plans, unsatisfiable = skill_result  # type: ignore[misc]

        # 兼容旧三参数算法：它返回套餐时，也必须补齐结构后再校验，绝不信任名称估餐份。
        # 用菜单集合核对商品，避免对每个返回明细再次遍历整张菜单。
        menu_combo_codes = {item.code for item in menu.items if item.is_combo}
        selected_combo_codes = {
            item.code for plan in plans for item in plan.items
            if item.code in menu_combo_codes
        }
        missing_selected = selected_combo_codes - combo_details.keys() - set(combo_missing)
        if missing_selected:
            selected_result = await self.combos.get_details(request.store_code, sorted(missing_selected))
            combo_details.update(selected_result.details)
            if selected_result.missing_codes:
                context.notes.append("部分已选套餐无法取得结构，相关方案将按事实校验结果拒绝")
            if selected_result.degraded_reason:
                context.notes.append(selected_result.degraded_reason)
            caps["combo_details"] = bool(combo_details)

        # ---- 保存前必须校验：商品、规格、数量、金额与硬约束
        kept, dropped = validate_plans(
            plans, request.requirements, menu.items, combo_details
        )

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
        # 事实缺失说明需传到前端，不能只留在传给 B 的内部上下文中。
        suggestions.extend(context.notes)
        suggestions.extend(dropped)
        if dropped and not kept:
            # B 的方案全部被判不合法：如实说明，不拿空列表冒充「无解」
            unsatisfiable = (
                unsatisfiable
                or "候选方案未通过服务端校验：" + "；".join(dropped)
            )

        # 服务端保存方案并绑定门店与需求，结算时按此重建金额
        for plan in kept:
            self.store.save(plan, request.store_code, request.requirements)

        # 有方案可用时，切换门店前的旧方案一并失效
        if kept:
            self.store.invalidate_other_stores(request.store_code)

        return PlanResponse(
            plans=kept,
            requirements=request.requirements,
            total_budget_fen=request.requirements.total_budget_fen,
            data_source=menu.data_source,
            fetched_at=menu.fetched_at,
            degraded_reason=menu.degraded_reason,
            unsatisfiable_reason=unsatisfiable,
            suggestions=suggestions,
            capabilities=caps,
        )

    # ------------------------------------------------------------ 辅助

    # 这里只判断有没有足够数据让 B 尝试，不保证预算、数量等约束一定可解。
    @classmethod
    def _beverage_data_ready(cls, items, combo_details, skill_module) -> bool:
        if cls._beverage_free_candidates(items):
            return True
        if skill_module is None or not cls._skill_accepts_context(skill_module):
            return False
        for item in items:
            detail = combo_details.get(item.code)
            if not item.is_combo or item.is_merchandise or item.requires_membership or detail is None:
                continue
            if not detail.rounds:
                continue
            if all(
                round_.min_quantity is not None and round_.max_quantity is not None
                and (round_.min_quantity == 0 or any(
                    option.beverage_status == "none" and option.diff_price_fen is not None
                    for option in round_.options
                )) for round_ in detail.rounds
            ) and any(
                option.food_role == "main" and option.beverage_status == "none"
                and option.diff_price_fen is not None
                for round_ in detail.rounds for option in round_.options
            ):
                return True
        return False

    @staticmethod
    def _beverage_free_candidates(items: list[MenuItemOut]) -> list[MenuItemOut]:
        """可确证不含饮品的「正餐主食」单品。

        这是「不带饮料」能否成立的数据前提：拿不到这类商品就无法在确证的
        前提下满足该硬约束，此时必须明确拒绝，而不是硬凑一份含饮料的套餐。
        """
        from ..providers.mapper import is_staple_main

        return [
            i
            for i in items
            if i.food_role == "main"
            and is_staple_main(i.name, i.category)
            and i.beverage_status == "none"
            and not i.is_merchandise
            and not i.requires_membership
        ]


plan_service = PlanService()
