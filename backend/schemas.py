# 修订说明：保留默认轮次数量，按候选内容识别必选饮料，兼容现有接口字段。
"""公共数据模型（A 维护，三人共用）。

金额一律使用整数「分」。任何进入前端的金额都是分，前端只负责格式化，
不重新计算业务价格。

V1.1 增量（见 docs/接口扩展草案.md）：
- Requirements 增加不带饮料、会员状态与习惯分析字段
- 新增 RequirementsDraft，支持跨多轮对话保留部分需求
- MenuItemOut 增加 food_role / beverage_status，支撑「不带饮料」判定
- 新增套餐选配结构与 PlanningContext，供 B 的方案算法取用
"""

from __future__ import annotations

from typing import ClassVar, Literal, Optional, Tuple

from pydantic import BaseModel, Field

PlanType = Literal["cheapest", "balanced", "taste"]
SpicyLevel = Literal["spicy", "mild", "unknown"]
DataSource = Literal["live_mcp", "demo_snapshot"]
# 餐品在一餐里扮演的角色：主食 / 小食 / 饮料 / 套餐 / 未知
FoodRole = Literal["main", "side", "beverage", "combo", "unknown"]
# 是否含饮料：none 已证实不含 / contains 含（或本身就是）/ unknown 未证实
BeverageStatus = Literal["none", "contains", "unknown"]
# 用户自报的会员状态；不能替代账号资格核验，也不自动购卡
MembershipStatus = Literal["unknown", "none", "active"]
Appetite = Literal["light", "normal", "heavy"]
BudgetType = Literal["total", "per_person"]


# ---------------------------------------------------------------- 需求


class DietaryConstraint(BaseModel):
    kind: Literal["non_spicy"]
    people: int = Field(ge=0, le=20, description="需要不辣餐份的人数")


class Requirements(BaseModel):
    people: int = Field(ge=1, le=20, description="总人数")
    budget_type: BudgetType = "total"
    budget_fen: int = Field(ge=0, description="预算，单位分；per_person 时为每人均")
    dietary_constraints: list[DietaryConstraint] = Field(default_factory=list)
    appetite: Appetite = "normal"
    preferred_tags: list[str] = Field(default_factory=list)
    # 不带饮料：true 代表整单不含饮品（含茶、牛奶、咖啡等）；
    # false 代表「未限制」，不是「必须配饮料」
    exclude_beverage: bool = False
    # 用户自报的当前会员状态，只用于比较场景，不替代账号资格核验
    membership_status: MembershipStatus = "unknown"
    # 习惯分析：每月用餐次数，0 与「未提供」分开
    meal_frequency_per_month: Optional[int] = Field(None, ge=0, le=200)
    # 习惯分析：想要分析的月数
    habit_horizon_months: Optional[int] = Field(None, ge=1, le=60)
    # 常买品类，与本餐 preferred_tags 分开；不等于真实订单历史
    habitual_tags: list[str] = Field(default_factory=list)

    @property
    def total_budget_fen(self) -> int:
        if self.budget_type == "per_person":
            return self.budget_fen * self.people
        return self.budget_fen

    @property
    def non_spicy_declared(self) -> int:
        """用户声明的不辣人数（未收敛）。用于判断「超过总人数」这类矛盾。"""
        return sum(c.people for c in self.dietary_constraints if c.kind == "non_spicy")

    @property
    def non_spicy_people(self) -> int:
        """规划口径的不辣餐份需求：收敛到总人数，避免出现超过人数的份数目标。"""
        return min(self.non_spicy_declared, self.people)

    def to_draft(self) -> "RequirementsDraft":
        return RequirementsDraft(**self.model_dump())


