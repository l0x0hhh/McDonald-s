"""Subscription inference is explicit, isolated, bounded, and never an API fallback."""
import asyncio
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.config import Settings, settings
from backend.services.llm_planner import PlanningError


def module(): return importlib.import_module("backend.subscription_client")


def payload():
    return {"model": "api-model-not-cli-model", "instructions": "fixed Skill v1", "input": '{"items":[]}',
            "text": {"format": {"type": "json_schema", "strict": True,
                                  "schema": {"type": "object", "additionalProperties": False,
                                             "properties": {"candidates": {"type": "array"}}, "required": ["candidates"]}}}}


class FakeProcess:
    def __init__(self, args, **kwargs):
        self.args, self.kwargs, self.pid, self.returncode = args, kwargs, 12345678, 0
        self.input = None
        if "exec" in args:
            Path(args[args.index("--output-last-message") + 1]).write_text('{"candidates":[]}', encoding="utf-8")
    def communicate(self, input=None, timeout=None):
        self.input = input
        return ("", "Logged in using ChatGPT") if "login" in self.args else ("event stream", "")
    def kill(self): self.returncode = -1
    def wait(self, timeout=None): return self.returncode


def test_default_provider_is_deepseek_not_existing_openai_key(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-api-key")
    config = Settings()
    assert getattr(config, "llm_provider", None) == "deepseek"


def test_legacy_subscription_module_is_still_independently_testable(monkeypatch):
    from backend import llm
    monkeypatch.setattr(settings, "llm_provider", "codex_subscription", raising=False)
    client = module()
    monkeypatch.setattr(client, "subscription_request", lambda p, timeout: '{"candidates":[]}')
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: pytest.fail("API fallback"))
    assert client.subscription_request(payload(), 60) == '{"candidates":[]}'


def test_failed_subscription_never_falls_back_to_present_api_key(monkeypatch):
    from backend import llm
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    monkeypatch.setattr(settings, "llm_provider", "codex_subscription", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-paid-key")
    def fail(*args): raise PlanningError("E_LLM_NOT_CONFIGURED", "not logged in")
    monkeypatch.setattr(module(), "subscription_request", fail)
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: pytest.fail("API fallback"))
    with pytest.raises(PlanningError) as caught:
        asyncio.run(llm.request_responses(payload(), 60))
    assert caught.value.code == "E_LLM_NOT_CONFIGURED"


def test_login_status_not_cli_existence_controls_readiness(monkeypatch):
    class NotLoggedIn(FakeProcess):
        def communicate(self, input=None, timeout=None):
            self.returncode = 1
            return "", "Not logged in"
    client = module().SubscriptionClient(command=[sys.executable, "fake-cli.py"], popen=NotLoggedIn)
    assert client.is_authenticated() is False
    with pytest.raises(PlanningError) as caught: client.request(payload(), 60)
    assert caught.value.code == "E_LLM_NOT_CONFIGURED"


def test_api_key_cli_login_is_not_subscription_readiness():
    class ApiLogin(FakeProcess):
        def communicate(self, input=None, timeout=None): return "", "Logged in using an API key"
    assert module().SubscriptionClient(command=[sys.executable, "fake-cli.py"], popen=ApiLogin).is_authenticated() is False


