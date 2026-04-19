"""
News Radar · Unit tests for src/cleaner.py

重點：覆蓋「為什麼這篇會被 Drop」的所有分支，
未來改動 cleaner 行為時，這組測試是回歸保險。
"""
from __future__ import annotations
import asyncio
import pytest

from src.cleaner import (
    extract_markdown,
    extract_og_image,
    extract_og_video,
    _classify_video_url,
    keyword_filter,
    min_length_filter,
    resolve_min_word_count,
    clean_and_filter,
)
from src.schema import NewsItem


def _make_item(**over) -> NewsItem:
    base = dict(
        id="test-1",
        feed_name="TestFeed",
        feed_tier="primary",
        url="https://example.com/a",
        title="Anthropic launches Claude Sonnet 4.6",
        published_at="2026-04-18T00:00:00+00:00",
        fetched_at="2026-04-19T00:00:00+00:00",
        language="en",
    )
    base.update(over)
    return NewsItem(**base)


# ------- extract_markdown -------

def test_extract_markdown_picks_up_body(sample_html_en):
    md, wc = extract_markdown(sample_html_en)
    assert md, "trafilatura should produce markdown for a normal blog post"
    assert wc > 50, "word_count should be non-trivial for a multi-paragraph post"
    assert "Anthropic" in md
    assert "Jensen Huang" in md


def test_extract_markdown_paywall_is_short(sample_html_paywall):
    md, wc = extract_markdown(sample_html_paywall)
    # paywall HTML 萃取出的內文應該遠低於任何合理的 min_word_count 門檻。
    # 保守設 < 50：預設門檻 100、article 門檻 200，< 50 還有大量 margin，
    # 足以證明 paywall 一定會被 too_short 擋下，而又不會因 fixture 字數微調就誤殺測試。
    if md is None:
        assert wc == 0
    else:
        assert wc < 50, f"paywall 萃取字數 {wc} 不應接近任何真實 min_word_count 門檻"


# ------- extract_og_image -------

def test_extract_og_image_from_meta(sample_html_en):
    url = extract_og_image(sample_html_en)
    assert url == "https://example.com/og-claude-4-6.png"


def test_extract_og_image_absent_returns_none(sample_html_paywall):
    assert extract_og_image(sample_html_paywall) is None


# ------- keyword_filter -------

def test_keyword_filter_blacklist_fires_first():
    item = _make_item(
        title="Trump defends crypto investment strategy",
        clean_markdown="Donald Trump said the crypto space is booming.",
    )
    passed, reason = keyword_filter(
        item, must_any=["Trump"], must_exclude=["crypto"]
    )
    assert passed is False
    assert reason and reason.startswith("blacklist:")


def test_keyword_filter_requires_whitelist_hit():
    item = _make_item(
        title="Coffee prices hit new high",
        clean_markdown="Arabica coffee futures...",
    )
    passed, reason = keyword_filter(
        item, must_any=["OpenAI", "Anthropic"], must_exclude=[]
    )
    assert passed is False
    assert reason == "no_keyword_match"


def test_keyword_filter_case_insensitive():
    item = _make_item(
        title="openAI ships GPT-5",
        clean_markdown="OpenAI announced...",
    )
    passed, reason = keyword_filter(
        item, must_any=["OpenAI"], must_exclude=[]
    )
    assert passed is True
    assert reason is None


# ------- min_length_filter -------

def test_min_length_filter_drops_short():
    item = _make_item()
    item.word_count = 42
    passed, reason = min_length_filter(item, 100)
    assert passed is False
    assert reason.startswith("too_short[")


def test_min_length_filter_passes_long_enough():
    item = _make_item()
    item.word_count = 250
    passed, reason = min_length_filter(item, 100)
    assert passed is True
    assert reason is None


# ------- resolve_min_word_count / tier-aware filter (Phase 8.9) -------

def test_resolve_int_config_is_backward_compatible():
    # 舊 config 仍可用整數
    assert resolve_min_word_count(80, "article") == 80
    assert resolve_min_word_count(80, "social") == 80


