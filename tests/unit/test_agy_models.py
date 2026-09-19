"""agy 模型鏈自動跟上最新一代。每一條都對應一個「寫死版本號」會出事的情境。"""

from src import agy_models
from src.agy_models import newest_gemini, with_latest

# 2026-09-19 實際的 `agy models` 輸出（顯示名稱部分）
LISTED = [
    "Gemini 3.8 Flash (High)", "Gemini 3.8 Flash (Medium)", "Gemini 3.8 Flash (Low)",
    "Gemini 3.7 Flash (High)", "Gemini 3.7 Flash (Medium)", "Gemini 3.7 Flash (Low)",
    "Gemini 3.6 Flash (High)", "Gemini 3.6 Flash (Medium)", "Gemini 3.6 Flash (Low)",
    "Gemini 3.1 Pro (High)", "Gemini 3.1 Pro (Low)",
    "Claude Sonnet 4.6 (Thinking)", "Claude Opus 4.6 (Thinking)", "GPT-OSS 120B (Medium)",
]
ENV_CHAIN = ["Gemini 3.7 Flash (High)", "Gemini 3.6 Flash (High)",
             "Gemini 3.1 Pro (High)", "Claude Opus 4.6 (Thinking)"]


def test_parses_agy_models_output():
    out = "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\nclaude-opus-4-6-thinking\tClaude Opus 4.6 (Thinking)\n"
    assert agy_models._parse(out) == ["Gemini 3.8 Flash (High)", "Claude Opus 4.6 (Thinking)"]


def test_newest_is_highest_version_at_top_effort():
    # 3.8 Flash 勝過 3.1 Pro：新一代優先於舊一代的 Pro
    assert newest_gemini(LISTED) == "Gemini 3.8 Flash (High)"


def test_never_picks_medium_or_low():
    assert newest_gemini(["Gemini 3.9 Flash (Low)", "Gemini 3.9 Flash (Medium)",
                          "Gemini 3.8 Flash (High)"]) == "Gemini 3.8 Flash (High)"


def test_pro_beats_flash_at_same_version():
    assert newest_gemini(["Gemini 3.8 Flash (High)", "Gemini 3.8 Pro (High)"]) == "Gemini 3.8 Pro (High)"


def test_version_compared_numerically_not_as_text():
    # 字串比較會認為 "3.10" < "3.9"
    assert newest_gemini(["Gemini 3.9 Flash (High)", "Gemini 3.10 Flash (High)"]) == "Gemini 3.10 Flash (High)"


def test_new_model_goes_first_and_old_chain_stays_as_fallback(monkeypatch):
    monkeypatch.setenv("AGY_AUTO_LATEST", "1")
    assert with_latest(ENV_CHAIN, LISTED) == ["Gemini 3.8 Flash (High)"] + ENV_CHAIN


def test_retired_models_are_dropped(monkeypatch):
    monkeypatch.setenv("AGY_AUTO_LATEST", "1")
    listed = [m for m in LISTED if m != "Gemini 3.6 Flash (High)"]
    assert "Gemini 3.6 Flash (High)" not in with_latest(ENV_CHAIN, listed)


def test_unknown_model_list_leaves_chain_untouched(monkeypatch):
    # agy models 查不到時不能把鏈清空，否則寫稿整個停擺
    monkeypatch.setenv("AGY_AUTO_LATEST", "1")
    assert with_latest(ENV_CHAIN, []) == ENV_CHAIN


def test_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("AGY_AUTO_LATEST", "0")
    assert with_latest(ENV_CHAIN, LISTED) == ENV_CHAIN


def test_explicit_agy_model_still_wins(monkeypatch):
    from src import llm_brain
    monkeypatch.setenv("AGY_MODEL", "Claude Opus 4.6 (Thinking)")
    monkeypatch.setattr(agy_models, "available_models", lambda **_: LISTED)
    assert llm_brain._agy_model_chain()[0] == "Claude Opus 4.6 (Thinking)"
