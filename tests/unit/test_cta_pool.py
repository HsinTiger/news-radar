"""
News Radar · Unit tests for src/cta_pool.py (Phase 2, 2026-05-14)

驗收 config/platforms/threads_v2.md §14.6 的反指紋導流邏輯：
- 1/3 機率注入 → ENV 可覆寫到 0/1 for testing
- 排除最近 2 篇用過的風格類
- LLM prompt fragment 不洩漏任何例句（避免照抄）
- JSON 歷史檔 idempotent、損壞時 graceful
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import pytest

from src import cta_pool


# ----------------------------------------------------------------------
# History-file 隔離 fixture：每個 test 用 tmp_path 取代真實檔案
# ----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    """每個 test 用獨立 tmp_path 模擬 cta_history.json，不污染真實檔。"""
    fake_path = tmp_path / "cta_history.json"
    monkeypatch.setattr(cta_pool, "CTA_HISTORY_PATH", fake_path)
    # 同時清掉 ENV 干擾
    monkeypatch.delenv("NEWS_RADAR_CTA_PROBABILITY", raising=False)
    yield fake_path


# ----------------------------------------------------------------------
# decide_cta 機率邏輯
# ----------------------------------------------------------------------

def test_probability_zero_never_injects():
    """probability=0 → 永遠 None。"""
    for _ in range(50):
        assert cta_pool.decide_cta(probability=0.0) is None


def test_probability_one_always_injects():
    """probability=1 → 永遠 not None，且 in ALL_STYLES。"""
    for _ in range(10):
        result = cta_pool.decide_cta(probability=1.0)
        assert result is not None
        assert result in cta_pool.ALL_STYLES


def test_probability_default_reads_env(monkeypatch):
    """ENV 覆寫機率：=0 → 永遠 None；=1 → 永遠 inject。"""
    monkeypatch.setenv("NEWS_RADAR_CTA_PROBABILITY", "0")
    for _ in range(20):
        assert cta_pool.decide_cta() is None

    monkeypatch.setenv("NEWS_RADAR_CTA_PROBABILITY", "1")
    for _ in range(10):
        assert cta_pool.decide_cta() is not None


def test_env_invalid_falls_back_to_default(monkeypatch):
    """ENV 非數字 / 超範圍 → 用預設 1/3，不會炸。"""
    for bad in ("foo", "1.5", "-0.1", ""):
        monkeypatch.setenv("NEWS_RADAR_CTA_PROBABILITY", bad)
        # 不會 throw、回傳合法值（None 或 CTAStyle）
        for _ in range(5):
            result = cta_pool.decide_cta()
            assert result is None or result in cta_pool.ALL_STYLES


# ----------------------------------------------------------------------
# 排除最近 2 類邏輯
# ----------------------------------------------------------------------

def test_excludes_last_2_styles_on_next_call(isolated_history):
    """歷史檔有 ['A','B'] → 緊接著的下一次決策必不抽 A 或 B（sliding window 語意）。

    每次 trial 都重設歷史回到 [A,B]，測「下一次必排除」這個性質。
    若不 reset，第 2 次起 sliding window 已經位移、A 或 B 會回到候選池——
    這是符合 §14.6.1 規格的行為。
    """
    initial = json.dumps({
        "recent": [
            {"style": "A", "ts": "2026-05-14T10:00:00+00:00"},
            {"style": "B", "ts": "2026-05-14T09:00:00+00:00"},
        ]
    }, ensure_ascii=False)

    seen = set()
    for _ in range(40):
        isolated_history.write_text(initial, encoding="utf-8")
        chosen = cta_pool.decide_cta(probability=1.0)
        assert chosen is not None
        assert chosen not in ("A", "B"), f"sliding window 第 1 次出現了排除類 {chosen}"
        seen.add(chosen)
    assert seen == {"C", "D", "E"}, f"預期見到 C/D/E，實際 {seen}"


def test_empty_history_picks_from_all_five(isolated_history):
    """空歷史 → 從全 5 類隨機抽。"""
    # 確認檔案不存在 / 空
    assert not isolated_history.exists()
    seen = set()
    for _ in range(60):
        chosen = cta_pool.decide_cta(probability=1.0)
        assert chosen is not None
        seen.add(chosen)
    # 60 輪後 5 類全見過（機率上 ≥ 99.99%）
    assert seen == set(cta_pool.ALL_STYLES)


def test_history_writes_after_inject(isolated_history):
    """成功注入後，歷史檔多 1 筆，且最新在前。"""
    chosen1 = cta_pool.decide_cta(probability=1.0)
    chosen2 = cta_pool.decide_cta(probability=1.0)
    chosen3 = cta_pool.decide_cta(probability=1.0)
    assert chosen1 is not None and chosen2 is not None and chosen3 is not None

    raw = json.loads(isolated_history.read_text(encoding="utf-8"))
    recent = raw["recent"]
    assert len(recent) == 3
    # 最新在 [0]、reverse chronological
    assert recent[0]["style"] == chosen3
    assert recent[1]["style"] == chosen2
    assert recent[2]["style"] == chosen1
    # 每筆都有 ts
    assert all("ts" in entry for entry in recent)


def test_history_not_written_when_not_injecting(isolated_history):
    """機率 0 → 一筆都不寫。"""
    for _ in range(20):
        cta_pool.decide_cta(probability=0.0)
    assert not isolated_history.exists()


def test_history_truncates_to_max_len(isolated_history):
    """歷史超過 HISTORY_MAX_LEN 自動 truncate。"""
    # 預埋 HISTORY_MAX_LEN + 5 筆
    over = cta_pool.HISTORY_MAX_LEN + 5
    isolated_history.write_text(
        json.dumps({
            "recent": [
                {"style": "C", "ts": f"2026-05-14T00:00:0{i}+00:00"}
                for i in range(over)
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    # 注入一筆 → 應該變成 HISTORY_MAX_LEN
    cta_pool.decide_cta(probability=1.0)
    raw = json.loads(isolated_history.read_text(encoding="utf-8"))
    assert len(raw["recent"]) == cta_pool.HISTORY_MAX_LEN


# ----------------------------------------------------------------------
# 損壞 / 舊格式相容
# ----------------------------------------------------------------------

def test_corrupt_json_treats_as_empty(isolated_history):
    """歷史檔損壞 → 視為空、不 throw。"""
    isolated_history.write_text("{not valid json", encoding="utf-8")
    chosen = cta_pool.decide_cta(probability=1.0)
    assert chosen is not None  # 還是能正常運作


def test_legacy_string_list_format_still_works(isolated_history):
    """舊格式（list[str] 而非 list[dict]）→ 仍能讀出風格、不 crash。

    sliding window 同 test_excludes_last_2_styles_on_next_call：reset 每次 trial。
    """
    initial = json.dumps({"recent": ["A", "B"]}, ensure_ascii=False)
    for _ in range(20):
        isolated_history.write_text(initial, encoding="utf-8")
        chosen = cta_pool.decide_cta(probability=1.0)
        assert chosen not in ("A", "B"), f"舊格式 sliding window 失效，抽到 {chosen}"


# ----------------------------------------------------------------------
# Prompt fragment 不洩漏例句（CRITICAL 反指紋設計）
# ----------------------------------------------------------------------

# threads_v2.md §14.6.2 的例句，**絕對不能**出現在 LLM prompt 內
EXAMPLE_PHRASES_THAT_MUST_NOT_LEAK = [
    "這只是冰山一角",
    "3500 字版本攤在電子報",
    "完整推導我另寫了一份",
    "篇幅關係，我在 hsin73",
    "Threads 寫不下，深度版另發",
    "我寫到 3000 字了",
    "完整版太長，怕被罵端著",
    "想看完整推導的話 search hsin73",
    "真有興趣的人會自己找到",
    "明早電子報攤開 3 個衍生情境",
    "下一篇長文會講為什麼這個結論不完全對",
]


@pytest.mark.parametrize("style", list(cta_pool.ALL_STYLES))
def test_prompt_fragment_does_not_leak_example_phrases(style):
    """Critical：LLM prompt 不可包含 §14.6.2 的任何例句、否則照抄 → 反指紋失效。"""
    fragment = cta_pool.get_cta_prompt_fragment(style)
    for phrase in EXAMPLE_PHRASES_THAT_MUST_NOT_LEAK:
        assert phrase not in fragment, (
            f"風格 {style} 的 prompt fragment 洩漏例句：「{phrase}」。"
            f"這違反 §14.6.2 反指紋設計、會導致 LLM 照抄。"
        )


@pytest.mark.parametrize("style", list(cta_pool.ALL_STYLES))
def test_prompt_fragment_contains_required_constraints(style):
    """Prompt fragment 必須含硬性限制（≤25字、無 URL、禁用詞）。"""
    fragment = cta_pool.get_cta_prompt_fragment(style)
    assert "25 字" in fragment
    assert "https://" in fragment  # 出現在 "不寫 https://" 限制中
    assert "歡迎訂閱" in fragment   # 出現在禁用詞限制
    assert "原創" in fragment       # 強調自己寫、不照抄


@pytest.mark.parametrize("style", list(cta_pool.ALL_STYLES))
def test_prompt_fragment_only_targets_threads(style):
    """Prompt fragment 必須明確說「只加在 Threads 變體」，避免 FB/IG 被誤加 CTA。"""
    fragment = cta_pool.get_cta_prompt_fragment(style)
    assert "只加在 Threads 變體" in fragment or "只加在 threads" in fragment.lower()


# ----------------------------------------------------------------------
# peek_recent_history（reflector / debug 用）
# ----------------------------------------------------------------------

def test_peek_recent_history(isolated_history):
    """peek 回傳新到舊的風格序列。"""
    isolated_history.write_text(
        json.dumps({
            "recent": [
                {"style": "C", "ts": "2026-05-14T10:00:00+00:00"},
                {"style": "A", "ts": "2026-05-14T09:00:00+00:00"},
                {"style": "E", "ts": "2026-05-14T08:00:00+00:00"},
            ]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    assert cta_pool.peek_recent_history(n=3) == ["C", "A", "E"]
    assert cta_pool.peek_recent_history(n=2) == ["C", "A"]
    assert cta_pool.peek_recent_history(n=10) == ["C", "A", "E"]


def test_peek_empty_history(isolated_history):
    """無歷史檔 → peek 回 []。"""
    assert cta_pool.peek_recent_history() == []
