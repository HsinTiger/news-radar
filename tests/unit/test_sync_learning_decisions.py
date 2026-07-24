from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.sync_learning_decisions import (
    DecisionSyncError,
    _validate_cadence_value,
    process_decisions,
    validate_local_lease,
)
from src.reflector.platform_policy import cadence_for_target
from src.reflector.proposals import read_proposals, write_proposal
from src.schedule_policy import decide_schedule, load_policy


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "data/01_harvest/schema.sql"
POLICY = ROOT / "config/social_automation_policy.json"


def test_cadence_validator_checks_spacing_across_midnight() -> None:
    with pytest.raises(DecisionSyncError, match="minimum spacing"):
        _validate_cadence_value(
            {
                "target_posts_per_day": 2,
                "minimum_interval_hours": 8,
                "local_slots": [0, 20],
            },
            "cadence",
        )


def _environment(
    tmp_path: Path,
    *,
    current: float = 1.0,
    proposed: float = 1.1,
    fire_id: str = "topic-proposal-0001",
) -> tuple[Path, Path, str]:
    db_path = tmp_path / "state.db"
    proposals_dir = tmp_path / "proposals"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        conn.execute(
            """
            INSERT INTO topic_weights(
              category_id,display_name,weight,last_updated_at,update_reason,sample_count
            ) VALUES('ai_model','AI Models',?,'2026-07-01T00:00:00+00:00','seed',10)
            """,
            (current,),
        )
        conn.commit()
    write_proposal(
        {
            "fire_id": fire_id,
            "fire_at": "2026-07-23T00:00:00+00:00",
            "analyzer": "topic",
            "platform": "all",
            "proposal_type": "adjust_weight",
            "evidence": {
                "sample_ids": [],
                "metrics": {
                    "old_weight": current,
                    "new_weight": proposed,
                    "applied_delta": proposed - current,
                    "total_samples": 12,
                },
                "confidence": "HIGH",
            },
            "action": {
                "target_config": "topic_weights",
                "field": "ai_model",
                "current_value": current,
                "proposed_value": proposed,
            },
            "boss_attention_required": True,
        },
        db_path=db_path,
        base_dir=proposals_dir,
    )
    return db_path, proposals_dir, fire_id


def _decision(fire_id: str, status: str) -> dict[str, str]:
    return {
        "id": fire_id,
        "status": status,
        "decision_comment": "owner decision",
        "decided_at": "2026-07-23T01:02:03+00:00",
    }


def _cadence_environment(
    tmp_path: Path,
    *,
    metric_coverage: float = 1.0,
) -> tuple[Path, Path, str]:
    db_path = tmp_path / "cadence.db"
    proposals_dir = tmp_path / "cadence-proposals"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
    fire_id = "cadence-proposal-0001"
    current = {
        "target_posts_per_day": 1,
        "minimum_interval_hours": 20.0,
        "local_slots": [8],
    }
    proposed = cadence_for_target(2)
    write_proposal(
        {
            "fire_id": fire_id,
            "fire_at": "2026-07-23T00:00:00+00:00",
            "analyzer": "platform_policy",
            "platform": "threads",
            "proposal_type": "adjust_cadence",
            "evidence": {
                "sample_ids": [],
                "metrics": {
                    "current": {
                        "posts": 25,
                        "valid_metrics": 25,
                        "metric_coverage": metric_coverage,
                        "nonzero_posts": 25,
                        "nonzero_rate": 1.0,
                        "median_action_score": 3.0,
                    },
                    "baseline": {
                        "posts": 16,
                        "valid_metrics": 16,
                        "metric_coverage": 1.0,
                        "nonzero_posts": 16,
                        "nonzero_rate": 1.0,
                        "median_action_score": 2.0,
                    },
                    "score_ratio": 1.5,
                },
                "confidence": "HIGH",
            },
            "action": {
                "target_config": "social_schedule",
                "field": "threads.cadence",
                "current_value": current,
                "proposed_value": proposed,
            },
            "boss_attention_required": True,
        },
        db_path=db_path,
        base_dir=proposals_dir,
    )
    return db_path, proposals_dir, fire_id


def test_approved_topic_weight_is_applied_once_with_readback(tmp_path: Path) -> None:
    db_path, proposals_dir, fire_id = _environment(tmp_path)

    first = process_decisions(
        db_path,
        proposals_dir,
        [_decision(fire_id, "approved")],
    )
    assert first["counts"] == {"applied": 1}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        weight = conn.execute(
            "SELECT weight,sample_count,update_reason FROM topic_weights WHERE category_id='ai_model'"
        ).fetchone()
        lineage = conn.execute(
            "SELECT hsin_decision,hsin_decision_at,deployed_at FROM reflector_proposal_lineage"
        ).fetchone()
        history_count = conn.execute(
            "SELECT COUNT(*) FROM topic_weight_history WHERE update_reason='owner_approved_proposal'"
        ).fetchone()[0]
    assert weight["weight"] == pytest.approx(1.1)
    assert weight["sample_count"] == 22
    assert weight["update_reason"] == f"owner_approved:{fire_id}"
    assert lineage["hsin_decision"] == "approve"
    assert lineage["hsin_decision_at"] == "2026-07-23T01:02:03+00:00"
    assert lineage["deployed_at"]
    assert history_count == 1

    record = read_proposals(base_dir=proposals_dir)[0]
    assert record["hsin_decision"] == "approve"
    assert record["deployed_at"] == lineage["deployed_at"]

    second = process_decisions(
        db_path,
        proposals_dir,
        [_decision(fire_id, "approved")],
    )
    assert second["counts"] == {"already_applied": 1}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM topic_weight_history WHERE update_reason='owner_approved_proposal'"
        ).fetchone()[0] == 1


