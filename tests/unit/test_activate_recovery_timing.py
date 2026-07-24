from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.activate_recovery_timing import FIRE_ID, activate
from src.reflector.proposals import read_proposals
from src.schedule_policy import load_policy


POLICY = Path(__file__).resolve().parents[2] / "config/social_automation_policy.json"


def _db(path: Path) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            CREATE TABLE reflector_proposal_lineage(
              fire_id TEXT PRIMARY KEY,fire_at TEXT,analyzer TEXT,
              proposal_type TEXT,target_config TEXT,hsin_decision TEXT,
              hsin_decision_at TEXT,deployed_at TEXT,evidence_json TEXT
            )
            """
        )


def test_activation_records_exact_applied_timing_proposal(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    proposals = tmp_path / "proposals"
    _db(db_path)
    first = activate(
        db_path,
        proposals,
        load_policy(POLICY),
        apply=True,
        decided_at="2026-07-24T14:14:00+08:00",
    )
    assert first["proposed"]["local_slots"] == [16]
    record = read_proposals(base_dir=proposals)[0]
    assert record["fire_id"] == FIRE_ID
    assert record["hsin_decision"] == "approve"
    assert record["deployed_at"]
    assert record["action"]["current_value"]["local_slots"] == [12]
    assert record["action"]["proposed_value"]["local_slots"] == [16]
    assert record["evidence"]["metrics"]["frequency_change"] == 0
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT hsin_decision,deployed_at,evidence_json "
            "FROM reflector_proposal_lineage WHERE fire_id=?",
            (FIRE_ID,),
        ).fetchone()
    assert row[0] == "approve"
    assert row[1]
    assert json.loads(row[2])["confidence"] == "MED"

    second = activate(
        db_path,
        proposals,
        load_policy(POLICY),
        apply=True,
        decided_at="2026-07-24T14:14:00+08:00",
    )
    assert second["already_applied"] is True
    assert len(read_proposals(base_dir=proposals)) == 1
