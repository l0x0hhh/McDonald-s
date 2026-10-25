"""Settings and the fixed DeepSeek boundary never expose credential values."""
import asyncio
import importlib
import io
import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from backend.config import Settings, settings
from backend.main import app
from backend import llm


@pytest.fixture
def local_settings(monkeypatch, tmp_path):
    module = importlib.import_module('backend.services.settings_service')
    store = module.LocalSettingsStore(tmp_path / 'settings.local.json')
    monkeypatch.setattr(module, 'local_store', store)
    monkeypatch.setattr(settings, 'deepseek_api_key', '')
    monkeypatch.setattr(settings, 'mcd_mcp_token', '')
    monkeypatch.setattr(settings, 'mcd_mcp_url', 'https://fixture.invalid/mcp')
    return store


def test_settings_route_has_fixed_model_and_no_key_values(local_settings):
    response = TestClient(app).get('/api/v1/settings')
    assert response.status_code == 200
    assert response.json()['data'] == {
        'provider': 'deepseek', 'model': 'deepseek-flash',
        'deepseek_configured': False, 'mcd_mcp_configured': False,
    }


def test_default_provider_ignores_old_subscription_and_openai(monkeypatch, tmp_path):
    monkeypatch.setenv('LLM_PROVIDER', 'codex_subscription')
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-openai-never-used')
    monkeypatch.setenv('LLM_API_KEY', 'fixture-generic-never-used')
    monkeypatch.setenv('LLM_MODEL', 'arbitrary-old-model')
    monkeypatch.setenv('LLM_BASE_URL', 'https://other.invalid')
    config = Settings(local_path=tmp_path / 'settings.local.json')
    assert config.llm_provider == 'deepseek'
    assert config.llm_model == 'deepseek-flash'
    assert config.llm_base_url == 'https://api.deepseek.com'
    assert not config.llm_configured


def test_save_persist_restart_and_empty_fields_preserve(local_settings, caplog):
    client = TestClient(app)
    secret_a, secret_b = 'fixture-deepseek-secret-unique', 'fixture-mcd-secret-unique'
    response = client.post('/api/v1/settings', json={'deepseek_api_key': secret_a, 'mcd_mcp_key': secret_b})
    assert response.status_code == 200
    assert response.json()['data'] == {'provider': 'deepseek', 'model': 'deepseek-flash',
                                       'deepseek_configured': True, 'mcd_mcp_configured': True}
    restarted = Settings(local_path=local_settings.path)
    assert restarted.deepseek_api_key == secret_a
    assert restarted.mcd_mcp_token == secret_b
    for body in ({}, {'deepseek_api_key': '', 'mcd_mcp_key': '   '}):
        assert client.post('/api/v1/settings', json=body).status_code == 200
    assert Settings(local_path=local_settings.path).deepseek_api_key == secret_a
    assert Settings(local_path=local_settings.path).mcd_mcp_token == secret_b
    assert not list(local_settings.path.parent.glob('*.tmp'))
    for text in (response.text, client.get('/api/v1/settings').text, client.get('/api/v1/config').text, caplog.text):
        assert secret_a not in text and secret_b not in text


@pytest.mark.parametrize('body', [
    {'model': 'bad'}, {'provider': 'bad'}, {'base_url': 'https://bad.invalid'},
    {'deepseek_api_key': 123}, {'mcd_mcp_key': ['fixture-secret-in-validation']},
    {'deepseek_api_key': 'fixture-secret-in-validation\ninvalid'},
])
def test_invalid_body_has_bounded_error_without_input(local_settings, body):
    response = TestClient(app).post('/api/v1/settings', json=body)
    assert response.status_code == 422
    assert 'fixture-secret-in-validation' not in response.text
    assert not local_settings.path.exists()


@pytest.mark.parametrize('headers', [
    {'Origin': 'https://evil.invalid'},
    {'Origin': 'http://192.168.1.44:5173', 'Sec-Fetch-Site': 'cross-site'},
    {'Origin': 'http://localhost:5173', 'Sec-Fetch-Site': 'same-site'},
])
def test_cross_origin_writes_rejected(local_settings, headers):
    response = TestClient(app).post('/api/v1/settings', json={'mcd_mcp_key': 'fixture'}, headers=headers)
    assert response.status_code == 403
    assert not local_settings.path.exists()


