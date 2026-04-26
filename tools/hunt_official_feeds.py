#!/usr/bin/env python3
"""
News Radar · Tier-1 official feed URL hunt for the 8 that failed verification.

每個失敗的 feed 測 3-5 個候選 URL pattern。第一個能回 RSS/Atom + items > 0 的勝出。
403 改用更完整 browser headers 重試。bis_press parse_fail 改用 lxml recover mode。
"""
from __future__ import annotations

import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# Browser-grade headers — for IMF/OECD/anti-bot sites
FULL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.2 Safari/605.1.15"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# 8 failed feeds × 3-5 candidate URLs each
HUNT = {
    "whitehouse_briefings": [
        "https://www.whitehouse.gov/feed/",
        "https://www.whitehouse.gov/news/feed/",
        "https://www.whitehouse.gov/briefing-room/statements-releases/feed/",
        "https://www.whitehouse.gov/briefings-statements/feed/",
    ],
    "us_treasury_press": [
        "https://home.treasury.gov/news/press-releases/feed",
        "https://home.treasury.gov/news/feed",
        "https://home.treasury.gov/system/files/rss/press-releases.xml",
        "https://www.treasury.gov/rss/press-releases",
    ],
    "ecb_monetary_policy": [
        "https://www.ecb.europa.eu/rss/mopo.rss",
        "https://www.ecb.europa.eu/feeds/mopo.xml",
        "https://www.ecb.europa.eu/rss/fie.html",  # all financial-related
        "https://www.ecb.europa.eu/rss/all.html",   # all ECB
    ],
    "boj_releases_en": [
        "https://www.boj.or.jp/en/rss/whatsnew.xml",
        "https://www.boj.or.jp/en/whatsnew_e.xml",
        "https://www.boj.or.jp/en/rss/index.xml",
        "https://www.boj.or.jp/en/feed.xml",
    ],
    "imf_news": [
        "https://www.imf.org/en/News/RSS",  # original, retry with full headers
        "https://www.imf.org/external/np/sec/rss/news.xml",
        "https://www.imf.org/en/News/Articles/RSS",
        "https://www.imf.org/external/np/sec/rss/news.aspx",
    ],
    "world_bank_news": [
        "https://www.worldbank.org/en/news/all?format=rss",
        "https://www.worldbank.org/en/news.rss",
        "https://www.worldbank.org/en/news/all.rss",
        "https://www.worldbank.org/rss/news.xml",
    ],
    "oecd_news": [
        "https://www.oecd.org/news/rss.xml",  # original, retry with full headers
        "https://www.oecd.org/newsroom/feed/",
        "https://www.oecd.org/rss/news.xml",
        "https://oecdtv.oecd.org/news.rss",
    ],
    "bis_press": [
        "https://www.bis.org/list/press_releases/index.rss",  # original, retry with lxml recover
        "https://www.bis.org/rss/press_releases.rss",
        "https://www.bis.org/list/all/index.rss",
        "https://www.bis.org/list/press_releases/from_01012024/index.rss",
    ],
}


def try_parse(body: bytes, allow_recover: bool = False) -> tuple[bool, int, str]:
    """Returns (parsed_ok, item_count, err_msg)."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        if not allow_recover:
            return False, 0, str(e)[:60]
        # Try lxml recover mode if available
        try:
            from lxml import etree
            parser = etree.XMLParser(recover=True)
            root_lxml = etree.fromstring(body, parser=parser)
            items = root_lxml.findall(".//item") + root_lxml.findall(".//{http://www.w3.org/2005/Atom}entry")
            return True, len(items), f"lxml_recover_used: {str(e)[:50]}"
        except ImportError:
            return False, 0, f"parse_err (lxml not installed): {str(e)[:50]}"
        except Exception as ee:
            return False, 0, f"recover_failed: {str(ee)[:50]}"
    items = root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry")
    return True, len(items), ""


def hunt_one(feed_name: str, candidates: list[str], allow_recover: bool) -> tuple[str, str]:
    """Try each candidate. Return (winning_url_or_empty, summary_line)."""
    notes = []
    for url in candidates:
        req = urllib.request.Request(url, headers=FULL_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                status = resp.status
                ct = resp.headers.get("Content-Type", "?").split(";")[0].strip()
                body = resp.read()
        except urllib.error.HTTPError as e:
            notes.append(f"  [{e.code}] {url}")
            continue
        except Exception as e:
            notes.append(f"  [{type(e).__name__}] {url}: {str(e)[:40]}")
            continue
        ok, items, err = try_parse(body, allow_recover=allow_recover)
        if ok and items > 0:
            note = f"  [200 ✓] {url}  items={items}  {ct}"
            if err:
                note += f"  ({err})"
            notes.append(note)
            return url, "\n".join(notes)
        elif ok:
            notes.append(f"  [200 0-items] {url}  {ct}")
        else:
            notes.append(f"  [200 parse-fail] {url}  err={err}")
    return "", "\n".join(notes)


def main() -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"=== Tier 1 URL hunt · {now} ===\n")

    found = {}
    for feed_name, candidates in HUNT.items():
        # bis_press: 原本 parse_fail，允許 lxml recover
        allow_recover = (feed_name == "bis_press")
        winning_url, log = hunt_one(feed_name, candidates, allow_recover=allow_recover)
        if winning_url:
            print(f"✅ {feed_name}")
            print(log)
            found[feed_name] = winning_url
        else:
            print(f"❌ {feed_name}  all {len(candidates)} candidates failed")
            print(log)
        print()

    print(f"=== Summary ===  Found alt for {len(found)}/{len(HUNT)} feeds")
    if found:
        print("\n=== Winning URLs (paste back to me) ===")
        for n, u in found.items():
            print(f"{n}: {u}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
