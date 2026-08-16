"""選角契約：專欄識別、拉丁 marker 的詞界、訊號優先於 topic_category。"""
from src.image_brain import _match_markers, _HARD_TITLE_MARKERS, pick_character


def test_latin_markers_require_word_boundaries():
    """`API` 曾經比對到 `Shapiro`（SH-API-RO），把一篇美國政治的 podcast
    判成硬科技。拉丁字母 marker 一律要詞界。"""
    for decoy in ("Ben Shapiro", "TAIWAN retail chain", "public policy", "domain"):
        assert _match_markers(decoy, _HARD_TITLE_MARKERS) == [], decoy
    assert "AI" in _match_markers("AI 模型的算力帳單", _HARD_TITLE_MARKERS)
    assert "SaaS" in _match_markers("SaaS 估值雪崩", _HARD_TITLE_MARKERS)


def test_company_column_always_uses_robot():
    assert pick_character("other", "company", "任何標題") == "robot"


def test_signal_outranks_a_wrong_topic_category():
    """topic_category 會錯：一篇 222 奈米殺菌燈的稿子被上游標成 ai_model。
    標題與 tag 的訊號要蓋過它。"""
    signal = "殺菌光為何還沒裝滿教室 遠紫外線 公共衛生 醫療科技"
    assert pick_character("ai_model", "morning", signal) == "owl"


def test_hard_signal_wins_when_topic_is_other():
    """topic_category 長年是 other，tag 才是有題材訊號的那一份。"""
    signal = "當用戶不再打開 Canva SaaS aiagent Google DeepMind"
    assert pick_character("other", "podcast", signal) == "robot"


def test_podcast_mode_is_not_hardwired_to_owl():
    """曾經 mode=='podcast' 直接回 owl，導致封面清一色達達。"""
    assert pick_character("other", "podcast", "電網與算力的瓶頸 資料中心") == "robot"
