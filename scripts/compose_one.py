"""
News Radar · Compose One (單篇強制重 compose)
===========================================
給 run_pipeline.py 不支援 `--news-id` 的時代一個逃生閥：
指定一則 news_id，跑 scorer + topic_classifier + composer + quality guard
一整條，產出新的 platform_drafts、覆蓋掉舊的 failed draft。

為什麼需要：
    Phase 8.19 之前的 emergency template 汙染了 28 筆 legacy draft，
    已經被 flush 掉。但如果某則新聞你想特別重發（例如要測 publish
    pipeline），run_pipeline.py --compose-only 是 buffer-fill 邏輯，
    由 scorer 排序決定選誰，不能指定。此 script 專治這個。

設計原則：
    - 直接呼叫 run_pipeline.process_item —— 共用所有 scoring / topic /
      composer / guard 流程，不重複造輪子。
    - `--force` 把 publish_threshold 設 0.0，確保 draft 一定進 queue
      （可為測試用途）；沒 --force 就走預設門檻。
    - 不呼叫 Meta API（compose-only）；Cloud 端 run_publish_queue 才發。
    - 會覆蓋同 news_id 的舊 draft（因為 draft_id = sha1(news_id + "_v1")
      是 deterministic 的，insert_draft 用 INSERT OR REPLACE）。

使用方式：
    source .venv/bin/activate
    python -m scripts.compose_one --news-id <news_id> [--force]

退出碼：
    0  compose 成功（draft 已入 DB / queue）
    1  news_id 不存在、LLM 失敗、guard 擋下，等 non-fatal reasons
    2  fatal error（DB 壞了、import 失敗）

Phase 8.20 · 2026-04-21 overnight
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 讓 `python scripts/compose_one.py` 也能 import src.*
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db as dbmod  # noqa: E402
import run_pipeline  # noqa: E402
from run_pipeline import process_item  # noqa: E402


async def _run(news_id: str, force: bool) -> str:
    dbmod.init_db()

    # --force 要 bypass 兩道門檻：
    #   (1) MIN_SCORE_THRESHOLD（分數太低就 return "dropped"、連 compose 都不跑）
    #   (2) publish_threshold / AUTO_PUBLISH_THRESHOLD（分數未達就不入 queue）
    # 非 fatal 的 monkey-patch，只在這支 test utility 的 process 內生效。
    if force:
        orig_min = run_pipeline.MIN_SCORE_THRESHOLD
        run_pipeline.MIN_SCORE_THRESHOLD = 0.0
        print(f"[ComposeOne] --force：MIN_SCORE_THRESHOLD {orig_min} → 0.0（本次 run 生效）")

    conn = dbmod.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM news_items WHERE id = ?",
            (news_id,),
        ).fetchone()
        if row is None:
            print(f"[ComposeOne] ❌ news_id 不存在：{news_id}")
            return "not_found"

        print(f"[ComposeOne] 目標新聞：{row['title'][:70]}")
        print(f"    ↳ published_at: {row['published_at']}")
        print(f"    ↳ force mode: {'YES (bypass MIN_SCORE + AUTO_PUBLISH thresholds)' if force else 'no (使用預設門檻)'}")

        # 看有沒有舊 draft 要被覆蓋
        old = conn.execute(
            "SELECT id, queue_status, generated_at FROM drafts WHERE news_id = ? ORDER BY generated_at DESC",
            (news_id,),
        ).fetchall()
        if old:
            print(f"[ComposeOne] 現有 drafts × {len(old)}（draft_id 相同者會被 INSERT OR REPLACE 覆蓋）：")
            for r in old:
                print(f"    · {r['id'][:16]}… qs={r['queue_status']} generated_at={r['generated_at']}")

        # 跑 process_item；compose_only=True 讓結果入 queue 但不呼叫 Meta API
        threshold = 0.0 if force else None
        result = await process_item(
            conn,
            row,
            publish_threshold=threshold,
            compose_only=True,
        )
        conn.commit()
        print(f"\n[ComposeOne] process_item result: {result}")

        # 結果驗證：看 DB 裡現在有沒有新 draft + 三平台
        new = conn.execute(
            "SELECT id, queue_status, generated_at FROM drafts WHERE news_id = ? ORDER BY generated_at DESC LIMIT 1",
            (news_id,),
        ).fetchone()
        if new:
            pd_count = conn.execute(
                "SELECT COUNT(*) AS c FROM platform_drafts WHERE draft_id = ?",
                (new["id"],),
            ).fetchone()["c"]
            print(f"[ComposeOne] ✅ DB 最新 draft: {new['id'][:16]}… qs={new['queue_status']} platforms={pd_count}")
        return result
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="強制重 compose 指定 news_id 的 draft（run_pipeline --compose-only 的單篇版）。"
    )
    ap.add_argument("--news-id", required=True, help="target news_items.id")
    ap.add_argument(
        "--force", action="store_true",
        help="publish_threshold=0.0（無視分數直接入 queue，測試用）",
    )
    args = ap.parse_args()

    result = asyncio.run(_run(args.news_id, force=args.force))
    if result in ("queued", "drafted", "published"):
        return 0
    if result in ("dropped", "dropped_quality_block", "skipped_no_llm"):
        print(f"[ComposeOne] ⚠️  result={result}（非 fatal，但沒產出可發 draft）")
        return 1
    if result == "not_found":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
