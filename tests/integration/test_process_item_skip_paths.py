"""
News Radar · Integration test · process_item skip invariants (Phase 8.19)

Phase 8.19 移除了兩個 quality-crushing footguns：
  1. composer 回 None 時塞「緊急代班範本」到 queue（emergency template）
  2. scorer 回 None 時偽造 confidence_score=1.0 強推 auto-approve（暴力發布模式）

這個測試鎖定替代行為的合約：
  - scorer 雙路徑都失敗 → process_item 回 "skipped_no_llm"
  - composer 雙路徑都失敗 → process_item 回 "skipped_no_llm"
  - 任一種 skip 都不該建立 drafts / platform_drafts / publish_queue 列
  - 任一種 skip 都不該改 news_items.status（讓下一輪 cron Gemini 恢復後重試）

測試策略：monkeypatch `run_pipeline.score_news` / `run_pipeline.compose_multi_platform`
直接回 None，其餘流程不動，驗 DB 終態。
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone

import pytest

import run_pipeline
from src import db as dbmod


def _seed_fetched_news(
    conn: sqlite3.Connection, news_id: str = "n_skip"
) -> sqlite3.Row:
    """塞一筆 status='fetched'、內容夠長的新聞，回傳對應的 row。"""
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO news_items
        (id, feed_name, feed_tier, url, title, published_at, fetched_at,
         og_image_url, clean_markdown, word_count, status)
        VALUES (?, 'TestFeed', 'primary', ?, ?, ?, ?, ?, ?, 500, 'fetched')
        """,
        (
            news_id,
            f"https://example.com/{news_id}",
            f"Test article {news_id}",
            now_iso,
            now_iso,
            f"https://example.com/{news_id}/img.jpg",
            "Lorem ipsum " * 100,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM news_items WHERE id = ?", (news_id,)
    ).fetchone()
    assert row is not None
    return row


async def _noop_score_news(*args, **kwargs):
    """模擬 Gemini → Claude CLI 都失敗：scorer 回 None。"""
    return None


async def _noop_compose(*args, **kwargs):
    """模擬 Gemini → Claude CLI 都失敗：composer 回 None。"""
    return None


async def _fake_make_passthrough_score(monkeypatch):
    """給 composer-skip 測試用：讓 scorer 回一個高分 NewsScore，流程才會走到 composer。"""
    from src.schema import NewsScore, ScoreBreakdown

    async def _high_score(*_args, **_kwargs):
        return NewsScore(
            confidence_score=0.95,
            editorial_note="test editorial hook",
            score_breakdown=ScoreBreakdown(
                data_density=0.9,
                strategic_signal=0.9,
                news_novelty=0.9,
                persona_fit=0.9,
            ),
            primary_topic_tag="test",
        )

    monkeypatch.setattr(run_pipeline, "score_news", _high_score)


def test_scorer_fail_returns_skipped_no_llm(tmp_db, monkeypatch):
    """scorer 回 None 時：
       (a) process_item 回 "skipped_no_llm"
       (b) drafts / platform_drafts / publish_queue 都沒列
       (c) news_items.status 仍為 'fetched'（讓下一輪重試）
    """
    monkeypatch.setattr(run_pipeline, "score_news", _noop_score_news)

    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_scorer_fail")

        result = asyncio.run(run_pipeline.process_item(conn, row))

        assert result == "skipped_no_llm"

        # 沒有 draft 被建立
        drafts = conn.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE news_id = ?",
            ("n_scorer_fail",),
        ).fetchone()
        assert drafts["n"] == 0

        # news 狀態保持 fetched（下一輪 Gemini 恢復後會重跑）
        news = conn.execute(
            "SELECT status, drop_reason FROM news_items WHERE id = ?",
            ("n_scorer_fail",),
        ).fetchone()
        assert news["status"] == "fetched"
        assert news["drop_reason"] is None

        # publish_queue 不該有對應列
        queue_count = conn.execute(
            """SELECT COUNT(*) AS n FROM drafts
               WHERE news_id = ? AND queue_status IS NOT NULL""",
            ("n_scorer_fail",),
        ).fetchone()
        assert queue_count["n"] == 0


def test_composer_fail_returns_skipped_no_llm(tmp_db, monkeypatch):
    """scorer 過關、composer 回 None 時：
       (a) process_item 回 "skipped_no_llm"
       (b) 沒有 draft 進 queue（queue_status 保持 NULL）
       (c) news_items.status 沒被標成 'dropped'
    """
    # 先讓 scorer 回高分，確保流程走到 composer
    asyncio.run(_fake_make_passthrough_score(monkeypatch))
    monkeypatch.setattr(run_pipeline, "compose_multi_platform", _noop_compose)

    # 避開 media gating 真的連外網
    import src.image_manager as im
    async def _always_ok(*_args, **_kwargs):
        return True
    monkeypatch.setattr(im, "check_media_accessibility", _always_ok)

    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_composer_fail")

        result = asyncio.run(run_pipeline.process_item(conn, row))

        assert result == "skipped_no_llm"

        # 沒有 draft 被建立
        drafts = conn.execute(
            "SELECT COUNT(*) AS n FROM drafts WHERE news_id = ?",
            ("n_composer_fail",),
        ).fetchone()
        assert drafts["n"] == 0

        # 關鍵：沒有走「暴力發布」，status 不該是 dropped
        news = conn.execute(
            "SELECT status FROM news_items WHERE id = ?",
            ("n_composer_fail",),
        ).fetchone()
        assert news["status"] == "fetched"


def test_scorer_fail_does_not_fabricate_confidence(tmp_db, monkeypatch):
    """Regression guard：確保舊版『scorer-fail 塞 confidence_score=1.0 強推』沒復活。
    若未來有人重新加入 fallback 偽分數，這個測試會抓到。
    """
    monkeypatch.setattr(run_pipeline, "score_news", _noop_score_news)

    with dbmod.get_conn() as conn:
        row = _seed_fetched_news(conn, "n_regress")

        asyncio.run(run_pipeline.process_item(conn, row))

        # 任何 draft 紀錄都不該出現；舊 bug 會塞一筆 confidence_score=1.0
        fabricated = conn.execute(
            """SELECT COUNT(*) AS n FROM drafts
               WHERE confidence_score >= 0.99""",
        ).fetchone()
        assert fabricated["n"] == 0, (
            "偵測到 confidence_score >= 0.99 的 draft。"
            "Phase 8.19 已移除暴力發布偽造分數，若此測試 fail 表示 footgun 復活。"
        )
