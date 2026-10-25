"""Checkout contracts: real SQLite persistence, external MCP boundary replaced only."""
import asyncio
import copy
import json
import sqlite3

import pytest

from backend.schemas import OrderIn, PlanItemOut, PlanOut, Requirements, SelectedOptionOut


def plan_fixture():
    return PlanOut(
        plan_id="plan-one", type="recommended", title="一餐", summary="",
        items=[PlanItemOut(code="combo", name="套餐", quantity=1,
                           unit_price_fen=1500, original_unit_price_fen=1800,
                           line_total_fen=1500, servings=1, spicy_level="mild",
                           selected_options=[SelectedOptionOut(round_id="main", option_code="burger")])],
        subtotal_fen=1800, total_fen=1500, budget_fen=5000,
        remaining_fen=3500, servings_total=1, non_spicy_servings=1,
    )


class FakeProvider:
    def __init__(self):
        self.price = {"data": {"price": 1321, "originalPrice": 1800, "discount": 479,
                               "productPrice": 1300, "packingPrice": 21,
                               "takeWayList": [{"code": "take", "title": "到店取餐", "subtitle": ""}],
                               "productList": [{"productCode": "combo", "quantity": 1, "subtotal": 1300}]}}
        self.result = {"data": {"orderId": "official-123", "payH5Url": "https://m.mcd.cn/pay?id=123",
                                "orderDetail": {"orderStatus": "1", "expirePayTime": "2026-10-25T12:00:00+08:00"}}}
        self.query = {"data": {"orderId": "official-123", "orderStatus": "2"}}
        self.created = []
        self.calculated = []
        self.failure = None
        self.gate = None

    async def calculate_price(self, arguments):
        self.calculated.append(copy.deepcopy(arguments))
        return copy.deepcopy(self.price)

    async def create_order(self, arguments):
        self.created.append(copy.deepcopy(arguments))
        if self.gate:
            await self.gate.wait()
        if self.failure:
            raise self.failure
        return copy.deepcopy(self.result)

    async def query_order(self, order_id):
        return copy.deepcopy(self.query)


def setup_checkout(tmp_path, *, plans=None):
    from backend.services.checkout_service import CheckoutService
    from backend.services.order_repository import OrderRepository
    provider = FakeProvider()
    repository = OrderRepository(tmp_path / "orders.sqlite3")
    return CheckoutService(plans=plans, provider=provider, repository=repository), provider, repository


async def quoted_request(service, plan=None, key="request-key-0001"):
    plan = await service.quote(plan or plan_fixture(), "store-1", Requirements(people=1, budget_fen=5000))
    return plan, OrderIn(plan_id=plan.plan_id, quote_id=plan.quote_id, take_way_code="take", idempotency_key=key)


def test_official_quote_fen_unknown_fees_and_round_mapping(tmp_path):
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        quoted, _ = await quoted_request(service)
        assert quoted.total_fen == 1321
        assert quoted.remaining_fen == 3679
        assert quoted.quote_breakdown["tablewarePrice"] is None
        assert quoted.quote_breakdown["packingPrice"] == 21
        assert quoted.quote_breakdown["productPrice"] == 1300
        assert quoted.price_basis == "live_quote"
        assert quoted.quote_id and quoted.quoted_at
        assert provider.calculated[0] == {"storeCode": "store-1", "orderType": 1, "beType": 1,
                                          "items": [{"productCode": "combo", "quantity": 1,
                                                     "roundList": [{"round": "main", "comboItemList": [{"code": "burger", "quantity": 1}]}]}]}
    asyncio.run(run())


def test_multi_spec_lines_do_not_receive_fabricated_official_prices(tmp_path):
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        plan = plan_fixture()
        second = plan.items[0].model_copy(deep=True)
        second.selected_options[0].option_code = "chicken"
        second.line_total_fen = second.unit_price_fen = 1600
        plan.items.append(second)
        provider.price["data"]["productList"] = [
            {"productCode": "combo", "quantity": 1, "subtotal": 650},
            {"productCode": "combo", "quantity": 1, "subtotal": 650}]
        quoted, _ = await quoted_request(service, plan)
        assert [i.line_total_fen for i in quoted.items] == [1500, 1600]
        assert all(i.line_price_basis == "menu_reference" for i in quoted.items)
        assert len(provider.calculated[0]["items"]) == 2
    asyncio.run(run())


