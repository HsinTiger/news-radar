import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _insert_post(
    conn: sqlite3.Connection,
    *,
    row_id: str,
    draft_id: str,
    status: str,
    platform_post_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO platform_posts(
          id,draft_id,platform,format,platform_post_id,status,created_at,updated_at
        ) VALUES(?,?,'threads','feed',?,?,?,?)
        """,
        (
            row_id,
            draft_id,
            platform_post_id,
            status,
            "2099-01-01T00:00:00Z",
            "2099-01-02T00:00:00Z",
        ),
    )


def test_migration_reconciles_superseded_failure_and_enforces_one_row() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        (ROOT / "cloudflare-worker/migrations/0001_operational_control.sql").read_text(
            encoding="utf-8"
        )
    )
    _insert_post(
        conn,
        row_id="legacy-failed",
        draft_id="draft-1",
        status="failed",
        platform_post_id=None,
    )
    _insert_post(
        conn,
        row_id="legacy-published",
        draft_id="draft-1",
        status="published",
        platform_post_id="threads-1",
    )
    _insert_post(
        conn,
        row_id="genuine-failure",
        draft_id="draft-2",
        status="failed",
        platform_post_id=None,
    )
    conn.executescript(
        (ROOT / "cloudflare-worker/migrations/0006_canonical_post_identity.sql").read_text(
            encoding="utf-8"
        )
    )

    rows = conn.execute(
        "SELECT id,draft_id,status,platform_post_id FROM platform_posts ORDER BY draft_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("post_draft-1_threads_feed", "draft-1", "published", "threads-1"),
        ("post_draft-2_threads_feed", "draft-2", "failed", None),
    ]
    with pytest.raises(sqlite3.IntegrityError):
        _insert_post(
            conn,
            row_id="another-id",
            draft_id="draft-1",
            status="failed",
            platform_post_id=None,
        )
