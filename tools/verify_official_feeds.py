#!/usr/bin/env python3
"""
News Radar · Tier-1 official feed URL verification

Per spec/feeds_international_official_sources.md §6 step 1.
Reports HTTP status, parse success, item count for each Tier-1 URL.
"""
from __future__ import annotations

import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.2 Safari/605.1.15"
)

TIER_1_FEEDS = [
    ("whitehouse_briefings",  "https://www.whitehouse.gov/briefing-room/feed/"),
    ("us_treasury_press",     "https://home.treasury.gov/rss/press-releases"),
    ("fed_press_releases",    "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("fed_speeches",          "https://www.federalreserve.gov/feeds/speeches.xml"),
    ("ecb_press",             "https://www.ecb.europa.eu/rss/press.html"),
    ("ecb_monetary_policy",   "https://www.ecb.europa.eu/rss/mopo.html"),
    ("eu_commission_press",   "https://ec.europa.eu/commission/presscorner/api/rss?language=en"),
    ("boj_releases_en",       "https://www.boj.or.jp/en/rss/whatsnew_e.xml"),
    ("imf_news",              "https://www.imf.org/en/News/RSS"),
    ("world_bank_news",       "https://www.worldbank.org/en/news/rss"),
    ("who_news",              "https://www.who.int/rss-feeds/news-english.xml"),
    ("oecd_news",             "https://www.oecd.org/news/rss.xml"),
    ("bis_press",             "https://www.bis.org/list/press_releases/index.rss"),
]


def verify_one(name: str, url: str) -> tuple[str, dict]:
    """Returns (verdict, info). verdict ∈ {ok, no_items, parse_fail, http_err, exception}."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = resp.status
            ct = resp.headers.get("Content-Type", "?").split(";")[0].strip()
            body = resp.read()
    except urllib.error.HTTPError as e:
        return ("http_err", {"status": e.code, "reason": str(e.reason)[:60]})
    except Exception as e:
        return ("exception", {"err": f"{type(e).__name__}: {str(e)[:60]}"})

    info = {"status": status, "content_type": ct, "size": len(body)}

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        info["parse_err"] = str(e)[:60]
        return ("parse_fail", info)

    rss_items = root.findall(".//item")
    atom_entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    info["items"] = len(rss_items) + len(atom_entries)

    if info["items"] == 0:
        return ("no_items", info)
    return ("ok", info)


def main() -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"=== Tier 1 URL verification · {now} ===\n")

    ok_count = 0
    for name, url in TIER_1_FEEDS:
        verdict, info = verify_one(name, url)
        if verdict == "ok":
            ok_count += 1
            print(f"OK   {name:<24} {info['status']} {info['size']:>7}B  "
                  f"{info['content_type']:<24} items={info['items']}")
        elif verdict == "no_items":
            print(f"WARN {name:<24} {info['status']} {info['size']:>7}B  "
                  f"{info['content_type']:<24} parsed_but_zero_items")
        elif verdict == "parse_fail":
            print(f"WARN {name:<24} {info['status']} {info['size']:>7}B  "
                  f"parse_fail: {info['parse_err']}")
        elif verdict == "http_err":
            print(f"FAIL {name:<24} HTTP {info['status']:>4}  {info['reason']}")
        else:
            print(f"FAIL {name:<24} {info['err']}")

    print(f"\n=== Summary ===  OK={ok_count}/{len(TIER_1_FEEDS)}  "
          f"Fail={len(TIER_1_FEEDS) - ok_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
