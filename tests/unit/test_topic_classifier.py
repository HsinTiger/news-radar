"""Phase 8.20 Step 2：topic_classifier 單元測試。

只測 keyword fast-path + compute_weighted_score；
LLM path 需要 call_for_json 真實呼叫，放到 integration tests 去。
"""
from __future__ import annotations

from src.topic_classifier import (
    TopicClassification,
    classify_topic_keyword,
    compute_weighted_score,
)
from src.topic_taxonomy import category_ids


# ---------- keyword fast-path ----------

def test_claude_opus_hits_ai_model():
    c = classify_topic_keyword(
        "Anthropic 發布 Claude Opus 4.7，SWE-bench 拿下 78%", ""
    )
    assert c is not None and c.category_id == "ai_model"
    assert 0 < c.confidence <= 1


def test_tsmc_hbm_hits_supply_chain():
    c = classify_topic_keyword("台積電 3 奈米產能滿載，HBM 供應緊張", "")
    assert c is not None and c.category_id == "supply_chain"


def test_nvidia_earnings_hits_earnings():
    c = classify_topic_keyword("Nvidia 法說：Q3 毛利率 75%", "")
    assert c is not None and c.category_id == "earnings"


def test_taiex_hits_tw_stocks():
    c = classify_topic_keyword("外資大舉買超台股 300 億，加權指數創高", "")
    assert c is not None and c.category_id == "tw_stocks"


def test_claude_code_hits_ai_agent_not_ai_model():
    """『Claude Code』應命中 ai_agent（因為列表含『Claude Code』完整字串），
    而非 ai_model（列表只含『Claude Opus/Sonnet/Haiku』，不含 bare 'Claude'）。"""
    c = classify_topic_keyword(
        "Claude Code 與 Cursor 整合：新的 agent SDK 功能", ""
    )
    assert c is not None
    assert c.category_id == "ai_agent", f"expected ai_agent, got {c.category_id}"


def test_irrelevant_content_misses():
    c = classify_topic_keyword("今日台北多雲短暫陣雨", "氣溫 24 度")
    assert c is None, f"weather article should miss keyword path, got {c}"


def test_keyword_confidence_is_conservative():
    """keyword path 不該自封『高信心』——留空間給 LLM 在模棱兩可時接手。"""
    c = classify_topic_keyword("台積電新廠動工", "")
    assert c is not None and c.confidence <= 0.7


def test_keyword_category_always_in_taxonomy():
    samples = [
        "GPT-5 發表",
        "CHIPS 法案新提案",
        "iPhone 18 發表",
        "Apple Vision Pro 銷量",
    ]
    valid = set(category_ids())
    for s in samples:
        c = classify_topic_keyword(s, "")
        if c is not None:
            assert c.category_id in valid, f"{s} → {c.category_id} 不在 taxonomy"


# ---------- compute_weighted_score ----------

def test_weighted_score_basic():
    assert abs(compute_weighted_score(0.85, 1.70) - 1.445) < 1e-6


def test_weighted_score_clips_high():
    assert compute_weighted_score(1.0, 2.5) == 2.0
    assert compute_weighted_score(1.5, 1.5) == 2.0


def test_weighted_score_clips_low():
    assert compute_weighted_score(-0.2, 1.0) == 0.0
    assert compute_weighted_score(0.0, 1.7) == 0.0


def test_weighted_score_preserves_zero():
    assert compute_weighted_score(0.5, 0.0) == 0.0
