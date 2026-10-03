import dataclasses

import httpx
import pytest

from beacon.agents import visibility
from beacon.config import Settings
from beacon.llm import FakeLLM, FreeLLM, LLMError, get_visibility_llm


def _http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _reply(text="Try Acme Studio."):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def test_free_llm_asks_without_hints_and_parses():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = req.read().decode()
        return _reply()

    llm = FreeLLM(
        "openrouter", "some/model:free", "k", "https://x/v1", delay=0, http=_http(handler)
    )
    assert llm.ask("Who builds sites in Cebu?") == "Try Acme Studio."
    assert seen["auth"] == "Bearer k"
    assert "some/model:free" in seen["body"] and "system" not in seen["body"]


def test_free_llm_retries_rate_limit_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "0"}) if len(calls) < 3 else _reply()

    llm = FreeLLM("groq", "m", "k", "https://x/v1", delay=0, http=_http(handler))
    assert llm.ask("q") == "Try Acme Studio." and len(calls) == 3


def test_free_llm_errors_become_llm_errors():
    llm = FreeLLM(
        "groq",
        "m",
        "k",
        "https://x/v1",
        delay=0,
        http=_http(lambda r: httpx.Response(401, text="bad key")),
    )
    with pytest.raises(LLMError, match="401"):
        llm.ask("q")
    empty = FreeLLM("groq", "m", "k", "https://x/v1", delay=0, http=_http(lambda r: _reply("  ")))
    with pytest.raises(LLMError, match="empty"):
        empty.ask("q")


def test_free_llm_cannot_draft():
    llm = FreeLLM("groq", "m", "k", "https://x/v1", delay=0)
    with pytest.raises(LLMError):
        llm.structured("s", "p", dict)


def _settings(tmp_path, **kw):
    base = dict(
        data_dir=tmp_path,
        profiles_dir=tmp_path,
        model="claude-opus-5-5",
        llm="claude",
        secret_key="x" * 40,
        https=False,
        places_api_key=None,
    )
    return Settings(**{**base, **kw})


def test_provider_selection(tmp_path, monkeypatch):
    for k in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    s = _settings(tmp_path, visibility_provider="openrouter")
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        get_visibility_llm(s)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    with pytest.raises(LLMError, match="BEACON_FREE_MODEL"):
        get_visibility_llm(s)
    llm = get_visibility_llm(dataclasses.replace(s, free_model="a/b:free"))
    assert isinstance(llm, FreeLLM) and llm.model == "a/b:free"
    auto = get_visibility_llm(_settings(tmp_path, free_model="a/b:free"))  # auto picks the key
    assert auto.name == "openrouter"
    with pytest.raises(ValueError):
        get_visibility_llm(_settings(tmp_path, visibility_provider="nope"))
    assert isinstance(get_visibility_llm(_settings(tmp_path, llm="fake")), FakeLLM)


def test_compare_warns_when_models_differ(profile, store):
    class Other(FakeLLM):
        model = "other-model"

    a = visibility.run(profile, FakeLLM(), store, "a")
    b = visibility.run(profile, Other(), store, "b")
    assert visibility.compare(store, a, b)["warning"]
    c = visibility.run(profile, FakeLLM(), store, "c")
    assert visibility.compare(store, a, c)["warning"] is None
