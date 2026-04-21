"""
News Radar · Queue Inspector（Phase 8.20 debug CLI）
======================================================
給 Hsin debug「為什麼這則沒被發？」或「queue 到底長什麼樣」。

印出 drafts 表的佇列狀態與每張卡的關鍵資訊：
  * draft id / title / age / status / queue_status / queue age
  * topic_category + weighted_score（排序依據）
  * platform_drafts 展開狀態（FB / IG / Threads 是否都有）
  * publish_log 的歷史（若已發）

用法：
    python -m scripts.queue_inspect                    # 全部，依 weighted_score 排序
    python -m scripts.queue_inspect --state queued     # 只看 queued
    python -m scripts.queue_inspect --state stale      # 只看 stale（需 publisher 重新 bump）
    python -m scripts.queue_inspect --state failed     # 只看 failed
    python -m scripts.queue_inspect --id <draft_id>    # 單張卡的完整展開
    python -m scripts.queue_inspect --last-hours 24    # 過去 24h 有活動的
    python -m scripts.queue_inspect --json             # 讓 jq 用

—— 2026-04-21 overnight, Cowork Claude
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "data" / "01_harvest" / "news_radar.db"


def _open_ro(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _age(iso_ts: Optional[str]) -> str:
    """粗略 age hint（'3h ago' / '2d ago'）。"""
    if not iso_ts:
        return "—"
    from datetime import datetime, timezone
    try:
        # 容納 '2026-04-21T07:00:00+00:00' 與 '2026-04-21 07:00:00'
        s = iso_ts.replace(" ", "T")
        if "+" not in s and "Z" not in s:
            s = s + "+00:00"
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        delta = datetime.now(timezone.utc) - dt
        sec = int(delta.total_seconds())
        if sec < 60:
            return f"{sec}s ago"
        if sec < 3600:
            return f"{sec // 60}m ago"
        if sec < 86400:
            return f"{sec // 3600}h ago"
        return f"{sec // 86400}d ago"
    except Exception:
        return iso_ts[:19]


def _fetch_draft_summaries(
    conn: sqlite3.Connection,
    state: Optional[str],
    last_hours: Optional[int],
) -> List[Dict[str, Any]]:
    """組 queue 總覽資料：drafts JOIN news_items 的精要欄位。"""
    # 先判斷欄位是否存在（舊 DB 可能缺 weighted_score / queue_status）
    news_cols = {r[1] for r in conn.execute("PRAGMA table_info(news_items)").fetchall()}
    drafts_cols = {r[1] for r in conn.execute("PRAGMA table_info(drafts)").fetchall()}

    w_score_expr = "n.weighted_score" if "weighted_score" in news_cols else "NULL"
    t_cat_expr = "n.topic_category" if "topic_category" in news_cols else "NULL"
    q_status_expr = "d.queue_status" if "queue_status" in drafts_cols else "NULL"

    where: List[str] = []
    params: List[Any] = []
    if state is not None:
        if "queue_status" not in drafts_cols:
            return []
        if state.lower() == "null":
            where.append("d.queue_status IS NULL")
        else:
            where.append("d.queue_status = ?")
            params.append(state)
    if last_hours is not None:
        where.append(f"d.generated_at >= datetime('now', ?)")
        params.append(f"-{int(last_hours)} hours")

    sql = f"""
        SELECT d.id        AS draft_id,
               d.news_id,
               d.generated_at,
               d.status,
               {q_status_expr} AS queue_status,
               d.confidence_score,
               d.publish_at,
               n.title,
               n.url,
               n.feed_name,
               n.published_at,
               {t_cat_expr}    AS topic_category,
               {w_score_expr}  AS weighted_score
          FROM drafts d
          JOIN news_items n ON n.id = d.news_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += """
         ORDER BY COALESCE(weighted_score, 0) DESC,
                  d.generated_at DESC
         LIMIT 200
    """
    rows = conn.execute(sql, params).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({k: r[k] for k in r.keys()})
    return out


def _fetch_platform_drafts(
    conn: sqlite3.Connection, draft_id: str
) -> List[Dict[str, Any]]:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_drafts'"
    ).fetchone():
        return []
    rows = conn.execute(
        "SELECT platform, char_count, reviewer_action, created_at, full_text "
        "FROM platform_drafts WHERE draft_id = ? ORDER BY platform",
        (draft_id,),
    ).fetchall()
    return [
        {k: r[k] for k in r.keys()} for r in rows
    ]


