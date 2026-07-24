from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.engagement import (
    MAX_LATE_HOURS,
    PollTask,
    _with_collection_audit,
    select_posts_to_poll,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE publish_log(
          id INTEGER PRIMARY KEY,draft_id TEXT,platform TEXT,
          platform_post_id TEXT,posted_at TEXT,success INTEGER
        );
        CREATE TABLE engagement_stats(
          draft_id TEXT,platform TEXT,post_age_bucket INTEGER
        );
        """
    )
    return conn


def test_hourly_tolerance_catches_posts_not_aligned_to_cron_minute() -> None:
    conn = _conn()
    now = datetime(2026, 7, 23, 12, 11, tzinfo=timezone.utc)
    conn.execute(
        "INSERT INTO publish_log VALUES(1,'d1','threads','p1',?,1)",
        ((now - timedelta(hours=1, minutes=35)).isoformat(),),
    )
    tasks = select_posts_to_poll(conn, now)
    assert [(task.draft_id, task.bucket) for task in tasks] == [("d1", 1)]
    assert MAX_LATE_HOURS == 1.25


def test_bucket_is_never_collected_before_nominal_age() -> None:
    conn = _conn()
    now = datetime(2026, 7, 23, 12, 11, tzinfo=timezone.utc)
    conn.execute(
        "INSERT INTO publish_log VALUES(1,'d1','threads','p1',?,1)",
        ((now - timedelta(minutes=59)).isoformat(),),
    )
    assert select_posts_to_poll(conn, now) == []


def test_bucket_expires_after_the_truthful_late_window() -> None:
    conn = _conn()
    now = datetime(2026, 7, 23, 12, 11, tzinfo=timezone.utc)
    conn.execute(
        "INSERT INTO publish_log VALUES(1,'d1','threads','p1',?,1)",
        ((now - timedelta(hours=2, minutes=16)).isoformat(),),
    )
    assert select_posts_to_poll(conn, now) == []


def test_collection_audit_preserves_actual_age_and_bucket() -> None:
    posted = datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc)
    task = PollTask("d1", "threads", "p1", 1, posted)
    raw, age = _with_collection_audit(
        {"data": []}, task, posted + timedelta(hours=1, minutes=35)
    )

    assert age == 1.5833
    assert raw["_collector"] == {
        "scheduled_bucket_hours": 1,
        "actual_post_age_hours": 1.5833,
        "late_by_hours": 0.5833,
    }


def test_each_bucket_is_idempotent() -> None:
    conn = _conn()
    now = datetime(2026, 7, 23, 12, 11, tzinfo=timezone.utc)
    conn.execute(
        "INSERT INTO publish_log VALUES(1,'d1','threads','p1',?,1)",
        ((now - timedelta(hours=24, minutes=20)).isoformat(),),
    )
    assert [task.bucket for task in select_posts_to_poll(conn, now)] == [24]
    conn.execute("INSERT INTO engagement_stats VALUES('d1','threads',24)")
    assert select_posts_to_poll(conn, now) == []


def test_hourly_tick_at_minute_eleven_fits_the_late_only_window() -> None:
    # When the pre-bucket :11 tick is too early, the following hourly tick is
    # less than one hour late for every possible publish minute.
    for publish_minute in range(60):
        late_minutes = (11 - publish_minute) % 60
        assert late_minutes / 60 <= MAX_LATE_HOURS
