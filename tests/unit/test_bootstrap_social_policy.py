from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.bootstrap_social_policy import (
    FIRE_ID,
    apply_policy,
    supersede_drifted_topic_proposals,
)
from src.reflector.proposals import read_proposals, write_proposal


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE topic_weights(
          category_id TEXT PRIMARY KEY, weight REAL, sample_count INTEGER,
          last_updated_at TEXT, update_reason TEXT, last_delta REAL
        );
        CREATE TABLE topic_weight_history(
          id INTEGER PRIMARY KEY, category_id TEXT, recorded_at TEXT,
          weight_before REAL, weight_after REAL, update_reason TEXT,
          delta REAL, samples_in_window INTEGER, rationale TEXT
        );
        CREATE TABLE reflector_proposal_lineage(
          fire_id TEXT PRIMARY KEY, fire_at TEXT, analyzer TEXT,
          proposal_type TEXT, target_config TEXT, hsin_decision TEXT,
          hsin_decision_at TEXT, deployed_at TEXT, evidence_json TEXT
        );
        CREATE TABLE reflection_events(
          id INTEGER PRIMARY KEY, ran_at TEXT, signals_summary TEXT,
          samples_used INTEGER, soul_version_before TEXT,
          soul_version_after TEXT, patch_markdown TEXT,
          rules_added_json TEXT, rationale TEXT, input_tokens INTEGER,
          output_tokens INTEGER, cost_usd REAL, status TEXT
        );
        """
    )
    for category in ("earnings", "ai_agent"):
        conn.execute(
            "INSERT INTO topic_weights(category_id,weight,sample_count) VALUES(?,?,?)",
            (category, 1.0, 12),
        )
    conn.commit()
    return conn


def _policy() -> dict:
    return {
        "schema_version": 1,
        "initial_topic_weights": {"earnings": 1.4, "ai_agent": 0.8},
    }


def test_dry_run_does_not_write() -> None:
    conn = _conn()
    result = apply_policy(
        conn, _policy(), apply=False, decided_at="2026-07-23T18:00:00+08:00"
    )
    assert len(result["changes"]) == 2
    assert conn.execute("SELECT COUNT(*) FROM topic_weight_history").fetchone()[0] == 0


def test_apply_is_audited_and_idempotent() -> None:
    conn = _conn()
    first = apply_policy(
        conn, _policy(), apply=True, decided_at="2026-07-23T18:00:00+08:00"
    )
    assert "applied_at" in first
    assert conn.execute(
        "SELECT weight FROM topic_weights WHERE category_id='earnings'"
    ).fetchone()[0] == 1.4
    assert conn.execute("SELECT COUNT(*) FROM topic_weight_history").fetchone()[0] == 2
    lineage = conn.execute(
        "SELECT hsin_decision,deployed_at,evidence_json "
        "FROM reflector_proposal_lineage WHERE fire_id=?",
        (FIRE_ID,),
    ).fetchone()
    assert lineage["hsin_decision"] == "approved"
    assert lineage["deployed_at"]
    evidence = json.loads(lineage["evidence_json"])
    assert evidence["threads_posts"] == 103
    assert evidence["threads_median_actions"] == 0.0
    assert evidence["threads_source_tier_medians"]["primary"]["median_views"] == 379.0
    second = apply_policy(
        conn, _policy(), apply=True, decided_at="2026-07-23T18:00:00+08:00"
    )
    assert second["changes"] == []
    assert conn.execute("SELECT COUNT(*) FROM topic_weight_history").fetchone()[0] == 2


def test_supersede_drifted_pending_topic_proposal(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    proposals_dir = tmp_path / "proposals"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE topic_weights(category_id TEXT PRIMARY KEY,weight REAL);
            INSERT INTO topic_weights VALUES('earnings',1.35);
            CREATE TABLE reflector_proposal_lineage(
              fire_id TEXT PRIMARY KEY,fire_at TEXT,analyzer TEXT,
              proposal_type TEXT,target_config TEXT,hsin_decision TEXT,
              hsin_decision_at TEXT,deployed_at TEXT,evidence_json TEXT
            );
            """
        )
    fire_id = write_proposal(
        {
            "analyzer": "topic",
            "platform": "all",
            "proposal_type": "adjust_weight",
            "evidence": {"sample_ids": [], "metrics": {}, "confidence": "MED"},
            "action": {
                "target_config": "topic_weights",
                "field": "earnings",
                "current_value": 1.35,
                "proposed_value": 1.5,
            },
            "boss_attention_required": True,
            "fire_at": "2026-07-23T14:00:00+00:00",
        },
        db_path=db_path,
        base_dir=proposals_dir,
    )
    changed = supersede_drifted_topic_proposals(
        db_path,
        proposals_dir,
        decided_at="2026-07-24T14:14:00+08:00",
    )
    assert changed == [fire_id]
    assert read_proposals(base_dir=proposals_dir)[0]["hsin_decision"] == "amend"
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT hsin_decision FROM reflector_proposal_lineage WHERE fire_id=?",
            (fire_id,),
        ).fetchone()[0] == "amend"
