"""
Phase 8.20 Step 2 · 背填腳本
============================
給 Phase 8.20 前就存在的 news_items 補上 topic_category / topic_confidence /
topic_rationale / weighted_score。

設計：
  - **只用 keyword fast-path**（省 LLM 費用 + 無網路也能跑）。
    miss 的文章直接歸 'other' + confidence=0.0（rationale='backfill_keyword_miss'）。
  - **冪等**：已有 topic_category 的 row 不動（除非 --force）。
  - **無破壞**：只 UPDATE topic 相關欄位 + weighted_score，不動 status / 其它欄位。

用法：
    python -m scripts.backfill_topic_classifier           # 只跑 miss 的
    python -m scripts.backfill_topic_classifier --force   # 全部重跑
    python -m scripts.backfill_topic_classifier --llm     # miss 的打 LLM（較慢）

—— 2026-04-21 overnight, Cowork Claude
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional

from src import db as dbmod
from src.topic_classifier import (
    TopicClassification,
    classify_topic_keyword,
    classify_topic_llm,
    compute_weighted_score,
)


async def _classify(title: str, content: str, use_llm: bool) -> TopicClassification:
    kw = classify_topic_keyword(title, content)
    if kw is not None:
        return kw
    if use_llm:
        llm = await classify_topic_llm(title, content)
        if llm is not None:
            return llm
    return TopicClassification(
        category_id="other",
        confidence=0.0,
        rationale="backfill_keyword_miss",
    )


async def run(force: bool = False, use_llm: bool = False) -> int:
    dbmod.init_db()
    conn = dbmod.get_conn()
    try:
        if force:
            rows = conn.execute(
                "SELECT id, title, clean_markdown FROM news_items"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, clean_markdown FROM news_items "
                "WHERE topic_category IS NULL OR topic_category = ''"
            ).fetchall()
        total = len(rows)
        print(f"[backfill] 候選 {total} 筆 (force={force}, llm={use_llm})")

        # 先找每個 draft 的 confidence_score（算 weighted_score 用）
        # news_items → drafts 是 1:N，取 canonical(第一筆)最新的 score
        scores_map = {
            r[0]: float(r[1] or 0.0)
            for r in conn.execute(
                "SELECT news_id, MAX(confidence_score) FROM drafts GROUP BY news_id"
            ).fetchall()
        }

        updated = 0
        for row in rows:
            news_id, title, content = row[0], row[1] or "", row[2] or ""
            cls = await _classify(title, content, use_llm=use_llm)
            weight = dbmod.get_topic_weight(conn, cls.category_id, default=1.0)
            base = scores_map.get(news_id, 0.0)
            weighted = compute_weighted_score(base, weight)
            dbmod.set_news_topic(
                conn,
                news_id,
                category_id=cls.category_id,
                confidence=cls.confidence,
                rationale=cls.rationale,
                weighted_score=weighted,
            )
            updated += 1
            print(
                f"  · {news_id[:8]} | {cls.category_id:22} "
                f"weight={weight:.2f} base={base:.2f} → {weighted:.2f} "
                f"| {title[:40]}"
            )

        print(f"[backfill] 完成，更新 {updated} 筆")
        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="連已有 topic_category 的 row 也重跑")
    parser.add_argument("--llm", action="store_true",
                        help="keyword miss 的文章打 LLM 補分類（較慢、有 token 成本）")
    args = parser.parse_args()
    return asyncio.run(run(force=args.force, use_llm=args.llm))


if __name__ == "__main__":
    sys.exit(main())
