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


# --- agy 帳號層級不可用時不要逐一空等（2026-09-20：中午排程佔著鎖跑 70 分鐘沒結果） ---

def test_quota_and_hang_errors_are_account_level():
    from src.llm_brain import _agy_quota_or_hang
    assert _agy_quota_or_hang("agy exit 3: RESOURCE_EXHAUSTED (code 429)")
    assert _agy_quota_or_hang("[agy] print timeout after 30m0s with turn in progress")
    # 這個是「這次輸出不合 schema」，換模型或重試有意義，不能跳過整條鏈
    assert not _agy_quota_or_hang("4 validation errors for EditorialResearchBrief")


def test_single_agy_call_wait_is_capped():
    from src import llm_brain
    assert llm_brain.AGY_PRINT_TIMEOUT_MAX_S <= 900


def test_claude_cli_gets_the_same_json_field_contract_as_agy():
    """2026-09-20：claude 沒拿到欄位表，自己編結構 → 9 個必填欄位 missing。"""
    from src.llm_brain import _json_contract_block
    from substack_radar.composer import EditorialResearchBrief

    block = _json_contract_block(EditorialResearchBrief)
    assert "article_form" in block and "source_digest" in block
    assert "raw JSON" in block


def test_second_call_after_agy_is_known_dead_falls_through_instead_of_crashing(monkeypatch):
    """2026-10-16 06:00：研究簡報那次已確認 agy 各家沒額度，寫正文那次一個都不試，
    result 是 None → AttributeError，整篇沒寫成。"""
    import asyncio
    from pydantic import BaseModel
    from src import llm_brain

    class Tiny(BaseModel):
        answer: str

    monkeypatch.setenv("LLM_HAIKU_STANDIN", "1")
    monkeypatch.setattr(llm_brain, "_AGY_DEAD_SNAPSHOT", {"gemini", "claude"})
    monkeypatch.setattr(llm_brain, "_agy_available", lambda: True)
    monkeypatch.setattr(llm_brain, "_agy_model_chain", lambda: ["Gemini 3.8 Flash (High)", "Claude Opus 4.6 (Thinking)"])
    async def haiku(**_k):
        return llm_brain.LLMResult(data=Tiny(answer="ok"), provider="claude_cli", model="haiku")
    monkeypatch.setattr(llm_brain, "_try_claude_cli", haiku)
    monkeypatch.setattr(llm_brain, "_claude_cli_available", lambda: True)
    r = asyncio.run(llm_brain.call_for_json(system="s", prompt="p", response_model=Tiny,
                                            backends=("antigravity_cli", "gateway", "claude_cli")))
    assert r.data.answer == "ok" and r.provider == "claude_cli"


def test_gateway_stands_in_for_dead_agy_before_haiku(monkeypatch):
    """信哥 2026-10-17：「agy 沒額度可以用 ccr 去頂替」→ 預設閘道排在 Haiku 前面。"""
    import asyncio
    from pydantic import BaseModel
    from src import llm_brain

    class Tiny(BaseModel):
        answer: str

    monkeypatch.delenv("LLM_HAIKU_STANDIN", raising=False)
    monkeypatch.setattr(llm_brain, "_AGY_DEAD_SNAPSHOT", {"gemini", "claude"})
    monkeypatch.setattr(llm_brain, "_agy_available", lambda: True)
    monkeypatch.setattr(llm_brain, "_agy_model_chain", lambda: ["Gemini 3.8 Flash (High)", "Claude Opus 4.6 (Thinking)"])
    monkeypatch.setenv("OSS_GATEWAY_KEY", "k")
    tried = []
    async def gateway(**kw):
        tried.append(kw["provider"].name)
        return llm_brain.LLMResult(data=Tiny(answer="gw"), provider="gateway", model="gemini-2.5-flash")
    async def haiku(**_k):
        tried.append("claude_cli")
        return llm_brain.LLMResult(data=Tiny(answer="ok"), provider="claude_cli", model="haiku")
    monkeypatch.setattr(llm_brain, "_try_openai_compatible", gateway)
    monkeypatch.setattr(llm_brain, "_try_claude_cli", haiku)
    monkeypatch.setattr(llm_brain, "_claude_cli_available", lambda: True)
    r = asyncio.run(llm_brain.call_for_json(system="s", prompt="p", response_model=Tiny,
                                            backends=("antigravity_cli", "gateway", "claude_cli")))
    assert r.provider == "gateway" and tried == ["gateway"]


def test_agy_dead_marker_lets_the_next_process_skip_agy(monkeypatch, tmp_path):
    """2026-10-17：每個排程是新行程，原本都要重撞一輪 agy；記號讓下一個排程直接跳過。"""
    import time
    from src import llm_brain

    monkeypatch.setattr(llm_brain, "AGY_DEAD_MARKER", tmp_path / ".agy_dead.json")
    monkeypatch.setattr(llm_brain, "_AGY_DEAD_SNAPSHOT", {"claude", "gemini"})
    llm_brain._set_agy_exhausted()
    monkeypatch.setattr(llm_brain, "_AGY_DEAD_SNAPSHOT", set())       # 模擬新行程
    assert llm_brain._dead_families_snapshot() == {"claude", "gemini"}
    monkeypatch.setattr(llm_brain.time, "time", lambda: 10**12)        # 過期 → 再試 agy
    assert llm_brain._dead_families_snapshot() == set()


def test_agy_outer_timeout_matches_print_timeout_cap():
    import inspect
    from src import llm_brain
    src = inspect.getsource(llm_brain._try_agy)
    assert "timeout=timeout_s + 20" not in src and "AGY_PRINT_TIMEOUT_MAX_S) + 30" in src
