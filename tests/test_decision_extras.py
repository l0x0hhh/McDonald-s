"""Decision-layer checks for verified comparisons and partial evidence."""
import unittest
from datetime import datetime, timezone

from backend.skill.campaigns import recommend_campaigns
from backend.skill.membership import compare_membership
from backend.skill.nutrition import build_nutrition_suggestion, summarize_nutrition


def membership_facts():
    basket = [{"code": "burger", "quantity": 2, "selected_options": [{"round": "main", "code": "beef"}]}]
    def quote(payable):
        return {"basket": basket, "payable_fen": payable, "verified": True,
                "eligible": True, "price_basis": "live_quote", "source": "A quote", "fetched_at": "2026-10-24T12:00:00+08:00"}
    return {
        "without_card": quote(1000),
        "existing_card": quote(800),
        "new_card": quote(800),
        "card_fee_fen": 300,
        "card_fee_verified": True,
        "card_fee_included_in_new_quote": False,
    }


class MembershipTests(unittest.TestCase):
    def test_negative_net_saving_is_reported(self):
        result = compare_membership(membership_facts())
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["without_card_fen"], 1000)
        self.assertEqual(result["existing_card_food_fen"], 800)
        self.assertEqual(result["new_card_total_fen"], 1100)
        self.assertEqual(result["net_saving_fen"], -100)

    def test_included_card_fee_is_not_added_again(self):
        facts = membership_facts()
        facts["new_card"]["payable_fen"] = 1300
        facts["new_card"]["includes_card_fee"] = True
        facts["card_fee_included_in_new_quote"] = True
        result = compare_membership(facts)
        self.assertEqual(result["new_card_food_fen"], 1000)
        self.assertEqual(result["new_card_total_fen"], 1300)
        self.assertEqual(result["net_saving_fen"], -300)

    def test_different_selected_options_cannot_be_called_member_saving(self):
        facts = membership_facts()
        facts["new_card"]["basket"] = [
            {"code": "burger", "quantity": 2,
             "selected_options": [{"round": "main", "code": "chicken"}]}
        ]
        result = compare_membership(facts)
        self.assertEqual(result["status"], "unavailable")
        self.assertIs(result["same_basket"], False)
        self.assertIsNone(result["net_saving_fen"])

    def test_unknown_eligibility_or_card_fee_is_unavailable(self):
        facts = membership_facts()
        facts["existing_card"]["eligible"] = None
        result = compare_membership(facts)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["without_card_fen"])
        facts = membership_facts()
        facts["card_fee_verified"] = False
        self.assertEqual(compare_membership(facts)["status"], "unavailable")
        facts = membership_facts()
        facts["without_card"]["price_basis"] = "menu_estimate"
        self.assertEqual(compare_membership(facts)["status"], "unavailable")

    def test_habit_requires_full_period_and_changes_with_frequency(self):
        facts = membership_facts()
        facts.update({"fixed_basket": True, "repeat_basket_eligible": True,
                      "repeat_saving_verified": True, "mutual_exclusion_verified": True,
                      "other_restrictions_verified": True, "card_validity_months": 2,
                      "benefit_unlimited": True})
        req = {"meal_frequency_per_month": 1, "habit_horizon_months": 3}
        self.assertIsNone(compare_membership(facts, req)["habit_estimate"])
        facts["card_validity_months"] = 3
        slow = compare_membership(facts, req)["habit_estimate"]
        fast = compare_membership(facts, {**req, "meal_frequency_per_month": 4})["habit_estimate"]
        self.assertEqual(slow["break_even_uses"], 2)
        self.assertEqual(slow["expected_uses"], 3)
        self.assertEqual(fast["expected_uses"], 12)
        self.assertGreater(fast["period_net_saving_fen"], slow["period_net_saving_fen"])
        facts.pop("mutual_exclusion_verified")
        self.assertIsNone(compare_membership(facts, req)["habit_estimate"])

    def test_quote_traceability_mixed_basis_and_expiry(self):
        facts = membership_facts()
        facts["without_card"].pop("source")
        self.assertEqual(compare_membership(facts)["status"], "unavailable")
        facts = membership_facts()
        facts["new_card"]["price_basis"] = "demo_snapshot"
        self.assertEqual(compare_membership(facts)["status"], "unavailable")
        facts = membership_facts()
        facts["new_card"]["valid_until"] = "2020-01-01T00:00:00Z"
        self.assertEqual(compare_membership(facts)["status"], "unavailable")

    def test_structural_options_ignore_order_but_not_spec_only_combo(self):
        facts = membership_facts()
        options = [{"round_id": "main", "code": "beef", "quantity": 1},
                   {"round_id": "side", "code": "fries", "quantity": 1}]
        for quote in (facts["without_card"], facts["existing_card"], facts["new_card"]):
            quote["basket"] = [{"code": "combo", "food_role": "combo", "quantity": 1,
                                "selected_options": options}]
        facts["new_card"]["basket"][0]["selected_options"] = list(reversed(options))
        self.assertEqual(compare_membership(facts)["status"], "available")
        for quote in (facts["without_card"], facts["existing_card"], facts["new_card"]):
            quote["basket"] = [{"code": "combo", "food_role": "combo", "quantity": 1,
                                "spec": "牛肉堡+薯条"}]
        self.assertEqual(compare_membership(facts)["status"], "unavailable")


