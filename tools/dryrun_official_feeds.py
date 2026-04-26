#!/usr/bin/env python3
"""
News Radar · Tier-1 official feed dry-run harvest

Per spec/feeds_international_official_sources.md §6 step 4.
For each verified Tier-1 feed, fetch + parse + sample first item.
Output: HTTP status, parse success, item count, sample title/link/summary
length, has_media_content hint.

Doesn't write to DB (read-only validation). Uses feedparser (same library
as src/fetcher.py) so behavior matches what the real harvester will see
on next launchd cron tick.
"""
from __future__ import annotations

import urllib.request
import urllib.error
from datetime import datetime, timezone

import feedparser  # already a dep (used by src/fetcher.py)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.2 Safari/605.1.15"
)

VERIFIED_TIER1 = [
    ("fed_press_releases",  "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("fed_speeches",        "https://www.federalreserve.gov/feeds/speeches.xml"),
    ("ecb_press",           "https://www.ecb.europa.eu/rss/press.html"),
    ("eu_commission_press", "https://ec.europa.eu/commission/presscorner/api/rss?language=en"),
    ("who_news",            "https://www.who.int/rss-feeds/news-english.xml"),
    ("boj_releases_en",     "https://www.boj.or.jp/en/rss/whatsnew.xml"),
]


def dryrun_one(name: str, url: str) -> None:
    print(f"\n=== {name} ===")
    print(f"url: {url}")

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            status = resp.status
    except Exception as e:
        print(f"❌ fetch fail: {type(e).__name__}: {str(e)[:80]}")
        return

    parsed = feedparser.parse(body)
    items = parsed.entries
    feed_title = parsed.feed.get("title", "?") if hasattr(parsed, "feed") else "?"

    print(f"HTTP {status} | items {len(items)} | feed_title: {str(feed_title)[:60]}")

    if parsed.bozo:
        # feedparser sets bozo=1 when there were parser warnings
        bozo_err = str(parsed.get("bozo_exception", ""))[:80]
        print(f"⚠️  feedparser bozo flag set: {bozo_err}")

    if not items:
        print("⚠️  no items — feed parsed but empty")
        return

    first = items[0]
    title = first.get("title", "?")
    link = first.get("link", "?")
    published = first.get("published", "?")
    summary = first.get("summary", first.get("description", ""))

    # media hints (some feeds embed image URLs in entries)
    has_media_content = bool(first.get("media_content"))
    has_media_thumb = bool(first.get("media_thumbnail"))
    has_enclosure = bool(first.get("enclosures"))

    print(f"sample title    : {title[:80]}")
    print(f"sample link     : {link[:100]}")
    print(f"sample published: {published[:40]}")
    print(f"summary length  : {len(summary)} chars")
    if summary:
        # quick coherence check: 看 summary 是不是純文字 vs HTML 渣
        summary_head = summary[:160].replace("\n", " ")
        looks_html = "<" in summary_head and ">" in summary_head
        print(f"summary head    : {summary_head}")
        print(f"summary kind    : {'HTML markup' if looks_html else 'plain text'}")
    print(f"has media hints : media_content={has_media_content} "
          f"media_thumbnail={has_media_thumb} enclosure={has_enclosure}")


def main() -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"=== Tier 1 official feeds dry-run · {now} ===")
    print(f"Verifying {len(VERIFIED_TIER1)} feeds (read-only, no DB writes).")

    for name, url in VERIFIED_TIER1:
        dryrun_one(name, url)

    print("\n=== Dry-run complete ===")
    print("Notes for the report:")
    print("  - 'plain text' summary kind = cleaner.py + trafilatura should produce")
    print("    coherent clean_markdown.")
    print("  - 'HTML markup' summary kind = will be cleaned by trafilatura on full")
    print("    article fetch; RSS summary itself is not used as content.")
    print("  - feed bozo flag = feedparser saw recoverable parse warnings; usually OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