@pytest.mark.parametrize("change", ["price", "takeway"])
def test_price_or_takeway_change_returns_new_quote_without_creating(tmp_path, change):
    from backend.services.checkout_service import CheckoutError
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        quoted, request = await quoted_request(service)
        if change == "price":
            provider.price["data"]["price"] = 1400
        else:
            provider.price["data"]["takeWayList"] = [{"code": "counter", "title": "柜台取餐"}]
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(request)
        assert caught.value.code == "E_PRICE_CHANGED"
        assert caught.value.data["updated_plan"]["quote_id"] != quoted.quote_id
        assert provider.created == []
    asyncio.run(run())


def test_order_success_replay_survives_restart_and_plan_clear(tmp_path):
    from backend.services.checkout_service import CheckoutService
    from backend.services.plan_store import PlanStore
    from backend.services.order_repository import OrderRepository
    async def run():
        plans = PlanStore(tmp_path / "plans.json")
        service, provider, _ = setup_checkout(tmp_path, plans=plans)
        quoted, request = await quoted_request(service)
        plans.save(quoted, "store-1", Requirements(people=1, budget_fen=5000))
        result = await service.create_order(request)
        assert result.order_id == "official-123"
        assert result.status == "pending_payment"
        assert result.total_fen == 1321
        assert result.payment_url == "https://m.mcd.cn/pay?id=123"
        assert result.expires_at == "2026-10-25T12:00:00+08:00"
        assert provider.created[0] == dict(provider.calculated[-1], takeWayCode="take")
        plans.invalidate_all()
        restarted = CheckoutService(plans=plans, provider=provider, repository=OrderRepository(tmp_path / "orders.sqlite3"))
        assert (await restarted.create_order(request)).model_dump() == result.model_dump()
        assert len(provider.created) == 1
        confirmed = await restarted.get_order("official-123")
        assert confirmed.status == "preparing"
    asyncio.run(run())


def test_same_key_different_request_conflicts_before_plan_lookup(tmp_path):
    from backend.services.checkout_service import CheckoutError
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        await service.create_order(request)
        changed = request.model_copy(update={"plan_id": "missing"})
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(changed)
        assert caught.value.code == "E_IDEMPOTENCY_CONFLICT"
        assert len(provider.created) == 1
    asyncio.run(run())


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), ValueError("decode failed"), RuntimeError("parse failed")])
def test_submission_failure_is_durable_unknown_and_never_retried(tmp_path, failure):
    from backend.services.checkout_service import CheckoutError, CheckoutService
    from backend.services.order_repository import OrderRepository
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.failure = failure
        for checkout in [service, CheckoutService(provider=provider, repository=OrderRepository(tmp_path / "orders.sqlite3"))]:
            with pytest.raises(CheckoutError) as caught:
                await checkout.create_order(request)
            assert caught.value.code == "E_ORDER_SUBMISSION_UNKNOWN"
            assert "order_id" not in (caught.value.data or {})
        assert len(provider.created) == 1
        with sqlite3.connect(tmp_path / "orders.sqlite3") as db:
            assert db.execute("SELECT state FROM orders").fetchone()[0] == "submission_unknown"
    asyncio.run(run())


def test_process_restart_leaves_submitting_unknown_without_repeat(tmp_path):
    from backend.services.checkout_service import CheckoutError, CheckoutService
    from backend.services.order_repository import OrderRepository
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.gate = asyncio.Event()
        task = asyncio.create_task(service.create_order(request))
        while not provider.created:
            await asyncio.sleep(0)
        with sqlite3.connect(tmp_path / "orders.sqlite3") as db:
            assert db.execute("SELECT state FROM orders").fetchone()[0] == "submitting"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        restarted = CheckoutService(provider=provider, repository=OrderRepository(tmp_path / "orders.sqlite3"))
        with pytest.raises(CheckoutError) as caught:
            await restarted.create_order(request)
        assert caught.value.code == "E_ORDER_SUBMISSION_UNKNOWN"
        assert len(provider.created) == 1
    asyncio.run(run())


