from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.verify_compose import verify_compose
from scripts.verify_publish import verify as verify_publish


GOOD_THREADS_TEXT = (
    "交通部公告新制下週上路，通勤規則確定改變。\n\n"
    "根據交通部公告，通勤族可能增加轉乘時間；出門前先查詢新班次。\n\n"
    "哪一段轉乘最需要交通部補上配套？\n\n"
    "#交通政策"
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE drafts(
          id TEXT PRIMARY KEY,title TEXT,confidence_score REAL,status TEXT,
          queue_status TEXT,generated_at TEXT,news_id TEXT
        );
        CREATE TABLE platform_drafts(
          draft_id TEXT,platform TEXT,char_count INTEGER,full_text TEXT
        );
        CREATE TABLE recovery_experiments(draft_id TEXT,platform TEXT);
        CREATE TABLE publish_log(
          id INTEGER PRIMARY KEY,draft_id TEXT,platform TEXT,platform_post_id TEXT,
          posted_at TEXT,success INTEGER,error_message TEXT
        );
        """
    )
    return conn


def _seed_threads_draft(conn: sqlite3.Connection, *, lineage: bool = True) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO drafts VALUES('d1','交通新制',0.9,'auto_approved','queued',?,'n1')",
        (now,),
    )
    text = GOOD_THREADS_TEXT
    conn.execute(
        "INSERT INTO platform_drafts VALUES('d1','threads',?,?)",
        (len(text), text),
    )
    if lineage:
        conn.execute("INSERT INTO recovery_experiments VALUES('d1','threads')")
    conn.commit()


def _seed_held_threads_draft(conn: sqlite3.Connection, draft_id: str = "held") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO drafts VALUES(?, '未通過品質稿', 0.8, 'pending_review', NULL, ?, 'n-held')",
        (draft_id, now),
    )
    text = "沒有來源，也沒有提供讀者可採取的具體行動。"
    conn.execute(
        "INSERT INTO platform_drafts VALUES(?, 'threads', ?, ?)",
        (draft_id, len(text), text),
    )
    conn.commit()


def _seed_nonqueued_quality_draft(conn: sqlite3.Connection) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO drafts VALUES('not-queued', '交通公告', 0.8, "
        "'pending_review', NULL, ?, 'n-not-queued')",
        (now,),
    )
    text = GOOD_THREADS_TEXT
    conn.execute(
        "INSERT INTO platform_drafts VALUES('not-queued', 'threads', ?, ?)",
        (len(text), text),
    )
    conn.commit()


def test_compose_verifier_accepts_exact_threads_scope() -> None:
    conn = _conn(); _seed_threads_draft(conn)
    assert verify_compose(
        conn, expected_platforms={"threads"}, since_minutes=30, recovery=True
    ) == 0


def test_compose_verifier_rejects_wrong_scope_or_missing_lineage() -> None:
    conn = _conn(); _seed_threads_draft(conn, lineage=False)
    assert verify_compose(
        conn, expected_platforms={"threads"}, since_minutes=30, recovery=True
    ) == 1
    assert verify_compose(
        conn,
        expected_platforms={"facebook", "instagram", "threads"},
        since_minutes=30,
        recovery=False,
    ) == 1


def test_recovery_verifier_allows_held_candidate_before_ready_draft() -> None:
    conn = _conn()
    _seed_held_threads_draft(conn)
    _seed_nonqueued_quality_draft(conn)
    _seed_threads_draft(conn)

    assert verify_compose(
        conn, expected_platforms={"threads"}, since_minutes=30, recovery=True
    ) == 0


def test_recovery_verifier_rejects_release_when_all_candidates_are_held() -> None:
    conn = _conn()
    _seed_held_threads_draft(conn)
    _seed_nonqueued_quality_draft(conn)

    assert verify_compose(
        conn, expected_platforms={"threads"}, since_minutes=30, recovery=True
    ) == 1


def test_compose_verifier_exact_boundary_excludes_prior_held_draft() -> None:
    conn = _conn()
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    conn.execute(
        "INSERT INTO drafts VALUES('old','舊稿',0.9,'pending_review',NULL,?,'n0')",
        (old,),
    )
    conn.execute(
        "INSERT INTO platform_drafts VALUES('old','threads',30,'無來源舊稿不應污染新 run')"
    )
    boundary = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    _seed_threads_draft(conn)

    assert verify_compose(
        conn,
        expected_platforms={"threads"},
        since_minutes=30,
        recovery=True,
        since_iso=boundary,
    ) == 0


def test_full_pipeline_passes_exact_compose_boundary_to_verifier() -> None:
    workflow = Path(".github/workflows/full_pipeline.yml").read_text(encoding="utf-8")
    assert "state/compose_started_at.txt" in workflow
    assert '--since-iso "$(cat state/compose_started_at.txt)"' in workflow


def test_publish_verifier_requires_success_for_requested_platform() -> None:
    conn = _conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO publish_log VALUES(1,'d1','threads','thread-1',?,1,NULL)",
        (now,),
    )
    conn.commit()
    assert verify_publish(30, True, {"threads"}, conn=conn) == 0
    assert verify_publish(30, True, {"facebook"}, conn=conn) == 1
