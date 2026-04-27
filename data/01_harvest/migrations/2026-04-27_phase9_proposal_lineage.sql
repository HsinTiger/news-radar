-- ============================================================
-- News Radar · Migration · 2026-04-27
-- Phase 9 Item 2 · reflector_proposal_lineage table
-- ============================================================
--
-- WHY:
--   Phase 9 unified reflector dual-records every analyzer firing:
--     1. Canonical record  →  data/05_reflect/proposals/YYYY-WW.jsonl
--        (append-only, human-readable, git-friendly audit trail)
--     2. Queryable mirror  →  reflector_proposal_lineage (this table)
--        (cross-analyzer SQL queries: "recent proposals from analyzer X",
--         "all pending proposals", "approved-but-not-deployed")
--
--   Items 3-7 (analyzers) write through src/reflector/proposals.py which
--   keeps both stores in lockstep. Dashboard / boss-review UI reads from
--   the table; PR review reads the jsonl directly.
--
-- WHAT:
--   1. CREATE TABLE reflector_proposal_lineage exactly per spec
--      §3 Item 2 lines 164-175 of phase_9_implementation_plan.md.
--   2. CREATE INDEX on (analyzer, fire_at) for the hot query pattern
--      ("recent proposals from analyzer X" — used by Items 3+ to avoid
--      double-firing on the same evidence within a cooldown window).
--
-- IDEMPOTENCY:
--   IF NOT EXISTS on both. Safe to re-run.
--
-- DEPLOYMENT:
--   Auto-applied by src/db.py::init_db() on next run (helper
--   _migrate_proposal_lineage).
-- ============================================================

CREATE TABLE IF NOT EXISTS reflector_proposal_lineage (
    fire_id            TEXT PRIMARY KEY,
    fire_at            TEXT NOT NULL,
    analyzer           TEXT NOT NULL,
    proposal_type      TEXT NOT NULL,
    target_config      TEXT NOT NULL,
    hsin_decision      TEXT,                    -- 'approve'|'reject'|'amend' OR NULL while pending
    hsin_decision_at   TEXT,
    deployed_at        TEXT,
    evidence_json      TEXT NOT NULL            -- full evidence sub-doc (JSON string)
);

CREATE INDEX IF NOT EXISTS idx_reflector_proposal_lineage_analyzer_fire_at
    ON reflector_proposal_lineage (analyzer, fire_at);