def test_concurrent_duplicate_requests_create_only_once(tmp_path):
    from backend.services.checkout_service import CheckoutError
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.gate = asyncio.Event()
        first = asyncio.create_task(service.create_order(request))
        while not provider.created:
            await asyncio.sleep(0)
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(request)
        assert caught.value.code == "E_ORDER_SUBMISSION_UNKNOWN"
        provider.gate.set()
        assert (await first).order_id == "official-123"
        assert len(provider.created) == 1
    asyncio.run(run())


@pytest.mark.parametrize("url", ["http://m.mcd.cn/pay", "https://m.mcd.cn.evil.test/pay", "https://evil.test/pay", "https://user@m.mcd.cn/pay", "javascript:alert(1)"])
def test_untrusted_payment_url_never_returned_or_created_twice(tmp_path, url):
    from backend.services.checkout_service import CheckoutError
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.result["data"]["payH5Url"] = url
        for _ in range(2):
            with pytest.raises(CheckoutError) as caught:
                await service.create_order(request)
            assert caught.value.code == "E_ORDER_SUBMISSION_UNKNOWN"
        assert len(provider.created) == 1
    asyncio.run(run())


@pytest.mark.parametrize("bad", [True, "1321", 13.21, None, -1])
def test_invalid_quote_price_is_rejected_not_coerced(tmp_path, bad):
    from backend.services.checkout_service import CheckoutError
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        provider.price["data"]["price"] = bad
        with pytest.raises(CheckoutError) as caught:
            await quoted_request(service)
        assert caught.value.code == "E_QUOTE_FAILED"
    asyncio.run(run())


def test_quote_budget_and_invalid_takeway_block_creation(tmp_path):
    from backend.services.checkout_service import CheckoutError
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        with pytest.raises(CheckoutError) as caught:
            await service.quote(plan_fixture(), "store-1", Requirements(people=1, budget_fen=1000))
        assert caught.value.code == "E_QUOTE_OVER_BUDGET"
        _, request = await quoted_request(service)
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(request.model_copy(update={"take_way_code": "guessed"}))
        assert caught.value.code == "E_TAKE_WAY_INVALID"
        assert provider.created == []
    asyncio.run(run())


def test_provider_uses_official_tools_and_does_not_retry_create():
    from backend.providers.checkout_provider import CheckoutProvider
    class Client:
        def __init__(self):
            self.calls = []
        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return {"data": {"orderId": "123"}}
    async def run():
        client = Client()
        provider = CheckoutProvider(client=client)
        assert await provider.calculate_price({"storeCode": "s"}) == {"data": {"orderId": "123"}}
        await provider.create_order({"storeCode": "s"})
        await provider.query_order("123")
        assert client.calls == [("calculate-price", {"storeCode": "s"}), ("create-order", {"storeCode": "s"}), ("query-order", {"orderId": "123"})]
    asyncio.run(run())


def test_abrupt_process_exit_retains_submitting_then_restart_never_retries(tmp_path):
    from backend.services.checkout_service import CheckoutError, CheckoutService
    from backend.services.order_repository import OrderRepository
    class SimulatedProcessDeath(BaseException):
        pass
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.failure = SimulatedProcessDeath()
        with pytest.raises(SimulatedProcessDeath):
            await service.create_order(request)
        with sqlite3.connect(tmp_path / "orders.sqlite3") as db:
            assert db.execute("SELECT state FROM orders").fetchone()[0] == "submitting"
        restarted = CheckoutService(provider=provider, repository=OrderRepository(tmp_path / "orders.sqlite3"))
        with pytest.raises(CheckoutError) as caught:
            await restarted.create_order(request)
        assert caught.value.code == "E_ORDER_SUBMISSION_UNKNOWN"
        with sqlite3.connect(tmp_path / "orders.sqlite3") as db:
            assert db.execute("SELECT state FROM orders").fetchone()[0] == "submission_unknown"
        assert len(provider.created) == 1
    asyncio.run(run())


@pytest.mark.parametrize("upstream_status, expected", [("1", "pending_payment"), ("2", "preparing"), ("10", "preparing"),
                                                       ("4", "delivering"), ("6", "completed"), ("8", "completed"),
                                                       ("7", "cancelled"), ("99", "unknown")])
def test_query_status_requires_official_confirmation(tmp_path, upstream_status, expected):
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        await service.create_order(request)
        provider.query["data"]["orderStatus"] = upstream_status
        result = await service.get_order("official-123")
        assert result.status == expected
        assert bool(result.payment_url) == (expected == "pending_payment")
    asyncio.run(run())


