from __future__ import annotations

import sqlite3

from src import db as dbmod
from src.recovery_mode import editorial_mandate_for, rank_candidates, record_experiments


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE topic_weights(category_id TEXT PRIMARY KEY,weight REAL);
        INSERT INTO topic_weights VALUES('current_affairs',1.6);
        INSERT INTO topic_weights VALUES('tech_product_launch',1.55);
        INSERT INTO topic_weights VALUES('other',0.7);
        CREATE TABLE recovery_experiments(
          id TEXT PRIMARY KEY,draft_id TEXT,platform TEXT,experiment_type TEXT,
          hypothesis TEXT,baseline_followers INTEGER,baseline_primary_metric TEXT,
          baseline_primary_value REAL,baseline_captured_at TEXT,content_format TEXT,
          actual_format TEXT,actual_format_at TEXT,topic TEXT,created_at TEXT,
          UNIQUE(draft_id,platform)
        );
        """
    )
    return conn


def test_rank_candidates_applies_weight_before_freshness() -> None:
    conn = _conn()
    rows = [
        {"title": "一般消息", "clean_markdown": "沒有特定分類", "published_at": "2026-07-24T10:00:00Z"},
        {"title": "食安事件擴大", "clean_markdown": "食安與產品召回", "published_at": "2026-07-24T09:00:00Z"},
    ]
    ranked = rank_candidates(conn, rows)
    assert ranked[0]["title"] == "食安事件擴大"


def test_rank_candidates_prefers_primary_source_inside_same_topic() -> None:
    conn = _conn()
    rows = [
        {
            "title": "較新的二手報導",
            "topic_category": "current_affairs",
            "feed_tier": "secondary",
            "source_type": "article",
            "published_at": "2026-07-24T10:00:00Z",
        },
        {
            "title": "較早的第一手來源",
            "topic_category": "current_affairs",
            "feed_tier": "primary",
            "source_type": "article",
            "published_at": "2026-07-24T09:00:00Z",
        },
    ]
    ranked = rank_candidates(conn, rows)
    assert ranked[0]["title"] == "較早的第一手來源"


def test_rank_candidates_downranks_social_source() -> None:
    conn = _conn()
    rows = [
        {
            "title": "社群轉述",
            "topic_category": "current_affairs",
            "feed_tier": "primary",
            "source_type": "social",
            "published_at": "2026-07-24T10:00:00Z",
        },
        {
            "title": "正式文章",
            "topic_category": "current_affairs",
            "feed_tier": "secondary",
            "source_type": "article",
            "published_at": "2026-07-24T09:00:00Z",
        },
    ]
    ranked = rank_candidates(conn, rows)
    assert ranked[0]["title"] == "正式文章"


def test_record_experiments_is_platform_specific() -> None:
    conn = _conn()
    record_experiments(
        conn,
        draft_id="draft-1",
        platforms={"threads", "instagram"},
        topic="tech_product_launch",
        content_format="carousel",
        created_at="2026-07-24T00:00:00Z",
    )
    rows = conn.execute(
        "SELECT platform,experiment_type,baseline_followers FROM recovery_experiments ORDER BY platform"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("instagram", "format", 9),
        ("threads", "utility", 3748),
    ]
    dbmod.mark_recovery_actual_format(
        conn, "draft-1", "instagram", "feed", "2026-07-24T01:00:00Z"
    )
    assert conn.execute(
        "SELECT actual_format FROM recovery_experiments WHERE platform='instagram'"
    ).fetchone()[0] == "feed"


def test_editorial_mandate_matches_persisted_experiment_type() -> None:
    mandate = editorial_mandate_for(
        {"threads", "instagram"}, "tech_product_launch"
    )
    assert "threads: type=utility" in mandate
    assert "instagram: type=format" in mandate
