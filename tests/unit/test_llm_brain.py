"""
News Radar · Unit tests for src/llm_brain.py (Phase 8.19)

覆蓋重點：
- JSON extraction: 各種 claude 可能回的 wrapper 格式都抽得到
- Envelope parsing: claude -p --output-format json 的 wrapper 不會把內容吞掉
- Decision tree:
    (a) Gemini 成功 → 不碰 claude
    (b) Gemini 失敗 → 試 claude → 成功
    (c) 都失敗 → data=None, provider="none"
    (d) 無 GEMINI_API_KEY → 直接試 claude
    (e) 無 claude CLI → 直接 none
- Pydantic validation 失敗時回 None，不 crash

不走網路、不呼叫真實 claude CLI。所有外部依賴都 mock。
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

import src.llm_brain as llm_brain
from src.llm_brain import (
    LLMResult,
    _extract_json_blob,
    _parse_claude_envelope,
    call_for_json,
)


# ----------------------------------------------------------------------
# 測試用 schema
# ----------------------------------------------------------------------

class _Demo(BaseModel):
    name: str
    score: float


# ----------------------------------------------------------------------
# _extract_json_blob: 各種 claude 輸出樣式
# ----------------------------------------------------------------------

def test_extract_json_bare_object():
    s = '{"name": "x", "score": 0.5}'
    out = _extract_json_blob(s)
    assert out is not None
    assert json.loads(out)["score"] == 0.5


def test_extract_json_with_whitespace():
    s = '\n\n  {"name": "x", "score": 0.5}  \n'
    out = _extract_json_blob(s)
    assert out is not None
    assert json.loads(out)["name"] == "x"


def test_extract_json_markdown_fence_with_lang():
    s = "Sure, here is the JSON:\n```json\n{\"name\": \"x\", \"score\": 1.0}\n```\nHope that helps!"
    out = _extract_json_blob(s)
    assert out is not None
    assert json.loads(out)["score"] == 1.0


def test_extract_json_markdown_fence_bare():
    s = "Output:\n```\n{\"name\": \"y\", \"score\": 0.2}\n```"
    out = _extract_json_blob(s)
    assert out is not None
    assert json.loads(out)["name"] == "y"


def test_extract_json_preamble_and_postamble():
    """claude 有時會閒聊：『Sure, here is...』 {json} 『Let me know...』"""
    s = "Sure thing! {\"name\": \"z\", \"score\": 0.9} Let me know if you need anything else."
    out = _extract_json_blob(s)
    assert out is not None
    assert json.loads(out)["score"] == 0.9


def test_extract_json_array():
    s = '[{"a": 1}, {"a": 2}]'
    out = _extract_json_blob(s)
    assert out is not None
    assert json.loads(out) == [{"a": 1}, {"a": 2}]


def test_extract_json_no_json_returns_none():
    s = "I cannot help with that request."
    out = _extract_json_blob(s)
    assert out is None


def test_extract_json_empty_returns_none():
    assert _extract_json_blob("") is None
    assert _extract_json_blob("   ") is None


def test_extract_json_broken_braces_returns_none():
    s = "{ not really json at all }"
    out = _extract_json_blob(s)
    assert out is None


# ----------------------------------------------------------------------
# _parse_claude_envelope
# ----------------------------------------------------------------------

def test_parse_envelope_typical():
    envelope_str = json.dumps({
        "type": "result",
        "subtype": "success",
        "result": "the actual text response",
        "session_id": "abc",
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })
    text, env = _parse_claude_envelope(envelope_str)
    assert text == "the actual text response"
    assert env["total_cost_usd"] == 0.01
    assert env["usage"]["input_tokens"] == 100


def test_parse_envelope_fallback_when_not_json():
    """若 claude 沒回 --output-format json（版本差異），整段當 text"""
    s = "raw text with {\"name\": \"x\", \"score\": 1} embedded"
    text, env = _parse_claude_envelope(s)
    assert text == s
    assert env == {}


def test_parse_envelope_missing_result_field():
    """envelope 是 dict 但沒 result → result_text 是空字串（不 crash）"""
    s = json.dumps({"type": "result", "error": "quota exceeded"})
    text, env = _parse_claude_envelope(s)
    assert text == ""
    assert env["error"] == "quota exceeded"


# ----------------------------------------------------------------------
# Decision tree: Gemini 成功
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_for_json_gemini_success(monkeypatch):
    """Gemini 成功時，不試 claude。"""
    expected = _Demo(name="g", score=0.8)

    async def fake_gemini(**kwargs):
        return LLMResult(
            data=expected, provider="gemini", model="gemini-test",
            input_tokens=50, output_tokens=20,
        )

    async def fake_claude(**kwargs):
        raise AssertionError("claude should not be called when gemini succeeds")

    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setattr("src.llm_brain._try_gemini", fake_gemini)
    monkeypatch.setattr("src.llm_brain._try_claude_cli", fake_claude)

    r = await call_for_json(system="sys", prompt="p", response_model=_Demo, backends=("gemini", "claude_cli"))
    assert r.provider == "gemini"
    assert r.data == expected
    assert r.input_tokens == 50


# ----------------------------------------------------------------------
# Decision tree: Gemini 失敗 → claude 成功
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_for_json_gemini_fails_claude_succeeds(monkeypatch):
    expected = _Demo(name="c", score=0.1)

    async def fake_gemini(**kwargs):
        return LLMResult(data=None, provider="gemini", raw_error="429 quota")

    async def fake_claude(**kwargs):
        return LLMResult(
            data=expected, provider="claude_cli", model="claude",
            input_tokens=200, output_tokens=80, cost_usd=0.012,
        )

    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setattr("src.llm_brain._try_gemini", fake_gemini)
    monkeypatch.setattr("src.llm_brain._try_claude_cli", fake_claude)
    monkeypatch.setattr("src.llm_brain._claude_cli_available", lambda: True)

    r = await call_for_json(system="sys", prompt="p", response_model=_Demo)
    assert r.provider == "claude_cli"
    assert r.data == expected
    assert r.cost_usd == 0.012


# ----------------------------------------------------------------------
# Decision tree: 兩邊都失敗 → data=None
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_for_json_both_fail(monkeypatch):
    async def fake_gemini(**kwargs):
        return LLMResult(data=None, provider="gemini", raw_error="timeout")

    async def fake_claude(**kwargs):
        return LLMResult(data=None, provider="claude_cli", raw_error="no JSON")

    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setattr("src.llm_brain._try_gemini", fake_gemini)
    monkeypatch.setattr("src.llm_brain._try_claude_cli", fake_claude)
    monkeypatch.setattr("src.llm_brain._claude_cli_available", lambda: True)

    r = await call_for_json(system="sys", prompt="p", response_model=_Demo)
    assert r.data is None
    assert r.provider == "none"


# ----------------------------------------------------------------------
# Decision tree: 無 Gemini key → 直接試 claude
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_for_json_no_gemini_key_goes_to_claude(monkeypatch):
    expected = _Demo(name="c", score=0.3)

    async def fake_gemini(**kwargs):
        raise AssertionError("gemini should be skipped when no API key")

    async def fake_claude(**kwargs):
        return LLMResult(data=expected, provider="claude_cli", model="claude")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("src.llm_brain._try_gemini", fake_gemini)
    monkeypatch.setattr("src.llm_brain._try_claude_cli", fake_claude)
    monkeypatch.setattr("src.llm_brain._claude_cli_available", lambda: True)

    r = await call_for_json(system="sys", prompt="p", response_model=_Demo)
    assert r.provider == "claude_cli"
    assert r.data == expected


# ----------------------------------------------------------------------
# Decision tree: 無 claude CLI 且 gemini 失敗 → none
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_for_json_gemini_fails_no_claude_cli(monkeypatch):
    async def fake_gemini(**kwargs):
        return LLMResult(data=None, provider="gemini", raw_error="500")

    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setattr("src.llm_brain._try_gemini", fake_gemini)
    monkeypatch.setattr("src.llm_brain._claude_cli_available", lambda: False)

    r = await call_for_json(system="sys", prompt="p", response_model=_Demo)
    assert r.data is None
    assert r.provider == "none"


@pytest.mark.asyncio
async def test_default_quota_circuit_skips_shared_gemini_wrappers(monkeypatch):
    expected = _Demo(name="fallback", score=0.9)
    calls = {"litellm": 0, "gemini": 0, "opencode": 0}

    async def fake_litellm(**kwargs):
        calls["litellm"] += 1
        return LLMResult(
            data=None,
            provider="litellm",
            raw_error="429 RESOURCE_EXHAUSTED quota",
        )

    async def fake_gemini(**kwargs):
        calls["gemini"] += 1
        raise AssertionError("direct Gemini shares the exhausted LiteLLM quota")

    async def fake_openai_compatible(**kwargs):
        calls["opencode"] += 1
        return LLMResult(
            data=expected,
            provider="opencode",
            model="big-pickle",
        )

    monkeypatch.setattr(llm_brain, "_QUOTA_EXHAUSTED_BACKENDS", set())
    monkeypatch.setattr(llm_brain, "_claude_cli_available", lambda: False)
    monkeypatch.setattr(llm_brain, "_litellm_available", lambda: True)
    monkeypatch.setattr(llm_brain, "_try_litellm", fake_litellm)
    monkeypatch.setattr(llm_brain, "_try_gemini", fake_gemini)
    monkeypatch.setattr(
        llm_brain,
        "_try_openai_compatible",
        fake_openai_compatible,
    )
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini")
    monkeypatch.setenv("OPENCODE_API_KEY", "fake-opencode")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    first = await call_for_json(system="sys", prompt="p", response_model=_Demo)
    second = await call_for_json(system="sys", prompt="p2", response_model=_Demo)

    assert first.data == second.data == expected
    assert calls == {"litellm": 1, "gemini": 0, "opencode": 2}
    assert llm_brain._QUOTA_EXHAUSTED_BACKENDS == {"litellm", "gemini"}


@pytest.mark.asyncio
async def test_default_cloud_falls_back_to_github_models(monkeypatch):
    expected = _Demo(name="github", score=0.95)
    providers = []

    async def fake_litellm(**kwargs):
        return LLMResult(
            data=None,
            provider="litellm",
            raw_error="429 RESOURCE_EXHAUSTED quota",
        )

    async def fake_gemini(**kwargs):
        raise AssertionError("direct Gemini shares the exhausted LiteLLM quota")

    async def fake_openai_compatible(*, provider, **kwargs):
        providers.append(provider.name)
        return LLMResult(
            data=expected,
            provider=provider.name,
            model=provider.model_default,
        )

    monkeypatch.setattr(llm_brain, "_QUOTA_EXHAUSTED_BACKENDS", set())
    monkeypatch.setattr(llm_brain, "_claude_cli_available", lambda: False)
    monkeypatch.setattr(llm_brain, "_litellm_available", lambda: True)
    monkeypatch.setattr(llm_brain, "_try_litellm", fake_litellm)
    monkeypatch.setattr(llm_brain, "_try_gemini", fake_gemini)
    monkeypatch.setattr(
        llm_brain,
        "_try_openai_compatible",
        fake_openai_compatible,
    )
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini")
    monkeypatch.setenv("GITHUB_TOKEN", "short-lived-actions-token")
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)

    result = await call_for_json(system="sys", prompt="p", response_model=_Demo)

    assert result.data == expected
    assert result.provider == "github_models"
    assert result.model == "openai/gpt-4.1-mini"
    assert providers == ["github_models"]


@pytest.mark.asyncio
async def test_github_models_4o_uses_separate_quota_after_4_1_rate_limit(monkeypatch):
    expected = _Demo(name="github-4o", score=0.91)
    providers = []

    async def fake_litellm(**kwargs):
        return LLMResult(
            data=None,
            provider="litellm",
            raw_error="429 RESOURCE_EXHAUSTED quota",
        )

    async def fake_gemini(**kwargs):
        raise AssertionError("direct Gemini shares the exhausted LiteLLM quota")

    async def fake_openai_compatible(*, provider, **kwargs):
        providers.append(provider.name)
        if provider.name == "github_models":
            return LLMResult(
                data=None,
                provider=provider.name,
                model=provider.model_default,
                raw_error="http 429: model quota exhausted",
            )
        assert provider.name == "github_models_4o"
        return LLMResult(
            data=expected,
            provider=provider.name,
            model=provider.model_default,
        )

    monkeypatch.setattr(llm_brain, "_QUOTA_EXHAUSTED_BACKENDS", set())
    monkeypatch.setattr(llm_brain, "_claude_cli_available", lambda: False)
    monkeypatch.setattr(llm_brain, "_litellm_available", lambda: True)
    monkeypatch.setattr(llm_brain, "_try_litellm", fake_litellm)
    monkeypatch.setattr(llm_brain, "_try_gemini", fake_gemini)
    monkeypatch.setattr(
        llm_brain,
        "_try_openai_compatible",
        fake_openai_compatible,
    )
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini")
    monkeypatch.setenv("GITHUB_TOKEN", "short-lived-actions-token")
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)

    result = await call_for_json(system="sys", prompt="p", response_model=_Demo)

    assert result.data == expected
    assert result.provider == "github_models_4o"
    assert result.model == "openai/gpt-4o-mini"
    assert providers == ["github_models", "github_models_4o"]
    assert "github_models" in llm_brain._QUOTA_EXHAUSTED_BACKENDS


# ----------------------------------------------------------------------
# Claude CLI: subprocess-level test（mock asyncio.create_subprocess_exec）
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_try_claude_cli_success_envelope(monkeypatch):
    """claude 回典型 envelope，result 欄位帶 JSON → validate 成 Pydantic。"""
    from src.llm_brain import _try_claude_cli

    envelope = {
        "type": "result",
        "subtype": "success",
        "result": '{"name": "ok", "score": 0.77}',
        "total_cost_usd": 0.005,
        "usage": {"input_tokens": 150, "output_tokens": 40},
    }

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(
        return_value=(json.dumps(envelope).encode("utf-8"), b"")
    )

    async def fake_spawn(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr("src.llm_brain._claude_cli_available", lambda: True)
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_spawn)

    r = await _try_claude_cli(
        system="sys", prompt="p", response_model=_Demo, timeout_s=10,
    )
    assert r.data is not None
    assert r.data.name == "ok"
    assert r.data.score == pytest.approx(0.77)
    assert r.input_tokens == 150
    assert r.cost_usd == 0.005


@pytest.mark.asyncio
async def test_try_claude_cli_fenced_json(monkeypatch):
    """claude 在 markdown code fence 裡包 JSON 的情況。"""
    from src.llm_brain import _try_claude_cli

    envelope = {
        "type": "result",
        "result": "Sure, here is the JSON:\n```json\n{\"name\": \"fenced\", \"score\": 0.42}\n```",
        "total_cost_usd": 0.003,
        "usage": {"input_tokens": 100, "output_tokens": 30},
    }

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(
        return_value=(json.dumps(envelope).encode("utf-8"), b"")
    )

    async def fake_spawn(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr("src.llm_brain._claude_cli_available", lambda: True)
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_spawn)

    r = await _try_claude_cli(
        system="sys", prompt="p", response_model=_Demo, timeout_s=10,
    )
    assert r.data is not None
    assert r.data.name == "fenced"


@pytest.mark.asyncio
async def test_try_claude_cli_nonzero_exit(monkeypatch):
    from src.llm_brain import _try_claude_cli

    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.communicate = AsyncMock(return_value=(b"", b"some error"))

    async def fake_spawn(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr("src.llm_brain._claude_cli_available", lambda: True)
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_spawn)

    r = await _try_claude_cli(
        system="sys", prompt="p", response_model=_Demo, timeout_s=10,
    )
    assert r.data is None
    assert "exit=1" in (r.raw_error or "")


@pytest.mark.asyncio
async def test_try_claude_cli_invalid_json_in_result(monkeypatch):
    """result 裡沒有可 parse 的 JSON → data=None 不 crash。"""
    from src.llm_brain import _try_claude_cli

    envelope = {
        "type": "result",
        "result": "I cannot help with that request.",
    }

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(
        return_value=(json.dumps(envelope).encode("utf-8"), b"")
    )

    async def fake_spawn(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr("src.llm_brain._claude_cli_available", lambda: True)
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_spawn)

    r = await _try_claude_cli(
        system="sys", prompt="p", response_model=_Demo, timeout_s=10,
    )
    assert r.data is None
    assert "no JSON blob" in (r.raw_error or "")


@pytest.mark.asyncio
async def test_try_claude_cli_pydantic_validation_fails(monkeypatch):
    """JSON 合法但欄位對不上 schema → data=None、error 描述包 validate 關鍵字。"""
    from src.llm_brain import _try_claude_cli

    envelope = {
        "type": "result",
        "result": '{"wrong_field": "x"}',
    }

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(
        return_value=(json.dumps(envelope).encode("utf-8"), b"")
    )

    async def fake_spawn(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr("src.llm_brain._claude_cli_available", lambda: True)
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_spawn)

    r = await _try_claude_cli(
        system="sys", prompt="p", response_model=_Demo, timeout_s=10,
    )
    assert r.data is None
    assert "validate failed" in (r.raw_error or "")


@pytest.mark.asyncio
async def test_try_claude_cli_not_available(monkeypatch):
    from src.llm_brain import _try_claude_cli

    monkeypatch.setattr("src.llm_brain._claude_cli_available", lambda: False)

    r = await _try_claude_cli(
        system="sys", prompt="p", response_model=_Demo, timeout_s=10,
    )
    assert r.data is None
    assert "not found" in (r.raw_error or "")