def test_expired_quote_requires_new_confirmation(tmp_path, monkeypatch):
    import backend.services.checkout_service as checkout_module
    from backend.services.checkout_service import CheckoutError
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        now = checkout_module.time.time()
        monkeypatch.setattr(checkout_module.time, "time", lambda: now)
        quoted, request = await quoted_request(service)
        monkeypatch.setattr(checkout_module.time, "time", lambda: now + 301)
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(request)
        assert caught.value.code == "E_PRICE_CHANGED"
        assert caught.value.data["updated_plan"]["quote_id"] != quoted.quote_id
        assert provider.created == []
    asyncio.run(run())


def test_upstream_shorter_expiry_and_multiple_takeways_preserved(tmp_path):
    import time
    async def run():
        service, provider, repo = setup_checkout(tmp_path)
        provider.price["data"]["expiresAt"] = time.time() + 10
        provider.price["data"]["takeWayList"] = [{"code": "eat-in", "title": "堂食"}, {"code": "take-in-store", "title": "外带店内自提"}]
        quoted, _ = await quoted_request(service)
        assert [way["code"] for way in quoted.take_way_options] == ["eat-in", "take-in-store"]
        assert repo.get_quote(quoted.quote_id)["expires_at"] <= time.time() + 10
    asyncio.run(run())


def test_snapshot_survives_quote_service_restart(tmp_path):
    from backend.services.checkout_service import CheckoutService
    from backend.services.order_repository import OrderRepository
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        restarted = CheckoutService(provider=provider, repository=OrderRepository(tmp_path / "orders.sqlite3"))
        result = await restarted.create_order(request)
        assert result.order_id == "official-123"
        assert provider.created[0]["items"][0]["roundList"][0]["comboItemList"][0]["code"] == "burger"
    asyncio.run(run())


def test_new_order_rejects_cleared_plan_but_no_upstream_creation(tmp_path):
    from backend.services.checkout_service import CheckoutError
    from backend.services.plan_store import PlanStore
    async def run():
        plans = PlanStore(tmp_path / "plans.json")
        service, provider, _ = setup_checkout(tmp_path, plans=plans)
        quoted, request = await quoted_request(service)
        plans.save(quoted, "store-1", Requirements(people=1, budget_fen=5000))
        plans.invalidate_all()
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(request)
        assert caught.value.code == "E_PLAN_NOT_FOUND"
        assert provider.created == []
    asyncio.run(run())


def test_store_or_selected_options_change_requires_fresh_confirmation(tmp_path):
    from backend.services.checkout_service import CheckoutError
    from backend.services.plan_store import PlanStore
    async def run():
        plans = PlanStore(tmp_path / "plans.json")
        service, provider, _ = setup_checkout(tmp_path, plans=plans)
        quoted, request = await quoted_request(service)
        quoted.items[0].selected_options[0].option_code = "chicken"
        plans.save(quoted, "store-2", Requirements(people=1, budget_fen=5000))
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(request)
        assert caught.value.code == "E_PRICE_CHANGED"
        assert caught.value.data["updated_plan"]["items"][0]["selected_options"][0]["option_code"] == "chicken"
        assert provider.created == []
    asyncio.run(run())


def test_query_identity_mismatch_does_not_mark_order_paid(tmp_path):
    from backend.services.checkout_service import CheckoutError
    async def run():
        service, provider, repo = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        await service.create_order(request)
        provider.query["data"]["orderId"] = "not-this-order"
        with pytest.raises(CheckoutError) as caught:
            await service.get_order("official-123")
        assert caught.value.code == "E_ORDER_QUERY_FAILED"
        assert repo.get_by_key(request.idempotency_key)["result"]["status"] == "pending_payment"
    asyncio.run(run())


def test_result_persistence_failure_leaves_unknown_not_a_second_order(tmp_path, monkeypatch):
    from backend.services.checkout_service import CheckoutError, CheckoutService
    from backend.services.order_repository import OrderRepository
    async def run():
        service, provider, repo = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        def failed_save(*args):
            raise sqlite3.OperationalError("disk full")
        monkeypatch.setattr(repo, "save_result", failed_save)
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(request)
        assert caught.value.code == "E_ORDER_SUBMISSION_UNKNOWN"
        restarted = CheckoutService(provider=provider, repository=OrderRepository(tmp_path / "orders.sqlite3"))
        with pytest.raises(CheckoutError):
            await restarted.create_order(request)
        assert len(provider.created) == 1
    asyncio.run(run())


