"""Phase 8.20 Step 1：topic_taxonomy 單元測試。
只測 pure Python（不碰 DB / LLM），確保類別 id / 權重 / prompt 輸出符合合約。
"""
from __future__ import annotations

from src.topic_taxonomy import (
    TOPIC_CATEGORIES,
    TopicCategory,
    taxonomy_as_dict,
    category_ids,
    seed_weight_for,
    classifier_prompt_block,
)


def test_category_count_is_ten():
    """Phase 8.20 seed 固定 10 類；新增/刪除類別視同 schema 變動，要提醒。"""
    assert len(TOPIC_CATEGORIES) == 10, (
        f"taxonomy 類別數被改動了：目前 {len(TOPIC_CATEGORIES)}，預期 10。"
        "若要調整，請同步更新 schema.sql / topic_keywords.yaml / 這個測試。"
    )


def test_category_ids_unique_and_snake_case():
    ids = category_ids()
    assert len(set(ids)) == len(ids), "category_id 有重複"
    for cid in ids:
        assert cid == cid.lower(), f"{cid} 不是全小寫"
        assert " " not in cid, f"{cid} 含空白"
        # 只允許小寫字母 / 數字 / 底線
        assert all(ch.isalnum() or ch == "_" for ch in cid), f"{cid} 含非法字元"


def test_seed_weights_in_range():
    for c in TOPIC_CATEGORIES:
        assert 0.3 <= c.seed_weight <= 2.0, (
            f"{c.id} seed_weight={c.seed_weight} 超出 back-prop 邊界"
        )


def test_ai_categories_outrank_others():
    """Hsin 指定：ai_model / ai_agent / ai_application 必須高於其他類。"""
    d = taxonomy_as_dict()
    ai_weights = [d["ai_model"].seed_weight, d["ai_agent"].seed_weight,
                  d["ai_application"].seed_weight]
    non_ai_weights = [d[k].seed_weight for k in d
                      if k not in {"ai_model", "ai_agent", "ai_application"}]
    assert min(ai_weights) > max(non_ai_weights), (
        "AI 三類的最低權重必須高於非 AI 類別的最高權重"
    )


def test_other_exists_and_is_lowest():
    d = taxonomy_as_dict()
    assert "other" in d, "必須保留 other 類別當作 fallback 渠道"
    assert d["other"].seed_weight == min(c.seed_weight for c in TOPIC_CATEGORIES)


def test_seed_weight_for_unknown_returns_other():
    assert seed_weight_for("nonexistent_category") == seed_weight_for("other")


def test_classifier_prompt_block_contains_all_ids():
    block = classifier_prompt_block()
    for cid in category_ids():
        assert f"`{cid}`" in block, f"{cid} 未出現在 classifier prompt"


def test_frozen_dataclass():
    """TopicCategory 應為 frozen（避免下游 monkey-patch）。"""
    c = TOPIC_CATEGORIES[0]
    try:
        c.seed_weight = 99.9  # type: ignore[misc]
    except Exception:
        return  # FrozenInstanceError 或 AttributeError 皆算通過
    raise AssertionError("TopicCategory 應該是 frozen，但被改動了")
