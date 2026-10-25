"""Explicit test-only upstream injections. Production never imports this module."""
import copy
import json
from pathlib import Path

from backend.providers import ProviderResult, McpError
from backend.schemas import PlanningContext, Requirements
from backend.services.combo_service import ComboService
from backend.services.menu_service import MenuService
from backend.services.nutrition_service import NutritionService
from backend.services.llm_planner import LLMPlanner
from backend.services.plan_service import PlanService
from backend.services.plan_store import PlanStore
from backend.services.checkout_service import CheckoutService
from backend.services.order_repository import OrderRepository


class FixtureMcp:
    def __init__(self):
        self.raw = json.loads((Path(__file__).parents[1] / "data" / "snapshot_1400011.json").read_text(encoding="utf-8"))

    def result(self, data):
        return ProviderResult(copy.deepcopy(data), "live_mcp", "2026-10-25T00:00:00Z")

    async def get_stores(self, city, keyword): return self.result(self.raw["stores"])
    async def get_menu(self, store_code): return self.result(self.raw["menu"])
    async def get_nutrition(self): return self.result(self.raw["nutrition"])
    async def get_meal_detail(self, store_code, code):
        if code not in self.raw["meal_details"]:
            raise McpError("test fixture has no detail")
        return self.result(self.raw["meal_details"][code])
    async def aclose(self): pass


class FixtureModel:
    async def __call__(self, payload, timeout):
        data = json.loads(payload["input"])
        req = data["requirements"]
        # A deterministic *injected model response*, not a production fallback.
        eligible = [i for i in data["items"] if i["food_role"] == "main" and not i["is_combo"]
                    and i["available"] and not i["requires_membership"] and i["spicy_level"] == "mild"
                    and i["servings"] > 0 and i["beverage_status"] == "none"]
        eligible.sort(key=lambda i: i["price_fen"])
        if not eligible or eligible[0]["price_fen"] * req["people"] > (
            req["budget_fen"] * (req["people"] if req["budget_type"] == "per_person" else 1)):
            return {"candidates": []}
        return {"candidates": [{"title": "每人一个汉堡", "summary": "汉堡分给各位，不吃辣的人也有一份。",
                                 "reasons": ["主食够分，不含饮品"],
                                 "items": [{"code": eligible[0]["code"], "quantity": req["people"], "selected_options": []}]}]}


class FixtureCheckoutProvider:
    def __init__(self, mcp): self.mcp, self.creates = mcp, 0
    async def calculate_price(self, args):
        from backend.providers.mapper import map_menu, map_meal_detail
        items, _ = map_menu(args["storeCode"], self.mcp.raw["menu"])
        menu = {i.code: i for i in items}
        total = 0
        for line in args["items"]:
            price = menu[line["productCode"]].price_fen
            if line.get("roundList"):
                detail = map_meal_detail(self.mcp.raw["meal_details"][line["productCode"]], "live_mcp")
                options = {(r.round_id, o.code): o for r in detail.rounds for o in r.options}
                price += sum(options[(r["round"], o["code"])].diff_price_fen * o["quantity"]
                             for r in line["roundList"] for o in r["comboItemList"])
            total += price * line["quantity"]
        return {"success": True, "data": {"price": total, "originalPrice": total, "discount": 0,
                                           "takeWayList": [{"code": "pickup", "title": "自取", "subtitle": ""}]}}
    async def create_order(self, args):
        self.creates += 1
        return {"success": True, "data": {"orderId": "fixture-official-order", "payH5Url": "https://m.mcd.cn/mcp/scanToPay?orderId=fixture-official-order",
                                           "orderDetail": {"orderStatus": "待支付", "status": "10"}}}
    async def query_order(self, args):
        return {"success": True, "data": {"orderId": "fixture-official-order", "orderStatus": "待支付", "status": "10"}}


def make_services(tmp_path):
    provider = FixtureMcp()
    menus = MenuService(mcp=provider)
    plans = PlanStore(tmp_path / "plans.json")
    checkout = CheckoutService(plans=plans, provider=FixtureCheckoutProvider(provider),
                               repository=OrderRepository(tmp_path / "orders.sqlite3"))
    service = PlanService(menus=menus, combos=ComboService(mcp=provider),
                          nutrition=NutritionService(menus=menus), store=plans,
                          planner=LLMPlanner(requester=FixtureModel()), checkout=checkout)
    return menus, service


def inject_chat(monkeypatch):
    from backend import llm
    monkeypatch.setattr(llm, "_request_model", lambda *args: {"updates": {}, "evidence": {}})
