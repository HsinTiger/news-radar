#!/usr/bin/env python3
"""
News Radar · Feeds 存活探測器
==================================

讀 `config/config.yaml`，逐一對每個 RSS feed 做：

  1. GET feed URL → 回應碼、feed entry 數量
  2. 若 feed OK，取前 3 個 entry 的文章 URL，逐一探測 HTML 抓取成功率
  3. 標記「已死」feed（Bloomberg bot 牆、OpenAI /index/ 404、Medium login wall 等）

產出 `data/01_harvest/feeds_health.md` + stdout 即時進度。

零 token，但會真的打網路（~ N 個 feed + 3N 篇文章），執行時間 30-90 秒。

用法：
    python tools/diagnose_feeds.py
    python tools/diagnose_feeds.py --samples 5        # 每個 feed 探測 5 篇
    python tools/diagnose_feeds.py --timeout 10       # 單請求 timeout 秒數
    python tools/diagnose_feeds.py --feed OpenAI      # 只探測名稱含 OpenAI 的 feed
"""
from __future__ import annotations
import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import feedparser
import yaml


_BASE = Path(__file__).resolve().parent.parent
DEFAULT_CFG = _BASE / "config" / "config.yaml"
DEFAULT_OUT = _BASE / "data" / "01_harvest" / "feeds_health.md"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")


async def probe_feed(client: httpx.AsyncClient, feed: dict, samples: int,
                     timeout: float) -> dict:
    name = feed["name"]
    url = feed["url"]
    tier = feed.get("tier", "secondary")
    result = {
        "name": name,
        "tier": tier,
        "url": url,
        "feed_status": None,
        "feed_error": None,
        "entries": 0,
        "samples_total": 0,
        "samples_ok": 0,
        "sample_errors": [],
        "verdict": "?",
    }

    print(f"[feeds] → {name}")

    # 1. Feed URL 本身
    try:
        r = await client.get(url, timeout=timeout, follow_redirects=True,
                             headers={"User-Agent": UA})
        result["feed_status"] = r.status_code
        if r.status_code >= 400:
            result["feed_error"] = f"HTTP {r.status_code}"
            result["verdict"] = "DEAD_FEED"
            print(f"       ❌ feed HTTP {r.status_code}")
            return result
        feed_body = r.text
    except Exception as e:
        result["feed_error"] = str(e)[:120]
        result["verdict"] = "DEAD_FEED"
        print(f"       ❌ feed error: {e}")
        return result

    # 2. 解析 entry
    parsed = feedparser.parse(feed_body)
    result["entries"] = len(parsed.entries)
    if not parsed.entries:
        result["verdict"] = "EMPTY_FEED"
        print(f"       ⚠️  feed 解析結果為空")
        return result

    # 3. 抽 samples 篇文章探測
    urls = [e.get("link") for e in parsed.entries[:samples] if e.get("link")]
    result["samples_total"] = len(urls)
    for u in urls:
        try:
            r = await client.get(u, timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": UA,
                                          "Accept": "text/html,application/xhtml+xml"})
            if r.status_code < 400 and len(r.text) > 500:
                result["samples_ok"] += 1
            else:
                result["sample_errors"].append(f"HTTP {r.status_code} @ {u[:60]}")
        except Exception as e:
            result["sample_errors"].append(f"{type(e).__name__} @ {u[:60]}")

    # 4. Verdict
    if result["samples_total"] == 0:
        result["verdict"] = "FEED_ONLY_NO_ARTICLES"
    elif result["samples_ok"] == 0:
        result["verdict"] = "ALL_ARTICLES_BLOCKED"
    elif result["samples_ok"] < result["samples_total"] / 2:
        result["verdict"] = "PARTIALLY_BLOCKED"
    else:
        result["verdict"] = "HEALTHY"

    print(f"       feed HTTP {result['feed_status']}, entries={result['entries']}, "
          f"samples {result['samples_ok']}/{result['samples_total']} → {result['verdict']}")
    return result


async def run(cfg_path: Path, feed_filter: str | None, samples: int,
              timeout: float, out_path: Path) -> None:
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    feeds = cfg.get("feeds", [])
    if feed_filter:
        feeds = [f for f in feeds if feed_filter.lower() in f["name"].lower()]
    if not feeds:
        print("[feeds] ❌ 沒有符合篩選條件的 feed", file=sys.stderr)
        sys.exit(1)

    print(f"[feeds] 探測 {len(feeds)} 個 feed，每個抓 {samples} 篇 sample\n")

    async with httpx.AsyncClient() as client:
        # 依序跑避免一次打爆同一站
        results = []
        for f in feeds:
            results.append(await probe_feed(client, f, samples, timeout))

    # Markdown report
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines: list[str] = []
    P = lines.append
    P("# News Radar · Feeds 存活報告")
    P("")
    P(f"- 產生時間：`{now}`")
    P(f"- 設定檔　：`{cfg_path}`")
    P(f"- 每 feed 抽樣篇數：{samples}")
    P("")

    P("## 1. Verdict 總覽")
    P("")
    counter: dict[str, int] = {}
    for r in results:
        counter[r["verdict"]] = counter.get(r["verdict"], 0) + 1
    for v, n in sorted(counter.items(), key=lambda x: -x[1]):
        P(f"- `{v}`: {n}")
    P("")

    P("## 2. 每個 Feed 詳情")
    P("")
    P("| Tier | Feed | Feed HTTP | Entries | Samples | Verdict |")
    P("|---|---|---:|---:|---:|---|")
    for r in results:
        samp = f"{r['samples_ok']}/{r['samples_total']}" if r["samples_total"] else "-"
        P(f"| {r['tier']} | {r['name']} | {r['feed_status'] or 'ERR'} | "
          f"{r['entries']} | {samp} | `{r['verdict']}` |")
    P("")

    P("## 3. 錯誤明細（文章層）")
    P("")
    any_err = False
    for r in results:
        if r["sample_errors"]:
            any_err = True
            P(f"### {r['name']}")
            for e in r["sample_errors"]:
                P(f"- {e}")
            P("")
    if not any_err:
        P("_(所有 sample 均無錯誤)_")
        P("")

    P("---")
    P("> 產出工具：`tools/diagnose_feeds.py`")
    P("> 針對 `DEAD_FEED` / `ALL_ARTICLES_BLOCKED` 應直接從 config.yaml 移除；")
    P("> `PARTIALLY_BLOCKED` 可考慮保留但不列 primary。")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[feeds] ✅ 報告寫入：{out_path}")


def main():
    ap = argparse.ArgumentParser(description="Feeds 存活探測（打網路，但零 token）")
    ap.add_argument("--cfg", type=Path, default=DEFAULT_CFG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--feed", type=str, default=None,
                    help="只測名稱含此字串的 feed（case-insensitive）")
    ap.add_argument("--samples", type=int, default=3,
                    help="每個 feed 抽幾篇文章探測")
    ap.add_argument("--timeout", type=float, default=15.0,
                    help="單一 HTTP 請求 timeout 秒數")
    args = ap.parse_args()

    asyncio.run(run(args.cfg, args.feed, args.samples, args.timeout, args.out))


if __name__ == "__main__":
    main()