class NutritionTests(unittest.TestCase):
    def test_partial_metrics_preserve_unknown_fiber(self):
        items = [{"code": "burger", "name": "汉堡", "food_role": "main", "quantity": 2,
                  "serving_size": "1个"}]
        nutrition = {"burger": {"matched": True, "energy_kcal": 300,
                                "protein_g": 10, "fiber_g": None, "serving_size": "1个",
                                "source": "mcd_mcp:list-nutrition-foods"}}
        result = summarize_nutrition(items, nutrition)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["totals"]["energy_kcal"], 600)
        self.assertIsNone(result["totals"]["fiber_g"])
        self.assertEqual(result["coverage"]["fiber_g"]["known_units"], 0)
        self.assertEqual(result["coverage"]["fiber_g"]["total_units"], 2)
        self.assertEqual(result["suggestions"], [])

    def test_unknown_combo_never_matches_combo_name(self):
        items = [{"code": "combo", "name": "套餐", "food_role": "combo", "quantity": 1,
                  "components": []}]
        result = summarize_nutrition(items, {"套餐": {"matched": True, "energy_kcal": 999}})
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["totals"]["energy_kcal"])
        self.assertIn("套餐最终组成未知", result["missing_items"][0]["reason"])
        self.assertIs(result["total_units_known"], False)
        self.assertEqual(result["unknown_combo_units"], 1)
        self.assertEqual(result["coverage"]["energy_kcal"]["total_units"], 0)

    def test_component_portion_and_metric_source(self):
        items = [{"code": "combo", "name": "套餐", "food_role": "combo", "quantity": 2,
                  "components": [{"code": "burger", "name": "汉堡", "quantity": 1,
                                  "serving_size": "1个"},
                                 {"code": "side", "name": "配菜", "quantity": 1,
                                  "serving_ratio": 0.5}]}]
        nutrition = {
            "burger": {"matched": True, "energy_kcal": 200, "serving_size": "1个", "source": "official"},
            "side": {"matched": True, "energy_kcal": 100, "fiber_g": 2,
                     "serving_size": "大份", "source": "official", "data_source": "demo_snapshot",
                     "metric_sources": {"fiber_g": {"source": "third-party", "estimated": True,
                                                     "serving_size": "大份", "fetched_at": "2026-10-24"}}},
        }
        result = summarize_nutrition(items, nutrition)
        self.assertEqual(result["totals"]["energy_kcal"], 500)
        self.assertEqual(result["totals"]["fiber_g"], 2)
        fiber_sources = [source for source in result["sources"] if source["metric"] == "fiber_g"]
        self.assertEqual(fiber_sources[0]["source"], "third-party")
        self.assertIs(fiber_sources[0]["estimated"], True)
        self.assertEqual(fiber_sources[0]["data_source"], "demo_snapshot")

    def test_size_mismatch_and_unverified_replacement_are_rejected(self):
        items = [{"code": "burger", "name": "汉堡", "food_role": "main", "quantity": 1,
                  "serving_size": "小份"}]
        nutrition = {"burger": {"matched": True, "energy_kcal": 200, "serving_size": "大份"}}
        context = {"replacement_candidates": [{"verified": True, "total_fen": 1200,
                   "budget_fen": 1000, "source": "A", "checks": {
                       "budget": True, "dietary": True, "beverage": True,
                       "servings": True, "preference": True}}]}
        result = summarize_nutrition(items, nutrition, context)
        self.assertIsNone(result["totals"]["energy_kcal"])
        self.assertEqual(result["suggestions"], [])

    def test_nonfinite_metric_is_unknown(self):
        items = [{"code": "x", "name": "食品", "food_role": "main", "quantity": 1}]
        for invalid in (float("nan"), float("inf")):
            with self.subTest(invalid=invalid):
                result = summarize_nutrition(items, {"x": {"matched": True, "protein_g": invalid}})
                self.assertIsNone(result["totals"]["protein_g"])

    def test_verified_replacement_requires_final_budget_and_source(self):
        def plan(code, total, protein, source="official"):
            return {"items": [{"code": code, "name": code, "quantity": 1}],
                    "total_fen": total, "budget_fen": 1000,
                    "checks": [{"name": "预算", "satisfied": True}],
                    "nutrition_summary": {"totals": {"protein_g": protein},
                                          "coverage": {"protein_g": {"complete": True}},
                                          "sources": [{"metric": "protein_g", "source": source}]}}
        current = plan("old", 700, 10)
        self.assertIsNotNone(build_nutrition_suggestion(current, plan("new", 900, 15)))
        self.assertIsNone(build_nutrition_suggestion(current, plan("expensive", 1100, 15)))
        self.assertIsNone(build_nutrition_suggestion(current, plan("unknown", 900, 15, "")))


