# 在线验收：真实请求已配置模型，不允许规则回退计作在线成功，输出脱敏结果。
"""Run with: python -B -m backend.scripts.verify_online_llm --timeout 20."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from backend import config  # 复用 backend/.env 加载，不复制密钥。
from backend.llm import _request_model, _validated_result
from backend.schemas import ChatIn
from backend.services.chat_service import ChatService


CASES = (
    ("多人预算忌辣", "6人，总预算150元，其中2人不吃辣", None,
     {"people": 6, "total_budget_fen": 15000, "non_spicy_people": 2, "ready": True}),
    ("先说人数", "我们4个人", None, {"people": 4, "ready": False}),
    ("跨轮补预算", "人均30元", "先说人数", {"people": 4, "total_budget_fen": 12000, "ready": True}),
    ("部分人不喝饮料", "其中2人不喝饮料", "跨轮补预算",
     {"people": 4, "exclude_beverage": False, "ready": False, "beverage_pending": True}),
    ("确认整单口径", "整单不限制饮料，喜欢牛肉", "部分人不喝饮料",
     {"people": 4, "exclude_beverage": False, "preferred_tags": ["beef"], "ready": True}),
    ("更正口味", "不喜欢牛肉，改成鸡肉", "确认整单口径", {"preferred_tags": ["chicken"], "ready": True}),
    ("否定大饭量", "不是大饭量", "更正口味", {"appetite": "normal", "ready": True}),
    ("麦金卡自报", "我有麦金卡", "否定大饭量", {"membership_status": "active", "ready": True}),
    ("中文金额模型补充", "三位，人均三十元，喜欢鸡肉", None,
     {"people": 3, "total_budget_fen": 9000, "preferred_tags": ["chicken"], "ready": True}),
)


def snapshot(result: dict) -> dict:
    draft, requirement = result["draft"], result.get("requirements")
    return {
        "people": draft.people, "budget_type": draft.budget_type, "budget_fen": draft.budget_fen,
        "total_budget_fen": requirement.total_budget_fen if requirement else None,
        "non_spicy_people": requirement.non_spicy_people if requirement else None,
        "exclude_beverage": draft.exclude_beverage, "appetite": draft.appetite,
        "preferred_tags": draft.preferred_tags, "membership_status": draft.membership_status,
        "ready": requirement is not None and not result["missing_fields"],
        "beverage_pending": "beverage_scope" in result["missing_fields"],
        "missing_fields": result["missing_fields"], "parse_source": result["parse_source"],
    }


async def verify(timeout: float) -> list[dict]:
    results, drafts = [], {}
    for name, message, previous, expected in CASES:
        start = time.perf_counter()
        if previous and previous not in drafts:
            row = {"case": name, "passed": False, "status": "blocked", "error": "前序用例未获得草稿"}
        else:
            try:
                current = drafts.get(previous)
                # 直接验证真实 HTTP 返回，再走与业务相同的证据校验，不使用任何模型 mock。
                raw = await asyncio.wait_for(asyncio.to_thread(_request_model, message, current, timeout), timeout + 2)
                result = _validated_result(raw, message, current)
                drafts[name] = result["draft"]
                actual = snapshot(result)
                errors = [f"{key}: expected {value!r}, actual {actual.get(key)!r}"
                          for key, value in expected.items() if actual.get(key) != value]
                if actual["parse_source"] != "llm":
                    errors.append("在线验收不允许规则或演示来源")
                row = {"case": name, "passed": not errors, "status": "completed", "actual": actual,
                       "errors": errors, "model_updates": raw.get("updates"), "model_evidence": raw.get("evidence")}
            except Exception as exc:
                row = {"case": name, "passed": False, "status": "error",
                       "error": str(exc).replace(os.environ.get("LLM_API_KEY", ""), "[REDACTED]")}
        row["seconds"] = round(time.perf_counter() - start, 2)
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if row.get("status") == "error" and any(f"HTTP {code}" in row["error"] for code in (401, 402, 403)):
            return results  # 凭据或额度错误无需继续产生重复请求。

    # 使用业务 ChatService 再验证一次真实调用，规则降级不能通过此项。
    start = time.perf_counter()
    config.settings.data_mode = "auto"
    result = await ChatService().parse(ChatIn(message="2人，总预算100元，不要饮料"))
    row = {"case": "业务在线入口", "passed": result.parse_source == "llm" and result.ready
           and result.requirements.people == 2 and result.requirements.exclude_beverage,
           "parse_source": result.parse_source, "ready": result.ready, "notes": result.notes,
           "seconds": round(time.perf_counter() - start, 2)}
    results.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="真实在线模型验收；backend/.env 中配置 LLM_* 后运行。")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--output", type=Path, default=Path(".qa_tmp/online-llm-results.json"))
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout 必须大于0")
    missing = [key for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY") if not os.getenv(key, "").strip()]
    if missing:
        print("请先在 backend/.env 填写：" + "、".join(missing), flush=True)
        return 2
    results = asyncio.run(verify(args.timeout))
    summary = {"model": os.environ["LLM_MODEL"], "base_url": os.environ["LLM_BASE_URL"],
               "expected_cases": len(CASES) + 1, "completed_cases": len(results),
               "passed": sum(bool(row["passed"]) for row in results),
               "failed": sum(not row["passed"] for row in results), "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已保存：{args.output}；通过 {summary['passed']}，失败 {summary['failed']}", flush=True)
    return 0 if len(results) == len(CASES) + 1 and summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
