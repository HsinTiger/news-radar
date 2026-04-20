"""
News Radar · Integration test · Publish queue state machine (Phase 8.18/8.19)

驗證目標：
1. enqueue_draft → queue_status = "queued"
2. pick_freshest_queued 選中「published_at 最新」的那一篇（不是最老的）
3. mark_queue_published → queue_status = "published"
4. mark_queue_stale_except → 其他還 queued 的變 stale
5. count_queue_status 正確回報數量

這個測試不走 LLM，只驗 DB 層的 queue semantics 沒壞。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from src import db as dbmod


def _now_minus_hours(h: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


def _seed_news(conn: sqlite3.Connection, news_id: str, published_at: str) -> None:
    """直接 insert 一筆假新聞（不走 Pydantic，避開序列化細節）。"""
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO news_items
        (id, feed_name, feed_tier, url, title, published_at, fetched_at,
         og_image_url, clean_markdown, word_count, status)
        VALUES (?, 'TestFeed', 'primary', ?, ?, ?, ?, ?, '正文', 200, 'scored')
        """,
        (news_id, f"https://example.com/{news_id}", f"Title {news_id}",
         published_at, datetime.now(timezone.utc).isoformat(),
         f"https://example.com/{news_id}/img.jpg"),
    )
    conn.commit()


def _seed_approved_draft(
    conn: sqlite3.Connection, draft_id: str, news_id: str,
) -> None:
    """直接 insert 一筆已 auto_approved 但未入 queue 的 draft。
    run_pipeline.py 的 compose-only 路徑會做到這一步，然後呼叫 enqueue_draft。
    """
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO drafts
        (id, news_id, persona_version,
         confidence_score, score_breakdown, full_text, llm_provider, llm_model,
         input_tokens, output_tokens, cached_tokens, cost_usd, generated_at,
         status)
        VALUES (?, ?, 'v1', 0.92, ?, '正文', 'gemini',
                'gemini-2.0-flash-lite', 100, 200, 0, 0.001, ?,
                'auto_approved')
        """,
        (draft_id, news_id,
         json.dumps({"data_density": 0.9, "strategic_signal": 0.9,
                     "news_novelty": 0.8, "persona_fit": 0.9}),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def test_enqueue_sets_queue_status(tmp_db):
    """單一 enqueue_draft 後 queue_status 正確變 queued。"""
    with dbmod.get_conn() as conn:
        _seed_news(conn, "n1", _now_minus_hours(1))
        _seed_approved_draft(conn, "d1", "n1")

        dbmod.enqueue_draft(conn, "d1", publish_at="2026-04-20T00:00:00+00:00")

        counts = dbmod.count_queue_status(conn)
        assert counts.get("queued") == 1


def test_pick_freshest_queued_prefers_newest_news(tmp_db):
    """
    最關鍵：queue 裡有三個 queued，每個對應不同 published_at 的 news，
    pick_freshest_queued 必須挑 news.published_at 最新的那個。
    """
    with dbmod.get_conn() as conn:
        _seed_news(conn, "old", _now_minus_hours(6))
        _seed_news(conn, "mid", _now_minus_hours(3))
        _seed_news(conn, "new", _now_minus_hours(1))
        _seed_approved_draft(conn, "d_old", "old")
        _seed_approved_draft(conn, "d_mid", "mid")
        _seed_approved_draft(conn, "d_new", "new")

        for draft_id in ("d_old", "d_mid", "d_new"):
            dbmod.enqueue_draft(conn, draft_id, publish_at="2026-04-20T00:00:00+00:00")

        row = dbmod.pick_freshest_queued(conn)
        assert row is not None
        assert row["id"] == "d_new"  # 最新 news 對應的 draft


def test_mark_queue_published_transitions_status(tmp_db):
    """mark_queue_published 之後，queue_status = published，再選時就不會被選到。"""
    with dbmod.get_conn() as conn:
        _seed_news(conn, "n1", _now_minus_hours(1))
        _seed_approved_draft(conn, "d1", "n1")
        dbmod.enqueue_draft(conn, "d1", publish_at="2026-04-20T00:00:00+00:00")

        row = dbmod.pick_freshest_queued(conn)
        assert row is not None and row["id"] == "d1"

        dbmod.mark_queue_published(conn, "d1")

        # 再選就沒了
        assert dbmod.pick_freshest_queued(conn) is None

        counts = dbmod.count_queue_status(conn)
        assert counts.get("published") == 1
        assert counts.get("queued", 0) == 0


def test_mark_queue_stale_except_keeps_only_one(tmp_db):
    """mark_queue_stale_except 把其他 queued 變 stale，只留指定的那個。"""
    with dbmod.get_conn() as conn:
        for i in range(3):
            _seed_news(conn, f"n{i}", _now_minus_hours(i + 1))
            _seed_approved_draft(conn, f"d{i}", f"n{i}")
            dbmod.enqueue_draft(conn, f"d{i}", publish_at="2026-04-20T00:00:00+00:00")

        # d0 對應 n0 (1h前) 是最新
        dbmod.mark_queue_stale_except(conn, keep_draft_id="d0")

        counts = dbmod.count_queue_status(conn)
        assert counts.get("queued") == 1, f"expected 1 queued, got {counts}"
        assert counts.get("stale", 0) == 2


def test_mark_queue_failed_records_error(tmp_db):
    with dbmod.get_conn() as conn:
        _seed_news(conn, "n1", _now_minus_hours(1))
        _seed_approved_draft(conn, "d1", "n1")
        dbmod.enqueue_draft(conn, "d1", publish_at="2026-04-20T00:00:00+00:00")

        dbmod.mark_queue_failed(conn, "d1", reason="Meta API 500")

        counts = dbmod.count_queue_status(conn)
        assert counts.get("failed") == 1
        # drafts.status 也該反映 publish_failed（caller 合約）


def test_pick_fallback_any_approved(tmp_db):
    """queue 空、但有舊的 auto_approved draft 時，fallback 能挑到它。"""
    with dbmod.get_conn() as conn:
        # 模擬一個沒進 queue 但 status=auto_approved 的舊 draft
        _seed_news(conn, "old", _now_minus_hours(5))
        _seed_approved_draft(conn, "d_old", "old")
        # 沒呼叫 enqueue_draft，queue_status 仍為 NULL

        row = dbmod.pick_fallback_any_approved(conn)
        assert row is not None
        assert row["id"] == "d_old"

        # 比之下 pick_freshest_queued 應該回 None
        assert dbmod.pick_freshest_queued(conn) is None


def test_count_queue_status_empty_db(tmp_db):
    """空 DB 時 count_queue_status 不該 crash。"""
    with dbmod.get_conn() as conn:
        counts = dbmod.count_queue_status(conn)
        assert isinstance(counts, dict)
        assert counts.get("queued", 0) == 0
