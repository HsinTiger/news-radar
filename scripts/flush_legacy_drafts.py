"""
News Radar · Flush Legacy Drafts
================================
掃 drafts 表裡還沒被 quality guard 檢查過的舊稿，把會被 guard 擋下的全部
標為 queue_status='failed'，避免它們每小時一篇慢慢塞 publisher。

為什麼需要：
    Phase 8.20 Step 4 之後，publisher 改回 exit 0（guard 擋是預期行為），
    但 queue 裡仍然殘留 Phase 8.19 前產的 28 則 legacy draft
    （queue_status=NULL 且 full_text 含『【系統代班速報】』等 guard 命中字樣）。
    原本每小時 cron 會挑一則 → 被擋 → mark_failed，~28 小時才能 drain 完。
    這個 script 一次做完，queue 變乾淨、compose-side 才有機會補新 draft 進來。

設計原則：
    - 只讀 → 印 preview → 要求 --apply 才真的寫 DB（default 是 dry-run）。
    - 純 stdlib + src.content_quality_guard（也是純函式），
      不依賴 pydantic / httpx（沙箱可跑）。
    - 和 run_publish_queue 共用同一份 guard 邏輯——guard 改了這裡自動跟上，
      不會出現『publisher 擋但 flush 沒擋』或反之的分裂。

使用方式：
    # 預設 dry-run，印出哪些 draft 會被標 failed
    python -m scripts.flush_legacy_drafts

    # 真的動手
    python -m scripts.flush_legacy_drafts --apply

    # 只掃特定 queue_status
    python -m scripts.flush_legacy_drafts --include null,queued,stale
    python -m scripts.flush_legacy_drafts --include null --apply

    # 覆寫 DB 路徑（相容 GH Actions state branch）
    python -m scripts.flush_legacy_drafts --db-path path/to/news_radar.db

退出碼：
    0  正常完成（不論有沒有實際改動）
    1  DB 打不開、argument 錯誤等 fatal error

Phase 8.20 · 2026-04-21 overnight
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# 讓 `python scripts/flush_legacy_drafts.py` 也能 import src.*
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.content_quality_guard import (  # noqa: E402
    check_quality,
    format_issues,
    has_blocking_issues,
)

DEFAULT_DB_PATH = ROOT / "data" / "01_harvest" / "news_radar.db"
DEFAULT_INCLUDE = ("null", "queued", "stale")
VALID_INCLUDE = {"null", "queued", "stale", "failed"}  # failed 列進來沒意義、但允許 override


# ---------- DB helpers（只用 sqlite3，不經 src.db） ----------

def _open_conn(db_path: Path, read_only: bool) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _candidates(conn: sqlite3.Connection, include: Iterable[str]) -> List[sqlite3.Row]:
    """回傳目前 queue_status 在 include 內的 drafts（附 news.title 方便 log）。"""
    include = list(include)
    # queue_status IS NULL 要特別用 IS NULL，不能走 IN
    want_null = "null" in include
    non_null = [x for x in include if x != "null"]

    where_parts: List[str] = []
    params: List[str] = []
    if want_null:
        where_parts.append("d.queue_status IS NULL")
    if non_null:
        placeholders = ",".join("?" for _ in non_null)
        where_parts.append(f"d.queue_status IN ({placeholders})")
        params.extend(non_null)
    if not where_parts:
        return []

    sql = (
        "SELECT d.id AS draft_id, "
        "       COALESCE(d.queue_status, 'null') AS qs, "
        "       d.generated_at, "
        "       n.id AS news_id, "
        "       n.title AS news_title "
        "  FROM drafts d "
        "  JOIN news_items n ON d.news_id = n.id "
        " WHERE " + " OR ".join(where_parts) + " "
        " ORDER BY d.generated_at DESC"
    )
    return conn.execute(sql, params).fetchall()


def _platform_drafts(conn: sqlite3.Connection, draft_id: str) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT platform, final_text, full_text FROM platform_drafts WHERE draft_id = ? ORDER BY platform",
        (draft_id,),
    ).fetchall()


def _mark_failed(conn: sqlite3.Connection, draft_id: str, reason: str) -> None:
    conn.execute(
        "UPDATE drafts SET queue_status = 'failed' WHERE id = ?",
        (draft_id,),
    )


# ---------- 核心邏輯 ----------

def evaluate(
    conn: sqlite3.Connection,
    include: Iterable[str],
) -> Tuple[List[dict], List[dict]]:
    """對每個候選 draft 跑 guard，分成『會被擋』和『乾淨』兩堆。

    回傳 (would_flush, would_keep)，每項是 dict：
      {
        "draft_id": "...",
        "current_qs": "null" / "queued" / "stale",
        "news_title": "...",
        "reason": "..."    # 只在 would_flush 才有
      }
    """
    would_flush: List[dict] = []
    would_keep: List[dict] = []
    for row in _candidates(conn, include):
        draft_id = row["draft_id"]
        news_title = (row["news_title"] or "")[:80]
        pds = _platform_drafts(conn, draft_id)
        if not pds:
            # 孤兒 draft（沒 platform_drafts）：publisher 也會標 failed，這裡也標
            would_flush.append({
                "draft_id": draft_id,
                "current_qs": row["qs"],
                "news_title": news_title,
                "reason": "no_platform_drafts",
            })
            continue

        block_reasons: List[str] = []
        for pd in pds:
            text = pd["final_text"] or pd["full_text"] or ""
            issues = check_quality(text, title=news_title)
            if has_blocking_issues(issues):
                block_reasons.append(f"{pd['platform']}: {format_issues(issues)}")
        if block_reasons:
            reason = " || ".join(block_reasons)
            # 截長避免 stdout 爆版
            if len(reason) > 180:
                reason = reason[:177] + "..."
            would_flush.append({
                "draft_id": draft_id,
                "current_qs": row["qs"],
                "news_title": news_title,
                "reason": reason,
            })
        else:
            would_keep.append({
                "draft_id": draft_id,
                "current_qs": row["qs"],
                "news_title": news_title,
            })

    return would_flush, would_keep


def summarize(would_flush: List[dict], would_keep: List[dict]) -> None:
    total = len(would_flush) + len(would_keep)
    print(f"[FlushLegacy] 掃描 {total} 筆 candidate drafts")
    print(f"    ↳ 會被 guard 擋（flush 目標）：{len(would_flush)} 筆")
    print(f"    ↳ 乾淨、保留在 queue：        {len(would_keep)} 筆")
    if would_flush:
        print("\n[FlushLegacy] 預計 mark_failed 的 draft（最多印 20 則）：")
        for i, d in enumerate(would_flush[:20], 1):
            print(f"  {i:2d}. [{d['current_qs']:>6}] {d['draft_id'][:16]}…  「{d['news_title']}」")
            print(f"       ↳ {d['reason']}")
        if len(would_flush) > 20:
            print(f"  … 以及其他 {len(would_flush) - 20} 筆")
    if would_keep:
        print(f"\n[FlushLegacy] 乾淨的 draft（最多印 10 則）：")
        for i, d in enumerate(would_keep[:10], 1):
            print(f"  {i:2d}. [{d['current_qs']:>6}] {d['draft_id'][:16]}…  「{d['news_title']}」")
        if len(would_keep) > 10:
            print(f"  … 以及其他 {len(would_keep) - 10} 筆")


# ---------- CLI ----------

def _parse_include(raw: str) -> List[str]:
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    bad = [p for p in parts if p not in VALID_INCLUDE]
    if bad:
        raise SystemExit(f"--include 內含無效值：{bad}；允許：{sorted(VALID_INCLUDE)}")
    return parts


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Flush quality-guard-failing legacy drafts to queue_status='failed'."
    )
    ap.add_argument(
        "--db-path", default=str(DEFAULT_DB_PATH),
        help=f"SQLite DB 路徑（預設：{DEFAULT_DB_PATH}）",
    )
    ap.add_argument(
        "--include", default=",".join(DEFAULT_INCLUDE),
        help=f"掃哪些 queue_status（逗號分隔）。預設：{','.join(DEFAULT_INCLUDE)}",
    )
    ap.add_argument(
        "--apply", action="store_true",
        help="實際 UPDATE DB。不帶此 flag 即 dry-run（預設）。",
    )
    args = ap.parse_args()

    include = _parse_include(args.include)
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"[FlushLegacy] ❌ DB 不存在：{db_path}")
        return 1

    mode = "APPLY" if args.apply else "dry-run"
    print(f"[FlushLegacy] mode={mode} · db={db_path} · include={include}")
    print()

    conn = _open_conn(db_path, read_only=not args.apply)
    try:
        would_flush, would_keep = evaluate(conn, include)
        summarize(would_flush, would_keep)

        if not args.apply:
            print("\n[FlushLegacy] dry-run 完成，未改動 DB。加 --apply 才會真的 mark_failed。")
            return 0

        if not would_flush:
            print("\n[FlushLegacy] 沒有需要 flush 的 draft，DB 無變動。")
            return 0

        ts = datetime.now(timezone.utc).isoformat()
        print(f"\n[FlushLegacy] 開始 mark_failed × {len(would_flush)} 筆 @ {ts} ...")
        for d in would_flush:
            _mark_failed(conn, d["draft_id"], reason=d["reason"])
        conn.commit()
        print(f"[FlushLegacy] ✅ 完成。queue_status='failed' 更新 {len(would_flush)} 筆。")

        # 顯示 after-state for sanity
        rows = conn.execute(
            """SELECT COALESCE(queue_status,'null') qs, COUNT(*) c
                 FROM drafts GROUP BY COALESCE(queue_status,'null')"""
        ).fetchall()
        dist = {r["qs"]: r["c"] for r in rows}
        print(f"[FlushLegacy] 變更後 queue 狀態分佈：{dist}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