class RequirementsDraft(BaseModel):
    """部分需求草稿：允许必填项缺失，用于跨多轮对话保留已收集的信息。

    people / budget_fen 等未收集时为 None，绝不用 0 或默认值占位，
    否则无法区分「用户说 0 元」和「用户还没说」。
    """

    people: Optional[int] = Field(None, ge=1, le=20)
    budget_type: Optional[BudgetType] = None
    budget_fen: Optional[int] = Field(None, ge=0)
    dietary_constraints: Optional[list[DietaryConstraint]] = None
    appetite: Optional[Appetite] = None
    preferred_tags: Optional[list[str]] = None
    exclude_beverage: Optional[bool] = None
    membership_status: Optional[MembershipStatus] = None
    meal_frequency_per_month: Optional[int] = Field(None, ge=0, le=200)
    habit_horizon_months: Optional[int] = Field(None, ge=1, le=60)
    habitual_tags: Optional[list[str]] = None

    REQUIRED_FIELDS: ClassVar[Tuple[str, ...]] = ("people", "budget_fen")

    def missing_fields(self) -> list[str]:
        """返回仍缺的必填项；忌口人数未确认时单独列为 non_spicy_people。"""
        missing: list[str] = []
        if self.people is None:
            missing.append("people")
        if self.budget_fen is None:
            missing.append("budget")
        if self.non_spicy_unknown():
            missing.append("non_spicy_people")
        return missing

    def non_spicy_unknown(self) -> bool:
        """说了「有人不吃辣」但没给人数时为真。"""
        if self.dietary_constraints is None:
            return False
        return any(c.people == 0 for c in self.dietary_constraints if c.kind == "non_spicy")

    def non_spicy_declared(self) -> int:
        if not self.dietary_constraints:
            return 0
        return sum(c.people for c in self.dietary_constraints if c.kind == "non_spicy")

    def contradictions(self) -> list[str]:
        """已收集信息之间的矛盾：必须让用户更正，不能静默收敛。"""
        problems: list[str] = []
        if self.people is not None and self.non_spicy_declared() > self.people:
            problems.append(
                f"不吃辣 {self.non_spicy_declared()} 人超过了总人数 {self.people} 人，请确认人数"
            )
        return problems

    def is_complete(self) -> bool:
        return not self.missing_fields() and not self.contradictions()

    def to_requirements(self) -> Requirements:
        """补齐默认值后转成完整需求；调用前必须先确认 is_complete()。"""
        return Requirements(
            people=self.people or 0,
            budget_type=self.budget_type or "total",
            budget_fen=self.budget_fen or 0,
            dietary_constraints=self.dietary_constraints or [],
            appetite=self.appetite or "normal",
            preferred_tags=self.preferred_tags or [],
            exclude_beverage=bool(self.exclude_beverage),
            membership_status=self.membership_status or "unknown",
            meal_frequency_per_month=self.meal_frequency_per_month,
            habit_horizon_months=self.habit_horizon_months,
            habitual_tags=self.habitual_tags or [],
        )

    def merge(self, patch: "RequirementsDraft") -> "RequirementsDraft":
        """用本轮新解析出的信息覆盖已有草稿，未涉及的字段保持原值。"""
        data = self.model_dump()
        for key, value in patch.model_dump().items():
            if value is None:
                continue
            if isinstance(value, list) and not value:
                continue  # 空列表视为「本轮没提到」，不覆盖已有内容
            data[key] = value
        return RequirementsDraft(**data)


# ---------------------------------------------------------------- 门店


class StoreOut(BaseModel):
    store_code: str
    name: str
    address: str
    distance: Optional[int] = None
    business_status: Optional[bool] = None
    business_hours: Optional[str] = None
    supports_reservation: bool = False


class StoreListOut(BaseModel):
    stores: list[StoreOut]
    data_source: DataSource
    fetched_at: str
    degraded_reason: Optional[str] = None


# ---------------------------------------------------------------- 菜单


class MenuItemOut(BaseModel):
    code: str
    name: str
    price_fen: int
    original_price_fen: int
    category: str
    tags: list[str] = Field(default_factory=list)
    image: Optional[str] = None
    is_combo: bool = False
    servings: int = 1
    spicy_level: SpicyLevel = "unknown"
    requires_membership: bool = False
    membership_note: Optional[str] = None
    # 一餐里的角色：主食 / 小食 / 饮料 / 套餐。来源为品类与名称规则，
    # 套餐的实际角色由 query-meal-detail 的轮次决定
    food_role: FoodRole = "unknown"
    # 是否含饮料。none 仅用于已证实不含饮品的单品或组合；
    # 不能因为名称里没写「可乐」就填 none
    beverage_status: BeverageStatus = "unknown"
    # 角色/含饮料判定的依据，便于前端解释与 B 复核
    role_evidence: Optional[str] = None
    # 非餐品（周边商品、导航项、蘸酱）：任何方案都不得选用
    is_merchandise: bool = False