def test_rejected_proposal_is_mirrored_without_execution(tmp_path: Path) -> None:
    db_path, proposals_dir, fire_id = _environment(tmp_path)
    result = process_decisions(
        db_path,
        proposals_dir,
        [_decision(fire_id, "rejected")],
    )
    assert result["counts"] == {"rejected_mirrored": 1}
    with sqlite3.connect(db_path) as conn:
        weight = conn.execute(
            "SELECT weight FROM topic_weights WHERE category_id='ai_model'"
        ).fetchone()[0]
        decision, deployed_at = conn.execute(
            "SELECT hsin_decision,deployed_at FROM reflector_proposal_lineage"
        ).fetchone()
    assert weight == pytest.approx(1.0)
    assert decision == "reject"
    assert deployed_at is None
    assert read_proposals(base_dir=proposals_dir)[0]["hsin_decision"] == "reject"


def test_current_value_drift_fails_before_mirroring_decision(tmp_path: Path) -> None:
    db_path, proposals_dir, fire_id = _environment(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE topic_weights SET weight=1.05 WHERE category_id='ai_model'")
        conn.commit()

    with pytest.raises(DecisionSyncError, match="drift gate failed"):
        process_decisions(
            db_path,
            proposals_dir,
            [_decision(fire_id, "approved")],
        )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT hsin_decision FROM reflector_proposal_lineage"
        ).fetchone()[0] is None
    assert read_proposals(base_dir=proposals_dir)[0]["hsin_decision"] is None


def test_two_approved_proposals_for_same_topic_fail_closed(tmp_path: Path) -> None:
    db_path, proposals_dir, first_id = _environment(tmp_path)
    second_id = "topic-proposal-0002"
    first = read_proposals(base_dir=proposals_dir)[0]
    second = dict(first)
    second["fire_id"] = second_id
    second["fire_at"] = "2026-07-23T00:01:00+00:00"
    second["action"] = dict(first["action"])
    second["action"]["proposed_value"] = 1.12
    second["evidence"] = dict(first["evidence"])
    second["evidence"]["metrics"] = dict(first["evidence"]["metrics"])
    second["evidence"]["metrics"]["new_weight"] = 1.12
    second["evidence"]["metrics"]["applied_delta"] = 0.12
    second.pop("hsin_decision", None)
    second.pop("hsin_decision_at", None)
    second.pop("hsin_decision_comment", None)
    second.pop("deployed_at", None)
    write_proposal(second, db_path=db_path, base_dir=proposals_dir)

    with pytest.raises(DecisionSyncError, match="target the same field"):
        process_decisions(
            db_path,
            proposals_dir,
            [_decision(first_id, "approved"), _decision(second_id, "approved")],
        )


def test_out_of_range_or_large_delta_is_rejected(tmp_path: Path) -> None:
    db_path, proposals_dir, fire_id = _environment(
        tmp_path,
        current=1.0,
        proposed=1.31,
    )
    with pytest.raises(DecisionSyncError, match="weekly delta"):
        process_decisions(
            db_path,
            proposals_dir,
            [_decision(fire_id, "approved")],
        )


def test_local_lease_must_be_well_formed_and_unexpired(tmp_path: Path) -> None:
    lease = tmp_path / "lease.json"
    now = datetime.now(timezone.utc)
    lease.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "github:test",
                "token": "a" * 32,
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    assert validate_local_lease(lease, now=now)["owner"] == "github:test"

    expired = json.loads(lease.read_text(encoding="utf-8"))
    expired["expires_at"] = (now - timedelta(seconds=1)).isoformat()
    lease.write_text(json.dumps(expired), encoding="utf-8")
    with pytest.raises(DecisionSyncError, match="expired"):
        validate_local_lease(lease, now=now)


def test_approved_platform_cadence_changes_scheduler_only_after_execution(
    tmp_path: Path,
) -> None:
    db_path, proposals_dir, fire_id = _cadence_environment(tmp_path)
    result = process_decisions(
        db_path,
        proposals_dir,
        [_decision(fire_id, "approved")],
    )
    assert result["counts"] == {"applied": 1}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM social_policy_overrides WHERE platform='threads'"
        ).fetchone()
        history = conn.execute(
            "SELECT COUNT(*) FROM social_policy_history WHERE platform='threads'"
        ).fetchone()[0]
        decision = decide_schedule(
            conn,
            load_policy(POLICY),
            datetime(2026, 7, 23, 12, 10, tzinfo=timezone.utc),
        )
    assert row["target_posts_per_day"] == 2
    assert row["minimum_interval_hours"] == 12.0
    assert json.loads(row["local_slots_json"]) == [8, 20]
    assert row["source_proposal_id"] == fire_id
    assert history == 1
    threads = next(item for item in decision.platform_decisions if item.platform == "threads")
    assert threads.due is True
    assert threads.policy_source == f"proposal:{fire_id}"

    second = process_decisions(
        db_path,
        proposals_dir,
        [_decision(fire_id, "approved")],
    )
    assert second["counts"] == {"already_applied": 1}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM social_policy_history"
        ).fetchone()[0] == 1


def test_cadence_approval_fails_closed_on_metric_coverage(tmp_path: Path) -> None:
    db_path, proposals_dir, fire_id = _cadence_environment(
        tmp_path,
        metric_coverage=0.5,
    )
    with pytest.raises(DecisionSyncError, match="metric coverage"):
        process_decisions(
            db_path,
            proposals_dir,
            [_decision(fire_id, "approved")],
        )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM social_policy_overrides"
        ).fetchone()[0] == 0
