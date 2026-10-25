# 回归说明：覆盖子组人数、部分饮料偏好、否定更正、会员别名及在线模型证据边界。
"""B requirement extraction and shared chat contract behavior tests."""
import asyncio
import os
import unittest
import io
import json
import urllib.error
from unittest.mock import patch

from backend.llm import ExtractionError, _request_model, extract_requirements
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

    # 子组人数不能替换总人数；当前模型不支持部分饮料需求时必须保留草稿并追问。
    def test_partial_beverage_preserves_total_and_requires_scope(self):
        examples = (
            ("我们4个人，总预算120元，其中2人不喝饮料", 4),
            ("我们4人，总预算120元，其中2人要饮料", 4),
            ("我们6人，总预算150元，2人不要饮料，4人正常", 6),
            ("我们4人，总预算120元，有些人不想要饮料", 4),
        )
        for message, total in examples:
            with self.subTest(message=message):
                result = rule_extract(message)
                self.assertEqual(result["draft"].people, total)
                self.assertFalse(result["draft"].exclude_beverage)
                self.assertIsNone(result["requirements"])
                self.assertIn("beverage_scope", result["missing_fields"])
                self.assertIn("暂不支持按人分配", result["follow_up"])

    def test_partial_beverage_followup_and_explicit_resolution(self):
        first = rule_extract("4人，总预算120元，不要饮料")
        partial = rule_extract("其中2人要饮料", first["draft"])
        self.assertEqual(partial["draft"].people, 4)
        self.assertTrue(partial["draft"].exclude_beverage)
        unrelated = rule_extract("喜欢鸡肉", partial["draft"])
        self.assertIn("beverage_scope", unrelated["missing_fields"])
        resolved = rule_extract("整单不限制饮料", unrelated["draft"])
        self.assertEqual(resolved["requirements"].people, 4)
        self.assertFalse(resolved["requirements"].exclude_beverage)
        self.assertEqual(resolved["requirements"].preferred_tags, ["chicken"])

    def test_same_sentence_whole_order_resolution_and_all_people_without_drinks(self):
        resolved = rule_extract("4人，总预算120元，其中2人不要饮料，算了整单不要饮料")
        self.assertEqual(resolved["requirements"].people, 4)
        self.assertTrue(resolved["requirements"].exclude_beverage)
        for message in ("2人不要饮料，总预算100元", "4人，总预算100元，其中4人不喝饮料"):
            with self.subTest(message=message):
                result = rule_extract(message)
                self.assertIsNotNone(result["requirements"])
                self.assertTrue(result["requirements"].exclude_beverage)

    def test_negated_appetite_preserves_previous_and_allows_correction(self):
        for phrase in ("不是大饭量", "并非大饭量", "不算大饭量"):
            with self.subTest(phrase=phrase):
                self.assertEqual(rule_extract("2人，总预算100元，" + phrase)["requirements"].appetite, "normal")
        previous = rule_extract("2人，总预算100元，轻饭量")["draft"]
        self.assertEqual(rule_extract("不是大饭量", previous)["requirements"].appetite, "light")
        self.assertEqual(rule_extract("不是轻饭量，改成大饭量", previous)["requirements"].appetite, "heavy")

    def test_taste_negation_is_local_to_clause_and_replacement(self):
        previous = rule_extract("4人，总预算120元，喜欢牛肉")["draft"]
        for phrase in ("不喜欢牛肉，改成鸡肉", "不喜欢牛肉改成鸡肉", "不喜欢牛肉，喜欢鸡肉"):
            with self.subTest(phrase=phrase):
                self.assertEqual(rule_extract(phrase, previous)["requirements"].preferred_tags, ["chicken"])
        # “不要牛肉”仍属于目前不能核实的硬忌口，不能因提到鸡肉就自动确认需求。
        hard_diet = rule_extract("不要牛肉，喜欢鸡肉", previous)
        self.assertEqual(hard_diet["draft"].preferred_tags, ["chicken"])
        self.assertIsNone(hard_diet["requirements"])
        self.assertIn("dietary_constraints", hard_diet["missing_fields"])

    def test_named_membership_cards_and_negative_status(self):
        for phrase, status in (("我有麦金卡", "active"), ("已有麦金学期卡", "active"),
                               ("我有早餐卡", "active"), ("我没有麦金卡", "none"),
                               ("我没开卡", "none"), ("我没有会员卡，后来已经开卡", "active")):
            with self.subTest(phrase=phrase):
                self.assertEqual(rule_extract("1人，总预算50元，" + phrase)["requirements"].membership_status, status)

    # 即使模型截短引文，只拿“2人”或“大饭量”，服务端也必须核查完整原句。
    def test_model_cannot_turn_subgroup_into_total_or_whole_order(self):
        previous = rule_extract("4人，总预算120元")["draft"]
        with patch("backend.llm._request_model", return_value={
            "updates": {"people": 2, "exclude_beverage": True},
            "evidence": {"people": "2人", "exclude_beverage": "不喝饮料"},
        }):
            result = asyncio.run(extract_requirements("其中2人不喝饮料", previous))
        self.assertEqual(result["parse_source"], "llm")
        self.assertEqual(result["draft"].people, 4)
        self.assertFalse(result["draft"].exclude_beverage)
        self.assertIn("beverage_scope", result["missing_fields"])
        self.assertIsNone(result["requirements"])

    def test_model_cannot_reinstate_negated_appetite_or_taste(self):
        with patch("backend.llm._request_model", return_value={
            "updates": {"appetite": "heavy", "preferred_tags": ["beef"]},
            "evidence": {"appetite": "大饭量", "preferred_tags": "牛肉"},
        }):
            result = asyncio.run(extract_requirements("2人，总预算100元，不是大饭量，不喜欢牛肉"))
        self.assertEqual(result["requirements"].appetite, "normal")
        self.assertEqual(result["requirements"].preferred_tags, [])

    def test_deepseek_request_uses_json_non_thinking_and_selected_model(self):
        response = {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"updates": {}, "evidence": {}})}}]}
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://api.deepseek.com", "LLM_MODEL": "deepseek-flash", "LLM_API_KEY": "test-only"}):
            with patch("backend.llm.urllib.request.urlopen") as request:
                request.return_value.__enter__.return_value = io.BytesIO(json.dumps(response).encode())
                self.assertEqual(_request_model("2人，总预算100元", None, 12), {"updates": {}, "evidence": {}})
                payload = json.loads(request.call_args.args[0].data)
                self.assertEqual(payload["model"], "deepseek-flash")
                self.assertEqual(payload["thinking"], {"type": "disabled"})
                self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_http_error_retains_status_without_leaking_credentials(self):
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://api.deepseek.com", "LLM_MODEL": "deepseek-flash", "LLM_API_KEY": "test-only"}):
            with patch("backend.llm.urllib.request.urlopen", side_effect=urllib.error.HTTPError("https://api.deepseek.com/chat/completions", 401, "Unauthorized", {}, None)):
                with self.assertRaisesRegex(ExtractionError, "HTTP 401") as error:
                    _request_model("2人，总预算100元", None, 12)
                self.assertNotIn("test-only", str(error.exception))

    def test_online_model_adds_evidenced_chinese_budget_beyond_rule_template(self):
        message = "三位，人均三十元，喜欢鸡肉"
        self.assertIsNone(rule_extract(message)["requirements"])
        with patch("backend.llm._request_model", return_value={
            "updates": {"budget_type": "per_person", "budget_fen": 3000},
            "evidence": {"budget_type": "人均三十元", "budget_fen": "三十元"},
        }):
            result = asyncio.run(extract_requirements(message))
        self.assertEqual(result["parse_source"], "llm")
        self.assertEqual(result["requirements"].people, 3)
        self.assertEqual(result["requirements"].total_budget_fen, 9000)
        with patch("backend.llm._request_model", return_value={
            "updates": {"budget_fen": 300}, "evidence": {"budget_fen": "三位"},
        }):
            unsupported = asyncio.run(extract_requirements(message))
        self.assertIsNone(unsupported["requirements"])


if __name__ == "__main__":
    unittest.main()
