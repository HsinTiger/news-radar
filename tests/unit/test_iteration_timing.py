from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.content_quality_guard import QUALITY_GUARD_VERSION
from src.iteration_engine import analyze_optimal_times


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE engagement_stats(
          id INTEGER PRIMARY KEY,draft_id TEXT,platform TEXT,platform_post_id TEXT,
          fetched_at TEXT,likes INTEGER,comments INTEGER,shares INTEGER,saves INTEGER,
          reposts INTEGER,quotes INTEGER,replies INTEGER,views INTEGER,reach INTEGER,
          raw_json TEXT,post_age_bucket INTEGER
        );
        CREATE TABLE publish_log(
          id INTEGER PRIMARY KEY,draft_id TEXT,platform TEXT,platform_post_id TEXT,
          posted_at TEXT,success INTEGER
        );
        CREATE TABLE recovery_experiments(draft_id TEXT,platform TEXT);
        CREATE TABLE content_quality_evaluations(
          draft_id TEXT,platform TEXT,stage TEXT,decision TEXT,guard_version TEXT
        );
        """
    )
    return conn


def _seed_post(
    conn: sqlite3.Connection,
    index: int,
    *,
    utc_hour: int,
    views: int,
    likes: int,
    guard_version: str = QUALITY_GUARD_VERSION,
    raw_json: str = "{}",
) -> None:
    posted = (datetime.now(timezone.utc) - timedelta(days=10)).replace(
        hour=utc_hour, minute=0, second=0, microsecond=0
    ).isoformat()
    draft_id = f"d{index}"
    post_id = f"p{index}"
    conn.execute(
        "INSERT INTO publish_log VALUES(?,?,?,?,?,1)",
        (index, draft_id, "threads", post_id, posted),
    )
    conn.execute(
        "INSERT INTO recovery_experiments VALUES(?,?)", (draft_id, "threads")
    )
    conn.execute(
        "INSERT INTO content_quality_evaluations VALUES(?,?,?,?,?)",
        (draft_id, "threads", "pre_publish", "pass", guard_version),
    )
    conn.execute(
        "INSERT INTO engagement_stats VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            index, draft_id, "threads", post_id, posted,
            likes, 0, 0, 0, 0, 0, 0, views, 0, raw_json, 168,
        ),
    )


def test_timing_uses_posted_hour_mature_current_guard_and_robust_distribution(monkeypatch) -> None:
    conn = _conn()
    for i, views in enumerate((100, 120, 140), start=1):
        _seed_post(conn, i, utc_hour=0, views=views, likes=1)
    for i, views in enumerate((500, 600, 700), start=4):
        _seed_post(conn, i, utc_hour=10, views=views, likes=5)
    _seed_post(conn, 7, utc_hour=10, views=9999, likes=99, guard_version="legacy-v2")
    monkeypatch.setattr("src.iteration_engine.dbmod.get_conn", lambda: conn)

    result = analyze_optimal_times("threads", minimum_complete_posts=6)

    assert result["threads"]["decision_ready"] is True
    assert result["threads"]["complete_posts"] == 6
    assert result["threads"]["top3_hours"][0]["hour"] == 18
    assert result["threads"]["top3_hours"][0]["median_primary"] == 600


def test_timing_fails_closed_without_current_168h_cohort(monkeypatch) -> None:
    conn = _conn()
    _seed_post(conn, 1, utc_hour=0, views=500, likes=10, guard_version="legacy-v2")
    monkeypatch.setattr("src.iteration_engine.dbmod.get_conn", lambda: conn)

    result = analyze_optimal_times("threads")

    assert result["status"] == "insufficient_data"
    assert result["guard_version"] == QUALITY_GUARD_VERSION
