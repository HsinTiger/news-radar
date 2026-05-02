"""Unit tests for has_successful_publish — the publish idempotency guard.

Phase 9.5+ (2026-05-02): introduced after a Mac-shutdown incident caused
duplicate posts. The guard reads publish_log to decide whether to skip
the platform API call.

Strategy: build a temp SQLite DB with schema.sql, seed publish_log rows
matching the incident shape, exercise has_successful_publish, assert the
documented contract.

No subprocess. No Meta API. No live DB.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.db import has_successful_publish

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO_ROOT / "data" / "01_harvest" / "schema.sql"


@pytest.fixture
def conn(tmp_path):
    """Fresh schema-applied SQLite connection per test."""
    db_path = tmp_path / "idempotency_test.db"
    c = sqlite3.connect(str(db_path))
    c.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    c.commit()
    yield c
    c.close()


def _seed_publish_log(
    conn: sqlite3.Connection,
    *,
    draft_id: str,
    platform: str,
    success: bool,
    posted_at: str = "2026-05-02T05:05:54+00:00",
    error_message: str | None = None,
) -> None:
    """Insert a single publish_log row mirroring publisher's shape."""
    conn.execute(
        """
        INSERT INTO publish_log (draft_id, platform, platform_post_id, posted_at, success, error_message)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            platform,
            "fake_post_id_123" if success else None,
            posted_at,
            1 if success else 0,
            error_message,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Contract: returns True iff a success=1 row exists for (draft, platform)
# ---------------------------------------------------------------------------

def test_returns_false_when_publish_log_empty(conn):
    """No publish_log row at all → not yet published, retry is correct."""
    assert has_successful_publish(conn, "draft_x", "facebook") is False


def test_returns_true_after_successful_publish(conn):
    """Happy path: success=1 row → guard blocks duplicate."""
    _seed_publish_log(conn, draft_id="draft_x", platform="facebook", success=True)
    assert has_successful_publish(conn, "draft_x", "facebook") is True


def test_returns_false_when_only_failed_attempts_exist(conn):
    """Prior failure should NOT block a retry — only success rows do."""
    _seed_publish_log(
        conn, draft_id="draft_x", platform="instagram", success=False,
        error_message="image_prep: ratio=0.800 outside [0.81,1.9]",
    )
    assert has_successful_publish(conn, "draft_x", "instagram") is False


def test_isolation_across_platforms(conn):
    """A success on FB should not block IG / Threads for the same draft."""
    _seed_publish_log(conn, draft_id="draft_x", platform="facebook", success=True)
    assert has_successful_publish(conn, "draft_x", "facebook") is True
    assert has_successful_publish(conn, "draft_x", "instagram") is False
    assert has_successful_publish(conn, "draft_x", "threads") is False


def test_isolation_across_drafts(conn):
    """A success on draft_x.facebook should not block draft_y.facebook."""
    _seed_publish_log(conn, draft_id="draft_x", platform="facebook", success=True)
    assert has_successful_publish(conn, "draft_x", "facebook") is True
    assert has_successful_publish(conn, "draft_y", "facebook") is False


def test_mixed_history_success_wins(conn):
    """Pattern: failure → failure → eventual success. Guard returns True
    on the success row regardless of how many failures preceded it."""
    _seed_publish_log(conn, draft_id="d", platform="instagram", success=False,
                      posted_at="2026-05-01T22:00:45+00:00",
                      error_message="ratio rejected")
    _seed_publish_log(conn, draft_id="d", platform="instagram", success=False,
                      posted_at="2026-05-01T23:56:39+00:00",
                      error_message="ratio rejected")
    _seed_publish_log(conn, draft_id="d", platform="instagram", success=True,
                      posted_at="2026-05-02T06:30:00+00:00")
    assert has_successful_publish(conn, "d", "instagram") is True


def test_success_row_persists_blocks_subsequent_retries(conn):
    """Once a success exists, subsequent calls remain blocked — this is
    the load-bearing property for crash-recovery resilience."""
    _seed_publish_log(conn, draft_id="d", platform="facebook", success=True)
    for _ in range(5):
        assert has_successful_publish(conn, "d", "facebook") is True


def test_guard_uses_canonical_platform_names(conn):
    """publish_log stores 'facebook'/'instagram'/'threads' — caller MUST
    pass those, not 'fb'/'ig'. Wire-up code (run_pipeline.py) maps via
    PLATFORM_DB_NAME before calling."""
    _seed_publish_log(conn, draft_id="d", platform="facebook", success=True)
    # Canonical name → True
    assert has_successful_publish(conn, "d", "facebook") is True
    # Short form → False (not stored that way; this is the caller's bug
    # to avoid, not the guard's job to translate)
    assert has_successful_publish(conn, "d", "fb") is False
