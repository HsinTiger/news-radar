"""Unit tests for src.content_quality_guard（Phase 8.20 附帶）。

只測純 Python；DB / publisher / notify 不涉入。
"""
from __future__ import annotations

from src.content_quality_guard import (
    check_quality,
    format_issues,
    has_blocking_issues,
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
