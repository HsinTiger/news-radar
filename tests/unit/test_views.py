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
           (id, news_id, persona_version, generated_at, status, queue_status,
            confidence_score)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("d1", "n1", "v1", now, "published", "published", 0.85),
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
    # Item 1.5 fold-in: confidence_score column surfaced from drafts.
    assert row["confidence_score"] == pytest.approx(0.85)


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


# ---------------------------------------------------------------------------
# Item 1.6: per-platform extras (Cowork option-a fix to view-coverage gap).
# ---------------------------------------------------------------------------
# The per-row columns on v_post_engagement_aggregated were already added
# in Item 1 (fb_comments/fb_shares/fb_reach, ig_comments/ig_shares/
# ig_saves/ig_reach, th_replies/th_reposts/th_quotes/th_views). Item 1.6
# extends v_topic_engagement_x_platform with the corresponding 30-day
# AVG columns. These tests exercise non-default fixture values for every
# engagement_stats column so the AVG path is observable.


def _insert_extras_fixture(conn: sqlite3.Connection) -> None:
    """One published draft × engagement on all 3 platforms, with non-default
    values for every column the per-platform-extras checks assert against.

    Distinct prime-ish values per (platform, metric) make any column-mixup
    bug visible in the assertion diff.
    """
    now = _now_iso()
    recent = _ago_iso(1)

    conn.execute(
        """INSERT INTO news_items
           (id, feed_name, feed_tier, url, title, published_at, fetched_at,
            status, topic_category, weighted_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "nx", "ExtrasFeed", "primary", "https://example.com/extras",
            "Extras Article", recent, recent, "published",
            "ai_model", 1.5,
        ),
    )
    conn.execute(
        """INSERT INTO drafts
           (id, news_id, persona_version, generated_at, status, queue_status,
            confidence_score)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("dx", "nx", "v1", now, "published", "published", 0.9),
    )
    # Facebook: likes=5, comments=13, shares=17, views=23, reach=29
    conn.execute(
        """INSERT INTO engagement_stats
           (draft_id, platform, platform_post_id, fetched_at,
            likes, comments, shares, saves, reposts, quotes, replies,
            views, reach)
           VALUES (?, 'facebook', 'pp_fb_x', ?, 5, 13, 17, 0, 0, 0, 0,
                   23, 29)""",
        ("dx", recent),
    )
    # Instagram: likes=7, comments=19, shares=31, saves=37, reach=41
    conn.execute(
        """INSERT INTO engagement_stats
           (draft_id, platform, platform_post_id, fetched_at,
            likes, comments, shares, saves, reposts, quotes, replies,
            views, reach)
           VALUES (?, 'instagram', 'pp_ig_x', ?, 7, 19, 31, 37, 0, 0, 0,
                   0, 41)""",
        ("dx", recent),
    )
    # Threads: likes=11, replies=43, reposts=47, quotes=53, views=59
    conn.execute(
        """INSERT INTO engagement_stats
           (draft_id, platform, platform_post_id, fetched_at,
            likes, comments, shares, saves, reposts, quotes, replies,
            views, reach)
           VALUES (?, 'threads', 'pp_th_x', ?, 11, 0, 0, 0, 47, 53, 43,
                   59, 0)""",
        ("dx", recent),
    )
    conn.commit()


def test_v_post_engagement_aggregated_per_platform_extras(view_db):
    """Per-row extras on v_post_engagement_aggregated.

    Columns were added in Item 1; Item 1.6 just asserts they're load-bearing
    so a future view edit can't silently regress them.
    """
    _insert_extras_fixture(view_db)
    row = view_db.execute(
        "SELECT * FROM v_post_engagement_aggregated WHERE draft_id = 'dx'"
    ).fetchone()
    assert row is not None

    # Facebook
    assert row["fb_likes"] == 5
    assert row["fb_comments"] == 13
    assert row["fb_shares"] == 17
    assert row["fb_reach"] == 29
    # Instagram
    assert row["ig_likes"] == 7
    assert row["ig_comments"] == 19
    assert row["ig_shares"] == 31
    assert row["ig_saves"] == 37
    assert row["ig_reach"] == 41
    # Threads
    assert row["th_likes"] == 11
    assert row["th_replies"] == 43
    assert row["th_reposts"] == 47
    assert row["th_quotes"] == 53
    assert row["th_views"] == 59


