"""名称角色与辣度边界回归；只使用内存菜单和本地规则。"""

import pytest

from backend.providers import mapper
from backend.providers.mapper import (
    classify_food_role,
    classify_spicy,
    describe_composition,
    map_meal_detail,
    map_menu,
)
from backend.schemas import ComboDetailOut, ComboOptionOut, ComboRoundOut, MenuItemOut
from backend.services.meal_facts import resolve_meal_facts


@pytest.mark.parametrize("name", [
    "灰焰圆筒", "原味甜筒", "草莓新地", "巧克力圣代", "奥利奥麦旋风",
    "阿芙佳朵", "香草冰淇淋", "香芋派", "菠萝派", "苹果派", "红豆派",
    "巧克力派", "蓝莓派", "草莓派", "芒果派", "每日甜派", "葡式蛋挞",
])
@pytest.mark.parametrize("category", ["", "小食甜品"])
def test_personal_dessert_is_not_a_side(name, category):
    assert classify_food_role(name, category, False)[:3] == ("dessert", "none", False)


@pytest.mark.parametrize("name,category,role,status", [
    ("新品精选", "小食甜品", "unknown", "unknown"),
    ("精选拼盘", "小食拼盘", "side", "none"),
    ("神秘派对", "", "unknown", "unknown"),
    ("鸡肉派", "", "unknown", "unknown"),
    ("派对新品", "小食甜品", "unknown", "unknown"),
    ("中薯条", "小食甜品", "side", "none"),
    ("麦乐鸡5块", "小食甜品", "side", "none"),
    ("牛奶圆筒", "饮品", "dessert", "none"),
    ("咖啡新地", "麦咖啡", "dessert", "none"),
    ("浓缩咖啡阿芙佳朵", "麦咖啡", "dessert", "none"),
    ("冰淇淋拿铁", "饮品", "beverage", "contains"),
    ("苹果派风味拿铁", "麦咖啡", "beverage", "contains"),
    ("麦旋风风味咖啡", "饮品", "beverage", "contains"),
    ("玉米牛奶", "饮品", "beverage", "contains"),
    ("可乐中杯", "小食甜品", "beverage", "contains"),
])
def test_role_evidence_preserves_food_drink_and_unknown_boundaries(name, category, role, status):
    assert classify_food_role(name, category, False)[:3] == (role, status, False)


@pytest.mark.parametrize("name,is_combo,expected", [
    ("圆筒棒球帽", False, ("unknown", "unknown", True)),
    ("圆筒套餐", True, ("combo", "unknown", False)),
])
def test_merchandise_and_combo_priority_is_preserved(name, is_combo, expected):
    assert classify_food_role(name, "小食甜品", is_combo)[:3] == expected


@pytest.mark.parametrize("name,expected", [
    ("灰焰圆筒", "unknown"),
    ("灰焰板烧堡", "unknown"),
    ("灰焰咖啡", "unknown"),
    ("灰焰新品", "unknown"),
    ("龙焰牛肉堡", "spicy"),
    ("灰焰辣鸡翅", "spicy"),
    ("麦辣鸡腿汉堡", "spicy"),
    ("原味圆筒", "mild"),
    ("蜜汁BBQ风味薄皮脆汁鸡-带骨里脊", "mild"),
])
def test_brand_flame_alone_does_not_prove_spiciness_or_mildness(name, expected):
    assert classify_spicy(name) == expected


def test_explicit_spicy_and_mild_rules_override_ambiguous_brand(monkeypatch):
    rules = dict(mapper._load_rules())
    rules["spicy_exact"] = [*rules["spicy_exact"], "灰焰指定商品"]
    rules["mild_exact"] = [*rules["mild_exact"], "灰焰圆筒"]
    monkeypatch.setattr(mapper, "_RULES", rules)
    assert classify_spicy("灰焰指定商品") == "spicy"
    assert classify_spicy("灰焰圆筒") == "mild"


def test_mapped_dessert_contributes_no_meal_or_side_servings():
    items, _ = map_menu("fixture", {
        "categories": [{"name": "小食甜品", "meals": [{"code": "dessert"}]}],
        "meals": {"dessert": {"name": "灰焰圆筒", "currentPrice": "8", "originalPrice": "8"}},
    })
    item = items[0]
    assert (item.food_role, item.spicy_level) == ("dessert", "unknown")
    assert (item.servings, item.side_servings, item.non_spicy_servings) == (0, 0, 0)
    facts, errors = resolve_meal_facts(item)
    assert errors == []
    assert (facts.servings, facts.side_servings, facts.non_spicy_servings) == (0, 0, 0)


def test_explicit_dessert_role_has_a_composition_label():
    detail = ComboDetailOut(code="dessert", name="甜品组合", rounds=[
        ComboRoundOut(round_id="sweet", name="选择甜品", min_quantity=1, max_quantity=1,
                      options=[ComboOptionOut(code="cone", name="圆筒", food_role="dessert")]),
    ])
    assert describe_composition(detail) == "1 甜品（必选）"


def test_combo_dessert_is_described_but_not_counted_as_a_side():
    detail = map_meal_detail({"code": "combo", "name": "汉堡甜品套餐", "rounds": [
        {"id": "main", "name": "选择主食", "quantity": 1, "minQuantity": 1, "maxQuantity": 1,
         "choices": [{"code": "burger", "name": "双层吉士汉堡", "isDefault": True, "diffPrice": "+ ¥0"}]},
        {"id": "sweet", "name": "选择甜品", "quantity": 2, "minQuantity": 2, "maxQuantity": 2,
         "choices": [{"code": "cone", "name": "圆筒", "isDefault": True, "diffPrice": "+ ¥0"}]},
    ]}, "demo_snapshot")
    assert detail.composition == "1 主食（必选） + 2 甜品（必选）"
    item = MenuItemOut(code="combo", name="汉堡甜品套餐", price_fen=2000,
                       original_price_fen=2000, category="套餐", is_combo=True)
    facts, errors = resolve_meal_facts(item, detail)
    assert errors == []
    assert (facts.servings, facts.side_servings) == (1, 0)
    assert facts.components[1]["food_role"] == "dessert"
    assert (facts.components[1]["servings"], facts.components[1]["side_servings"]) == (0, 0)


def test_beverage_round_still_marks_dessert_named_drink_as_beverage():
    detail = map_meal_detail({"code": "combo", "name": "套餐", "rounds": [
        {"id": "drink", "name": "选择套餐内饮料", "quantity": 1, "minQuantity": 1, "maxQuantity": 1,
         "choices": [{"code": "drink", "name": "冰淇淋", "isDefault": True, "diffPrice": "+ ¥0"}]},
    ]}, "demo_snapshot")
    assert detail.rounds[0].options[0].food_role == "beverage"
    assert detail.beverage_status == "contains"
