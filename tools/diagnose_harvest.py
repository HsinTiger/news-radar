#!/usr/bin/env python3
"""
News Radar · Harvest 健康診斷器
==================================

讀 `data/01_harvest/news_radar.db`，產出一份 Markdown 報告，回答：

  1. 每個 feed 真的有抓到東西嗎？
  2. 每個 feed 的字數分布？（primary / secondary 分開看）
  3. drop_reasons 的長尾到底是哪些？
  4. YouTube 的 short-circuit 究竟落在哪些 item？（bug 證據）
  5. 近 7 天的 harvest 趨勢？

零 token、零網路。純 SQLite analytics。

用法：
    python tools/diagnose_harvest.py
    python tools/diagnose_harvest.py --db /path/to/news_radar.db
    python tools/diagnose_harvest.py --out data/01_harvest/diag_2026_04_19.md
"""
from __future__ import annotations
import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ---------- 設定 ----------
_BASE = Path(__file__).resolve().parent.parent
DEFAULT_DB = _BASE / "data" / "01_harvest" / "news_radar.db"
DEFAULT_OUT = _BASE / "data" / "01_harvest" / "diagnostic_report.md"

WORD_BUCKETS = [
    ("0-49", 0, 49),
    ("50-99", 50, 99),
    ("100-299", 100, 299),
    ("300-599", 300, 599),
    ("600-1199", 600, 1199),
    ("1200+", 1200, 10_000_000),
]


