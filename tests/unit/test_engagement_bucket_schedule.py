from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.engagement import TOLERANCE_HOURS, select_posts_to_poll


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
    assert TOLERANCE_HOURS == 0.75


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


def test_hourly_tick_at_minute_eleven_covers_every_publish_minute() -> None:
    # For any publish minute, one of the adjacent hourly :11 ticks is at most
    # 30 minutes from the desired age bucket, safely inside the 45-minute gate.
    for publish_minute in range(60):
        distance = abs(11 - publish_minute)
        nearest = min(distance, 60 - distance) / 60
        assert nearest <= TOLERANCE_HOURS