def test_same_origin_lan_proxy_and_cli_json_are_accepted(local_settings):
    client = TestClient(app)
    assert client.post('/api/v1/settings', json={}, headers={
        'Origin': 'http://192.168.1.44:5173', 'Sec-Fetch-Site': 'same-origin'}).status_code == 200
    assert client.post('/api/v1/settings', json={}).status_code == 200
    assert client.post('/api/v1/settings', content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415


def test_disk_failure_is_sanitized_and_does_not_change_live_keys(local_settings, monkeypatch):
    def failed(*args):
        raise OSError('fixture-private-disk-error')
    monkeypatch.setattr(local_settings, '_atomic_write', failed)
    response = TestClient(app).post('/api/v1/settings', json={'deepseek_api_key': 'fixture-private-save-key'})
    assert response.status_code == 500
    assert 'fixture-private' not in response.text
    assert settings.deepseek_api_key == ''


def test_deepseek_body_is_fixed_and_adapts_responses_schema(monkeypatch):
    monkeypatch.setattr(settings, 'deepseek_api_key', 'fixture-only-deepseek')
    monkeypatch.setattr(settings, 'llm_provider', 'codex_subscription')
    calls = []
    def response(request, timeout):
        calls.append((request, timeout))
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{"candidates":[]}'}, 'finish_reason': 'stop'}]}).encode())
    monkeypatch.setattr(llm.urllib.request, 'urlopen', response)
    payload = {'model': 'untrusted-model', 'instructions': 'fixed skill', 'input': '{"items":[]}',
               'text': {'format': {'schema': {'type': 'object', 'required': ['candidates']}}}}
    result = asyncio.run(llm.request_responses(payload, 17))
    assert result == '{"candidates":[]}'
    request, timeout = calls[0]
    assert request.full_url == 'https://api.deepseek.com/chat/completions'
    assert request.get_header('Authorization') == 'Bearer fixture-only-deepseek'
    body = json.loads(request.data)
    assert body['model'] == 'deepseek-flash'
    assert body['thinking'] == {'type': 'disabled'}
    assert body['response_format'] == {'type': 'json_object'}
    assert body['messages'][1] == {'role': 'user', 'content': '{"items":[]}'}
    assert 'JSON' in body['messages'][0]['content']
    assert 'candidates' in body['messages'][0]['content']
    assert timeout == 17


def test_deepseek_no_key_never_uses_openai_or_subscription(monkeypatch):
    monkeypatch.setattr(settings, 'deepseek_api_key', '')
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-not-used')
    monkeypatch.setenv('LLM_API_KEY', 'fixture-not-used')
    monkeypatch.setattr(llm.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('unexpected network'))
    with pytest.raises(Exception) as caught:
        asyncio.run(llm.request_responses({'instructions': 'JSON', 'input': '{}'}, 1))
    assert caught.value.code == 'E_LLM_NOT_CONFIGURED'


def test_upstream_error_does_not_echo_key_or_upstream_body(monkeypatch):
    monkeypatch.setattr(settings, 'deepseek_api_key', 'fixture-upstream-secret')
    def failed(*a, **k):
        raise urllib.error.HTTPError('https://fixture-upstream-secret.invalid', 401, 'fixture-upstream-secret', {},
                                     io.BytesIO(b'{"error":{"message":"fixture-upstream-secret"}}'))
    monkeypatch.setattr(llm.urllib.request, 'urlopen', failed)
    with pytest.raises(Exception) as caught:
        asyncio.run(llm.request_responses({'instructions': 'JSON', 'input': '{}'}, 1))
    assert caught.value.code == 'E_LLM_REQUEST_FAILED'
    assert 'fixture-upstream-secret' not in str(caught.value)


def test_hot_save_closes_clients_clears_caches_invalidates_quotes(local_settings, monkeypatch, tmp_path):
    from backend.services import combo_service, menu_service, plan_service
    from backend.services.nutrition_service import nutrition_service
    from backend.services.checkout_service import CheckoutService
    from backend.services.order_repository import OrderRepository
    from backend.services.plan_store import PlanStore
    closed = []
    class Client:
        async def aclose(self): closed.append(True)
    from backend.providers.mcp_provider import McpMenuProvider
    from backend.providers.checkout_provider import CheckoutProvider
    menu = McpMenuProvider(); menu._client = Client()
    combo = McpMenuProvider(); combo._client = Client()
    checkout_provider = CheckoutProvider(); checkout_provider._menu._client = Client()
    monkeypatch.setattr(menu_service, '_mcp', menu)
    monkeypatch.setattr(combo_service, '_mcp', combo)
    monkeypatch.setattr(menu_service, '_menu_cache', {'old': object()})
    monkeypatch.setattr(menu_service, '_stores_cache', {'old': object()})
    monkeypatch.setattr(combo_service, '_cache', {'old': object()})
    monkeypatch.setattr(nutrition_service, '_cache', {'old': object()})
    store = PlanStore(tmp_path / 'plans.json'); store._records['old'] = object()
    monkeypatch.setattr(plan_service, 'store', store)
    repository = OrderRepository(tmp_path / 'orders.sqlite3')
    repository.save_quote({'quote_id': 'old', 'basket_hash': 'old-basket'})
    repository.claim_submission('existing-order-barrier', 'fingerprint', {'quote_id': 'old', 'basket_hash': 'old-basket'})
    checkout = CheckoutService(plans=store, provider=checkout_provider, repository=repository)
    monkeypatch.setattr(plan_service, '_checkout', checkout)
    response = TestClient(app).post('/api/v1/settings', json={'mcd_mcp_key': 'fixture-new-mcd'})
    assert response.status_code == 200
    assert len(closed) == 3
    assert not menu_service._menu_cache and not menu_service._stores_cache
    assert not combo_service._cache and not nutrition_service._cache
    assert not store._records and repository.get_quote('old') is None
    assert repository.get_by_key('existing-order-barrier')['state'] == 'submitting'
    assert menu_service._mcp.client._token == 'fixture-new-mcd'
    asyncio.run(menu_service._mcp.aclose())


def test_hot_save_waits_for_existing_api_request(local_settings, monkeypatch):
    import httpx
    from backend.services import menu_service
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def stores(*args):
            started.set()
            await release.wait()
            assert settings.mcd_mcp_token == ''
            return SimpleNamespace(model_dump=lambda: {'stores': []})
        monkeypatch.setattr(menu_service, 'get_stores', stores)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            old = asyncio.create_task(client.get('/api/v1/stores'))
            await started.wait()
            saving = asyncio.create_task(client.post('/api/v1/settings', json={'mcd_mcp_key': 'fixture-after-old-request'}))
            await asyncio.sleep(0.03)
            assert not saving.done()
            release.set()
            assert (await old).status_code == 200
            assert (await saving).status_code == 200
            assert settings.mcd_mcp_token == 'fixture-after-old-request'
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['http', 'rpc', 'tool', 'parse', 'network', 'timeout'])
def test_mcp_errors_never_expose_upstream_credential_values(failure):
    import httpx
    from backend.providers.mcp_provider import McpHttpClient, McpError
    secret = 'fixture-mcd-credential-never-echo'
    async def scenario():
        client = McpHttpClient('https://fixture.invalid', secret)
        await client._client.aclose()
        class Transport:
            async def post(self, *args, **kwargs):
                if failure == 'network': raise httpx.ConnectError(secret)
                if failure == 'timeout': raise httpx.ReadTimeout(secret)
                body = {'error': {'message': secret}} if failure == 'rpc' else {'result': {'isError': True, 'content': [{'type': 'text', 'text': secret}]}}
                return httpx.Response(401 if failure == 'http' else 200,
                                      text=secret if failure in ('http', 'parse') else json.dumps(body),
                                      request=httpx.Request('POST', 'https://fixture.invalid'))
        client._client = Transport()
        client._initialized = True
        with pytest.raises(McpError) as caught:
            await client.call_tool('query-meals', {})
        assert secret not in str(caught.value)
    asyncio.run(scenario())


def test_atomic_replacement_failure_keeps_old_file_and_removes_temp(local_settings, monkeypatch):
    module = importlib.import_module('backend.services.settings_service')
    local_settings.save({'deepseek_api_key': 'fixture-original-key'})
    original = local_settings.path.read_bytes()
    def broken_replace(*args):
        raise OSError('fixture-private-replace-error')
    monkeypatch.setattr(module.os, 'replace', broken_replace)
    response = TestClient(app).post('/api/v1/settings', json={'deepseek_api_key': 'fixture-replacement-key'})
    assert response.status_code == 500
    assert 'fixture-' not in response.text
    assert local_settings.path.read_bytes() == original
    assert not list(local_settings.path.parent.glob('.settings-*.tmp'))


def test_environment_deepseek_key_and_fixed_mcp_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fixture-env-deepseek')
    monkeypatch.setenv('MCD_MCP_URL', '')
    config = Settings(local_path=tmp_path / 'settings.local.json')
    assert config.deepseek_api_key == 'fixture-env-deepseek'
    assert config.llm_configured
    assert config.mcd_mcp_url == 'https://mcp.mcd.cn'
