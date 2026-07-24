from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

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
            "title": "較新的台灣二手報導",
            "topic_category": "current_affairs",
            "feed_tier": "secondary",
            "source_type": "article",
            "published_at": "2026-07-24T10:00:00Z",
        },
        {
            "title": "較早的台灣第一手來源",
            "topic_category": "current_affairs",
            "feed_tier": "primary",
            "source_type": "article",
            "published_at": "2026-07-24T09:00:00Z",
        },
    ]
    ranked = rank_candidates(conn, rows)
    assert ranked[0]["title"] == "較早的台灣第一手來源"


def test_rank_candidates_downranks_social_source() -> None:
    conn = _conn()
    rows = [
        {
            "title": "台灣社群轉述",
            "topic_category": "current_affairs",
            "feed_tier": "primary",
            "source_type": "social",
            "published_at": "2026-07-24T10:00:00Z",
        },
        {
            "title": "台灣正式文章",
            "topic_category": "current_affairs",
            "feed_tier": "secondary",
            "source_type": "article",
            "published_at": "2026-07-24T09:00:00Z",
        },
    ]
    ranked = rank_candidates(conn, rows)
    assert ranked[0]["title"] == "台灣正式文章"


def test_rank_candidates_fail_closed_outside_taiwan_editorial_scope() -> None:
    conn = _conn()
    rows = [
        {
            "title": "New AI model launches overseas",
            "topic_category": "tech_product_launch",
            "feed_tier": "primary",
            "source_type": "article",
            "published_at": "2026-07-24T10:00:00Z",
        },
        {
            "title": "食藥署公布台灣食品回收批號",
            "topic_category": "current_affairs",
            "feed_tier": "primary",
            "source_type": "article",
            "published_at": "2026-07-24T09:00:00Z",
        },
    ]
    ranked = rank_candidates(conn, rows)
    assert [row["title"] for row in ranked] == ["食藥署公布台灣食品回收批號"]


def test_global_disaster_from_taiwan_media_is_not_automatically_taiwan_relevant() -> None:
    conn = _conn()
    row = {
        "title": "南亞暴雨釀洪災上百人下落不明",
        "topic_category": "current_affairs",
        "feed_name": "公視新聞 PTS",
        "published_at": "2026-07-24T10:00:00Z",
    }
    assert rank_candidates(conn, [row]) == []


def test_public_consequence_outranks_ceremonial_politics() -> None:
    conn = _conn()
    rows = [
        {
            "title": "立法院長接見訪賓並合影",
            "topic_category": "current_affairs",
            "feed_tier": "primary",
            "published_at": "2026-07-24T10:00:00Z",
        },
        {
            "title": "食安不合格產品回收批號公布",
            "topic_category": "current_affairs",
            "feed_tier": "secondary",
            "published_at": "2026-07-24T09:00:00Z",
        },
    ]
    assert rank_candidates(conn, rows)[0]["title"] == "食安不合格產品回收批號公布"


def test_future_dated_row_cannot_evict_current_taiwan_candidates() -> None:
    conn = _conn()
    now = datetime.now(timezone.utc)
    rows = [
        {
            "title": "台灣食安回收公告",
            "topic_category": "current_affairs",
            "published_at": now.isoformat(),
        },
        {
            "title": "Future poison row",
            "topic_category": "other",
            "published_at": (now + timedelta(days=3650)).isoformat(),
        },
    ]
    ranked = rank_candidates(conn, rows)
    assert [row["title"] for row in ranked] == ["台灣食安回收公告"]


def test_rank_candidates_keeps_owner_submission_for_one_off_post() -> None:
    conn = _conn()
    row = {
        "title": "Owner viewpoint",
        "topic_category": "other",
        "feed_name": "user_submission",
        "tags": '["user_submission"]',
        "published_at": "2026-07-24T10:00:00Z",
    }
    assert rank_candidates(conn, [row]) == [row]


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
    assert "same evidence/response/correction standard" in mandate
