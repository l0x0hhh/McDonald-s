"""B 规划器与 A 公共契约的离线验收；价格和商品均为测试样例。"""

import unittest

from backend.schemas import MenuItemOut, NutritionOut, PlanningContext, Requirements
from backend.skill import CAPABILITIES, PlanValidationError, generate_plans, validate_plan


def main(code, name, price, *, spicy="mild", beverage="none", tags=None, membership=False, eligible=False):
    return MenuItemOut(
        code=code, name=name, price_fen=price, original_price_fen=price,
        category="测试主食", tags=tags or [], food_role="main", beverage_status=beverage,
        servings=1, servings_verified=True, spicy_level=spicy,
        non_spicy_servings=int(spicy == "mild"),
        requires_membership=membership, eligibility_verified=eligible,
    )


def side(code="side", price=300):
    return MenuItemOut(
        code=code, name="小食", price_fen=price, original_price_fen=price,
        category="测试小食", food_role="side", beverage_status="none", side_servings=1,
    )


def drink():
    return MenuItemOut(
        code="drink", name="红茶", price_fen=100, original_price_fen=100,
        category="测试饮品", food_role="beverage", beverage_status="contains",
    )


class PlannerTests(unittest.TestCase):
    def test_declared_capabilities_match_A_contract(self):
        for name in ("exclude_beverage", "appetite_planning", "taste_matching"):
            self.assertIs(CAPABILITIES.get(name), True)
        self.assertNotIn("membership_comparison", CAPABILITIES)

    def test_public_contract_and_three_views(self):
        menu = [main("chicken", "鸡肉堡", 1000), main("beef", "牛肉堡", 1200), side(), side("side2", 400)]
        req = Requirements(people=4, budget_type="per_person", budget_fen=1600,
                           preferred_tags=["beef"], dietary_constraints=[{"kind": "non_spicy", "people": 2}])
        plans, reason = generate_plans(menu, req, {})
        self.assertIsNone(reason)
        self.assertGreaterEqual(len(plans), 1)
        self.assertLessEqual(len(plans), 3)
        signatures = {tuple(sorted((item.code, item.quantity) for item in plan.items)) for plan in plans}
        self.assertEqual(len(signatures), len(plans))
        for plan in plans:
            validate_plan(plan, menu, req)
            self.assertGreaterEqual(plan.servings_total, 4)
            self.assertGreaterEqual(plan.side_servings_total + max(0, plan.servings_total - 4), 2)
            self.assertGreaterEqual(plan.non_spicy_servings, 2)
            self.assertLessEqual(plan.total_fen, 6400)
            self.assertEqual(plan.subtotal_fen - plan.discount_fen, plan.total_fen)
            self.assertTrue(plan.summary)

    def test_appetite_templates_change_actual_quantity(self):
        menu = [main("m", "鸡肉堡", 1000), side()]
        expected = {"light": (1, 0), "normal": (1, 1), "heavy": (2, 1)}
        for appetite, (mains, sides) in expected.items():
            with self.subTest(appetite=appetite):
                req = Requirements(people=1, budget_fen=3000, appetite=appetite)
                plans, _ = generate_plans(menu, req, {})
                self.assertTrue(plans)
                self.assertEqual((plans[0].servings_total, plans[0].side_servings_total), (mains, sides))
                self.assertTrue(any(appetite == "light" and "轻饭量" in reason or appetite == "normal" and "正常饭量" in reason or appetite == "heavy" and "大饭量" in reason for reason in plans[0].reasons))

    def test_normal_appetite_can_use_extra_main_when_store_has_no_side(self):
        menu = [main("m", "鸡肉堡", 1000)]
        req = Requirements(people=1, budget_fen=2500, appetite="normal")
        plans, reason = generate_plans(menu, req, {})
        self.assertIsNone(reason)
        self.assertTrue(plans)
        self.assertEqual(plans[0].servings_total, 2)
        self.assertEqual(plans[0].side_servings_total, 0)
        validate_plan(plans[0], menu, req)

    def test_no_beverage_filters_drinks_and_unsafe_combo(self):
        combo = MenuItemOut(code="set", name="含茶套餐", price_fen=500, original_price_fen=500,
                            category="测试套餐", is_combo=True, food_role="combo", beverage_status="contains")
        detail = {"verified": True, "components": [
            {"code": "m", "name": "鸡肉堡", "quantity": 1, "food_role": "main", "beverage_status": "none", "servings": 1, "non_spicy_servings": 1},
            {"code": "s", "name": "小食", "quantity": 1, "food_role": "side", "beverage_status": "none", "side_servings": 1},
            {"code": "d", "name": "红茶", "quantity": 1, "food_role": "beverage", "beverage_status": "contains"},
        ]}
        menu = [main("m", "鸡肉堡", 1000), side(), drink(), combo]
        req = Requirements(people=1, budget_fen=2000, exclude_beverage=True)
        plans, _ = generate_plans(menu, req, {}, context=PlanningContext(combo_details={"set": detail}))
        self.assertTrue(plans)
        self.assertTrue(all(item.code not in ("drink", "set") for plan in plans for item in plan.items))
        self.assertTrue(all(item.beverage_status == "none" for plan in plans for item in plan.items))
        low_req = Requirements(people=1, budget_fen=800, exclude_beverage=True)
        self.assertEqual(generate_plans(menu, low_req, {}, context=PlanningContext(combo_details={"set": detail}))[0], [])

    def test_unknown_combo_not_counted_by_name(self):
        combo = MenuItemOut(code="set", name="双人不辣套餐", price_fen=100, original_price_fen=100,
                            category="测试套餐", is_combo=True, food_role="combo", servings=2,
                            spicy_level="mild")
        req = Requirements(people=2, budget_fen=1000, appetite="light",
                           dietary_constraints=[{"kind": "non_spicy", "people": 2}])
        plans, reason = generate_plans([combo], req, {})
        self.assertEqual(plans, [])
        self.assertTrue(reason)

    def test_verified_fixed_combo_uses_components(self):
        combo = MenuItemOut(code="set", name="固定套餐", price_fen=1200, original_price_fen=1200,
                            category="测试套餐", is_combo=True, food_role="combo")
        detail = {"verified": True, "components": [
            {"code": "m", "name": "鸡肉堡", "quantity": 1, "food_role": "main", "beverage_status": "none", "servings": 1, "non_spicy_servings": 1},
            {"code": "s", "name": "小食", "quantity": 1, "food_role": "side", "beverage_status": "none", "side_servings": 1},
        ]}
        req = Requirements(people=1, budget_fen=1200, exclude_beverage=True)
        plans, _ = generate_plans([combo], req, {}, context=PlanningContext(combo_details={"set": detail}))
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].items[0].components, detail["components"])
        validate_plan(plans[0], [combo], req, PlanningContext(combo_details={"set": detail}))

    def test_taste_ignores_marketing_tags(self):
        menu = [main("beef", "牛肉堡", 1200, tags=["上新", "chicken"]),
                main("chicken", "鸡肉堡", 1000, tags=["麦金卡", "beef"]), side()]
        req = Requirements(people=1, budget_fen=2000, preferred_tags=["beef"])
        plans, _ = generate_plans(menu, req, {})
        taste = next(plan for plan in plans if plan.type == "taste")
        self.assertTrue(any(item.code == "beef" for item in taste.items))
        self.assertFalse(any(item.code == "chicken" for item in taste.items))

    def test_membership_price_requires_verified_eligibility(self):
        menu = [main("member", "鸡肉堡", 100, membership=True), main("ordinary", "牛肉堡", 1000), side()]
        req = Requirements(people=1, budget_fen=2000, membership_status="active")
        plans, _ = generate_plans(menu, req, {})
        self.assertTrue(plans)
        self.assertTrue(all(item.code != "member" for plan in plans for item in plan.items))

    def test_verified_discount_can_rescue_above_budget_original(self):
        menu = [main("m", "鸡肉堡", 1200)]
        req = Requirements(people=1, budget_fen=1000, appetite="light")
        context = PlanningContext(store_code="s1", promotions=[{
            "id": "p", "description": "减三元", "item_codes": ["m"],
            "discount_fen": 300, "verified": True, "eligible": True,
        }])
        plans, _ = generate_plans(menu, req, {}, context=context)
        self.assertTrue(plans)
        self.assertEqual(plans[0].total_fen, 900)
        self.assertEqual(plans[0].applied_promotions[0]["discount_fen"], 300)
        validate_plan(plans[0], menu, req, context)

    def test_unverified_discount_cannot_rescue_budget(self):
        menu = [main("m", "鸡肉堡", 1200)]
        req = Requirements(people=1, budget_fen=1000, appetite="light")
        context = PlanningContext(promotions=[{
            "id": "p", "description": "未知优惠", "item_codes": ["m"],
            "discount_fen": 300, "verified": False, "eligible": True,
        }])
        self.assertEqual(generate_plans(menu, req, {}, context=context)[0], [])

    def test_validator_rejects_changed_amount_and_basket(self):
        menu = [main("m", "鸡肉堡", 1000), side()]
        req = Requirements(people=1, budget_fen=2000)
        plan = generate_plans(menu, req, {})[0][0]
        changed = plan.model_copy(deep=True)
        changed.total_fen -= 1
        with self.assertRaises(PlanValidationError):
            validate_plan(changed, menu, req)
        changed = plan.model_copy(deep=True)
        changed.items[0].spicy_level = "spicy"
        with self.assertRaises(PlanValidationError):
            validate_plan(changed, menu, req)
        changed = plan.model_copy(deep=True)
        changed.items[0].quantity += 1
        with self.assertRaises(PlanValidationError):
            validate_plan(changed, menu, req)

    def test_nutrition_replacement_is_recomputed_within_budget(self):
        menu = [main("chicken", "鸡肉堡", 1000), main("beef", "牛肉堡", 1200), side()]
        nutrition = {
            "chicken": NutritionOut(protein_g=10, matched=True, source="测试来源"),
            "beef": NutritionOut(protein_g=20, matched=True, source="测试来源"),
            "side": NutritionOut(protein_g=2, matched=True, source="测试来源"),
        }
        req = Requirements(people=1, budget_fen=2000)
        plans, _ = generate_plans(menu, req, nutrition)
        cheapest = next(plan for plan in plans if plan.type == "cheapest")
        suggestions = cheapest.nutrition_summary["suggestions"]
        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0]["metric"], "protein_g")
        self.assertLess(suggestions[0]["from_value"], suggestions[0]["to_value"])
        self.assertLessEqual(suggestions[0]["total_fen"], req.total_budget_fen)

    def test_twenty_people_finite_search(self):
        menu = [main("m", "鸡肉堡", 500), side(price=100)]
        req = Requirements(people=20, budget_fen=22000, appetite="heavy")
        plans, reason = generate_plans(menu, req, {})
        self.assertIsNone(reason)
        self.assertTrue(plans)
        self.assertEqual(plans[0].servings_total, 40)
        self.assertEqual(plans[0].side_servings_total, 10)


if __name__ == "__main__":
    unittest.main()