class MenuOut(BaseModel):
    store_code: str
    items: list[MenuItemOut]
    categories: list[str]
    data_source: DataSource
    fetched_at: str
    degraded_reason: Optional[str] = None


# ---------------------------------------------------------------- 套餐选配结构


class ComboOptionOut(BaseModel):
    """套餐某一轮里的一个可选项。"""

    code: str
    name: str
    # 差价（分）。解析失败记 None，绝不记 0——0 会被当成「确认不加价」
    diff_price_fen: Optional[int] = None
    diff_price_raw: Optional[str] = None
    is_default: bool = False
    food_role: FoodRole = "unknown"
    beverage_status: BeverageStatus = "unknown"
    # maxQuantity 原样保留：-1 表示不限
    max_quantity: Optional[int] = None


class ComboRoundOut(BaseModel):
    """套餐的一个选配轮次，如「选择主食」「选择套餐内饮料」。"""

    round_id: str
    name: str
    min_quantity: Optional[int] = None
    max_quantity: Optional[int] = None
    # 上游轮次 quantity 决定默认选配数量；缺失时不能把默认选项擅自算作一份。
    default_quantity: Optional[int] = None
    options: list[ComboOptionOut] = Field(default_factory=list)
    # 这一轮是不是饮料轮（如「选择套餐内饮料」）
    is_beverage_round: bool = False

    @property
    def required(self) -> bool:
        """是否必选：min_quantity >= 1 时这一轮无法去掉。"""
        return self.min_quantity is not None and self.min_quantity >= 1

    @property
    def default_option(self) -> Optional[ComboOptionOut]:
        for opt in self.options:
            if opt.is_default:
                return opt
        return None


class ComboDetailOut(BaseModel):
    """一份套餐的实际组成（来自 query-meal-detail 实测结构）。"""

    code: str
    name: str
    rounds: list[ComboRoundOut] = Field(default_factory=list)
    support_modify: bool = False
    food_role: FoodRole = "combo"
    beverage_status: BeverageStatus = "unknown"
    # 一份组合实际包含什么与份数；servings_verified=False 时 servings 只是估计
    servings: int = 1
    servings_verified: bool = False
    # 组成说明：如「1 主食 + 1 小食 + 1 饮料（饮料必选）」
    composition: Optional[str] = None
    source: DataSource = "live_mcp"
    fetched_at: str = ""
    # 取不到结构时的说明，不能把「没取到」写成「不含饮料」
    degraded_reason: Optional[str] = None

    @property
    def has_mandatory_beverage(self) -> bool:
        # 蓝区/粉区也可能全是饮品，不能只靠轮次名称判断必选饮料。
        """至少有一轮必选且只能选择饮品时，套餐必然含饮料。"""
        return any(
            r.required
            and (
                r.is_beverage_round
                or (bool(r.options) and all(o.beverage_status == "contains" for o in r.options))
            )
            for r in self.rounds
        )


class SelectedOptionOut(BaseModel):
    """方案里某个套餐实际选中的规格（结构化，便于 A 回传真实报价）。"""

    round_id: str
    round_name: Optional[str] = None
    option_code: str
    option_name: Optional[str] = None
    quantity: int = 1
    diff_price_fen: Optional[int] = None


# ---------------------------------------------------------------- 规划上下文（A → B）


class PlanningContext(BaseModel):
    """A 向 B 提供的本餐事实。B 不直接向上游请求，只用这里的数据。"""

    store_code: str
    combo_details: dict[str, ComboDetailOut] = Field(default_factory=dict)
    data_source: DataSource = "demo_snapshot"
    fetched_at: str = ""
    # 价格口径：menu_estimate 菜单参考价 / live_quote 真实报价 / demo_snapshot 演示快照
    price_basis: Literal["menu_estimate", "live_quote", "demo_snapshot"] = "menu_estimate"
    # 本次实际可用的能力，B 不得假设未列出的能力已生效
    capabilities: dict[str, bool] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- 需求解析


