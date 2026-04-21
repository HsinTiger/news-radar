"""
News Radar · Topic Classifier Dry-Run（Phase 8.20 debug CLI）
==============================================================
給 Hsin 快速試：一段新聞標題 + 內文會被分到哪一類。用於：

  * 邊界案例調校（「這題到底算 ai_model 還是 supply_chain？」）
  * 新增關鍵字前先試試看現況
  * 回溯檢查某篇 DB 裡被錯分的 news_item

用法：
    # 單則（標題 + 內文）
    python -m scripts.classify_dryrun --title "GPT-5 released" --content "..."

    # 從 DB 抽 news_item 重跑（會印原有分類 vs 目前邏輯分類）
    python -m scripts.classify_dryrun --news-id <sha1>

    # 批次：抽最近 20 篇已分類的 news_items 重跑，列出不一致的
    python -m scripts.classify_dryrun --recheck-recent 20

    # 僅跑 keyword path（免 LLM key）
    python -m scripts.classify_dryrun --title "..." --keyword-only

—— 2026-04-21 overnight, Cowork Claude
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.topic_classifier import (  # noqa: E402
    classify_topic,
    classify_topic_keyword,
    TopicClassification,
)

_DB_PATH = _ROOT / "data" / "01_harvest" / "news_radar.db"


def _open_ro(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _print_result(tag: str, title: str, c: TopicClassification,
                  expected: Optional[str] = None) -> None:
    """印一則分類結果；expected 非 None 時會標 ✅/❌。"""
    icon = ""
    if expected is not None:
        icon = " ✅" if c.category_id == expected else " ❌"
    print(f"{tag} · {title[:60]}")
    print(
        f"  → category={c.category_id} "
        f"confidence={c.confidence:.2f}{icon} "
        f"rationale={c.rationale}"
    )
    if expected is not None and expected != c.category_id:
        print(f"     原分類: {expected}")


async def run_single(title: str, content: str, keyword_only: bool) -> int:
    if keyword_only:
        kw = classify_topic_keyword(title, content)
        if kw is None:
            print(f"[dryrun] keyword 全 miss — 若跑完整路徑會進 LLM fallback")
            c = TopicClassification(
                category_id="(miss)", confidence=0.0, rationale="keyword miss",
            )
            _print_result("keyword", title, c)
            return 0
        _print_result("keyword", title, kw)
        return 0

    c = await classify_topic(title, content)
    _print_result("full", title, c)
    return 0


async def run_news_id(news_id: str, keyword_only: bool) -> int:
    conn = _open_ro(_DB_PATH)
    if conn is None:
        print(f"[dryrun] DB 不存在：{_DB_PATH}", file=sys.stderr)
        return 2
    try:
        r = conn.execute(
            "SELECT id, title, clean_markdown, topic_category, topic_confidence "
            "FROM news_items WHERE id = ?",
            (news_id,),
        ).fetchone()
        if r is None:
            print(f"[dryrun] 找不到 news_id={news_id}", file=sys.stderr)
            return 2
        title = r["title"] or ""
        content = (r["clean_markdown"] or "")[:3000]
        existing = r["topic_category"]
        print(f"[dryrun] news_id={news_id}  existing={existing}")
        if keyword_only:
            kw = classify_topic_keyword(title, content)
            c = kw or TopicClassification("(miss)", 0.0, "keyword miss")
            _print_result("keyword", title, c, expected=existing)
        else:
            c = await classify_topic(title, content)
            _print_result("full", title, c, expected=existing)
        return 0
    finally:
        conn.close()


async def run_recheck_recent(n: int, keyword_only: bool) -> int:
    conn = _open_ro(_DB_PATH)
    if conn is None:
        print(f"[dryrun] DB 不存在：{_DB_PATH}", file=sys.stderr)
        return 2
    try:
        rows = conn.execute(
            "SELECT id, title, clean_markdown, topic_category "
            "FROM news_items "
            "WHERE topic_category IS NOT NULL AND topic_category != '' "
            "ORDER BY fetched_at DESC LIMIT ?",
            (n,),
        ).fetchall()
        if not rows:
            print("[dryrun] 沒有已分類的 news_items 可以重跑")
            return 1
        mismatches: List[Tuple[str, str, str]] = []
        for r in rows:
            title = r["title"] or ""
            content = (r["clean_markdown"] or "")[:3000]
            existing = r["topic_category"]
            if keyword_only:
                kw = classify_topic_keyword(title, content)
                cid = kw.category_id if kw else "(miss)"
                conf = kw.confidence if kw else 0.0
                rationale = kw.rationale if kw else "keyword miss"
            else:
                c = await classify_topic(title, content)
                cid, conf, rationale = c.category_id, c.confidence, c.rationale

            match = "✅" if cid == existing else "❌"
            print(f"{match}  {existing:<18} → {cid:<18}  "
                  f"conf={conf:.2f}  {title[:50]}")
            if cid != existing:
                mismatches.append((r["id"], existing, cid))
        print(f"\n[dryrun] 結果: {len(rows) - len(mismatches)}/{len(rows)} 吻合"
              f"（{len(mismatches)} 筆分歧）")
        if mismatches:
            print("\n分歧清單:")
            for mid, old, new in mismatches:
                print(f"  {mid}  {old}  →  {new}")
        return 0
    finally:
        conn.close()


async def _async_main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", help="單則分類：標題")
    parser.add_argument("--content", default="", help="單則分類：內文")
    parser.add_argument("--news-id", help="從 DB 抓某則重跑")
    parser.add_argument("--recheck-recent", type=int, metavar="N",
                        help="從 DB 抓最近 N 篇已分類重跑，列不一致")
    parser.add_argument("--keyword-only", action="store_true",
                        help="只跑 keyword path（不打 LLM）")
    args = parser.parse_args()

    # 互斥選項
    modes = [bool(args.title), bool(args.news_id), bool(args.recheck_recent)]
    if sum(modes) != 1:
        parser.error("請擇一：--title / --news-id / --recheck-recent")

    if args.title:
        return await run_single(args.title, args.content, args.keyword_only)
    if args.news_id:
        return await run_news_id(args.news_id, args.keyword_only)
    if args.recheck_recent:
        return await run_recheck_recent(args.recheck_recent, args.keyword_only)
    return 1


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    sys.exit(main())