class CampaignTests(unittest.TestCase):
    def test_expired_other_store_or_wrong_taste_are_filtered(self):
        now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        base = {"id": "good", "title": "鸡肉新品", "start_at": "2026-10-23T00:00:00Z",
                "end_at": "2026-10-25T00:00:00Z", "store_ids": ["s1"],
                "taste_tags": ["chicken"], "source": "A campaign feed",
                "account_required": False,
                "conditions": ["门店活动"], "reward_description": "有机会获赠"}
        expired = {**base, "id": "expired", "end_at": "2026-10-24T00:00:00Z"}
        other_store = {**base, "id": "other", "store_ids": ["s2"]}
        other_taste = {**base, "id": "fish", "taste_tags": ["fish"]}
        result = recommend_campaigns([base, expired, other_store, other_taste],
                                     {"preferred_tags": ["chicken"]}, "s1", now)
        self.assertEqual([item["id"] for item in result], ["good"])
        self.assertIs(result[0]["reward_guaranteed"], False)
        self.assertEqual(result[0]["source"], "A campaign feed")

    def test_membership_campaign_needs_confirmed_eligibility(self):
        now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        offer = {"id": "card", "start_at": "2026-10-23T00:00:00Z",
                 "end_at": "2026-10-25T00:00:00Z", "store_ids": ["s1"],
                 "source": "A", "account_required": False,
                 "membership_required": True, "taste_tags": ["beef"]}
        req = {"preferred_tags": ["beef"], "membership_status": "active"}
        self.assertEqual(recommend_campaigns([offer], req, "s1", now), [])
        offer["eligibility_verified"] = True
        self.assertEqual(len(recommend_campaigns([offer], req, "s1", now)), 1)

    def test_unknown_account_condition_is_not_assumed_eligible(self):
        now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        offer = {"id": "account", "start_at": "2026-10-23T00:00:00Z",
                 "end_at": "2026-10-25T00:00:00Z", "store_ids": ["s1"],
                 "source": "A", "taste_tags": ["beef"]}
        req = {"preferred_tags": ["beef"]}
        self.assertEqual(recommend_campaigns([offer], req, "s1", now), [])
        offer["account_required"] = True
        self.assertEqual(recommend_campaigns([offer], req, "s1", now), [])
        offer["account_eligibility_verified"] = True
        offer["eligible"] = True
        self.assertEqual(len(recommend_campaigns([offer], req, "s1", now)), 1)


if __name__ == "__main__":
    unittest.main()