class ChatIn(BaseModel):
    message: str = ""
    # 可接收部分需求草稿，兼容已完整的原 Requirements 对象
    current: Optional[RequirementsDraft] = None


class ChatOut(BaseModel):
    requirements: Optional[Requirements] = None
    # 本轮合并后的部分需求；C 下一轮优先回传它
    draft: Optional[RequirementsDraft] = None
    missing_fields: list[str] = Field(default_factory=list)
    follow_up: Optional[str] = None
    ready: bool = False
    parse_source: Literal["llm", "rule", "demo_template"] = "rule"
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- 方案


class NutritionOut(BaseModel):
    energy_kcal: Optional[float] = None
    protein_g: Optional[float] = None
    fat_g: Optional[float] = None
    carbohydrate_g: Optional[float] = None
    sodium_mg: Optional[float] = None
    matched: bool = False
    source: str = "mcd_mcp:list-nutrition-foods"


class PlanItemOut(BaseModel):
    code: str
    name: str
    quantity: int
    unit_price_fen: int
    original_unit_price_fen: int
    line_total_fen: int
    servings: int
    spicy_level: SpicyLevel
    image: Optional[str] = None
    # 必要规格：套餐内的主食/小食/饮料选择等。由 B 的算法在拆套餐时填写，
    # 当前样例阶段为空，前端必须容忍 None 而不是显示占位文字。
    spec: Optional[str] = None
    # 结构化选配：A 用它回传真实报价，不能只凭中文 spec 重新猜商品
    selected_options: list[SelectedOptionOut] = Field(default_factory=list)
    nutrition: Optional[NutritionOut] = None


class ConstraintCheck(BaseModel):
    name: str
    satisfied: bool
    detail: str


class MembershipComparisonOut(BaseModel):
    """开卡对比结果。由 B 计算，A 只提供事实；本版未启用，恒为 null。

    所有无法证明的数值一律为 null，不得填 0。
    """

    status: Literal["available", "unavailable"] = "unavailable"
    reason: Optional[str] = None
    same_basket: bool = False
    without_card_fen: Optional[int] = None
    existing_card_food_fen: Optional[int] = None
    new_card_total_fen: Optional[int] = None
    card_fee_fen: Optional[int] = None
    net_saving_fen: Optional[int] = None
    price_basis: Literal["menu_estimate", "live_quote", "demo_snapshot"] = "menu_estimate"
    source: Optional[str] = None
    fetched_at: Optional[str] = None
    assumptions: list[str] = Field(default_factory=list)


class PlanOut(BaseModel):
    plan_id: str
    type: PlanType
    title: str
    summary: str
    items: list[PlanItemOut]
    subtotal_fen: int
    # 优惠金额 = subtotal_fen - total_fen。逐项明细体现在每个商品的
    # original_unit_price_fen 与 unit_price_fen 差额上，不在此处另造规则。
    discount_fen: int = 0
    total_fen: int
    budget_fen: int
    remaining_fen: int
    servings_total: int
    non_spicy_servings: int
    reasons: list[str] = Field(default_factory=list)
    checks: list[ConstraintCheck] = Field(default_factory=list)
    # 开卡对比：本版未启用，恒为 null；接入后由 B 填充
    membership_comparison: Optional[MembershipComparisonOut] = None


class PlanRequest(BaseModel):
    store_code: str
    requirements: Requirements


class PlanResponse(BaseModel):
    plans: list[PlanOut]
    requirements: Requirements
    total_budget_fen: int
    data_source: DataSource
    fetched_at: str
    degraded_reason: Optional[str] = None
    unsatisfiable_reason: Optional[str] = None
    suggestions: list[str] = Field(default_factory=list)
    # 本次请求实际可用的能力，C 据此提示；不要只凭字段存在判断功能可用
    capabilities: dict[str, bool] = Field(default_factory=dict)


# ---------------------------------------------------------------- 结算


class OrderIn(BaseModel):
    plan_id: str


class OrderOut(BaseModel):
    order_id: str
    plan_id: str
    store_code: str
    items: list[PlanItemOut]
    total_fen: int
    status: Literal["simulated"] = "simulated"
    is_demo: bool = True
    notice: str = "模拟结算：不产生真实扣款，不构成履约承诺"
    created_at: str
