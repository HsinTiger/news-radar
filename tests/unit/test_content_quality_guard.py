"""Unit tests for src.content_quality_guard（Phase 8.20 附帶）。

只測純 Python；DB / publisher / notify 不涉入。
"""
from __future__ import annotations

from src.content_quality_guard import (
    _RULES,
    check_quality,
    format_issues,
    has_blocking_issues,
    should_request_rewrite,
)


# ---- 真實陷阱：2026-04-19 實際發出去的 emergency_template 貼文 ----
TEMPLATED_FB_POST = """🚀 Zero-Copy GPU Inference from WebAssembly on Apple Silicon

【系統代班速報】

科技格局正在發生結構性位移，護城河的定義已從產品轉向生態數據。\
這反映了產業變遷下的必然選擇。與其追逐破碎的新聞，不如冷靜看清底層的戰略邏輯，\
體諒轉型期帶來的陣痛。面對充滿挑戰的市場，數據密度高的決策，將會成為未來的勝負點。

#科技戰略 #商業洞察 #數據驅動"""


# ---- 一篇合格的 reasoning-chain 貼文（Phase 8.19d 風格）----
HEALTHY_POST = """Anthropic 今日發佈 Claude Opus 4.7，在 SWE-bench Verified 拿下 78.2%，\
較前一代 4.6 的 72.1% 提升 6.1 個百分點，是迄今最接近人類中位數（82%）的開源評估成績。

在 Agent 場景上，官方數據顯示單次任務平均呼叫工具 12.4 次、\
錯誤重試率較 4.6 下降 31%，這意味著同樣的 task 在 API 費用上可以少掉約三分之一。

連帶影響：Cursor 已宣布 Pro 用戶預設切到 Opus 4.7，\
而 Amazon Bedrock 的 inference TPS 提升讓它成為北美 SaaS 最容易導入的 API。

#Claude #AI模型"""


def test_templated_post_is_blocked():
    issues = check_quality(TEMPLATED_FB_POST, title="Zero-Copy GPU Inference from WebAssembly on Apple Silicon")
    assert has_blocking_issues(issues), (
        f"真實的 emergency_template 貼文必須被擋下，issues={format_issues(issues)}"
    )
    codes = {i.code for i in issues}
    assert "templated_fallback_marker" in codes, "應命中『【系統代班速報】』招牌詞"
    assert "generic_hashtag_bundle" in codes, "應命中『#科技戰略+#商業洞察+#數據驅動』三連"


def test_healthy_post_passes():
    issues = check_quality(HEALTHY_POST, title="Anthropic 發表 Claude Opus 4.7")
    assert not has_blocking_issues(issues), (
        f"合格貼文誤判為假文：{format_issues(issues)}"
    )


def test_empty_text_is_blocked():
    assert has_blocking_issues(check_quality("", title="news title"))
    assert has_blocking_issues(check_quality("   \n  ", title="news title"))


def test_english_only_title_without_chinese_body_is_blocked():
    # 標題純英、正文也沒中文 = 沒翻譯
    english_only = """Meta announces new VR headset today. Price starts at $499.

Key features include eye tracking and wireless passthrough."""
    issues = check_quality(english_only, title="Meta Quest 4 Launch")
    codes = {i.code for i in issues}
    assert "untranslated_english_only" in codes


def test_english_title_with_translated_body_passes():
    # 標題英文、正文中文 = writer 已翻譯，合格
    mixed = """Meta 今日在 Connect 發表 Quest 4，售價 499 美元。新機搭載眼動追蹤與無線 passthrough，\
鎖定 Apple Vision Pro 的中階市場。"""
    issues = check_quality(mixed, title="Meta Quest 4 Launch")
    codes = {i.code for i in issues}
    assert "untranslated_english_only" not in codes, (
        f"英文標題+中文正文不該被擋：{format_issues(issues)}"
    )


def test_individual_hashtag_without_bundle_does_not_block():
    # 只用其中一個 hashtag 不該命中組合規則
    single = """文章正文足以通過長度檢查，這是一段在 2026 年測試的句子。\
示範單一 hashtag 的情形：#科技戰略 而已，其他 hashtag 都沒寫在這段文字裡。"""
    issues = check_quality(single, title="測試單一 hashtag")
    codes = {i.code for i in issues}
    assert "generic_hashtag_bundle" not in codes


def test_format_issues_on_empty():
    assert format_issues([]) == "OK"


# ========== Topic-4 redo（2026-04-22）：+9 patterns, rewrite severity ==========
# 設計：每個 pattern 都給 positive + 一個最容易 false-positive 的負測（context-gated）。

# ---- severity / should_request_rewrite API ----

