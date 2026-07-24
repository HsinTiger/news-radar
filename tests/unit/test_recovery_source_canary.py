from __future__ import annotations

import asyncio

import httpx

from scripts.recovery_source_canary import _same_site, check_source, validate_payload


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Official</title>
<item><title>Policy update</title><link>https://example.gov.tw/1</link>
<pubDate>Fri, 24 Jul 2026 08:00:00 GMT</pubDate>
<description>Official source publishes a sufficiently detailed factual summary for readers and downstream attribution checks.</description>
</item></channel></rss>"""


def test_primary_record_canary_accepts_parseable_dated_body() -> None:
    result = validate_payload(
        {
            "name": "official",
            "url": "https://example.gov.tw/rss.xml",
            "source_type": "rss_summary",
            "effective_min_word_count": 20,
        },
        status_code=200,
        content_type="application/rss+xml",
        payload=RSS,
    )
    assert result["ok"] is True
    assert result["entries"] == 1


def test_primary_record_canary_rejects_title_only_summary() -> None:
    result = validate_payload(
        {
            "name": "official",
            "url": "https://example.gov.tw/rss.xml",
            "source_type": "rss_summary",
            "effective_min_word_count": 200,
        },
        status_code=200,
        content_type="application/rss+xml",
        payload=RSS,
    )
    assert result["ok"] is False
    assert any(error.startswith("summary_too_short:") for error in result["errors"])


def test_primary_record_canary_rejects_official_feed_pointing_offsite() -> None:
    payload = RSS.replace(
        b"https://example.gov.tw/1", b"https://untrusted.example/1"
    )
    result = validate_payload(
        {
            "name": "official",
            "url": "https://example.gov.tw/rss.xml",
            "source_type": "rss_summary",
            "effective_min_word_count": 20,
        },
        status_code=200,
        content_type="application/rss+xml",
        payload=payload,
    )
    assert result["ok"] is False
    assert "entry_0_offsite_link" in result["errors"]


def test_same_site_accepts_www_and_official_subdomains_only() -> None:
    assert _same_site(
        "https://www.fda.gov.tw/feed", "https://fda.gov.tw/TC/news.aspx"
    )
    assert _same_site(
        "https://fsc.gov.tw/feed", "https://www.fsc.gov.tw/ch/home.jsp"
    )
    assert not _same_site(
        "https://fsc.gov.tw/feed", "https://fsc.gov.tw.evil.example/post"
    )


def test_article_source_canary_checks_the_runtime_body_path() -> None:
    article_body = " ".join(
        [
            "Official inspection report names the product batch, measured result, "
            "responsible agency, public action, and verification date."
        ]
        * 12
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rss.xml":
            return httpx.Response(200, content=RSS)
        return httpx.Response(
            200,
            text=f"<html><body><article><h1>Policy update</h1><p>{article_body}</p></article></body></html>",
        )

    async def exercise() -> dict:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await check_source(
                client,
                {
                    "name": "official",
                    "url": "https://example.gov.tw/rss.xml",
                    "source_type": "article",
                    "effective_min_word_count": 80,
                },
            )

    result = asyncio.run(exercise())
    assert result["ok"] is True
    assert result["article_http_status"] == 200
    assert result["article_word_count"] >= 80
