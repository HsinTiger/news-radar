"""
News Radar · Unit tests for fetcher 純函式

`fetch_feed` / `fetch_html` 打網路，不在 unit 範圍（放 integration 用 VCR 或 mock）；
這裡只測 deterministic 助手。
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta

from src.fetcher import (
    make_news_id,
    is_too_old,
    _parse_rss_time,
    _rewrite_url_for_extraction,
    _reddit_rss_to_markdown,
)


def test_make_news_id_stable():
    a = make_news_id("https://example.com/a")
    b = make_news_id("https://example.com/a")
    c = make_news_id("https://example.com/b")
    assert a == b
    assert a != c
    assert len(a) == 40  # sha1 hex


def test_is_too_old_yes():
    old = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
    assert is_too_old(old, max_age_hours=168) is True


def test_is_too_old_no():
    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert is_too_old(fresh, max_age_hours=168) is False


def test_is_too_old_malformed_returns_false():
    """壞資料不該讓整批被誤殺。"""
    assert is_too_old("not-a-date", max_age_hours=168) is False


def test_parse_rss_time_fallback_to_now():
    class FakeEntry(dict):
        def get(self, k, default=None):
            return super().get(k, default)

    s = _parse_rss_time(FakeEntry())
    # 應該是 ISO 格式
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    assert (datetime.now(timezone.utc) - dt).total_seconds() < 10


# ------------- URL rewriters（Phase 8.8）-------------

def test_rewrite_reddit_www_to_old():
    url = "https://www.reddit.com/r/singularity/comments/abc/title/"
    assert _rewrite_url_for_extraction(url) == \
        "https://old.reddit.com/r/singularity/comments/abc/title/"


def test_rewrite_reddit_new_to_old():
    url = "https://new.reddit.com/r/LocalLLaMA/comments/xyz/"
    assert _rewrite_url_for_extraction(url) == \
        "https://old.reddit.com/r/LocalLLaMA/comments/xyz/"


def test_rewrite_reddit_bare_host_to_old():
    url = "https://reddit.com/r/MachineLearning/comments/zzz/"
    assert _rewrite_url_for_extraction(url) == \
        "https://old.reddit.com/r/MachineLearning/comments/zzz/"


def test_rewrite_reddit_already_old_passthrough():
    url = "https://old.reddit.com/r/singularity/comments/abc/"
    assert _rewrite_url_for_extraction(url) == url


def test_rewrite_non_reddit_passthrough():
    url = "https://openai.com/blog/some-article"
    assert _rewrite_url_for_extraction(url) == url


# ------------- Reddit RSS summary → markdown（Phase 8.8）-------------

def test_reddit_rss_summary_extracts_text_post():
    html = (
        "<table><tr><td>"
        "<p>I've been running Sonnet 4.6 on my RTX 5090 and wanted to share "
        "benchmark results. The context window handling is surprisingly clean "
        "even at 900k tokens — latency only grows 30% vs the 200k baseline.</p>"
        "<p>Has anyone else tested this?</p>"
        "</td></tr></table>"
        "<a href='...'>[link]</a><a href='...'>[comments]</a>"
    )
    md = _reddit_rss_to_markdown(html)
    assert md is not None
    assert "Sonnet 4.6" in md
    assert "[link]" not in md
    assert "[comments]" not in md


def test_reddit_rss_summary_thin_link_post_returns_none():
    html = "<a>[link]</a><a>[comments]</a>"
    assert _reddit_rss_to_markdown(html) is None


def test_reddit_rss_summary_empty_returns_none():
    assert _reddit_rss_to_markdown("") is None


# ------------- X (RSSHub) summary 也走同一個 helper（Phase 8.10）-------------

def test_social_summary_handles_x_post_html():
    """Phase 8.10：X (via RSSHub) 的 summary 也是 HTML — 同一個 helper 要能處理。

    helper 名字雖然還是 `_reddit_rss_to_markdown`，但廣義上是
    「把任何 social feed 的 HTML summary 轉純文字」工具。
    """
    html = (
        "<p>Just shipped Grok 5. First model to hit 90% on GPQA Diamond with "
        "sub-second latency on vanilla H100. Open weights next month.</p>"
        "<blockquote>Quoted tweet body excerpted here.</blockquote>"
    )
    md = _reddit_rss_to_markdown(html)
    assert md is not None
    assert "Grok 5" in md
    assert "GPQA" in md
    # HTML 標籤應該全部剝掉
    assert "<p>" not in md
    assert "<blockquote>" not in md


def test_social_summary_handles_link_only_x_post_as_none():
    """X 上的純連結或單行推文（summary 太薄）應該回 None，
    讓下游走 fetch_html 路徑（雖然 X 通常會 403，但至少我們不會把 thin content
    當 clean_markdown 塞進 DB、讓 composer 撿到半殘的訊號）。"""
    html = "<p>https://t.co/abc123</p>"
    assert _reddit_rss_to_markdown(html) is None
