from __future__ import annotations

import sqlite3
from pathlib import Path

from src import db as dbmod
from src.content_quality_guard import QualityIssue


ROOT = Path(__file__).resolve().parents[2]


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        (ROOT / "data/01_harvest/schema.sql").read_text(encoding="utf-8")
    )
    return conn


def test_quality_evidence_is_idempotent_and_stores_no_post_body() -> None:
    conn = _db()
    issue = QualityIssue(
        code="uncited_stat",
        severity="rewrite",
        message="attribute the number",
        evidence="成長 42%",
    )
    first = dbmod.record_quality_evaluation(
        conn,
        draft_id="draft-1",
        news_id="news-1",
        platform="threads",
        stage="compose",
        attempt=1,
        full_text="營收成長 42%",
        issues=[issue],
    )
    second = dbmod.record_quality_evaluation(
        conn,
        draft_id="draft-1",
        news_id="news-1",
        platform="threads",
        stage="compose",
        attempt=1,
        full_text="營收成長 42%",
        issues=[issue],
    )
    row = conn.execute("SELECT * FROM content_quality_evaluations").fetchone()
    assert first == ("rewrite", True)
    assert second == ("rewrite", False)
    assert row["rewrite_count"] == 1
    assert row["decision"] == "rewrite"
    assert "營收成長 42%" not in str(dict(row))
    assert "成長 42%" not in row["issues_json"]


def test_block_has_priority_over_rewrite() -> None:
    conn = _db()
    issues = [
        QualityIssue("rewrite-me", "rewrite", "rewrite", "weak"),
        QualityIssue("block-me", "block", "block", "fatal"),
    ]
    decision, inserted = dbmod.record_quality_evaluation(
        conn,
        draft_id="draft-2",
        news_id="news-2",
        platform="facebook",
        stage="pre_publish",
        attempt=1,
        full_text="bad body",
        issues=issues,
    )
    assert (decision, inserted) == ("block", True)
    row = conn.execute(
        "SELECT block_count,rewrite_count FROM content_quality_evaluations"
    ).fetchone()
    assert tuple(row) == (1, 1)
