from __future__ import annotations

import sqlite3

from src import db as dbmod
from src.content_quality_guard import QUALITY_GUARD_VERSION


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE drafts(
          id TEXT PRIMARY KEY,news_id TEXT,status TEXT,queue_status TEXT
        );
        CREATE TABLE news_items(
          id TEXT PRIMARY KEY,published_at TEXT,title TEXT,url TEXT,
          og_image_url TEXT,og_video_url TEXT,og_video_is_direct INTEGER,
          topic_category TEXT
        );
        CREATE TABLE platform_drafts(draft_id TEXT,platform TEXT);
        CREATE TABLE publish_log(
          draft_id TEXT,platform TEXT,success INTEGER
        );
        CREATE TABLE recovery_experiments(draft_id TEXT,platform TEXT);
        CREATE TABLE content_quality_evaluations(
          id INTEGER PRIMARY KEY AUTOINCREMENT,draft_id TEXT,platform TEXT,
          stage TEXT,guard_version TEXT,decision TEXT
        );
        """
    )
    return conn


def _draft(conn: sqlite3.Connection, draft_id: str = "d1") -> None:
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?)",
        (
            "n1",
            "2026-07-24T12:00:00+00:00",
            "台灣公共利益新聞",
            "https://example.test/n1",
            "https://example.test/image.jpg",
            None,
            0,
            "tw_politics",
        ),
    )
    conn.execute("INSERT INTO drafts VALUES(?,?,?,?)", (draft_id, "n1", "auto_approved", "queued"))
    conn.execute("INSERT INTO platform_drafts VALUES(?,?)", (draft_id, "threads"))
    conn.execute("INSERT INTO recovery_experiments VALUES(?,?)", (draft_id, "threads"))


def _quality(
    conn: sqlite3.Connection,
    *,
    decision: str,
    version: str = QUALITY_GUARD_VERSION,
    platform: str = "threads",
) -> None:
    conn.execute(
        """
        INSERT INTO content_quality_evaluations(
          draft_id,platform,stage,guard_version,decision
        ) VALUES('d1',?,'compose',?,?)
        """,
        (platform, version, decision),
    )


def test_recovery_picker_excludes_experiment_without_current_guard_evidence() -> None:
    conn = _conn()
    _draft(conn)

    assert dbmod.pick_freshest_queued(
        conn, platforms={"threads"}, recovery_only=True
    ) is None


def test_recovery_picker_accepts_current_guard_pass() -> None:
    conn = _conn()
    _draft(conn)
    _quality(conn, decision="pass")

    row = dbmod.pick_freshest_queued(
        conn, platforms={"threads"}, recovery_only=True
    )

    assert row is not None
    assert row["id"] == "d1"


def test_recovery_picker_uses_latest_current_guard_decision() -> None:
    conn = _conn()
    _draft(conn)
    _quality(conn, decision="pass")
    _quality(conn, decision="rewrite")

    assert dbmod.pick_freshest_queued(
        conn, platforms={"threads"}, recovery_only=True
    ) is None


def test_recovery_picker_rejects_stale_guard_version_and_platform_mismatch() -> None:
    conn = _conn()
    _draft(conn)
    _quality(conn, decision="pass", version="2026-07-24.taiwan-daily-v6")
    _quality(conn, decision="pass", platform="facebook")

    assert dbmod.pick_freshest_queued(
        conn, platforms={"threads"}, recovery_only=True
    ) is None
