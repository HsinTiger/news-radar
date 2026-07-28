import re
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _worker_post_upsert_sql() -> str:
    source = (ROOT / "cloudflare-worker" / "worker.js").read_text(encoding="utf-8")
    match = re.search(
        r"`(INSERT INTO platform_posts\(.*?updated_at=excluded\.updated_at)`",
        source,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1)


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


def test_worker_post_sync_nulls_only_unknown_submission_foreign_keys() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        (ROOT / "cloudflare-worker/migrations/0001_operational_control.sql").read_text(
            encoding="utf-8"
        )
    )
    sql = _worker_post_upsert_sql()
    tail = (
        "threads",
        "carousel",
        "thread-1",
        "published",
        "title",
        "tw_stocks",
        None,
        "2099-01-01T00:00:00Z",
        "2099-01-01T00:00:00Z",
        "2099-01-01T00:00:00Z",
    )

    conn.execute(sql, ("post-direct", "draft-1", "owner-direct-key", *tail))
    assert conn.execute(
        "SELECT submission_id FROM platform_posts WHERE id='post-direct'"
    ).fetchone()[0] is None

    conn.execute(
        """INSERT INTO submissions(
          id,idempotency_key,target,source_type,content,requested_mode,status,
          created_at,updated_at
        ) VALUES('submission-1','key-1','meta','text','body','publish_now',
                 'queued','2099-01-01T00:00:00Z','2099-01-01T00:00:00Z')"""
    )
    controlled_tail = (*tail[:2], "thread-2", *tail[3:])
    conn.execute(
        sql,
        ("post-controlled", "draft-2", "submission-1", *controlled_tail),
    )
    assert conn.execute(
        "SELECT submission_id FROM platform_posts WHERE id='post-controlled'"
    ).fetchone()[0] == "submission-1"
