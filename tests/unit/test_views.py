"""News Radar · Unit tests for SQL substrate views (Phase 9 Item 1).

Each test:
  1. Creates a temp sqlite DB.
  2. Sources schema.sql, then views.sql.
  3. Inserts minimal fixture rows in the parent tables the view reads from.
  4. Asserts CREATE VIEW exists in sqlite_master AND that
     `SELECT * FROM v_<name>` returns ≥1 row.

No Meta API. No live DB writes. Mirrors tests/conftest.py::tmp_db pattern
but doesn't go through src.db.init_db (we want tight control over which
tables get fixtured and to keep these tests independent of unrelated
migrations / topic_weights seeding).

Spec : PM_Radar/specs/phase_9_implementation_plan.md §3 Item 1
Canon : PM_Radar/roadmap/phase_9_unified_reflector.md §6/§7/§8.3
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO_ROOT / "data" / "01_harvest" / "schema.sql"
_VIEWS_PATH = _REPO_ROOT / "data" / "01_harvest" / "views.sql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )


@pytest.fixture
def view_db(tmp_path):
    """Build a fresh sqlite DB at tmp_path with schema + views applied."""
    db_path = tmp_path / "views_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(_VIEWS_PATH.read_text(encoding="utf-8"))
    yield conn
    conn.close()


def _insert_min_fixture(conn: sqlite3.Connection) -> None:
    """One published draft × one news_item × engagement on all 3 platforms.

    Dates are recent so the 7d / 14d / 30d windowed views all see the row.
    """
    now = _now_iso()
    recent = _ago_iso(1)  # within 7d / 14d / 30d windows

    conn.execute(
        """INSERT INTO news_items
           (id, feed_name, feed_tier, url, title, published_at, fetched_at,
            status, topic_category, weighted_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "n1",
            "TestFeed",
            "primary",
            "https://example.com/a",
            "Test Article",
            recent,
            recent,
            "published",
            "ai_model",
            1.5,
        ),
    )
    conn.execute(
        """INSERT INTO drafts
           (id, news_id, persona_version, generated_at, status, queue_status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("d1", "n1", "v1", now, "published", "published"),
    )
    for platform, likes in (("facebook", 5), ("instagram", 7), ("threads", 11)):
        conn.execute(
            """INSERT INTO engagement_stats
               (draft_id, platform, platform_post_id, fetched_at,
                likes, comments, shares, replies, views, reach)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("d1", platform, f"pp_{platform}", recent, likes, 1, 1, 1, 100, 50),
        )
    conn.commit()


def _view_exists(conn: sqlite3.Connection, view_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",
        (view_name,),
    ).fetchone()
    return row is not None


def test_v_post_engagement_aggregated_returns_rows(view_db):
    _insert_min_fixture(view_db)
    assert _view_exists(view_db, "v_post_engagement_aggregated")
    rows = view_db.execute(
        "SELECT * FROM v_post_engagement_aggregated"
    ).fetchall()
    assert len(rows) >= 1
    row = rows[0]
    # latest-snapshot subqueries should pull each platform's likes
    assert row["fb_likes"] == 5
    assert row["ig_likes"] == 7
    assert row["th_likes"] == 11
    assert row["draft_id"] == "d1"
    assert row["latest_engagement_at"] is not None


def test_v_drafts_with_outcome_returns_rows(view_db):
    _insert_min_fixture(view_db)
    assert _view_exists(view_db, "v_drafts_with_outcome")
    rows = view_db.execute(
        "SELECT * FROM v_drafts_with_outcome"
    ).fetchall()
    assert len(rows) >= 1
    # NTILE(4) on a single row should still yield a value (1)
    assert rows[0]["engagement_quartile"] is not None


def test_v_feed_yield_7d_returns_rows(view_db):
    _insert_min_fixture(view_db)
    assert _view_exists(view_db, "v_feed_yield_7d")
    rows = view_db.execute(
        "SELECT * FROM v_feed_yield_7d"
    ).fetchall()
    assert len(rows) >= 1
    row = rows[0]
    assert row["feed_name"] == "TestFeed"
    assert row["publish_count_7d"] == 1
    assert row["fetch_count_7d"] == 1
    # one published item on all 3 platforms with likes>0 → ratio 1.0
    assert row["engagement_yield_ratio"] == pytest.approx(1.0)


def test_v_topic_engagement_x_platform_returns_rows(view_db):
    _insert_min_fixture(view_db)
    assert _view_exists(view_db, "v_topic_engagement_x_platform")
    rows = view_db.execute(
        "SELECT * FROM v_topic_engagement_x_platform"
    ).fetchall()
    assert len(rows) >= 1
    row = rows[0]
    assert row["topic_category"] == "ai_model"
    assert row["sample_count"] == 1
    assert row["fb_avg_likes_30d"] == pytest.approx(5.0)
    assert row["ig_avg_likes_30d"] == pytest.approx(7.0)
    assert row["th_avg_likes_30d"] == pytest.approx(11.0)
