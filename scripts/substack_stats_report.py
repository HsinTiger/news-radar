#!/usr/bin/env python3
"""Substack 成效報表（2026-10-17）：按專欄、發文時間實驗組別比較。

主要指標是參與率、看完、新訂閱、退訂、分享。開信率只當參考，因為 Apple Mail
隱私保護會自動「開信」、把開信率灌水。只讀 Substack 後台資料，不改任何東西。
發文時間實驗的組別記在 data/substack_drafts/.daily_published.json 的 send_arm。

2026-11-20 起多算三件事（讀者數據迴圈，見 substack_radar/reader_signals.py）：
- 每篇「比同期多幾成讀者」：不重複開信人數 ÷ 前後各 7 篇的中位數 − 1。開信率會被名單大小和 Apple 自動開信扭曲，
  這個數字扣掉了訂閱人數變化，才能拿不同月份的文章互相比。
- reader_calibration.json：多打開／少打開最多的文章，選題評審拿來當「我們讀者的真實口味」。
- title_arms.json：標題實驗兩組（有張力／中性）各自的成績，標題台用它做湯普森抽樣。

用法：.venv/bin/python scripts/substack_stats_report.py
輸出：data/substack_stats/latest.md、reader_calibration.json、title_arms.json
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))       # 要 import substack_radar.reader_signals
OUT = REPO / "data" / "substack_stats"
LEDGER = REPO / "data" / "substack_drafts" / ".daily_published.json"
COLUMNS = ("吹牛免稅", "賺錢有道", "智取未來", "Hsin選觀點", "主編精選")
TW = timezone(timedelta(hours=8))
DRAFTS = REPO / "data" / "substack_drafts"
TITLE_EXPERIMENT_START = "2026-11-21"      # 標題實驗從這天的文章開始算
SETTLE_DAYS = 3                            # 開信要兩三天才收得差不多，太新的不算


def detrend(rows: list[dict], *, window: int = 7, min_sent: int = 50, now: datetime | None = None) -> list[dict]:
    """每篇加上 res＝不重複開信人數 ÷ 前後各 window 篇的中位數 − 1。名單太小、還在累積的不算。"""
    now = now or datetime.now(TW)
    ok = sorted((r for r in rows if (r.get("sent") or 0) >= min_sent and r["when"] <= now - timedelta(days=SETTLE_DAYS)),
                key=lambda r: r["when"])
    out = []
    for i, r in enumerate(ok):
        nb = [x.get("opened") or 0 for j, x in enumerate(ok[max(0, i - window): i + window + 1], start=max(0, i - window))
              if j != i]
        exp = st.median(nb) if nb else 0
        if exp:
            out.append({**r, "res": (r.get("opened") or 0) / exp - 1})
    return out


def _headline(title: str) -> str:
    for c in COLUMNS:
        title = title.replace(f"｜{c}", "").replace(f"{c}｜", "")
    return title.strip()


def local_meta() -> dict:
    """Substack post id → 本機 metadata 的 source（台灣關卡、標題實驗分組）。"""
    out = {}
    for m in DRAFTS.glob("*/*/metadata.json"):
        try:
            d = json.loads(m.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("substack_draft_id"):
            out[str(d["substack_draft_id"])] = d.get("source") or {}
    return out


def write_reader_files(scored: list[dict], *, today: datetime | None = None, n: int = 8) -> dict:
    """寫 reader_calibration.json 與 title_arms.json，回兩份內容（報表用）。"""
    from substack_radar.reader_signals import ARMS, title_style

    today = today or datetime.now(TW)
    year = [r for r in scored if r["when"] >= today - timedelta(days=365)]
    ranked = sorted(year, key=lambda r: r["res"], reverse=True)
    pick = lambda xs: [{"title": _headline(r["title"]), "res": round(r["res"], 3), "date": f"{r['when']:%Y-%m-%d}"} for r in xs]
    calibration = {"updated": f"{today:%Y-%m-%d}", "top": pick(ranked[:n]), "bottom": pick(ranked[::-1][:n])}
    arms = {}
    for arm in ARMS:
        xs = [r["res"] for r in scored if f"{r['when']:%Y-%m-%d}" >= TITLE_EXPERIMENT_START
              and (r.get("title_style") or title_style(_headline(r["title"]))) == arm]
        arms[arm] = {"n": len(xs), "mean": round(st.mean(xs), 4) if xs else 0.0,
                     "sd": round(st.stdev(xs), 4) if len(xs) > 1 else None}
    title_arms = {"updated": f"{today:%Y-%m-%d}", "since": TITLE_EXPERIMENT_START, "arms": arms}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "reader_calibration.json").write_text(json.dumps(calibration, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "title_arms.json").write_text(json.dumps(title_arms, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"calibration": calibration, "title_arms": title_arms}


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

    meta = local_meta()
    rows = []
    for p in fetch_posts(api, base):
        s = dict(p.get("stats") or {})
        s["title"] = p.get("title") or ""
        s["column"] = column_of(s["title"])
        s["arm"] = arms.get(str(p.get("id")))
        s["when"] = datetime.fromisoformat(p["post_date"].replace("Z", "+00:00")).astimezone(TW)
        src = meta.get(str(p.get("id"))) or {}
        s["title_style"], s["tw_gate"] = src.get("title_style"), src.get("tw_gate")
        s["comments_n"] = p.get("comment_count") or 0
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

    scored = detrend(rows)
    files = write_reader_files(scored)
    month = [r for r in scored if r["when"] >= since]
    lines += ["", "## 近 30 天每篇比同期多幾成讀者（已扣掉訂閱人數變化）"]
    lines += [f"- {r['res']:+.0%}　{r.get('opened') or 0} 人　{r['when']:%m-%d} {_headline(r['title'])[:30]}"
              for r in sorted(month, key=lambda r: r["when"], reverse=True)] or ["- （還沒有滿三天的文章）"]
    outside = [max((r.get("views") or 0) - (r.get("opens") or 0), 0) for r in recent]
    spread = [r for r in recent if (r.get("shares") or 0) + (r.get("restacks") or 0) + (r.get("signups") or 0)
              + (r.get("comments_n") or 0) > 0]
    lines += ["", "## 有沒有走出信箱（近 30 天）",
              f"- 每篇從信箱以外來的瀏覽：平均 {st.mean(outside):.1f} 次" if outside else "- 每篇從信箱以外來的瀏覽：—",
              f"- 有人分享、轉貼、留言或帶進訂閱的文章：{len(spread)}/{len(recent)} 篇"]
    from substack_radar.reader_signals import ARM_LABEL
    lines += ["", f"## 標題實驗（{TITLE_EXPERIMENT_START} 起，有張力 vs 中性）"]
    for arm, a in files["title_arms"]["arms"].items():
        lines.append(f"- {ARM_LABEL[arm]}：{a['n']} 篇，平均比同期 {a['mean']:+.1%}" if a["n"] else f"- {ARM_LABEL[arm]}：還沒有資料")
    gate = defaultdict(list)
    for r in scored:
        if r.get("tw_gate"):
            gate[r["tw_gate"]].append(r["res"])
    if gate:
        lines += ["", "## 台灣關卡（選題時有沒有台灣讀者會問的問題）"]
        names = {"passed": "過關", "none_passed": "沒有一集過關", "skipped": "沒跑關卡"}
        lines += [f"- {names.get(k, k)}：{len(v)} 篇，平均比同期 {st.mean(v):+.1%}" for k, v in gate.items()]
    OUT.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    (OUT / "latest.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
