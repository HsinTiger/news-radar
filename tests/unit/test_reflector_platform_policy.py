from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.reflector.platform_policy import (
    cadence_for_target,
    run_review,
)
from src.reflector.proposals import read_proposals
from src.schedule_policy import load_policy


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "data/01_harvest/schema.sql"
POLICY = ROOT / "config/social_automation_policy.json"


def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def _seed_window(
    conn: sqlite3.Connection,
    platform: str,
    *,
    now: datetime,
    baseline_score: int,
    current_score: int,
    current_error: bool = False,
) -> None:
    for period, score, age_start in (
        ("baseline", baseline_score, 15),
        ("current", current_score, 1),
    ):
        for index in range(12):
            draft_id = f"{platform}-{period}-{index}"
            post_id = f"post-{draft_id}"
            posted_at = now - timedelta(days=age_start, minutes=index)
            conn.execute(
                """
                INSERT INTO publish_log(
                  draft_id,platform,platform_post_id,posted_at,success
                ) VALUES(?,?,?,?,1)
                """,
                (draft_id, platform, post_id, posted_at.isoformat()),
            )
            raw = {"error": {"code": 100}} if current_error and period == "current" else {}
            values = {
                "likes": score,
                "comments": 0,
                "shares": 0,
                "saves": 0,
                "replies": 0,
                "reposts": 0,
                "quotes": 0,
                "views": score * 200 if platform == "threads" else 0,
                "reach": score * 100 if platform == "instagram" else 0,
            }
            conn.execute(
                """
                INSERT INTO engagement_stats(
                  draft_id,platform,platform_post_id,fetched_at,likes,comments,
                  shares,saves,replies,reposts,quotes,views,reach,clicks,raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    draft_id,
                    platform,
                    post_id,
                    now.isoformat(),
                    values["likes"],
                    values["comments"],
                    values["shares"],
                    values["saves"],
                    values["replies"],
                    values["reposts"],
                    values["quotes"],
                    values["views"],
                    values["reach"],
                    0,
                    json.dumps(raw),
                ),
            )
    conn.commit()


def test_only_healthy_threads_evidence_creates_cadence_proposal(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    proposals_dir = tmp_path / "proposals"
    conn = _db(db_path)
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    _seed_window(
        conn,
        "threads",
        now=now,
        baseline_score=2,
        current_score=4,
    )
    _seed_window(
        conn,
        "facebook",
        now=now,
        baseline_score=2,
        current_score=4,
        current_error=True,
    )
    _seed_window(
        conn,
        "instagram",
        now=now,
        baseline_score=1,
        current_score=0,
    )

    reviews = run_review(
        conn,
        load_policy(POLICY),
        now=now,
        proposals_db_path=db_path,
        proposals_dir=proposals_dir,
    )
    by_platform = {review.platform: review for review in reviews}
    assert by_platform["facebook"].reason == "insufficient_metric_coverage"
    assert by_platform["instagram"].reason == "insufficient_nonzero_signal"
    assert by_platform["threads"].status == "propose"
    assert by_platform["threads"].score_ratio is not None
    assert by_platform["threads"].score_ratio > 1.25

    proposals = read_proposals(base_dir=proposals_dir)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["platform"] == "threads"
    assert proposal["proposal_type"] == "adjust_cadence"
    assert proposal["action"] == {
        "target_config": "social_schedule",
        "field": "threads.cadence",
        "current_value": {
            "target_posts_per_day": 2,
            "minimum_interval_hours": 8.0,
            "local_slots": [8, 20],
        },
        "proposed_value": cadence_for_target(3),
    }
    assert proposal["boss_attention_required"] is True

    second = run_review(
        conn,
        load_policy(POLICY),
        now=now,
        proposals_db_path=db_path,
        proposals_dir=proposals_dir,
    )
    assert next(row for row in second if row.platform == "threads").status == "pending"
    assert len(read_proposals(base_dir=proposals_dir)) == 1
    conn.close()


def test_cadence_generation_preserves_bounded_spacing() -> None:
    assert cadence_for_target(0)["local_slots"] == []
    assert cadence_for_target(1) == {
        "target_posts_per_day": 1,
        "minimum_interval_hours": 20.0,
        "local_slots": [20],
    }
    assert cadence_for_target(3) == {
        "target_posts_per_day": 3,
        "minimum_interval_hours": 6,
        "local_slots": [8, 14, 20],
    }
