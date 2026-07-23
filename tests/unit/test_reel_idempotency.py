from __future__ import annotations

import sqlite3

from scripts.pick_latest_draft import pick_latest_unpublished


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE drafts (
          id TEXT PRIMARY KEY, status TEXT, generated_at TEXT
        );
        CREATE TABLE publish_log (
          id INTEGER PRIMARY KEY, draft_id TEXT, platform TEXT, success INTEGER
        );
        INSERT INTO drafts VALUES ('old', 'published', '2026-01-01');
        INSERT INTO drafts VALUES ('new', 'published', '2026-01-02');
        """
    )
    return conn


def test_picker_skips_reel_already_published_to_target_platform() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO publish_log(draft_id, platform, success) VALUES ('new', 'instagram_reel', 1)"
    )
    assert pick_latest_unpublished(conn, "ig") == "old"


def test_failed_reel_attempt_remains_retryable() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO publish_log(draft_id, platform, success) VALUES ('new', 'instagram_reel', 0)"
    )
    assert pick_latest_unpublished(conn, "ig") == "new"


def test_platform_idempotency_is_independent() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO publish_log(draft_id, platform, success) VALUES ('new', 'instagram_reel', 1)"
    )
    assert pick_latest_unpublished(conn, "fb") == "new"