def test_resolve_dict_config_picks_by_source_type():
    cfg = {"default": 100, "article": 200, "social": 40, "video": 30}
    assert resolve_min_word_count(cfg, "article") == 200
    assert resolve_min_word_count(cfg, "social") == 40
    assert resolve_min_word_count(cfg, "video") == 30


def test_resolve_dict_config_fallback_to_default():
    cfg = {"default": 100, "article": 200}
    # forum 不在 dict 裡 → 走 default
    assert resolve_min_word_count(cfg, "forum") == 100


def test_resolve_invalid_shape_gives_safe_fallback():
    assert resolve_min_word_count(None, "article") == 100
    assert resolve_min_word_count("hundred", "article") == 100
    # bool 不是合法門檻
    assert resolve_min_word_count(True, "article") == 100


def test_min_length_filter_tiered_social_passes_short():
    """Phase 8.9 重點：Reddit post 80 字要通過 social=40 的門檻。"""
    item = _make_item(source_type="social")
    item.word_count = 80
    cfg_value = {"default": 200, "social": 40}
    passed, reason = min_length_filter(item, cfg_value)
    assert passed is True, f"expected pass (social=40), got {reason}"


def test_min_length_filter_tiered_article_still_strict():
    """同一篇 80 字若是 article 類型則仍被 drop，門檻=200。"""
    item = _make_item(source_type="article")
    item.word_count = 80
    cfg_value = {"default": 200, "social": 40, "article": 200}
    passed, reason = min_length_filter(item, cfg_value)
    assert passed is False
    assert reason.startswith("too_short[article]")
    assert "<200" in reason


def test_min_length_filter_drop_reason_includes_source_type():
    item = _make_item(source_type="video")
    item.word_count = 10
    passed, reason = min_length_filter(item, {"default": 100, "video": 30})
    assert passed is False
    assert reason.startswith("too_short[video]")


# ------- clean_and_filter (integration of the above) -------

def test_clean_and_filter_happy_path(sample_html_en, minimal_config):
    item = _make_item()
    updated, passed, reason = asyncio.run(
        clean_and_filter(item, sample_html_en, minimal_config)
    )
    assert passed is True, f"expected pass, got reason={reason}"
    assert updated.word_count > 0
    assert updated.og_image_url == "https://example.com/og-claude-4-6.png"


def test_clean_and_filter_drops_paywall(sample_html_paywall, minimal_config):
    item = _make_item()
    updated, passed, reason = asyncio.run(
        clean_and_filter(item, sample_html_paywall, minimal_config)
    )
    assert passed is False
    assert reason in {"extract_failed", "too_short"} or \
        (reason and (reason.startswith("too_short") or reason == "extract_failed"))


def test_clean_and_filter_preserves_prefilled_clean_markdown(minimal_config):
    """YouTube 路徑：clean_markdown 已預填，不應被 trafilatura 覆寫。"""
    item = _make_item(
        source_type="video",
        title="Jensen Huang interview on Apple Intelligence",
        clean_markdown=(
            "YouTube Interview Description:\nJensen Huang on NVIDIA Blackwell "
            "and the future of Apple Intelligence. A long-form CNBC interview "
            "discussing benchmark performance, manufacturing scale at TSMC, and "
            "agentic workloads on GPU clusters. Interview length about 45 minutes."
        ),
    )
    updated, passed, reason = asyncio.run(
        clean_and_filter(item, "", minimal_config)
    )
    assert updated.clean_markdown.startswith("YouTube Interview Description:")
    # word_count 重算後即使字數不夠，也要給出明確的 too_short 理由
    if not passed:
        assert reason and reason.startswith("too_short["), f"got {reason}"


