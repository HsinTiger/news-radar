"""
Phase 8.20 · Feed Audit（健康檢查 + 分類覆蓋率報告）
======================================================
給 Hsin 在 Mac 上跑，快速看清：
  1. config.yaml 裡的每一個 feed URL → HTTP 200? RSS parse 成功?
  2. 過去 30 天進 DB 的 news_items，依 topic_category 的分佈——
     讓 Hsin 看出『訊號來源覆蓋率 vs 權重』是否對齊。
  3. 相對於 Hsin 指定的三位 KOL 風格（蕭上農 / 游庭皓 / IEO），
     目前 feed 清單與訊號成品的差距。

用法：
    python -m scripts.audit_feeds              # 跑全部
    python -m scripts.audit_feeds --urls-only  # 只跑 HTTP 健檢（快）
    python -m scripts.audit_feeds --db-only    # 只跑 DB 分佈

—— 2026-04-21 overnight, Cowork Claude
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# 不 hard-import db（pydantic 相依）；真的要跑 --db-only 才進去
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def _load_feeds() -> List[dict]:
    import yaml  # type: ignore
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return list(raw.get("feeds") or [])


async def _check_one(url: str, timeout_s: float = 10.0) -> Tuple[int, str]:
    """回 (http_status, info_text)。network 錯誤 → status=0。"""
    try:
        import httpx  # type: ignore
    except ImportError:
        return (-1, "httpx 未安裝，跳過 HTTP 健檢；pip install httpx")

    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as cli:
            r = await cli.get(url, headers={"User-Agent": "News-Radar-FeedAudit/1.0"})
            return (r.status_code, f"{len(r.content)} bytes")
    except Exception as e:
        return (0, f"{type(e).__name__}: {str(e)[:120]}")


async def run_url_audit() -> int:
    feeds = _load_feeds()
    if not feeds:
        print("[audit] config.yaml 沒有 feeds 段落")
        return 1
    print(f"[audit] 共 {len(feeds)} 個 feed")
    print(f"{'name':<35} {'tier':<10} {'status':<7} {'detail'}")
    print("-" * 100)
    results: List[Tuple[str, int]] = []
    for f in feeds:
        name = f.get("name") or "?"
        url = f.get("url") or ""
        tier = f.get("tier") or "?"
        status, info = await _check_one(url)
        emoji = "✅" if status == 200 else ("⚠️" if status in (301, 302, 304) else "❌")
        print(f"{name[:34]:<35} {tier:<10} {emoji} {status:<4} {info[:60]}")
        results.append((name, status))

    oks = sum(1 for _n, s in results if s == 200)
    print(f"\n[audit] 200 OK: {oks}/{len(results)}  ({oks*100//max(1,len(results))}%)")
    return 0 if oks == len(results) else 2


def run_db_audit() -> int:
    # 只在真的跑時 import db（避免 pydantic missing 在 --urls-only 模式下崩）
    from src import db as dbmod  # type: ignore

    dbmod.init_db()
    conn = dbmod.get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
        classified = conn.execute(
            "SELECT COUNT(*) FROM news_items "
            "WHERE topic_category IS NOT NULL AND topic_category != ''"
        ).fetchone()[0]
        print(f"[audit] news_items 總數: {total} / 已分類: {classified}")

        print("\n--- 過去 30 天主題分佈（含未發布）---")
        rows = conn.execute(
            """
            SELECT topic_category, COUNT(*) AS cnt
              FROM news_items
             WHERE fetched_at >= datetime('now', '-30 days')
               AND topic_category IS NOT NULL
             GROUP BY topic_category
             ORDER BY cnt DESC
            """
        ).fetchall()
        if not rows:
            print("  （過去 30 天沒有分類過的資料；先跑 backfill_topic_classifier）")
        for r in rows:
            print(f"  {r[0]:<22} {r[1]:>4}")

        print("\n--- 當前 topic_weights（back-prop 前 seed 值）---")
        for r in conn.execute(
            "SELECT category_id, display_name, weight, sample_count, update_reason "
            "FROM topic_weights ORDER BY weight DESC"
        ).fetchall():
            print(
                f"  {r[0]:<22} {r[1]:<16} w={r[2]:.2f} samples={r[3]:<4} "
                f"reason={r[4]}"
            )

        print("\n--- 各 feed 最近 30 天貢獻貼文數 ---")
        rows = conn.execute(
            """
            SELECT feed_name, COUNT(*) AS cnt
              FROM news_items
             WHERE fetched_at >= datetime('now', '-30 days')
             GROUP BY feed_name
             ORDER BY cnt DESC
            """
        ).fetchall()
        for r in rows:
            print(f"  {r[0]:<40} {r[1]:>4}")
        return 0
    finally:
        conn.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls-only", action="store_true")
    parser.add_argument("--db-only", action="store_true")
    args = parser.parse_args()

    rc = 0
    if not args.db_only:
        rc |= await run_url_audit()
    if not args.urls_only:
        rc |= run_db_audit()
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
