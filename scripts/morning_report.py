"""
News Radar · Morning Report（每日系統狀態總覽）
================================================
每天 07:00 TW 自動產出，寫到 docs/morning/YYYY-MM-DD.md。
Hsin 早上起床看一眼就知道：

  1. 昨天發了幾篇？FB / IG / Threads 各幾篇？
  2. 目前 queued 幾篇？stale 幾篇？最舊的 queued 多久了？
  3. 最近 7 天各 feed 貢獻了多少 news_items？
  4. 最近一次 harvest / compose / publish 時間
  5. 任何警訊（queue 空 / feed 全 miss / 有 failed draft）

設計原則：
  * 全部走 read-only SQL 查詢，不修改任何資料
  * 沒 DB 也能跑（會印 "waiting for state branch" 的 placeholder）
  * --dry-run 只印到 stdout，不寫檔
  * 在 GH Actions 從 state branch 拉 DB 後跑

用法：
    python -m scripts.morning_report            # 寫到 docs/morning/YYYY-MM-DD.md
    python -m scripts.morning_report --dry-run  # 只印 stdout
    python -m scripts.morning_report --stdout   # 印到 stdout 同時寫檔

—— 2026-04-21 overnight, Cowork Claude
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "data" / "01_harvest" / "news_radar.db"
_OUT_DIR = _ROOT / "docs" / "morning"


# ---------- Low-level DB helpers（不依 pydantic，避免 sandbox 裝不起 requirements）----------

def _open_ro(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    r = conn.execute(sql, params).fetchone()
    return int(r[0]) if r and r[0] is not None else 0


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


# ---------- Report sections ----------

def section_publish_activity(conn: sqlite3.Connection) -> List[str]:
    """昨天 publish_log 的成功 / 失敗分佈。"""
    out: List[str] = []
    out.append("## 🚀 最近 24h 發布活動")
    out.append("")
    if not _table_exists(conn, "publish_log"):
        out.append("_publish_log 表尚未建立_")
        return out
    rows = conn.execute(
        """
        SELECT platform,
               SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS ok,
               SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS fail
          FROM publish_log
         WHERE posted_at >= datetime('now', '-1 day')
         GROUP BY platform
         ORDER BY platform
        """
    ).fetchall()
    if not rows:
        out.append("_過去 24 小時沒有發文紀錄_")
        return out
    out.append("| 平台 | 成功 | 失敗 |")
    out.append("|---|---:|---:|")
    total_ok = total_fail = 0
    for r in rows:
        out.append(f"| {r['platform']} | {r['ok']} | {r['fail']} |")
        total_ok += int(r["ok"] or 0)
        total_fail += int(r["fail"] or 0)
    out.append(f"| **合計** | **{total_ok}** | **{total_fail}** |")
    if total_fail > 0:
        out.append("")
        out.append("⚠️ 有發文失敗，請看 pipeline.yml 最新 run logs。")
    return out


def section_queue_status(conn: sqlite3.Connection) -> List[str]:
    """drafts 佇列狀態快照。"""
    out: List[str] = []
    out.append("## 📦 Queue 狀態")
    out.append("")
    if not _table_exists(conn, "drafts"):
        out.append("_drafts 表尚未建立_")
        return out
    # 8.18 queue_status 可能不存在於舊 DB
    cols = [r[1] for r in conn.execute("PRAGMA table_info(drafts)").fetchall()]
    if "queue_status" not in cols:
        out.append("_queue_status 欄位尚未 migrate（跑一次 init_db 即可）_")
        return out

    rows = conn.execute(
        """
        SELECT COALESCE(queue_status, 'null') AS qs, COUNT(*) AS cnt
          FROM drafts
         GROUP BY qs
         ORDER BY cnt DESC
        """
    ).fetchall()
    if not rows:
        out.append("_drafts 表目前是空的（pipeline 還沒跑過）_")
        return out
    out.append("| queue_status | 張數 |")
    out.append("|---|---:|")
    for r in rows:
        out.append(f"| `{r['qs']}` | {r['cnt']} |")

    # 最舊的 queued
    oldest = conn.execute(
        """
        SELECT id, generated_at
          FROM drafts
         WHERE queue_status = 'queued'
         ORDER BY generated_at ASC
         LIMIT 1
        """
    ).fetchone()
    if oldest:
        out.append("")
        out.append(f"- 最舊 queued draft：`{oldest['id'][:12]}…` @ {oldest['generated_at']}")
    return out


def section_feed_coverage(conn: sqlite3.Connection) -> List[str]:
    """最近 7 天各 feed 貢獻的 news_items 數。"""
    out: List[str] = []
    out.append("## 📡 近 7 天 Feed 貢獻量")
    out.append("")
    if not _table_exists(conn, "news_items"):
        out.append("_news_items 表尚未建立_")
        return out

    rows = conn.execute(
        """
        SELECT feed_name, COUNT(*) AS cnt
          FROM news_items
         WHERE fetched_at >= datetime('now', '-7 days')
         GROUP BY feed_name
         ORDER BY cnt DESC
         LIMIT 30
        """
    ).fetchall()
    if not rows:
        out.append("⚠️ _過去 7 天沒有新 items — feeds 可能全掛或 harvest 沒跑_")
        return out
    out.append("| feed | items |")
    out.append("|---|---:|")
    for r in rows:
        out.append(f"| {r['feed_name']} | {r['cnt']} |")
    return out


def section_topic_distribution(conn: sqlite3.Connection) -> List[str]:
    """最近 7 天 news_items 依 topic_category 分佈 + 當前權重。"""
    out: List[str] = []
    out.append("## 🧭 主題覆蓋 × 權重")
    out.append("")

    cols = [r[1] for r in conn.execute("PRAGMA table_info(news_items)").fetchall()]
    if "topic_category" not in cols:
        out.append("_topic_category 欄位尚未 migrate_")
        return out

    # 主題分佈
    dist = {r["topic_category"]: int(r["cnt"]) for r in conn.execute(
        """
        SELECT topic_category, COUNT(*) AS cnt
          FROM news_items
         WHERE fetched_at >= datetime('now', '-7 days')
           AND topic_category IS NOT NULL
           AND topic_category != ''
         GROUP BY topic_category
        """
    ).fetchall()}

    # 當前權重
    if not _table_exists(conn, "topic_weights"):
        out.append("_topic_weights 表尚未 seed（跑一次 init_db 即可）_")
        return out
    weights = conn.execute(
        "SELECT category_id, display_name, weight, sample_count, "
        "update_reason, last_updated_at FROM topic_weights ORDER BY weight DESC"
    ).fetchall()

    out.append("| 類別 | 權重 | 7d items | samples 累積 | 最近來源 |")
    out.append("|---|---:|---:|---:|:-:|")
    for r in weights:
        cid = r["category_id"]
        cnt = dist.get(cid, 0)
        out.append(
            f"| `{cid}` ({r['display_name']}) "
            f"| {r['weight']:.2f} "
            f"| {cnt} "
            f"| {r['sample_count']} "
            f"| {r['update_reason']} |"
        )
    # 未分類的
    unclassified = _scalar(
        conn,
        "SELECT COUNT(*) FROM news_items "
        "WHERE fetched_at >= datetime('now', '-7 days') "
        "  AND (topic_category IS NULL OR topic_category = '')"
    )
    if unclassified > 0:
        out.append("")
        out.append(f"⚠️ 近 7 天有 {unclassified} 筆 news_items 未分類——考慮跑 backfill_topic_classifier。")
    return out


def section_last_activity(conn: sqlite3.Connection) -> List[str]:
    """harvest / compose / publish 最近時間戳。"""
    out: List[str] = []
    out.append("## 🕒 最近活動時間戳")
    out.append("")

    def _get_max(sql: str) -> Optional[str]:
        r = conn.execute(sql).fetchone()
        if r and r[0]:
            return str(r[0])
        return None

    last_harvest = _get_max("SELECT MAX(fetched_at) FROM news_items") if _table_exists(conn, "news_items") else None
    last_compose = _get_max("SELECT MAX(generated_at) FROM drafts") if _table_exists(conn, "drafts") else None
    last_publish = _get_max("SELECT MAX(posted_at) FROM publish_log") if _table_exists(conn, "publish_log") else None
    last_reflect = (
        _get_max("SELECT MAX(ran_at) FROM reflection_events")
        if _table_exists(conn, "reflection_events") else None
    )

    out.append("| 節點 | 最近時間 |")
    out.append("|---|---|")
    out.append(f"| harvest | {last_harvest or '—'} |")
    out.append(f"| compose | {last_compose or '—'} |")
    out.append(f"| publish | {last_publish or '—'} |")
    out.append(f"| reflect | {last_reflect or '—'} |")
    return out


def section_warnings(conn: sqlite3.Connection) -> List[str]:
    """彙總紅黃牌。"""
    out: List[str] = []
    warnings: List[str] = []

    # 1. queue 空 + 沒有最近 compose
    if _table_exists(conn, "drafts"):
        cols = [r[1] for r in conn.execute("PRAGMA table_info(drafts)").fetchall()]
        if "queue_status" in cols:
            queued = _scalar(
                conn, "SELECT COUNT(*) FROM drafts WHERE queue_status = 'queued'"
            )
            if queued == 0:
                warnings.append(
                    "🟡 Queue 目前是空的——Mac 端 compose cron 可能沒跑，"
                    "或選不到合適新聞。"
                )

    # 2. 過去 24h 無 harvest
    if _table_exists(conn, "news_items"):
        last_24h = _scalar(
            conn,
            "SELECT COUNT(*) FROM news_items WHERE fetched_at >= datetime('now', '-1 day')"
        )
        if last_24h == 0:
            warnings.append(
                "🔴 過去 24h 沒有新 news_items——pipeline 沒跑 / feed 全掛，請檢查 pipeline.yml"
            )

    # 3. failed drafts
    if _table_exists(conn, "drafts"):
        cols = [r[1] for r in conn.execute("PRAGMA table_info(drafts)").fetchall()]
        if "queue_status" in cols:
            failed = _scalar(
                conn, "SELECT COUNT(*) FROM drafts WHERE queue_status = 'failed'"
            )
            if failed > 0:
                warnings.append(
                    f"🔴 有 {failed} 筆 draft queue_status=failed——"
                    "看 publish_log 最新的 error_message"
                )

    out.append("## ⚠️ 警訊彙整")
    out.append("")
    if warnings:
        out.extend([f"- {w}" for w in warnings])
    else:
        out.append("✅ 沒有發現異常。")
    return out


# ---------- Main report assembler ----------

def generate_report(conn: Optional[sqlite3.Connection]) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines: List[str] = []
    lines.append(f"# News Radar · Morning Report · {today}")
    lines.append("")
    lines.append(f"_自動產出於 {now} UTC_")
    lines.append("")

    if conn is None:
        lines.append("## ⏳ DB 尚未存在")
        lines.append("")
        lines.append(
            "找不到 `data/01_harvest/news_radar.db`。可能是第一次 run，"
            "或 state branch 還沒 seed。下次 pipeline.yml 跑完後會自動補上。"
        )
        lines.append("")
        return "\n".join(lines)

    # 按重要性排序
    for section in [
        section_warnings,
        section_queue_status,
        section_publish_activity,
        section_last_activity,
        section_topic_distribution,
        section_feed_coverage,
    ]:
        lines.extend(section(conn))
        lines.append("")

    lines.append("---")
    lines.append(f"_generated by `scripts/morning_report.py`_")
    return "\n".join(lines)


def write_report(md: str, date_str: Optional[str] = None) -> Path:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = _OUT_DIR / f"{date_str}.md"
    out.write_text(md, encoding="utf-8")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="只印到 stdout，不寫檔")
    parser.add_argument("--stdout", action="store_true",
                        help="印到 stdout 同時寫檔")
    parser.add_argument("--db-path", default=str(_DB_PATH),
                        help=f"DB 路徑（預設 {_DB_PATH}）")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path)
    conn = _open_ro(db_path)
    try:
        md = generate_report(conn)
    finally:
        if conn is not None:
            conn.close()

    if args.dry_run:
        print(md)
        return 0

    out = write_report(md)
    if args.stdout:
        print(md)
    print(f"[morning_report] wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