def test_unknown_marker_write_failure_still_returns_unknown_and_retains_claim(tmp_path, monkeypatch):
    from backend.services.checkout_service import CheckoutError, CheckoutService
    from backend.services.order_repository import OrderRepository
    async def run():
        service, provider, repo = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.failure = TimeoutError("timeout")
        def failed_unknown(*args):
            raise sqlite3.OperationalError("disk full")
        monkeypatch.setattr(repo, "mark_unknown", failed_unknown)
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(request)
        assert caught.value.code == "E_ORDER_SUBMISSION_UNKNOWN"
        restarted = CheckoutService(provider=provider, repository=OrderRepository(tmp_path / "orders.sqlite3"))
        with pytest.raises(CheckoutError) as caught:
            await restarted.create_order(request)
        assert caught.value.code == "E_ORDER_SUBMISSION_UNKNOWN"
        assert len(provider.created) == 1
    asyncio.run(run())


def test_aclose_releases_owned_provider_client():
    from backend.providers.checkout_provider import CheckoutProvider
    from backend.services.checkout_service import CheckoutService
    class Client:
        def __init__(self):
            self.closed = False
        async def aclose(self):
            self.closed = True
    async def run():
        client = Client()
        # No repository operation is needed for connection lifecycle testing.
        service = CheckoutService(provider=CheckoutProvider(client=client), repository=object())
        await service.aclose()
        assert client.closed
    asyncio.run(run())


def test_price_change_over_budget_returns_updated_quote_but_never_submits(tmp_path):
    from backend.services.checkout_service import CheckoutError
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.price["data"]["price"] = 6000
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(request)
        assert caught.value.code == "E_PRICE_CHANGED"
        updated = caught.value.data["updated_plan"]
        assert updated["total_fen"] == 6000
        assert updated["remaining_fen"] == -1000
        retry = request.model_copy(update={"quote_id": updated["quote_id"], "idempotency_key": "new-request-0002"})
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(retry)
        assert caught.value.code == "E_QUOTE_OVER_BUDGET"
        assert provider.created == []
    asyncio.run(run())


@pytest.mark.parametrize("updated_requirements", [
    {"people": 1, "budget_fen": 1000},
    {"people": 2, "budget_type": "per_person", "budget_fen": 500},
    {"people": 2, "budget_fen": 5000},
    {"people": 1, "budget_fen": 6000},
])
def test_same_basket_changed_requirements_requires_new_planning(tmp_path, updated_requirements):
    from backend.services.checkout_service import CheckoutError
    from backend.services.plan_store import PlanStore
    async def run():
        plans = PlanStore(tmp_path / "plans.json")
        service, provider, _ = setup_checkout(tmp_path, plans=plans)
        quoted, request = await quoted_request(service)
        # Only requirements change: retain identical basket, store and quote_id.
        plans.save(quoted, "store-1", Requirements(**updated_requirements))
        with pytest.raises(CheckoutError) as caught:
            await service.create_order(request)
        assert caught.value.code == "E_PLAN_NOT_FOUND"
        assert provider.created == []
    asyncio.run(run())


