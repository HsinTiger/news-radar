#!/usr/bin/env python3
"""
News Radar · 單篇 Replay 工具
==================================

給一個 `news_item.id` 或 `url`，重跑完整清洗 + 過濾 pipeline，但：

  - 不寫回 DB（默認 dry-run）
  - 每一步都印出中間結果（raw HTML 長度、markdown 前 400 字、關鍵字命中、
    word_count、是否通過 filter）
  - 方便定位：「這篇為什麼被 drop」或「為什麼 word_count 算出來是 0」

零 token。單篇 pipeline debug 的瑞士刀。

用法：
    # 透過 DB id（最短前綴即可）
    python tools/replay_item.py ab12cd

    # 透過完整 URL
    python tools/replay_item.py https://example.com/article

    # 強制重抓 HTML（不使用 DB 中快取的 raw_html）
    python tools/replay_item.py ab12cd --refetch

    # 寫回 DB（用於修正 bug 後重新清洗）
    python tools/replay_item.py ab12cd --commit
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import sqlite3
import sys
from pathlib import Path

import httpx

_BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE))

from src import db as dbmod  # noqa: E402
from src.schema import NewsItem  # noqa: E402
from src.fetcher import fetch_html, load_config  # noqa: E402
from src.cleaner import clean_and_filter, extract_markdown, extract_og_image  # noqa: E402


def _banner(title: str):
    print()
    print("─" * 70)
    print(f"  {title}")
    print("─" * 70)


def _find_item(conn: sqlite3.Connection, needle: str) -> sqlite3.Row | None:
    # 1) 完整 URL
    if needle.startswith("http"):
        row = conn.execute(
            "SELECT * FROM news_items WHERE url = ? LIMIT 1", (needle,)
        ).fetchone()
        if row:
            return row
        # URL 沒直接命中就 hash 比對
        nid = hashlib.sha1(needle.encode("utf-8")).hexdigest()
        row = conn.execute(
            "SELECT * FROM news_items WHERE id = ? LIMIT 1", (nid,)
        ).fetchone()
        return row
    # 2) id 前綴
    row = conn.execute(
        "SELECT * FROM news_items WHERE id LIKE ? LIMIT 2",
        (needle + "%",),
    ).fetchone()
    return row


async def replay(needle: str, refetch: bool, commit: bool) -> int:
    cfg = load_config()
    dbmod.init_db()
    conn = dbmod.get_conn()

    _banner(f"Replay：{needle}")
    row = _find_item(conn, needle)
    if not row:
        print(f"❌ 找不到這個 item（id 前綴 / URL 都試過）：{needle}", file=sys.stderr)
        return 2

    print(f"  id           : {row['id']}")
    print(f"  feed         : {row['feed_name']} ({row['feed_tier']})")
    print(f"  title        : {row['title']}")
    print(f"  url          : {row['url']}")
    print(f"  status (舊)  : {row['status']}  drop_reason={row['drop_reason']}")
    print(f"  word_count(舊): {row['word_count']}")

    # 重建 NewsItem
    item = NewsItem(
        id=row["id"],
        feed_name=row["feed_name"],
        feed_tier=row["feed_tier"],
        url=row["url"],
        title=row["title"],
        published_at=row["published_at"],
        fetched_at=row["fetched_at"],
        language=row["language"],
        raw_html=row["raw_html"],
        clean_markdown=row["clean_markdown"],
        word_count=row["word_count"] or 0,
        og_image_url=row["og_image_url"],
        tags=[],
        status="fetched",
    )

    # Step A：HTML 來源
    _banner("Step A · 取得 HTML")
    html: str | None = None
    if item.raw_html and not refetch:
        html = item.raw_html
        print(f"  來源：DB raw_html ({len(html)} chars)")
    elif "youtube.com" in item.url:
        print("  來源：YouTube → 依現行 fetcher 邏輯，會走短路（不抓 HTML）")
        html = ""
    else:
        async with httpx.AsyncClient() as client:
            html = await fetch_html(client, item.url)
        if html is None:
            print("  ❌ 抓不到 HTML，replay 到此結束")
            conn.close()
            return 3
        print(f"  來源：即時 GET ({len(html)} chars)")

    # Step B：trafilatura
    _banner("Step B · trafilatura 萃取")
    if item.clean_markdown and "youtube.com" in item.url:
        print("  （YouTube 跳過 trafilatura，使用 RSS summary 作為 clean_markdown）")
        md, wc = item.clean_markdown, 0
    else:
        md, wc = extract_markdown(html or "")
        if md:
            preview = md[:400].replace("\n", "⏎")
            print(f"  word_count (重估): {wc}")
            print(f"  markdown preview (400 chars):")
            print(f"    {preview}")
        else:
            print("  ❌ trafilatura 抽不到東西")

    # Step C：og:image
    _banner("Step C · og:image")
    og = extract_og_image(html) if html else None
    print(f"  og_image_url: {og}")

    # Step D：完整 cleaner pipeline
    _banner("Step D · clean_and_filter")
    updated, passed, reason = await clean_and_filter(item, html or "", cfg)
    print(f"  passed={passed}  reason={reason}")
    print(f"  updated.word_count = {updated.word_count}")
    print(f"  updated.clean_markdown len = "
          f"{len(updated.clean_markdown) if updated.clean_markdown else 0}")
    print(f"  updated.og_image_url = {updated.og_image_url}")

    # Step E：寫回 DB？
    if commit:
        _banner("Step E · 寫回 DB（--commit）")
        if not passed:
            updated.status = "dropped"
            updated.drop_reason = reason
        else:
            updated.status = "fetched"
            updated.drop_reason = None
        # 先刪掉舊的再 upsert（upsert_news 遇到已存在會跳過）
        conn.execute("DELETE FROM news_items WHERE id = ?", (updated.id,))
        conn.commit()
        dbmod.upsert_news(conn, updated)
        print(f"  ✅ 已覆寫 DB，新 status={updated.status} drop_reason={updated.drop_reason}")
    else:
        _banner("Step E · dry-run（未寫 DB，加 --commit 才會覆寫）")

    conn.close()
    return 0 if passed else 1


def main():
    ap = argparse.ArgumentParser(description="單篇 replay：印出每一層中間結果")
    ap.add_argument("needle", help="news_item.id 前綴 或 完整 URL")
    ap.add_argument("--refetch", action="store_true",
                    help="忽略 DB 中的 raw_html，強制重新 GET")
    ap.add_argument("--commit", action="store_true",
                    help="把 replay 結果寫回 DB（預設 dry-run）")
    args = ap.parse_args()
    sys.exit(asyncio.run(replay(args.needle, args.refetch, args.commit)))


if __name__ == "__main__":
    main()