def test_rule_count_matches_spec():
    """若新增/刪除規則請同步更新本測試 & news_radar_soul §品質守門員。
    初版 4 條（templated_fallback / generic_hashtag_bundle / untranslated_english / empty_or_too_short）
    + Topic-4 redo 9 條 = 13。"""
    assert len(_RULES) == 13


def test_should_request_rewrite_true_when_rewrite_issue_present():
    # fake URL → severity='rewrite'
    text = "某公司新品發表，更多資訊詳見 example.com/demo，用戶可線上預購體驗。"
    issues = check_quality(text, title="某公司發表")
    assert should_request_rewrite(issues) is True


def test_should_request_rewrite_false_when_only_block():
    # templated_fallback_marker 是 block，不是 rewrite
    issues = check_quality(TEMPLATED_FB_POST, title="Zero-Copy GPU Inference")
    assert should_request_rewrite(issues) is False, (
        f"block-only issues should not trigger rewrite: {format_issues(issues)}"
    )


def test_should_request_rewrite_empty_list():
    assert should_request_rewrite([]) is False


# ---- Pattern 1：ai_refusal_marker ----

def test_ai_refusal_marker_blocks():
    text = "抱歉，我無法完成這項任務，因為我沒有即時網路存取權限。請提供更多細節。"
    issues = check_quality(text, title="test")
    codes = {i.code for i in issues}
    assert "ai_refusal_marker" in codes
    assert has_blocking_issues(issues)


def test_discussion_of_limits_without_refusal_marker_passes():
    # 新聞討論 AI 系統的限制本身不該觸發；關鍵是沒有 "抱歉我無法" / "作為一個 AI" 這類 marker
    text = "報導指出，某模型在醫療場景的準確率受限於訓練資料範圍，未來版本將補強。"
    issues = check_quality(text, title="AI 醫療模型觀察")
    codes = {i.code for i in issues}
    assert "ai_refusal_marker" not in codes


# ---- Pattern 2：placeholder_marker ----

def test_placeholder_marker_blocks():
    text = "XXXX 公司宣布新品，TBD 的發表日期令人期待。詳見[公司名]官網公告。"
    issues = check_quality(text, title="新品")
    codes = {i.code for i in issues}
    assert "placeholder_marker" in codes
    assert has_blocking_issues(issues)


def test_normal_text_without_placeholders_passes():
    text = "Anthropic 於 2026 年發表新版 Claude Opus，並公告多項效能升級資料。"
    issues = check_quality(text, title="Claude Opus")
    codes = {i.code for i in issues}
    assert "placeholder_marker" not in codes


# ---- Pattern 3：fake_url_marker（rewrite）----

def test_fake_url_triggers_rewrite():
    text = "某新創發表 demo 網站，使用者可於 example.com/demo 體驗新功能，預計 Q3 正式上線。"
    issues = check_quality(text, title="新創 demo")
    codes_by_sev = {(i.code, i.severity) for i in issues}
    assert ("fake_url_marker", "rewrite") in codes_by_sev
    assert should_request_rewrite(issues) is True


def test_real_url_does_not_trigger_fake():
    text = "Anthropic 把 Claude 的 MCP 規格開源於 github.com/anthropic/mcp，開發者可直接下載。"
    issues = check_quality(text, title="MCP 開源")
    codes = {i.code for i in issues}
    assert "fake_url_marker" not in codes


# ---- Pattern 4：stale_year_without_current（warn，context-gated）----

def test_stale_year_only_warns():
    # 只提 2022，無 2024-2026 當代錨點 → flag
    text = "2022 年發表的研究指出，模型尺寸並非唯一關鍵，資料品質影響同樣顯著可觀。"
    issues = check_quality(text, title="research paper")
    codes = {i.code for i in issues}
    assert "stale_year_without_current" in codes


def test_stale_year_with_current_year_passes():
    # 同時含 2022 + 2026 → 這是在做歷史回顧，不是 LLM 用舊 data
    text = "回顧 2022 年的研究到 2026 年的實作演進，模型尺寸已非唯一關鍵，資料品質也同等重要。"
    issues = check_quality(text, title="research evolution")
    codes = {i.code for i in issues}
    assert "stale_year_without_current" not in codes


# ---- Pattern 5：fake_source_marker ----

def test_fake_source_blocks():
    text = "根據《某某日報》報導，某公司今日宣布新品發表，市場反應熱烈，值得持續關注。"
    issues = check_quality(text, title="某公司新品")
    codes = {i.code for i in issues}
    assert "fake_source_marker" in codes
    assert has_blocking_issues(issues)