def _fetch_publish_log(
    conn: sqlite3.Connection, draft_id: str
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT platform, posted_at, success, platform_post_id, error_message "
        "FROM publish_log WHERE draft_id = ? ORDER BY posted_at",
        (draft_id,),
    ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def _print_table(drafts: List[Dict[str, Any]]) -> None:
    if not drafts:
        print("(empty)")
        return
    # Header
    print(
        f"{'draft_id':<14} {'qs':<10} {'topic':<18} {'w_score':>7} "
        f"{'age':<10} {'plat_exp':<8} {'title'}"
    )
    print("-" * 120)
    for d in drafts:
        qs = str(d.get("queue_status") or "—")
        topic = str(d.get("topic_category") or "—")
        wscore = d.get("weighted_score")
        wscore_s = f"{wscore:.3f}" if wscore is not None else "—"
        age = _age(d.get("generated_at"))
        title = (d.get("title") or "")[:50]
        print(
            f"{d['draft_id'][:12]:<14} {qs:<10} {topic[:17]:<18} {wscore_s:>7} "
            f"{age:<10} {'?':<8} {title}"
        )


def _print_detail(
    conn: sqlite3.Connection, draft_id: str
) -> int:
    row = conn.execute(
        """
        SELECT d.*, n.title AS news_title, n.url AS news_url,
               n.feed_name, n.published_at AS news_published_at,
               n.topic_category, n.weighted_score
          FROM drafts d JOIN news_items n ON n.id = d.news_id
         WHERE d.id = ?
        """,
        (draft_id,),
    ).fetchone()
    if row is None:
        print(f"[queue_inspect] draft_id={draft_id} 不存在", file=sys.stderr)
        return 2
    print(f"# Draft {row['id']}")
    print(f"  title          : {row['news_title']}")
    print(f"  url            : {row['news_url']}")
    print(f"  feed           : {row['feed_name']}")
    print(f"  news published : {row['news_published_at']} ({_age(row['news_published_at'])})")
    print(f"  draft generated: {row['generated_at']} ({_age(row['generated_at'])})")
    print(f"  status         : {row['status']}")
    qs = row["queue_status"] if "queue_status" in row.keys() else None
    print(f"  queue_status   : {qs}")
    print(f"  publish_at     : {row['publish_at'] if 'publish_at' in row.keys() else None}")
    print(f"  topic          : {row['topic_category']}   weighted_score: {row['weighted_score']}")
    print(f"  confidence     : {row['confidence_score']}")

    # platform drafts
    pds = _fetch_platform_drafts(conn, draft_id)
    print(f"\n  Platform variants ({len(pds)}/3):")
    if not pds:
        print("    (none — composer 還沒展開 platform variants)")
    for pd in pds:
        print(f"    · {pd['platform']:<10} "
              f"char={pd['char_count']}  "
              f"review={pd['reviewer_action']}  "
              f"created={_age(pd['created_at'])}")

    # publish log
    logs = _fetch_publish_log(conn, draft_id)
    print(f"\n  Publish log ({len(logs)}):")
    if not logs:
        print("    (未發佈)")
    for lg in logs:
        ok = "✅" if lg["success"] else "❌"
        print(f"    {ok} {lg['platform']:<10} {lg['posted_at']}  "
              f"post_id={lg['platform_post_id']}  "
              f"err={lg['error_message'] or '—'}")

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", help="queued / stale / failed / published / null / none")
    parser.add_argument("--last-hours", type=int, help="只看最近 N 小時的 drafts")
    parser.add_argument("--id", dest="draft_id", help="單張卡詳情")
    parser.add_argument("--json", action="store_true", help="印 JSON（總表模式才有效）")
    parser.add_argument("--db-path", default=str(_DB_PATH), help="DB 路徑")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path)
    conn = _open_ro(db_path)
    if conn is None:
        print(f"[queue_inspect] DB 不存在：{db_path}", file=sys.stderr)
        return 2

    try:
        if args.draft_id:
            return _print_detail(conn, args.draft_id)

        # 總表模式
        drafts = _fetch_draft_summaries(
            conn, state=args.state, last_hours=args.last_hours
        )
        if args.json:
            print(json.dumps(drafts, ensure_ascii=False, indent=2, default=str))
        else:
            _print_table(drafts)
            print(f"\n[queue_inspect] {len(drafts)} rows  "
                  f"(state={args.state or 'any'}, last_hours={args.last_hours or 'any'})")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
