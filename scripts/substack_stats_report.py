#!/usr/bin/env python3
"""Substack 成效報表（2026-10-17）：按專欄、發文時間實驗組別比較。

主要指標是參與率、看完、新訂閱、退訂、分享。開信率只當參考，因為 Apple Mail
隱私保護會自動「開信」、把開信率灌水。只讀 Substack 後台資料，不改任何東西。
發文時間實驗的組別記在 data/substack_drafts/.daily_published.json 的 send_arm。

用法：.venv/bin/python scripts/substack_stats_report.py
輸出：data/substack_stats/latest.md
"""
from __future__ import annotations

import json
import os
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "substack_stats"
LEDGER = REPO / "data" / "substack_drafts" / ".daily_published.json"
COLUMNS = ("吹牛免稅", "賺錢有道", "智取未來", "Hsin選觀點", "主編精選")
TW = timezone(timedelta(hours=8))


def column_of(title: str) -> str:
    return next((c for c in COLUMNS if c in (title or "")), "其他")


def fetch_posts(api, base: str) -> list[dict]:
    posts: list[dict] = []
    for off in range(0, 1000, 50):
        r = api._session.get(f"{base}/api/v1/post_management/published?offset={off}&limit=50"
                             "&order_by=post_date&order_direction=desc", timeout=30).json()
        posts += r.get("posts") or []
        if len(r.get("posts") or []) < 50:
            break
    return posts


def summarize(group: list[dict]) -> str:
    def mean(key):
        xs = [p[key] for p in group if isinstance(p.get(key), (int, float))]
        return f"{st.mean(xs):.1%}" if xs else "—"
    tot = lambda key: sum(p.get(key) or 0 for p in group)
    return (f"{len(group)} 篇｜參與率 {mean('engagement_rate')}｜開信率（參考）{mean('open_rate')}｜"
            f"新訂閱 {tot('signups')}｜退訂 {tot('unsubscribes')}｜分享 {tot('shares')}｜"
            f"看完 {tot('subscribers_finished_post')}")


def main() -> int:
    load_dotenv(REPO / ".env", override=True)
    from substack import Api

    base = os.environ["SUBSTACK_PUBLICATION_URL"].rstrip("/")
    api = Api(cookies_string=os.environ["SUBSTACK_COOKIES_STRING"], publication_url=base)
    try:
        arms = {str(v.get("draft_id")): v.get("send_arm") for v in json.loads(LEDGER.read_text(encoding="utf-8")).values()}
    except Exception:
        arms = {}

    rows = []
    for p in fetch_posts(api, base):
        s = dict(p.get("stats") or {})
        s["title"] = p.get("title") or ""
        s["column"] = column_of(s["title"])
        s["arm"] = arms.get(str(p.get("id")))
        s["when"] = datetime.fromisoformat(p["post_date"].replace("Z", "+00:00")).astimezone(TW)
        rows.append(s)

    since = datetime.now(TW) - timedelta(days=30)
    recent = [r for r in rows if r["when"] >= since]
    lines = [f"# Substack 成效報表 {datetime.now(TW):%Y-%m-%d}", "",
             f"訂閱名單（最近一篇寄出）：{rows[0].get('sent') if rows else '—'} 人", "",
             "## 近 30 天，按專欄"]
    by_col = defaultdict(list)
    for r in recent:
        by_col[r["column"]].append(r)
    lines += [f"- {c}：{summarize(g)}" for c, g in by_col.items()] or ["- （沒有文章）"]
    lines += ["", "## 發文時間實驗（10/19–11/15，按週輪流早上／傍晚）"]
    by_arm = defaultdict(list)
    for r in rows:
        if r["arm"]:
            by_arm[r["arm"]].append(r)
    lines += [f"- {a}：{summarize(g)}" for a, g in by_arm.items()] or ["- 還沒有實驗資料"]
    lines += ["", "## 全部文章（歷史基準）", f"- {summarize(rows)}"]
    OUT.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    (OUT / "latest.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
