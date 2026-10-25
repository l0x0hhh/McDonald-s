"""一次性探针：核实 query-meal-detail / calculate-price 的真实返回结构。

属于 A 的职责（数据事实核实）。输出样例落到 docs/api-examples/raw/ 作为证据，
不进入业务链路。用法：

    .venv/Scripts/python.exe -m backend.scripts.probe_meal_detail
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from backend.providers.mcp_provider import McpMenuProvider

ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = ROOT / "docs" / "api-examples" / "raw"

# 探针目标：大堡口福三件套（报告里已核实的那个套餐）
SAMPLE_MEAL_CODE = "9900015602"


async def probe() -> int:
    provider = McpMenuProvider()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict = {}

    try:
        # 1) 工具清单：确认真实存在的工具名与入参 schema
        tools = await provider.client._request("tools/list")
        names = [t.get("name") for t in (tools or {}).get("tools", [])]
        results["tool_names"] = names
        (OUT_DIR / "tools_list.json").write_text(
            json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("工具数量:", len(names))
        for name in names:
            print("  -", name)
    except Exception as exc:  # noqa: BLE001 — 探针脚本，失败也要继续
        print("tools/list 失败:", exc)
        results["tool_names_error"] = str(exc)

    # 2) 套餐详情（入参实测为 code，不是 mealCode）
    try:
        detail = await provider.client.call_tool(
            "query-meal-detail",
            {"storeCode": "1400011", "code": SAMPLE_MEAL_CODE, "orderType": 1, "beType": 1},
        )
        results["query-meal-detail"] = detail
        (OUT_DIR / "query_meal_detail.json").write_text(
            json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        data = detail.get("data") or {}
        print("\n=== query-meal-detail ===")
        print("success:", detail.get("success"), "message:", detail.get("message"))
        print("data 键:", list(data.keys()) if isinstance(data, dict) else type(data).__name__)
        rounds = (data or {}).get("rounds") or []
        print("rounds 数量:", len(rounds))
        for r in rounds:
            print(
                "  round",
                r.get("round"),
                r.get("name"),
                "minQty",
                r.get("minQuantity"),
                "maxQty",
                r.get("maxQuantity"),
                "默认",
                (r.get("defaultItem") or {}).get("name")
                if isinstance(r.get("defaultItem"), dict)
                else None,
                "选项数",
                len(r.get("items") or r.get("comboItemList") or []),
            )
        if rounds:
            first = rounds[0]
            opts = first.get("items") or first.get("comboItemList") or []
            if opts:
                print("  首个 round 的首个选项字段:", list(opts[0].keys()))
                print("  样例选项:", json.dumps(opts[0], ensure_ascii=False)[:300])
    except Exception as exc:  # noqa: BLE001
        print("query-meal-detail 失败:", exc)
        results["query-meal-detail_error"] = str(exc)

    # 3) 算价（只读试算，不下单；入参为 productCode + roundList）
    try:
        price = await provider.client.call_tool(
            "calculate-price",
            {
                "storeCode": "1400011",
                "orderType": 1,
                "beType": 1,
                "items": [{"productCode": SAMPLE_MEAL_CODE, "quantity": 1}],
            },
        )
        results["calculate-price"] = price
        (OUT_DIR / "calculate_price.json").write_text(
            json.dumps(price, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\n=== calculate-price ===")
        print("success:", price.get("success"), "message:", price.get("message"))
        pdata = price.get("data") or {}
        print(
            "price:",
            pdata.get("price"),
            "originalPrice:",
            pdata.get("originalPrice"),
            "discount:",
            pdata.get("discount"),
            "productList 条数:",
            len(pdata.get("productList") or []),
        )
    except Exception as exc:  # noqa: BLE001
        print("calculate-price 失败:", exc)
        results["calculate-price_error"] = str(exc)

    await provider.aclose()

    (OUT_DIR / "probe_summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return 0


def main() -> int:
    return asyncio.run(probe())


if __name__ == "__main__":
    sys.exit(main())