def test_clean_and_filter_youtube_passes_with_tiered_threshold():
    """Phase 8.9 修復：YouTube description 短，但 video 門檻 30 放寬後能通過。"""
    item = _make_item(
        source_type="video",
        title="Jensen Huang on Blackwell",
        clean_markdown=(
            "YouTube Interview Description:\nJensen Huang discusses NVIDIA Blackwell "
            "and Apple Intelligence on CNBC. 40% better tokens per watt vs Hopper."
        ),
    )
    # 模擬 Phase 8.9 的新 config：min_word_count 是 dict 並含 video=30
    cfg = {
        "filters": {
            "min_word_count": {"default": 100, "article": 200, "video": 30, "social": 40},
            "max_age_hours": 168,
            "duplicate_similarity": 0.85,
        },
        "keywords": {
            "must_include_any": ["NVIDIA", "Jensen Huang"],
            "must_exclude_any": [],
        },
    }
    updated, passed, reason = asyncio.run(clean_and_filter(item, "", cfg))
    assert passed is True, f"expected YouTube to pass video=30, got reason={reason}"


# ------- Phase 8.16 · extract_og_video / _classify_video_url -------

def test_classify_video_url_direct_mp4():
    url, is_direct = _classify_video_url(
        "https://cdn.example.com/clips/scene.mp4"
    )
    assert url == "https://cdn.example.com/clips/scene.mp4"
    assert is_direct is True


def test_classify_video_url_direct_mp4_with_query_string():
    # 很多 CDN 會掛 signed URL 的 ?token=... 在副檔名後面 — 不應誤判為非直鏈
    url, is_direct = _classify_video_url(
        "https://cdn.example.com/clips/scene.mp4?token=abc123&ts=9999"
    )
    assert is_direct is True
    # URL 原樣保留（含 query），不要偷偷截掉 publisher 需要的簽章
    assert url.endswith("?token=abc123&ts=9999")


def test_classify_video_url_embed_is_not_direct():
    # YouTube embed iframe URL — 拿到 Meta 會被拒，is_direct 必須 False
    url, is_direct = _classify_video_url(
        "https://www.youtube.com/embed/dQw4w9WgXcQ"
    )
    assert url is not None
    assert is_direct is False


def test_classify_video_url_hls_m3u8_flagged_as_direct():
    # HLS 雖然 Meta 不收，但我們標 direct=True 以便 publisher 顯式 reject + log
    url, is_direct = _classify_video_url(
        "https://stream.example.com/live/playlist.m3u8"
    )
    assert is_direct is True


def test_classify_video_url_empty_inputs():
    assert _classify_video_url(None) == (None, False)
    assert _classify_video_url("") == (None, False)
    assert _classify_video_url("   ") == (None, False)


# extract_og_video

def test_extract_og_video_prefers_secure_url():
    html = """
    <html><head>
      <meta property="og:video" content="http://cdn.example.com/a.mp4">
      <meta property="og:video:url" content="http://cdn.example.com/b.mp4">
      <meta property="og:video:secure_url" content="https://cdn.example.com/c.mp4">
    </head><body></body></html>
    """
    url, is_direct = extract_og_video(html)
    assert url == "https://cdn.example.com/c.mp4"
    assert is_direct is True


def test_extract_og_video_falls_back_to_url_then_plain():
    html_with_url = """
    <html><head>
      <meta property="og:video" content="http://cdn.example.com/plain.mp4">
      <meta property="og:video:url" content="http://cdn.example.com/url.mp4">
    </head></html>
    """
    url, _ = extract_og_video(html_with_url)
    assert url == "http://cdn.example.com/url.mp4"

    html_only_plain = """
    <html><head>
      <meta property="og:video" content="http://cdn.example.com/plain.mp4">
    </head></html>
    """
    url2, _ = extract_og_video(html_only_plain)
    assert url2 == "http://cdn.example.com/plain.mp4"


def test_extract_og_video_twitter_player_stream():
    html = """
    <html><head>
      <meta name="twitter:player:stream" content="https://video.twimg.com/ext/mediaXYZ.mp4">
    </head></html>
    """
    url, is_direct = extract_og_video(html)
    assert url == "https://video.twimg.com/ext/mediaXYZ.mp4"
    assert is_direct is True