@pytest.mark.parametrize("unknown_status", [None, "99"])
@pytest.mark.parametrize("legacy_schema", [False, True])
def test_verified_payment_link_restores_after_unknown_query_and_restart(tmp_path, unknown_status, legacy_schema):
    from backend.services.checkout_service import CheckoutService
    from backend.services.order_repository import OrderRepository
    if legacy_schema:
        # Exercise an existing installation, not just newly created databases.
        with sqlite3.connect(tmp_path / "orders.sqlite3") as db:
            db.execute("""CREATE TABLE orders (
                idempotency_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                basket_hash TEXT NOT NULL, quote_id TEXT NOT NULL, state TEXT NOT NULL,
                record_json TEXT NOT NULL, order_id TEXT UNIQUE, result_json TEXT)""")
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        created = await service.create_order(request)
        original_url = "https://m.mcd.cn/pay?id=123"
        assert created.payment_url == original_url
        if legacy_schema:
            # A pre-fix populated row had the URL only in result_json. It must
            # survive the very first unknown query after upgrading as well.
            with sqlite3.connect(tmp_path / "orders.sqlite3") as db:
                raw = db.execute("SELECT record_json FROM orders").fetchone()[0]
                legacy_record = json.loads(raw)
                legacy_record.pop("verified_payment_url", None)
                db.execute("UPDATE orders SET record_json=?", (json.dumps(legacy_record),))
        provider.query["data"]["orderStatus"] = unknown_status
        unknown = await service.get_order("official-123")
        assert unknown.status == "unknown"
        assert unknown.payment_url is None
        assert (await service.create_order(request)).payment_url is None
        restarted = CheckoutService(provider=provider, repository=OrderRepository(tmp_path / "orders.sqlite3"))
        provider.query["data"]["orderStatus"] = "1"
        recovered = await restarted.get_order("official-123")
        assert recovered.status == "pending_payment"
        assert recovered.payment_url == original_url
        assert (await restarted.create_order(request)).payment_url == original_url
        assert len(provider.created) == 1
    asyncio.run(run())


def test_create_unknown_status_keeps_verified_link_internal_for_later_pending(tmp_path):
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.result["data"]["orderDetail"]["orderStatus"] = "99"
        created = await service.create_order(request)
        assert created.status == "unknown"
        assert created.payment_url is None
        provider.query["data"]["orderStatus"] = "1"
        recovered = await service.get_order("official-123")
        assert recovered.payment_url == "https://m.mcd.cn/pay?id=123"
        assert len(provider.created) == 1
    asyncio.run(run())


@pytest.mark.parametrize("upstream_status, expected", [
    ("待支付", "pending_payment"), ("已支付", "paid"),
    ("配餐中", "preparing"), ("订单配餐中", "preparing"),
    ("配送中", "delivering"), ("订单配送中", "delivering"),
    ("已完成", "completed"), ("订单已完成", "completed"),
    ("已取消", "cancelled"), ("订单已取消", "cancelled"),
])
def test_official_mcp_chinese_status_normalized_on_create_and_query(tmp_path, upstream_status, expected):
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.result["data"]["orderDetail"]["orderStatus"] = upstream_status
        created = await service.create_order(request)
        assert created.status == expected
        assert bool(created.payment_url) == (expected == "pending_payment")
        provider.query["data"]["orderStatus"] = upstream_status
        queried = await service.get_order(created.order_id)
        assert queried.status == expected
        assert queried.payment_url == created.payment_url
        assert len(provider.created) == 1
    asyncio.run(run())


@pytest.mark.parametrize("upstream_status", [None, "", "unknown-official-status"])
def test_missing_or_unknown_create_status_never_defaults_to_pending(tmp_path, upstream_status):
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        if upstream_status is None:
            provider.result["data"]["orderDetail"].pop("orderStatus")
        else:
            provider.result["data"]["orderDetail"]["orderStatus"] = upstream_status
        created = await service.create_order(request)
        assert created.status == "unknown"
        assert created.payment_url is None
        provider.query["data"]["orderStatus"] = "待支付"
        recovered = await service.get_order(created.order_id)
        assert recovered.status == "pending_payment"
        assert recovered.payment_url is not None
        assert len(provider.created) == 1
    asyncio.run(run())


@pytest.mark.parametrize("upstream_expiry, expected", [
    ("2026-10-25 13:30:48", "2026-10-25T13:30:48+08:00"),
    ("2026-10-25T13:30:48+08:00", "2026-10-25T13:30:48+08:00"),
    ("2026-10-25T05:30:48Z", "2026-10-25T05:30:48+00:00"),
])
def test_official_payment_expiry_has_explicit_timezone_on_create_and_query(tmp_path, upstream_expiry, expected):
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.result["data"]["orderDetail"]["expirePayTime"] = upstream_expiry
        created = await service.create_order(request)
        assert created.expires_at == expected
        provider.query["data"]["expirePayTime"] = upstream_expiry
        queried = await service.get_order(created.order_id)
        assert queried.expires_at == expected
    asyncio.run(run())


def test_existing_naive_expiry_normalized_even_when_query_omits_it(tmp_path):
    async def run():
        service, provider, repository = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        created = await service.create_order(request)
        legacy = created.model_dump()
        legacy["expires_at"] = "2026-10-25 13:30:48"
        repository.save_result(request.idempotency_key, legacy)
        queried = await service.get_order(created.order_id)
        assert queried.expires_at == "2026-10-25T13:30:48+08:00"
        assert len(provider.created) == 1
    asyncio.run(run())


