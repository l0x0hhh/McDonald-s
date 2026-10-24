"""B requirement extraction and shared chat contract behavior tests."""
import asyncio
import os
import unittest
from unittest.mock import patch

from backend.llm import ExtractionError, extract_requirements
from backend.schemas import Requirements, RequirementsDraft
from backend.skill.parser import RequirementError, parse_requirements, rule_extract


class ParserTests(unittest.TestCase):
    def test_shared_requirements_and_budget_units(self):
        requirement = parse_requirements({"people": 4, "budget_type": "per_person", "budget_fen": 3000})
        self.assertIsInstance(requirement, Requirements)
        self.assertEqual(requirement.total_budget_fen, 12000)

    def test_invalid_complete_requirements_fail(self):
        examples = (
            {"people": 0, "budget_type": "total", "budget_fen": 5000},
            {"people": True, "budget_type": "total", "budget_fen": 5000},
            {"people": 6, "budget_type": "total", "budget_fen": 15000,
             "dietary_constraints": [{"kind": "non_spicy", "people": 7}]},
            {"people": 4, "budget_fen": 3000},
            {"people": 4, "budget_type": "total", "budget_fen": 3000, "unsupported": True},
        )
        for data in examples:
            with self.subTest(data=data), self.assertRaises(RequirementError):
                parse_requirements(data)

    def test_demo_contract_and_explicit_source(self):
        result = asyncio.run(extract_requirements("6人，总预算150元，其中2人不吃辣", demo_mode=True))
        self.assertEqual(result["parse_source"], "demo_template")
        self.assertIsInstance(result["draft"], RequirementsDraft)
        self.assertIsInstance(result["requirements"], Requirements)
        self.assertEqual(result["requirements"].total_budget_fen, 15000)
        self.assertEqual(result["requirements"].non_spicy_people, 2)
        self.assertEqual(result["missing_fields"], [])

    def test_partial_chat_retains_hard_constraints(self):
        first = rule_extract("6人，不要饮料，大饭量，喜欢牛肉")
        self.assertIsNone(first["requirements"])
        self.assertEqual(first["missing_fields"], ["budget"])
        second = rule_extract("总预算150元，其中2人不吃辣", first["draft"])
        requirement = second["requirements"]
        self.assertEqual(requirement.people, 6)
        self.assertEqual(requirement.budget_fen, 15000)
        self.assertEqual(requirement.non_spicy_people, 2)
        self.assertTrue(requirement.exclude_beverage)
        self.assertEqual(requirement.appetite, "heavy")
        self.assertIn("beef", requirement.preferred_tags)

    def test_correction_and_beverage_release_preserve_other_fields(self):
        initial = rule_extract("6人，总预算150元，2人不吃辣，不要饮料，喜欢牛肉")
        changed = rule_extract("不是6人，是4人；可以喝饮料，牛肉改成鸡肉", initial["draft"])
        requirement = changed["requirements"]
        self.assertEqual(requirement.people, 4)
        self.assertEqual(requirement.non_spicy_people, 2)
        self.assertEqual(requirement.budget_fen, 15000)
        self.assertFalse(requirement.exclude_beverage)
        self.assertNotIn("beef", requirement.preferred_tags)
        self.assertIn("chicken", requirement.preferred_tags)

    def test_non_spicy_missing_and_bare_follow_up(self):
        first = rule_extract("6人，总预算150元，有人不吃辣")
        self.assertIn("non_spicy_people", first["missing_fields"])
        self.assertIsNone(first["requirements"])
        second = rule_extract("2人", first["draft"])
        self.assertEqual(second["requirements"].people, 6)
        self.assertEqual(second["requirements"].non_spicy_people, 2)

    def test_non_spicy_over_people_kept_for_confirmation(self):
        result = rule_extract("4人，总预算150元，6人不吃辣")
        self.assertIsNone(result["requirements"])
        self.assertIn("non_spicy_people", result["missing_fields"])
        self.assertEqual(result["draft"].dietary_constraints[0].people, 6)
        self.assertEqual(result["draft"].people, 4)
        corrected = rule_extract("不是4人，是6人", result["draft"])
        self.assertEqual(corrected["requirements"].non_spicy_people, 6)
        self.assertEqual(corrected["requirements"].people, 6)

    def test_conflicting_budget_and_unsupported_allergy_are_not_silenced(self):
        result = rule_extract("4人，总预算150元，人均30元，花生过敏")
        self.assertIsNone(result["requirements"])
        self.assertIn("budget", result["missing_fields"])
        self.assertIn("dietary_constraints", result["missing_fields"])
        self.assertIsNone(result["draft"].budget_type)

    def test_ambiguous_budget_requires_basis(self):
        result = rule_extract("6人预算150元")
        self.assertIsNone(result["requirements"])
        self.assertEqual(result["draft"].budget_fen, 15000)
        self.assertIn("budget", result["missing_fields"])
        confirmed = rule_extract("是总预算", result["draft"])
        self.assertEqual(confirmed["requirements"].total_budget_fen, 15000)

    def test_membership_and_self_reported_habits(self):
        result = rule_extract("1人，总预算40元，我还没开卡，每月3次，分析6个月，平时常买鸡肉")
        requirement = result["requirements"]
        self.assertEqual(requirement.membership_status, "none")
        self.assertEqual(requirement.meal_frequency_per_month, 3)
        self.assertEqual(requirement.habit_horizon_months, 6)
        self.assertIn("chicken", requirement.habitual_tags)
        self.assertNotIn("chicken", requirement.preferred_tags)

    def test_beverage_reversal_phrases(self):
        first = rule_extract("1人，总预算30元，不喝饮品")
        self.assertTrue(first["requirements"].exclude_beverage)
        changed = rule_extract("允许饮料", first["draft"])
        self.assertFalse(changed["requirements"].exclude_beverage)
        negated = rule_extract("不是不喝饮料", first["draft"])
        self.assertFalse(negated["requirements"].exclude_beverage)

    def test_online_without_key_raises_instead_of_faking_llm(self):
        with patch.dict(os.environ, {"LLM_API_KEY": ""}):
            with self.assertRaises(ExtractionError):
                asyncio.run(extract_requirements("4人，人均30元"))

    def test_online_mock_requires_literal_evidence(self):
        response = {"updates": {"people": 20, "preferred_tags": ["chicken"]},
                    "evidence": {"people": "4人", "preferred_tags": "喜欢鸡肉"}}
        with patch("backend.llm._request_model", return_value=response):
            result = asyncio.run(extract_requirements("4人，人均30元，喜欢鸡肉"))
        self.assertEqual(result["parse_source"], "llm")
        self.assertEqual(result["requirements"].people, 4)
        self.assertIn("chicken", result["requirements"].preferred_tags)

    def test_online_model_count_must_match_cited_words(self):
        with patch("backend.llm._request_model", return_value={
            "updates": {"people": 20}, "evidence": {"people": "六位"},
        }):
            bad = asyncio.run(extract_requirements("六位，总预算150元"))
        # 固定规则已能识别“六位”；模型声称 20 人不能覆盖这条证据。
        self.assertEqual(bad["requirements"].people, 6)
        with patch("backend.llm._request_model", return_value={
            "updates": {"people": 6}, "evidence": {"people": "六位"},
        }):
            good = asyncio.run(extract_requirements("六位，总预算150元"))
        self.assertEqual(good["requirements"].people, 6)

    def test_same_sentence_budget_correction_and_unsupported_diet(self):
        corrected = rule_extract("3位，不是总预算150元，是总预算120元，不要饮料")
        self.assertEqual(corrected["requirements"].people, 3)
        self.assertEqual(corrected["requirements"].budget_fen, 12000)
        self.assertTrue(corrected["requirements"].exclude_beverage)
        unsupported = rule_extract("3人，总预算120元，不含花生")
        self.assertIsNone(unsupported["requirements"])
        self.assertIn("dietary_constraints", unsupported["missing_fields"])

    def test_online_double_negative_does_not_create_no_beverage_constraint(self):
        with patch("backend.llm._request_model", return_value={
            "updates": {"exclude_beverage": True},
            "evidence": {"exclude_beverage": "不是不要饮料"},
        }):
            result = asyncio.run(extract_requirements("1人，总预算30元，不是不要饮料"))
        self.assertFalse(result["requirements"].exclude_beverage)


if __name__ == "__main__":
    unittest.main()