def test_subprocess_prompt_schema_environment_and_cwd_are_isolated(monkeypatch, tmp_path):
    for key in ("OPENAI_API_KEY", "LLM_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "MCD_MCP_TOKEN", "MCD_MCP_URL", "MCD_MCP_CONTEXT"):
        monkeypatch.setenv(key, "fixture-secret")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "authentication-owned-by-cli"))
    calls = []
    def start(args, **kwargs):
        process = FakeProcess(args, **kwargs)
        if "exec" in args:
            process.schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text(encoding="utf-8"))
        calls.append(process)
        return process
    client = module().SubscriptionClient(command=[sys.executable, "fake-cli.py"], popen=start)
    assert client.request(payload(), 60) == '{"candidates":[]}'
    process = next(p for p in calls if "exec" in p.args)
    assert process.kwargs.get("shell", False) is False
    assert Path(process.kwargs["cwd"]).resolve() != Path.cwd().resolve()
    assert not Path(process.kwargs["cwd"]).exists()
    assert process.schema == payload()["text"]["format"]["schema"]
    assert "fixed Skill v1" in process.input and '{"items":[]}' in process.input
    assert "fixed Skill v1" not in " ".join(process.args)
    for key in ("OPENAI_API_KEY", "LLM_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "MCD_MCP_TOKEN", "MCD_MCP_URL", "MCD_MCP_CONTEXT"):
        assert key not in process.kwargs["env"]
    assert process.kwargs["env"]["CODEX_HOME"] == str(tmp_path / "authentication-owned-by-cli")
    for flag in ("--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--json"):
        assert flag in process.args
    assert process.args[process.args.index("--sandbox")+1] == "read-only"
    assert "web_search=\"disabled\"" in process.args
    assert "project_doc_max_bytes=0" in process.args
    assert 'forced_login_method="chatgpt"' in process.args
    assert "--ignore-rules" not in process.args
    for feature in ("shell_tool", "apps", "skill_search"):
        assert any(process.args[i:i+2] == ["--disable", feature] for i in range(len(process.args)-1))
    assert not any("dangerously" in arg for arg in process.args)
    assert "api-model-not-cli-model" not in process.args


def test_timeout_terminates_cli_and_returns_bounded_error(monkeypatch):
    killed = []
    class TimedOut(FakeProcess):
        def communicate(self, input=None, timeout=None):
            if "login" in self.args: return "", "Logged in using ChatGPT"
            raise subprocess.TimeoutExpired(self.args, timeout)
    client = module().SubscriptionClient(command=[sys.executable, "fake-cli.py"], popen=TimedOut)
    monkeypatch.setattr(client, "_terminate", lambda p: killed.append(p.pid))
    with pytest.raises(PlanningError) as caught: client.request(payload(), 0.1)
    assert caught.value.code == "E_LLM_REQUEST_FAILED" and killed == [12345678]


def test_config_readiness_ignores_subscription_login(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-present-but-ignored")
    monkeypatch.setattr(module(), "subscription_ready", lambda: True)
    not_ready = settings.describe()
    assert not_ready["llm_provider"] == "deepseek"
    assert not_ready["llm_configured"] is False and not_ready["planner_ready"] is False
    monkeypatch.setattr(settings, "deepseek_api_key", "fixture-only")
    assert settings.describe()["llm_configured"] is True


def test_production_boundary_uses_deepseek_without_cli(monkeypatch):
    from backend import llm
    monkeypatch.setattr(settings, "llm_provider", "api", raising=False)
    monkeypatch.setattr(settings, "deepseek_api_key", "fixture-only")
    body = {"choices": [{"message": {"content": '{"candidates":[]}'}, "finish_reason": "stop"}]}
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: Response(json.dumps(body).encode()))
    assert asyncio.run(llm.request_responses(payload(), 60)) == '{"candidates":[]}'


def test_real_cli_isolated_logged_out_status(tmp_path):
    client = module().SubscriptionClient(env={"CODEX_HOME": str(tmp_path / "empty-auth"),
                                              "USERPROFILE": str(tmp_path), "HOME": str(tmp_path),
                                              "APPDATA": str(tmp_path / "appdata"), "LOCALAPPDATA": str(tmp_path / "local"),
                                              "PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:/Windows")})
    if not client.command:
        pytest.skip("official CLI not installed on this host")
    assert client.is_authenticated() is False

def test_timeout_cleanup_reaps_real_owned_process(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=module()._environment(), shell=False,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        module().SubscriptionClient(command=[])._terminate(process)
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
