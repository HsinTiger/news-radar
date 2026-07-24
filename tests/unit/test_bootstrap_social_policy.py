from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.bootstrap_social_policy import FIRE_ID, apply_policy


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
        "SELECT hsin_decision,deployed_at,evidence_json FROM reflector_proposal_lineage WHERE fire_id=?",
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
