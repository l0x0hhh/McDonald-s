"""Known quota failures are actionable, without leaking upstream messages."""
import io
import json
import urllib.error
import pytest
from backend import llm
from backend.services.llm_planner import PlanningError


@pytest.fixture(autouse=True)
def explicit_api_provider(monkeypatch):
    monkeypatch.setattr(llm._configuration.settings, "llm_provider", "api")


@pytest.mark.parametrize("code,kind", [("credit_balance_exhausted", "insufficient_quota"),
                                       ("insufficient_quota", "insufficient_quota")])
def test_exhausted_model_credit_has_distinct_error(monkeypatch, code, kind):
    monkeypatch.setattr(llm._configuration.settings, "deepseek_api_key", "fixture-only")
    body = json.dumps({"error": {"code": code, "type": kind, "message": "private upstream text"}}).encode()
    def failed(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.deepseek.com/chat/completions", 429, "Too Many Requests", {}, io.BytesIO(body))
    monkeypatch.setattr(llm.urllib.request, "urlopen", failed)
    with pytest.raises(PlanningError) as caught:
        llm._responses_request({"model": "fixture"}, 30)
    assert caught.value.code == "E_LLM_QUOTA"
    assert "额度" in caught.value.message
    assert "private upstream text" not in caught.value.message


def test_temporary_rate_limit_is_not_reported_as_exhausted_credit(monkeypatch):
    monkeypatch.setattr(llm._configuration.settings, "deepseek_api_key", "fixture-only")
    def failed(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.deepseek.com/chat/completions", 429, "Rate Limit", {},
                                     io.BytesIO(b'{"error":{"code":"rate_limit_exceeded"}}'))
    monkeypatch.setattr(llm.urllib.request, "urlopen", failed)
    with pytest.raises(PlanningError) as caught:
        llm._responses_request({"model": "fixture"}, 30)
    assert caught.value.code == "E_LLM_REQUEST_FAILED"
