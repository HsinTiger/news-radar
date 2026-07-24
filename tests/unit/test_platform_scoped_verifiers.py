from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from scripts.verify_compose import verify_compose
from scripts.verify_publish import verify as verify_publish


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
    text = "根據交通部公告，新制下週上路。對一般通勤者的實際影響是轉乘時間可能增加，可以先檢查班次。"
    conn.execute(
        "INSERT INTO platform_drafts VALUES('d1','threads',?,?)",
        (len(text), text),
    )
    if lineage:
        conn.execute("INSERT INTO recovery_experiments VALUES('d1','threads')")
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