def _open(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"[diag] ❌ 找不到 DB：{db_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _bucket(wc: int) -> str:
    for label, lo, hi in WORD_BUCKETS:
        if lo <= wc <= hi:
            return label
    return "?"


def _iso_parse(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ---------- 各維度分析 ----------

def analyze_status(conn) -> dict:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM news_items GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def analyze_per_feed(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT feed_name, feed_tier, status,
               COUNT(*) AS n,
               AVG(word_count) AS avg_wc,
               MIN(word_count) AS min_wc,
               MAX(word_count) AS max_wc
          FROM news_items
         GROUP BY feed_name, feed_tier, status
         ORDER BY feed_name, status
        """
    ).fetchall()
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["feed_name"], r["feed_tier"])
        slot = agg.setdefault(key, {
            "feed_name": r["feed_name"],
            "tier": r["feed_tier"],
            "total": 0,
            "by_status": {},
            "wc_sum": 0.0,
            "wc_n": 0,
        })
        slot["total"] += r["n"]
        slot["by_status"][r["status"]] = r["n"]
        if r["avg_wc"] is not None:
            slot["wc_sum"] += (r["avg_wc"] or 0) * r["n"]
            slot["wc_n"] += r["n"]
    out: list[dict] = []
    for slot in agg.values():
        slot["avg_wc"] = round(slot["wc_sum"] / slot["wc_n"], 1) if slot["wc_n"] else 0
        ok = slot["by_status"].get("fetched", 0) + slot["by_status"].get("scored", 0) \
            + slot["by_status"].get("drafted", 0) + slot["by_status"].get("published", 0)
        slot["pass"] = ok
        slot["dropped"] = slot["by_status"].get("dropped", 0)
        slot["pass_rate"] = (ok / slot["total"] * 100) if slot["total"] else 0
        out.append(slot)
    out.sort(key=lambda x: (x["tier"], -x["total"]))
    return out


def analyze_drop_reasons(conn) -> list[tuple[str, int]]:
    rows = conn.execute(
        """
        SELECT drop_reason, COUNT(*) AS n
          FROM news_items
         WHERE status = 'dropped'
         GROUP BY drop_reason
         ORDER BY n DESC
        """
    ).fetchall()
    return [(r["drop_reason"] or "(null)", r["n"]) for r in rows]


def analyze_word_histogram(conn) -> dict[str, Counter]:
    rows = conn.execute(
        "SELECT feed_tier, word_count FROM news_items WHERE status != 'dropped'"
    ).fetchall()
    hist: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        hist[r["feed_tier"] or "?"][_bucket(int(r["word_count"] or 0))] += 1
    return hist


def analyze_youtube_shortcircuit(conn) -> list[dict]:
    """找出 clean_markdown 以 'YouTube Interview Description' 起頭的 item。
    這些是被 fetcher.py 短路、完全繞過 trafilatura 的證據。
    """
    rows = conn.execute(
        """
        SELECT id, feed_name, title, word_count, status, drop_reason, url
          FROM news_items
         WHERE clean_markdown LIKE 'YouTube Interview Description%'
         ORDER BY fetched_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def analyze_7day_trend(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT substr(fetched_at, 1, 10) AS day,
               COUNT(*) AS total,
               SUM(CASE WHEN status='dropped' THEN 1 ELSE 0 END) AS dropped,
               AVG(word_count) AS avg_wc
          FROM news_items
         WHERE fetched_at >= ?
         GROUP BY day
         ORDER BY day DESC
        """,
        ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),),
    ).fetchall()
    return [dict(r) for r in rows]


def analyze_latest_items(conn, n: int = 15) -> list[dict]:
    rows = conn.execute(
        """
        SELECT feed_name, feed_tier, title, status, word_count, drop_reason, fetched_at
          FROM news_items
         ORDER BY fetched_at DESC
         LIMIT ?
        """,
        (n,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- 輸出 ----------

def render_md(conn, db_path: Path) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines: list[str] = []
    P = lines.append

    P(f"# News Radar · Harvest 診斷報告")
    P("")
    P(f"- 產生時間：`{now}`")
    P(f"- 資料庫　：`{db_path}`")
    P("")

    # 1. 總覽
    status = analyze_status(conn)
    total = sum(status.values())
    P("## 1. 總覽")
    P("")
    P(f"- 總 item 數：**{total}**")
    if total:
        for k, v in sorted(status.items(), key=lambda x: -x[1]):
            P(f"  - `{k}`: {v}（{v/total*100:.1f}%）")
    P("")

    # 2. 每個 feed
    P("## 2. 每個 Feed 的健康度")
    P("")
    P("| Tier | Feed | 總數 | 通過 | 丟棄 | 通過率 | 平均字數 |")
    P("|---|---|---:|---:|---:|---:|---:|")
    for f in analyze_per_feed(conn):
        P(f"| {f['tier']} | {f['feed_name']} | {f['total']} | {f['pass']} | "
          f"{f['dropped']} | {f['pass_rate']:.0f}% | {f['avg_wc']:.0f} |")
    P("")

    # 3. Drop reasons
    P("## 3. Drop 原因分布（長尾）")
    P("")
    reasons = analyze_drop_reasons(conn)
    if reasons:
        P("| Reason | 次數 |")
        P("|---|---:|")
        for reason, n in reasons:
            safe = reason.replace("|", "\\|")
            P(f"| `{safe}` | {n} |")
    else:
        P("_(沒有任何 dropped 紀錄)_")
    P("")

    # 4. 字數直方圖
    P("## 4. 字數分布（by tier，僅計算非 dropped）")
    P("")
    hist = analyze_word_histogram(conn)
    buckets = [b[0] for b in WORD_BUCKETS]
    P("| Tier | " + " | ".join(buckets) + " | 總 |")
    P("|---|" + "|".join(["---:"] * (len(buckets) + 1)) + "|")
    for tier in sorted(hist.keys()):
        c = hist[tier]
        row = [f"{c[b]}" for b in buckets]
        P(f"| {tier} | " + " | ".join(row) + f" | {sum(c.values())} |")
    P("")

    # 5. YouTube 短路證據
    P("## 5. YouTube 短路 item 清單（bug 追蹤）")
    P("")
    P("這些 item 的 `clean_markdown` 以 `YouTube Interview Description` 起頭，")
    P("代表 `fetcher.py` 直接把 RSS `summary` 當成 markdown 寫入，完全繞過 `trafilatura`。")
    P("")
    yt = analyze_youtube_shortcircuit(conn)
    if yt:
        P(f"共 **{len(yt)}** 筆。前 20 筆：")
        P("")
        P("| Feed | 字數 | 狀態 | Drop Reason | 標題 |")
        P("|---|---:|---|---|---|")
        for r in yt[:20]:
            title = (r["title"] or "")[:50].replace("|", "\\|")
            P(f"| {r['feed_name']} | {r['word_count']} | {r['status']} | "
              f"{r['drop_reason'] or ''} | {title} |")
    else:
        P("_(目前 DB 中沒有 YouTube 短路紀錄)_")
    P("")

    # 6. 近 7 天趨勢
    P("## 6. 近 7 天 Harvest 趨勢")
    P("")
    trend = analyze_7day_trend(conn)
    if trend:
        P("| 日期 | 抓到 | 丟棄 | 平均字數 |")
        P("|---|---:|---:|---:|")
        for d in trend:
            P(f"| {d['day']} | {d['total']} | {d['dropped']} | "
              f"{(d['avg_wc'] or 0):.0f} |")
    else:
        P("_(近 7 天沒有 fetch 紀錄)_")
    P("")

    # 7. 最新 item
    P("## 7. 最新 15 筆")
    P("")
    P("| 時間 | Tier | Feed | 字數 | 狀態 | 標題 |")
    P("|---|---|---|---:|---|---|")
    for r in analyze_latest_items(conn, 15):
        title = (r["title"] or "")[:50].replace("|", "\\|")
        t = (r["fetched_at"] or "")[:16]
        P(f"| {t} | {r['feed_tier']} | {r['feed_name']} | {r['word_count']} | "
          f"{r['status']} | {title} |")
    P("")

    P("---")
    P("> 產出工具：`tools/diagnose_harvest.py`")
    P("> 下一步：針對通過率過低的 feed 跑 `tools/diagnose_feeds.py`，")
    P("> 或對個別 item 跑 `tools/replay_item.py <id>` 重現清洗流程。")

    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Harvest 健康診斷（純 SQLite，零 token）")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB,
                    help=f"SQLite 路徑（預設 {DEFAULT_DB}）")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"Markdown 輸出路徑（預設 {DEFAULT_OUT}）")
    ap.add_argument("--print", action="store_true",
                    help="同時印到 stdout")
    args = ap.parse_args()

    conn = _open(args.db)
    report = render_md(conn, args.db)
    conn.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"[diag] ✅ 報告寫入：{args.out}")
    if args.print:
        print("\n" + "=" * 60 + "\n")
        print(report)


if __name__ == "__main__":
    main()
