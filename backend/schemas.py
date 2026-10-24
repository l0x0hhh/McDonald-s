"""公共数据模型（A 维护，三人共用）。

金额一律使用整数「分」。任何进入前端的金额都是分，前端只负责格式化，
不重新计算业务价格。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

PlanType = Literal["cheapest", "balanced", "taste"]
SpicyLevel = Literal["spicy", "mild", "unknown"]
DataSource = Literal["live_mcp", "demo_snapshot"]


# ---------------------------------------------------------------- 需求


class DietaryConstraint(BaseModel):
    kind: Literal["non_spicy"]
    people: int = Field(ge=0, le=20, description="需要不辣餐份的人数")


class Requirements(BaseModel):
    people: int = Field(ge=1, le=20, description="总人数")
    budget_type: Literal["total", "per_person"] = "total"
    budget_fen: int = Field(ge=0, description="预算，单位分；per_person 时为每人均")
    dietary_constraints: list[DietaryConstraint] = Field(default_factory=list)
    appetite: Literal["light", "normal", "heavy"] = "normal"
    preferred_tags: list[str] = Field(default_factory=list)

    @property
    def total_budget_fen(self) -> int:
        if self.budget_type == "per_person":
            return self.budget_fen * self.people
        return self.budget_fen

    @property
    def non_spicy_people(self) -> int:
        total = sum(c.people for c in self.dietary_constraints if c.kind == "non_spicy")
        return min(total, self.people)


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


class MenuOut(BaseModel):
    store_code: str
    items: list[MenuItemOut]
    categories: list[str]
    data_source: DataSource
    fetched_at: str
    degraded_reason: Optional[str] = None


# ---------------------------------------------------------------- 需求解析


class ChatIn(BaseModel):
    message: str = ""
    current: Optional[Requirements] = None


class ChatOut(BaseModel):
    requirements: Optional[Requirements] = None
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
    nutrition: Optional[NutritionOut] = None


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
    total_fen: int
    budget_fen: int
    remaining_fen: int
    servings_total: int
    non_spicy_servings: int
    reasons: list[str] = Field(default_factory=list)
    checks: list[ConstraintCheck] = Field(default_factory=list)


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