def test_v_topic_engagement_x_platform_per_platform_extras(view_db):
    """Item 1.6: full per-platform AVG metric set on the topic view."""
    _insert_extras_fixture(view_db)
    row = view_db.execute(
        "SELECT * FROM v_topic_engagement_x_platform "
        "WHERE topic_category = 'ai_model'"
    ).fetchone()
    assert row is not None
    # sample_count + likes path stay green (regression guard).
    assert row["sample_count"] == 1
    assert row["fb_avg_likes_30d"] == pytest.approx(5.0)
    assert row["ig_avg_likes_30d"] == pytest.approx(7.0)
    assert row["th_avg_likes_30d"] == pytest.approx(11.0)
    # Existing comments / replies AVG columns (since Item 1).
    assert row["fb_avg_comments_30d"] == pytest.approx(13.0)
    assert row["ig_avg_comments_30d"] == pytest.approx(19.0)
    assert row["th_avg_replies_30d"] == pytest.approx(43.0)
    # Item 1.6 new AVG columns.
    assert row["fb_avg_shares_30d"] == pytest.approx(17.0)
    assert row["fb_avg_reach_30d"] == pytest.approx(29.0)
    assert row["ig_avg_shares_30d"] == pytest.approx(31.0)
    assert row["ig_avg_saves_30d"] == pytest.approx(37.0)
    assert row["ig_avg_reach_30d"] == pytest.approx(41.0)
    assert row["th_avg_reposts_30d"] == pytest.approx(47.0)
    assert row["th_avg_quotes_30d"] == pytest.approx(53.0)
    assert row["th_avg_views_30d"] == pytest.approx(59.0)


# ---------------------------------------------------------------------------
# Item 1.5 fold-in: v_draft_hook_by_platform
# ---------------------------------------------------------------------------

# 50-char + 60-char strings (110 total) → FB hook = first 100 chars.
_FB_PART_A = "A" * 50          # 50 chars
_FB_PART_B = "B" * 60          # 60 chars
_FB_FULL = _FB_PART_A + _FB_PART_B  # 110 chars total

_IG_FULL_WITH_NEWLINE = "first line\nsecond line"
_IG_FULL_NO_NEWLINE = "no newlines here at all"

_TH_FULL = "T" * 35  # 35 chars → hook = first 30 chars


def test_v_draft_hook_by_platform_returns_rows(view_db):
    """Per-platform hook truncation rules + LEFT JOIN to engagement view."""
    _insert_min_fixture(view_db)

    now = _now_iso()
    # Use the existing fixture draft 'd1' for FB / Threads / IG-with-newline,
    # and a second draft 'd2' (no engagement) for the IG-no-newline case
    # so we exercise the LEFT JOIN NULL-engagement branch too.
    view_db.execute(
        """INSERT INTO platform_drafts
           (draft_id, platform, full_text, created_at)
           VALUES (?, ?, ?, ?)""",
        ("d1", "facebook", _FB_FULL, now),
    )
    view_db.execute(
        """INSERT INTO platform_drafts
           (draft_id, platform, full_text, created_at)
           VALUES (?, ?, ?, ?)""",
        ("d1", "instagram", _IG_FULL_WITH_NEWLINE, now),
    )
    view_db.execute(
        """INSERT INTO platform_drafts
           (draft_id, platform, full_text, created_at)
           VALUES (?, ?, ?, ?)""",
        ("d1", "threads", _TH_FULL, now),
    )
    # Second draft for IG-no-newline branch (not published → engagement NULL).
    view_db.execute(
        """INSERT INTO news_items
           (id, feed_name, feed_tier, url, title, published_at, fetched_at,
            status, topic_category, weighted_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "n2", "TestFeed", "primary", "https://example.com/b",
            "Second Article", now, now, "pending", "ai_model", 1.0,
        ),
    )
    view_db.execute(
        """INSERT INTO drafts
           (id, news_id, persona_version, generated_at, status, queue_status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("d2", "n2", "v1", now, "pending_review", "pending"),
    )
    view_db.execute(
        """INSERT INTO platform_drafts
           (draft_id, platform, full_text, created_at)
           VALUES (?, ?, ?, ?)""",
        ("d2", "instagram", _IG_FULL_NO_NEWLINE, now),
    )
    view_db.commit()

    assert _view_exists(view_db, "v_draft_hook_by_platform")

    rows = view_db.execute(
        "SELECT * FROM v_draft_hook_by_platform "
        "ORDER BY draft_id, platform"
    ).fetchall()
    by_key = {(r["draft_id"], r["platform"]): r for r in rows}

    # Facebook: hook = first 100 chars of 110-char full_text.
    fb = by_key[("d1", "facebook")]
    assert fb["hook"] == _FB_FULL[:100]
    assert len(fb["hook"]) == 100

    # Instagram (with newline): hook = pre-newline substring.
    ig_nl = by_key[("d1", "instagram")]
    assert ig_nl["hook"] == "first line"

    # Threads: hook = first 30 chars of 35-char full_text.
    th = by_key[("d1", "threads")]
    assert th["hook"] == _TH_FULL[:30]
    assert len(th["hook"]) == 30

    # Instagram (no newline): hook = full text unchanged.
    ig_plain = by_key[("d2", "instagram")]
    assert ig_plain["hook"] == _IG_FULL_NO_NEWLINE

    # Engagement metadata: d1 is published with engagement → fb_likes set;
    # d2 is unpublished → engagement columns NULL via LEFT JOIN.
    assert fb["fb_likes"] == 5
    assert fb["confidence_score"] == pytest.approx(0.85)
    assert ig_plain["fb_likes"] is None
    assert ig_plain["latest_engagement_at"] is None
