"""公共数据模型（A 维护，三人共用）。

金额一律使用整数「分」。任何进入前端的金额都是分，前端只负责格式化，
不重新计算业务价格。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

PlanType = Literal["cheapest", "balanced", "taste"]
SpicyLevel = Literal["spicy", "mild", "unknown"]
DataSource = Literal["live_mcp", "demo_snapshot"]
FoodRole = Literal["main", "side", "beverage", "combo", "dessert", "unknown"]
PriceBasis = Literal["menu_estimate", "live_quote", "demo_snapshot"]


# ---------------------------------------------------------------- 需求


class DietaryConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["non_spicy"]
    people: int = Field(strict=True, ge=0, le=20, description="需要不辣餐份的人数")


class Requirements(BaseModel):
    # 完整需求拒绝未知字段，避免前端的新硬约束被静默丢弃。
    model_config = ConfigDict(extra="forbid")
    people: int = Field(strict=True, ge=1, le=20, description="总人数")
    budget_type: Literal["total", "per_person"] = "total"
    budget_fen: int = Field(strict=True, ge=0, description="预算，单位分；per_person 时为每人均")
    dietary_constraints: list[DietaryConstraint] = Field(default_factory=list)
    appetite: Literal["light", "normal", "heavy"] = "normal"
    preferred_tags: list[str] = Field(default_factory=list)
    exclude_beverage: bool = Field(default=False, strict=True)
    membership_status: Literal["unknown", "none", "active"] = "unknown"
    meal_frequency_per_month: Optional[int] = Field(default=None, strict=True, ge=0)
    habit_horizon_months: Optional[int] = Field(default=None, strict=True, ge=1)
    habitual_tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_constraints(self):
        if len(self.dietary_constraints) > 1:
            raise ValueError("non_spicy 忌口条件只能有一条")
        if self.non_spicy_people > self.people:
            raise ValueError("不辣人数不能超过总人数，请确认人数")
        return self

    @property
    def total_budget_fen(self) -> int:
        if self.budget_type == "per_person":
            return self.budget_fen * self.people
        return self.budget_fen

    @property
    def non_spicy_people(self) -> int:
        total = sum(c.people for c in self.dietary_constraints if c.kind == "non_spicy")
        return total


class RequirementsDraft(BaseModel):
    """保留部分需求与待确认项；完成校验后才转换为 Requirements。"""

    model_config = ConfigDict(extra="forbid")
    people: Optional[int] = Field(default=None, strict=True, ge=1, le=20)
    budget_type: Optional[Literal["total", "per_person"]] = None
    budget_fen: Optional[int] = Field(default=None, strict=True, ge=0)
    dietary_constraints: Optional[list[DietaryConstraint]] = Field(default_factory=list)
    appetite: Optional[Literal["light", "normal", "heavy"]] = "normal"
    preferred_tags: Optional[list[str]] = Field(default_factory=list)
    exclude_beverage: Optional[bool] = Field(default=False, strict=True)
    membership_status: Optional[Literal["unknown", "none", "active"]] = "unknown"
    meal_frequency_per_month: Optional[int] = Field(default=None, strict=True, ge=0)
    habit_horizon_months: Optional[int] = Field(default=None, strict=True, ge=1)
    habitual_tags: Optional[list[str]] = Field(default_factory=list)
    pending_fields: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
    # 单品分类和套餐成分是决策事实，未知套餐不得靠名称猜出餐份或饮品。
    food_role: FoodRole = "unknown"
    beverage_status: Literal["none", "contains", "unknown"] = "unknown"
    servings_verified: bool = False
    non_spicy_servings: int = Field(default=0, ge=0)
    side_servings: int = Field(default=0, ge=0)
    available: bool = True
    price_verified: bool = True
    eligibility_verified: bool = False
    max_quantity: Optional[int] = Field(default=None, strict=True, ge=0)


class MenuOut(BaseModel):
    store_code: str
    items: list[MenuItemOut]
    categories: list[str]
    data_source: DataSource
    fetched_at: str
    degraded_reason: Optional[str] = None


# ---------------------------------------------------------------- 需求解析


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = ""
    current: Optional[RequirementsDraft] = None


class ChatOut(BaseModel):
    requirements: Optional[Requirements] = None
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
    calcium_mg: Optional[float] = None
    fiber_g: Optional[float] = None
    serving_size: Optional[str] = None
    fetched_at: Optional[str] = None
    estimated: bool = False
    metric_sources: dict = Field(default_factory=dict)
    data_source: Optional[DataSource] = None


class PlanItemOut(BaseModel):
    code: str
    name: str
    quantity: int = Field(strict=True, ge=1)
    unit_price_fen: int = Field(strict=True, ge=0)
    original_unit_price_fen: int = Field(strict=True, ge=0)
    line_total_fen: int = Field(strict=True, ge=0)
    servings: int = Field(strict=True, ge=0)
    spicy_level: SpicyLevel
    image: Optional[str] = None
    # 必要规格：套餐内的主食/小食/饮料选择等。由 B 的算法在拆套餐时填写，
    # 当前样例阶段为空，前端必须容忍 None 而不是显示占位文字。
    spec: Optional[str] = None
    nutrition: Optional[NutritionOut] = None
    food_role: FoodRole = "unknown"
    beverage_status: Literal["none", "contains", "unknown"] = "unknown"
    non_spicy_servings: int = 0
    side_servings: int = 0
    components: list[dict] = Field(default_factory=list)
    selected_options: list[dict] = Field(default_factory=list)


class ConstraintCheck(BaseModel):
    name: str
    satisfied: bool
    detail: str


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
    side_servings_total: int = 0
    membership_comparison: Optional[dict] = None
    nutrition_summary: Optional[dict] = None
    price_basis: PriceBasis = "menu_estimate"
    applied_promotions: list[dict] = Field(default_factory=list)


class PlanningContext(BaseModel):
    """只由数据服务注入的事实；HTTP 客户端不得提交报价或资格。"""

    store_code: str = ""
    data_source: DataSource = "demo_snapshot"
    fetched_at: str = ""
    price_basis: PriceBasis = "menu_estimate"
    combo_details: dict[str, dict] = Field(default_factory=dict)
    membership_facts: Optional[dict] = None
    promotions: list[dict] = Field(default_factory=list)
    campaigns: list[dict] = Field(default_factory=list)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
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
    algorithm_source: Literal["skill", "unavailable"] = "skill"
    campaigns: list[dict] = Field(default_factory=list)


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