def test_real_named_source_passes():
    text = "根據路透社報導，Nvidia 於今日公告 H200 新批次出貨時程，市場反應正面積極。"
    issues = check_quality(text, title="Nvidia H200")
    codes = {i.code for i in issues}
    assert "fake_source_marker" not in codes


# ---- Pattern 6：corporate_fluff_pileup（warn, count≥3）----

def test_corporate_fluff_pileup_warns():
    # 5 個 fluff terms
    text = "新創在下沉市場找痛點，用全鏈路打法建護城河，形成獨特的生態閉環態勢。"
    issues = check_quality(text, title="新創觀察")
    codes = {i.code for i in issues}
    assert "corporate_fluff_pileup" in codes


def test_single_fluff_term_does_not_trip_pileup():
    # 只有 1 個 fluff term
    text = "這家新創在下沉市場的定位清晰，產品價格策略也已成熟可驗證，並準備進軍北美市場。"
    issues = check_quality(text, title="新創定位")
    codes = {i.code for i in issues}
    assert "corporate_fluff_pileup" not in codes


# ---- Pattern 7：hyperbole_overuse（rewrite, count≥4）----

def test_hyperbole_overuse_rewrites():
    # 6 個 hyperbole terms
    text = "這是一次劃時代的顛覆，史上最重大突破，徹底改變業界的革命性產品發表會盛況空前。"
    issues = check_quality(text, title="新品發表")
    codes_by_sev = {(i.code, i.severity) for i in issues}
    assert ("hyperbole_overuse", "rewrite") in codes_by_sev
    assert should_request_rewrite(issues) is True


def test_two_hyperbole_terms_below_threshold_pass():
    # 只有 2 個，不該 trip
    text = "業界認為這是劃時代的一步，具備顛覆潛力，但實際落地還需要 12 個月時間觀察。"
    issues = check_quality(text, title="業界觀察")
    codes = {i.code for i in issues}
    assert "hyperbole_overuse" not in codes


# ---- Pattern 8：uncited_stat（warn, citation-proximity）----

def test_uncited_stat_warns():
    # "提升 10%" 出現但 ±40 字內沒有 citation marker
    text = "今日傳出一則消息：某公司毛利率提升 10%，成為業界焦點，投資人後續動向值得關注留意。"
    issues = check_quality(text, title="某公司消息")
    codes = {i.code for i in issues}
    assert "uncited_stat" in codes


def test_cited_stat_passes():
    # "提升 10%" 前方 10 字內即有 "根據" / "財報" → 算有憑據
    text = "根據財報，某公司毛利率提升 10%，成為業界焦點，投資人後續動向將持續關注。"
    issues = check_quality(text, title="某公司財報")
    codes = {i.code for i in issues}
    assert "uncited_stat" not in codes


# ---- Pattern 9：wave_opener_without_year（rewrite, year-anchor gate）----

def test_wave_opener_without_year_rewrites():
    text = "在 AI 的浪潮中，許多公司開始探索新方向，帶動產業轉型逐漸成為焦點話題。"
    issues = check_quality(text, title="AI 趨勢")
    codes_by_sev = {(i.code, i.severity) for i in issues}
    assert ("wave_opener_without_year", "rewrite") in codes_by_sev


def test_wave_opener_with_year_anchor_passes():
    # 同樣開場詞，但有 2026 年份錨點 → 算有落地
    text = "在 AI 的浪潮中，2026 年許多公司開始探索新方向，帶動產業轉型成為焦點話題。"
    issues = check_quality(text, title="AI 趨勢 2026")
    codes = {i.code for i in issues}
    assert "wave_opener_without_year" not in codes


# ---- meta：healthy post 不該觸發任何新增 rule ----

def test_healthy_post_no_rewrite_requested():
    issues = check_quality(HEALTHY_POST, title="Anthropic 發表 Claude Opus 4.7")
    assert should_request_rewrite(issues) is False, (
        f"合格貼文不該被判 rewrite：{format_issues(issues)}"
    )


def test_healthy_post_no_new_warn_either():
    """healthy post 也不該 warn——若有，代表 Topic-4 規則 FP。"""
    issues = check_quality(HEALTHY_POST, title="Anthropic 發表 Claude Opus 4.7")
    new_codes = {
        "ai_refusal_marker", "placeholder_marker", "fake_url_marker",
        "stale_year_without_current", "fake_source_marker", "corporate_fluff_pileup",
        "hyperbole_overuse", "uncited_stat", "wave_opener_without_year",
    }
    hit = {i.code for i in issues} & new_codes
    assert not hit, f"healthy post 誤觸 Topic-4 規則：{hit}；detail={format_issues(issues)}"