def test_extract_og_video_html_video_tag_src():
    html = """
    <html><body>
      <video src="https://cdn.example.com/inline.mp4" controls></video>
    </body></html>
    """
    url, is_direct = extract_og_video(html)
    assert url == "https://cdn.example.com/inline.mp4"
    assert is_direct is True


def test_extract_og_video_html_video_source_child():
    # 很多 site 不放 src 在 <video>，而是掛一個或多個 <source>
    html = """
    <html><body>
      <video controls>
        <source src="https://cdn.example.com/webm.webm" type="video/webm">
        <source src="https://cdn.example.com/fallback.mp4" type="video/mp4">
      </video>
    </body></html>
    """
    url, is_direct = extract_og_video(html)
    # 第一個 <source> 就該命中（我們現在是「找到就回」策略）
    assert url == "https://cdn.example.com/webm.webm"
    assert is_direct is True


def test_extract_og_video_embed_url_returns_not_direct():
    # YouTube 頁面的 og:video 常常是 embed iframe URL，不是 .mp4
    html = """
    <html><head>
      <meta property="og:video:url" content="https://www.youtube.com/embed/abc">
    </head></html>
    """
    url, is_direct = extract_og_video(html)
    assert url == "https://www.youtube.com/embed/abc"
    assert is_direct is False


def test_extract_og_video_no_video_returns_none():
    html = """
    <html><head>
      <meta property="og:image" content="https://cdn.example.com/cover.jpg">
    </head><body><p>no video here</p></body></html>
    """
    url, is_direct = extract_og_video(html)
    assert url is None
    assert is_direct is False


def test_extract_og_video_handles_empty_and_bad_html():
    assert extract_og_video("") == (None, False)
    # bs4 的 html.parser 對亂字串不會爆，但為了覆蓋防呆分支也寫一個
    url, is_direct = extract_og_video("<not really html>>><<")
    assert is_direct is False


def test_clean_and_filter_populates_og_video(minimal_config):
    """clean_and_filter 應把 og:video 寫回 NewsItem.og_video_url / og_video_is_direct。"""
    html = """
    <html><head>
      <meta property="og:title" content="Breaking: new demo">
      <meta property="og:image" content="https://example.com/cover.jpg">
      <meta property="og:video:secure_url" content="https://cdn.example.com/demo.mp4">
    </head><body>
      <article>
        <h1>Anthropic launches Claude Sonnet 4.6</h1>
        <p>Jensen Huang joined the keynote. NVIDIA Blackwell GPUs are now shipping
        at scale. The new model delivers substantial throughput gains on standard
        coding benchmarks, with agentic task success rate up roughly 40%.</p>
        <p>OpenAI and Anthropic both released updates this week.</p>
      </article>
    </body></html>
    """
    item = _make_item()
    updated, passed, reason = asyncio.run(clean_and_filter(item, html, minimal_config))
    assert updated.og_video_url == "https://cdn.example.com/demo.mp4"
    assert updated.og_video_is_direct is True


def test_clean_and_filter_does_not_overwrite_prefilled_video():
    """fetcher 已從 RSS enclosure 預填的 og_video_url 不應被 HTML 抽取的結果覆蓋。"""
    html = """
    <html><head>
      <meta property="og:video:url" content="https://cdn.example.com/html-side.mp4">
    </head><body><article><p>some body</p></article></body></html>
    """
    item = _make_item()
    item.og_video_url = "https://cdn.enclosure.example/podcast.mp3"
    item.og_video_is_direct = False  # .mp3 不在 direct video ext 列表裡
    cfg = {
        "filters": {
            "min_word_count": 1,
            "max_age_hours": 168,
            "duplicate_similarity": 0.85,
        },
        "keywords": {"must_include_any": [], "must_exclude_any": []},
    }
    updated, _, _ = asyncio.run(clean_and_filter(item, html, cfg))
    # 預填的 enclosure URL 要保留，不被 HTML 的 og:video 蓋掉
    assert updated.og_video_url == "https://cdn.enclosure.example/podcast.mp3"
