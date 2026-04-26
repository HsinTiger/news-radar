#!/usr/bin/env python3
"""
News Radar · Tier-1 official feed dry-run with real trafilatura extraction

Per spec/feeds_international_official_sources.md §6 step 4 +
PM notes 2026-04-26 (note #4 — extraction quality requires real
trafilatura on sample article URL, not just RSS-level summary inspection).

For each verified Tier-1 feed:
  1. Fetch + parse RSS (read-only)
  2. Sample first item: title / link / RSS-summary
  3. **Fetch the sample article URL with browser UA**
  4. **Run trafilatura.extract on the response body**
  5. Report:
     - clean_markdown length (chars)
     - one-line verdict: "coherent text" / "HTML soup" / "nav noise / body too short"

Doesn't write to DB. Adds ~5-10s per feed for the article fetch.
Total runtime expected ~30-60s for 6 feeds.
"""
from __future__ import annotations

import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

import feedparser
import trafilatura

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


def http_fetch(url: str, timeout: int = 25) -> Optional[bytes]:
    """Fetch URL with browser UA. Returns body bytes, or None on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def extract_quality_verdict(clean_markdown: Optional[str], body: bytes,
                            link: str) -> tuple[int, str]:
    """Returns (markdown_len, verdict_one_liner).

    Verdict heuristics:
      - "coherent_text"   : len > 500 + ratio of CJK-or-Latin chars high
      - "short_body"      : len 100-500 (probably nav-heavy or summary-only)
      - "nav_noise"       : len < 100 (cleaner couldn't isolate body)
      - "extract_failed"  : trafilatura returned None
      - "pdf_skipped"     : link looks like PDF (trafilatura cannot extract)
      - "html_soup"       : trafilatura returned text but the body is mostly
                            HTML markup that didn't get cleaned (rare)
    """
    if link.lower().endswith(".pdf"):
        return (0, "pdf_skipped (link is .pdf, trafilatura does not handle binary)")

    if not clean_markdown:
        # Try to figure out why
        if not body:
            return (0, "extract_failed (HTTP fetch returned no body)")
        size = len(body)
        return (0, f"extract_failed (trafilatura returned None on {size}-byte body)")

    md_len = len(clean_markdown)

    # Quick sanity: how much of the markdown is whitespace + punctuation?
    stripped = "".join(clean_markdown.split())
    if not stripped:
        return (md_len, "nav_noise (markdown all whitespace)")

    # Coarse check: <-tag-like chars suggesting HTML wasn't fully stripped
    angle_density = (clean_markdown.count("<") + clean_markdown.count(">")) / max(1, md_len)
    if angle_density > 0.02:
        return (md_len, f"html_soup (angle-bracket density {angle_density:.1%}, suspect HTML leak)")

    if md_len < 100:
        return (md_len, "nav_noise (body too short, cleaner likely captured menu/footer)")
    if md_len < 500:
        return (md_len, "short_body (under 500 chars; usable but thin)")
    return (md_len, "coherent_text (>500 chars, low markup)")


def dryrun_one(name: str, url: str) -> None:
    print(f"\n=== {name} ===")
    print(f"feed url: {url}")

    rss_body = http_fetch(url)
    if not rss_body:
        print(f"❌ feed fetch fail")
        return

    parsed = feedparser.parse(rss_body)
    items = parsed.entries
    feed_title = parsed.feed.get("title", "?") if hasattr(parsed, "feed") else "?"
    print(f"feed parse: items={len(items)}  title={str(feed_title)[:60]}")
    if parsed.bozo:
        bozo_err = str(parsed.get("bozo_exception", ""))[:80]
        print(f"  ⚠️ feedparser bozo flag: {bozo_err}")

    if not items:
        print("(no items today — endpoint health OK, just no new release; not a fail per PM note 2026-04-26 #2)")
        return

    first = items[0]
    title = first.get("title", "?")
    link = first.get("link", "?")
    rss_summary = first.get("summary", first.get("description", ""))

    print(f"sample title    : {str(title)[:80]}")
    print(f"sample link     : {str(link)[:100]}")
    print(f"RSS summary len : {len(rss_summary)} chars")

    # === The actual extraction quality check ===
    # PM note 2026-04-26 #4: must run trafilatura, not just look at RSS summary.
    print(f"--- fetching sample article + trafilatura extract ---")
    article_body = http_fetch(link)
    if not article_body:
        print(f"❌ sample article fetch failed (HTTP error / timeout)")
        print(f"   verdict: extract_failed (article URL unreachable)")
        return

    body_size = len(article_body)
    print(f"article body    : {body_size:,} bytes")

    try:
        clean_markdown = trafilatura.extract(
            article_body,
            output_format="markdown",
            include_comments=False,
            include_tables=False,
            include_links=False,
            no_fallback=False,
        )
    except Exception as e:
        print(f"❌ trafilatura raised: {type(e).__name__}: {str(e)[:80]}")
        clean_markdown = None

    md_len, verdict = extract_quality_verdict(clean_markdown, article_body, link)
    print(f"clean_markdown  : {md_len:,} chars")
    print(f"verdict         : {verdict}")
    if clean_markdown and md_len > 0:
        head = clean_markdown[:200].replace("\n", " ")
        print(f"markdown head   : {head}")


def main() -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"=== Tier 1 official feeds dry-run · {now} ===")
    print(f"Verifying {len(VERIFIED_TIER1)} feeds (read-only, no DB writes).")
    print(f"Per PM note 2026-04-26 #4: running real trafilatura on sample item per feed.")
    print(f"Adds ~5-10s per feed for the article fetch + extract; total ~30-60s.")

    for name, url in VERIFIED_TIER1:
        dryrun_one(name, url)

    print("\n=== Dry-run complete ===")
    print("Verdict legend (PM note #4):")
    print("  coherent_text   = >500 chars, looks like real article body. cleaner can use.")
    print("  short_body      = 100-500 chars, usable but thin. likely RSS-summary level.")
    print("  nav_noise       = <100 chars, cleaner captured menu/footer instead of body.")
    print("                    → strong signal to switch source_type=rss_summary mode")
    print("  html_soup       = >2% angle bracket density, HTML leaked through.")
    print("                    → trafilatura config may need tuning")
    print("  extract_failed  = trafilatura returned None.")
    print("                    → sample item is not extractable; cleaner will silently drop")
    print("  pdf_skipped     = link ends with .pdf; trafilatura does not handle PDF.")
    print("                    → would need pypdf path in cleaner.py to use this feed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