@pytest.mark.parametrize("upstream_status, expected", [
    ("待支付", "pending_payment"), ("已支付", "paid"),
    ("配餐中", "preparing"), ("配餐中-已支付", "preparing"),
    ("配餐中-餐厅确认配餐中", "preparing"), ("配送中", "delivering"),
    ("已完成", "completed"), ("已评价", "completed"), ("已取消", "cancelled"),
    ("暂未识别的状态", "unknown"), (None, "unknown"),
])
def test_live_chinese_order_status_is_authoritative_on_create_and_query(tmp_path, upstream_status, expected):
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        # The live service uses status=10 for a pending order. It is not the
        # numeric orderStatus=10 (restaurant-confirmed preparation) contract.
        provider.result["data"]["orderDetail"].update(orderStatus=upstream_status, status="10")
        created = await service.create_order(request)
        assert created.status == expected
        assert created.payment_url == ("https://m.mcd.cn/pay?id=123" if expected == "pending_payment" else None)
        provider.query["data"].update(orderStatus=upstream_status, status="10")
        queried = await service.get_order("official-123")
        assert queried.status == expected
        assert queried.payment_url == created.payment_url
        assert len(provider.created) == 1
    asyncio.run(run())


def test_live_chinese_pending_query_restores_original_verified_link(tmp_path):
    async def run():
        service, provider, repo = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.result["data"]["orderDetail"]["orderStatus"] = "99"
        assert (await service.create_order(request)).payment_url is None
        provider.query["data"].update(orderStatus="待支付", status="10")
        restored = await service.get_order("official-123")
        assert restored.status == "pending_payment"
        assert restored.payment_url == "https://m.mcd.cn/pay?id=123"
        assert repo.get_by_key(request.idempotency_key)["record"]["verified_payment_url"] == restored.payment_url
        assert len(provider.created) == 1
    asyncio.run(run())


@pytest.mark.parametrize("raw, normalized", [
    ("2026-10-25 13:30:48", "2026-10-25T13:30:48+08:00"),
    ("2026-10-25T13:30:48", "2026-10-25T13:30:48+08:00"),
    ("2026-10-25T05:30:48Z", "2026-10-25T05:30:48+00:00"),
    ("2026-10-25T13:30:48+08:00", "2026-10-25T13:30:48+08:00"),
    ("invalid", "invalid"), ("", "invalid"),
])
def test_payment_expiry_is_timezone_explicit_or_stays_invalid(tmp_path, raw, normalized):
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.result["data"]["orderDetail"]["expirePayTime"] = raw
        created = await service.create_order(request)
        assert created.expires_at == normalized
        provider.query["data"]["expirePayTime"] = raw
        queried = await service.get_order("official-123")
        assert queried.expires_at == normalized
        assert len(provider.created) == 1
    asyncio.run(run())


def test_over_budget_quote_exposes_only_verified_amounts_and_basket(tmp_path):
    from backend.services.checkout_service import CheckoutError
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        provider.price["data"]["price"] = 6000
        with pytest.raises(CheckoutError) as caught:
            await service.quote(plan_fixture(), "store-1", Requirements(people=1, budget_fen=5000))
        assert caught.value.code == "E_QUOTE_OVER_BUDGET"
        assert caught.value.data == {
            "total_fen": 6000, "budget_fen": 5000, "over_by_fen": 1000,
            "basket": {"plan_id": "plan-one", **provider.calculated[-1]},
        }
        assert provider.created == []
    asyncio.run(run())


def test_null_query_expiry_does_not_erase_known_payment_deadline(tmp_path):
    async def run():
        service, provider, _ = setup_checkout(tmp_path)
        _, request = await quoted_request(service)
        provider.result["data"]["orderDetail"]["expirePayTime"] = "2026-10-25 13:30:48"
        await service.create_order(request)
        provider.query["data"].update(orderStatus="待支付", expirePayTime=None)
        result = await service.get_order("official-123")
        assert result.expires_at == "2026-10-25T13:30:48+08:00"
        assert len(provider.created) == 1
    asyncio.run(run())
