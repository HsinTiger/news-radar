"""News Radar · Phase 9 Item 2 · proposals.jsonl + lineage tests.

Covers:
  (a) Schema migration round-trip — temp DB, schema.sql + migration applied,
      reflector_proposal_lineage table and (analyzer, fire_at) index exist.
  (b) Single-proposal write_proposal + read_proposals + lineage row.
  (c) Validation: rejects 3+ malformed proposal shapes.
  (d) update_decision round-trips on both jsonl and lineage.
  (e) Multi-week scenario: two ISO weeks → two separate files.

No Meta API. No live DB. Tests build a temp DB with schema.sql + the
2026-04-27 migration applied (matches the production init order modulo
intermediate Phase 8.x migrations that are irrelevant for this table).

Spec  : PM_Radar/specs/phase_9_implementation_plan.md §3 Item 2
Canon : PM_Radar/roadmap/phase_9_unified_reflector.md §1.3
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.reflector import mark_deployed
from src.reflector import proposals as proposals_mod
from src.reflector.proposals import (
    ProposalValidationError,
    read_proposals,
    update_decision,
    write_proposal,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO_ROOT / "data" / "01_harvest" / "schema.sql"
_MIGRATION_PATH = (
    _REPO_ROOT
    / "data"
    / "01_harvest"
    / "migrations"
    / "2026-04-27_phase9_proposal_lineage.sql"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    """Build temp DB + temp proposals dir; return (db_path, base_dir)."""
    db_path = tmp_path / "proposals_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    # The migration is also embedded in schema.sql §9; re-running the
    # migration script is a no-op via IF NOT EXISTS, but proves the file
    # by itself is sufficient for fresh DBs.
    conn.executescript(_MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    base_dir = tmp_path / "proposals"
    base_dir.mkdir()
    return db_path, base_dir


def _sample_proposal(**overrides) -> dict:
    """Minimal valid proposal; tests override fields as needed."""
    proposal = {
        "analyzer": "topic",
        "platform": "all",
        "proposal_type": "adjust_weight",
        "evidence": {
            "sample_ids": ["draft_a", "draft_b"],
            "metrics": {"avg_likes": 12.5, "sample_count": 2},
            "confidence": "MED",
        },
        "action": {
            "target_config": "topic_weights",
            "field": "ai_model.weight",
            "current_value": 1.50,
            "proposed_value": 1.40,
        },
        "boss_attention_required": False,
    }
    proposal.update(overrides)
    return proposal


# ---------------------------------------------------------------------------
# (a) Schema migration round-trip
# ---------------------------------------------------------------------------

def test_schema_migration_creates_table_and_index(env):
    db_path, _ = env
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        # Table exists.
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='reflector_proposal_lineage'"
        ).fetchone()
        assert row is not None

        # Expected columns.
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(reflector_proposal_lineage)"
        ).fetchall()}
        expected = {
            "fire_id", "fire_at", "analyzer", "proposal_type",
            "target_config", "hsin_decision", "hsin_decision_at",
            "deployed_at", "evidence_json",
        }
        assert expected <= cols, f"missing columns: {expected - cols}"

        # Index on (analyzer, fire_at) exists.
        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='reflector_proposal_lineage'"
        ).fetchall()
        idx_names = {r[0] for r in idx_rows}
        assert "idx_reflector_proposal_lineage_analyzer_fire_at" in idx_names


# ---------------------------------------------------------------------------
# (b) Single-proposal round-trip
# ---------------------------------------------------------------------------

def test_write_and_read_proposal_roundtrip(env):
    db_path, base_dir = env
    proposal = _sample_proposal(
        fire_at="2026-04-27T10:00:00+00:00",  # ISO week 2026-W17
    )

    fire_id = write_proposal(proposal, db_path=db_path, base_dir=base_dir)
    assert fire_id  # non-empty uuid

    # JSONL file landed at the right ISO-week path.
    # 2026-04-27 is a Monday → ISO week 2026-W18.
    week_file = base_dir / "2026-W18.jsonl"
    assert week_file.exists()
    lines = week_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    # JSONL line round-trips through read_proposals.
    parsed = read_proposals(base_dir=base_dir)
    assert len(parsed) == 1
    rec = parsed[0]
    assert rec["fire_id"] == fire_id
    assert rec["analyzer"] == "topic"
    assert rec["action"]["target_config"] == "topic_weights"
    assert rec["hsin_decision"] is None
    assert rec["deployed_at"] is None

    # Lineage row matches.
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM reflector_proposal_lineage WHERE fire_id = ?",
            (fire_id,),
        ).fetchone()
    assert row is not None
    assert row["analyzer"] == "topic"
    assert row["proposal_type"] == "adjust_weight"
    assert row["target_config"] == "topic_weights"
    assert row["hsin_decision"] is None
    # evidence_json stores the evidence sub-doc as JSON.
    import json as _json
    assert _json.loads(row["evidence_json"])["confidence"] == "MED"

    # read_proposals(week=...) also works.
    by_week = read_proposals(week="2026-W18", base_dir=base_dir)
    assert len(by_week) == 1
    assert by_week[0]["fire_id"] == fire_id


# ---------------------------------------------------------------------------
# (c) Validation rejects malformed proposals
# ---------------------------------------------------------------------------

def test_validation_rejects_missing_required_field(env):
    db_path, base_dir = env
    bad = _sample_proposal()
    del bad["analyzer"]
    with pytest.raises(ProposalValidationError, match="analyzer"):
        write_proposal(bad, db_path=db_path, base_dir=base_dir)


def test_validation_rejects_bogus_analyzer_enum(env):
    db_path, base_dir = env
    bad = _sample_proposal(analyzer="bogus")
    with pytest.raises(ProposalValidationError, match="analyzer"):
        write_proposal(bad, db_path=db_path, base_dir=base_dir)


def test_validation_rejects_evidence_not_object(env):
    db_path, base_dir = env
    bad = _sample_proposal(evidence=["not", "a", "dict"])
    with pytest.raises(ProposalValidationError, match="evidence"):
        write_proposal(bad, db_path=db_path, base_dir=base_dir)


def test_validation_rejects_invalid_proposal_type(env):
    """Bonus 4th case — covers the proposal_type enum branch."""
    db_path, base_dir = env
    bad = _sample_proposal(proposal_type="garbage_type")
    with pytest.raises(ProposalValidationError, match="proposal_type"):
        write_proposal(bad, db_path=db_path, base_dir=base_dir)


def test_validation_failure_does_not_write_jsonl_or_lineage(env):
    """Side-effect freedom: validation rejects BEFORE any write."""
    db_path, base_dir = env
    bad = _sample_proposal(analyzer="bogus")
    with pytest.raises(ProposalValidationError):
        write_proposal(bad, db_path=db_path, base_dir=base_dir)
    # No jsonl files created.
    assert list(base_dir.glob("*.jsonl")) == []
    # No lineage rows.
    with sqlite3.connect(str(db_path)) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM reflector_proposal_lineage"
        ).fetchone()[0]
    assert n == 0


# ---------------------------------------------------------------------------
# (d) Decision update round-trip
# ---------------------------------------------------------------------------

def test_update_decision_roundtrip(env):
    db_path, base_dir = env
    proposal = _sample_proposal(fire_at="2026-04-27T10:00:00+00:00")
    fire_id = write_proposal(proposal, db_path=db_path, base_dir=base_dir)

    update_decision(
        fire_id,
        decision="approve",
        comment="looks good",
        db_path=db_path,
        base_dir=base_dir,
    )

    # JSONL reflects.
    rec = read_proposals(base_dir=base_dir)[0]
    assert rec["hsin_decision"] == "approve"
    assert rec["hsin_decision_comment"] == "looks good"
    assert rec["hsin_decision_at"] is not None

    # Lineage reflects.
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT hsin_decision, hsin_decision_at "
            "FROM reflector_proposal_lineage WHERE fire_id = ?",
            (fire_id,),
        ).fetchone()
    assert row["hsin_decision"] == "approve"
    assert row["hsin_decision_at"] is not None


def test_update_decision_rejects_invalid_decision(env):
    db_path, base_dir = env
    fire_id = write_proposal(
        _sample_proposal(fire_at="2026-04-27T10:00:00+00:00"),
        db_path=db_path,
        base_dir=base_dir,
    )
    with pytest.raises(ProposalValidationError, match="decision"):
        update_decision(
            fire_id, decision="maybe", comment=None,
            db_path=db_path, base_dir=base_dir,
        )


def test_update_decision_unknown_fire_id_raises(env):
    db_path, base_dir = env
    with pytest.raises(LookupError):
        update_decision(
            "no-such-fire-id", decision="approve", comment=None,
            db_path=db_path, base_dir=base_dir,
        )


# ---------------------------------------------------------------------------
# (e) Multi-week scenario
# ---------------------------------------------------------------------------

def test_multiweek_partitioning(env):
    db_path, base_dir = env

    # ISO week 2026-W18 (2026-04-27 is a Monday).
    fid_a = write_proposal(
        _sample_proposal(fire_at="2026-04-27T08:00:00+00:00"),
        db_path=db_path, base_dir=base_dir,
    )
    # ISO week 2026-W19 (2026-05-04 is the next Monday).
    fid_b = write_proposal(
        _sample_proposal(
            fire_at="2026-05-04T08:00:00+00:00",
            analyzer="harvest",
            proposal_type="sunset_feed",
            action={
                "target_config": "feeds.yml",
                "field": "feeds.example.enabled",
                "current_value": True,
                "proposed_value": False,
            },
        ),
        db_path=db_path, base_dir=base_dir,
    )

    # Two distinct files.
    files = sorted(p.name for p in base_dir.glob("*.jsonl"))
    assert files == ["2026-W18.jsonl", "2026-W19.jsonl"]

    # Each file has exactly one line.
    for fname in files:
        lines = (base_dir / fname).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

    # read_proposals() with no arg returns both, in deterministic order.
    all_props = read_proposals(base_dir=base_dir)
    fire_ids = {p["fire_id"] for p in all_props}
    assert fire_ids == {fid_a, fid_b}
    assert len(all_props) == 2

    # Lineage table has both rows.
    with sqlite3.connect(str(db_path)) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM reflector_proposal_lineage"
        ).fetchone()[0]
    assert n == 2

    # Index hit shape — by-analyzer recent query (the index's purpose).
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT fire_id FROM reflector_proposal_lineage "
            "WHERE analyzer = ? ORDER BY fire_at DESC",
            ("harvest",),
        ).fetchall()
    assert [r[0] for r in rows] == [fid_b]


# ---------------------------------------------------------------------------
# (f) mark_deployed (Item 2.5)
# ---------------------------------------------------------------------------

def test_mark_deployed_roundtrip(env):
    """Item 2.5: mark_deployed flips deployed_at on jsonl + lineage in lockstep."""
    db_path, base_dir = env
    fire_id = write_proposal(
        _sample_proposal(fire_at="2026-04-27T10:00:00+00:00"),
        db_path=db_path,
        base_dir=base_dir,
    )

    # Pre-condition: deployed_at is None on both surfaces.
    rec_before = read_proposals(base_dir=base_dir)[0]
    assert rec_before["deployed_at"] is None
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT deployed_at FROM reflector_proposal_lineage WHERE fire_id = ?",
            (fire_id,),
        ).fetchone()[0] is None

    # Default deployed_at (current UTC).
    mark_deployed(fire_id, db_path=db_path, base_dir=base_dir)

    rec_after = read_proposals(base_dir=base_dir)[0]
    assert rec_after["fire_id"] == fire_id
    assert rec_after["deployed_at"] is not None
    # ISO-8601 with explicit +00:00 suffix (matches _utcnow_iso format).
    assert rec_after["deployed_at"].endswith("+00:00")
    # Parses cleanly as ISO-8601.
    from datetime import datetime as _dt
    parsed = _dt.fromisoformat(rec_after["deployed_at"])
    assert parsed.tzinfo is not None

    # Lineage row matches.
    with sqlite3.connect(str(db_path)) as conn:
        lineage_deployed = conn.execute(
            "SELECT deployed_at FROM reflector_proposal_lineage WHERE fire_id = ?",
            (fire_id,),
        ).fetchone()[0]
    assert lineage_deployed == rec_after["deployed_at"]

    # Other fields untouched.
    assert rec_after["analyzer"] == "topic"
    assert rec_after["hsin_decision"] is None


def test_mark_deployed_explicit_timestamp(env):
    """Override path: caller supplies an explicit deployed_at."""
    db_path, base_dir = env
    fire_id = write_proposal(
        _sample_proposal(fire_at="2026-04-27T10:00:00+00:00"),
        db_path=db_path,
        base_dir=base_dir,
    )

    explicit = "2026-05-01T12:34:56+00:00"
    mark_deployed(fire_id, deployed_at=explicit, db_path=db_path, base_dir=base_dir)

    rec = read_proposals(base_dir=base_dir)[0]
    assert rec["deployed_at"] == explicit
    with sqlite3.connect(str(db_path)) as conn:
        lineage_deployed = conn.execute(
            "SELECT deployed_at FROM reflector_proposal_lineage WHERE fire_id = ?",
            (fire_id,),
        ).fetchone()[0]
    assert lineage_deployed == explicit


def test_mark_deployed_unknown_fire_id_raises(env):
    """Cannot deploy a fire_id that was never proposed — explicit raise."""
    db_path, base_dir = env
    with pytest.raises(LookupError, match="fire_id"):
        mark_deployed(
            "no-such-fire-id-12345",
            db_path=db_path,
            base_dir=base_dir,
        )
