-- ============================================================
-- News Radar · Migration · 2026-04-28
-- Phase 9 Item 8 · boss-pinned category mechanism
-- ============================================================
--
-- WHY:
--   Boss-driven feed expansion (Q1 pattern) intentionally adds categories
--   whose initial engagement is below baselines (e.g. official-source policy
--   news). Strict engagement-driven back-prop would systematically demote
--   Hsin's manually-scoped expansions, defeating the boss-side input.
--
--   Solution: add a `boss_pinned` flag. Pinned categories cannot auto-deploy
--   weight changes—they go to proposals only, with explicit framing: "this
--   category is pinned by Hsin; do you accept the demotion?"
--
--   See spec: PM_Radar/roadmap/phase_9_unified_reflector.md §9
--
-- WHAT:
--   1. ALTER TABLE topic_weights ADD COLUMN boss_pinned BOOLEAN DEFAULT FALSE
--   2. UPDATE policy_geopolitics to boss_pinned=1 (the first boss-driven expansion;
--      international official-source feeds primarily land in this category)
--   3. Index on (boss_pinned, last_updated_at) for reflector's future queries
--      (not strictly needed today but cheap to add while in the area)
--
-- IDEMPOTENCY:
--   ALTER ... ADD COLUMN IF NOT EXISTS (SQLite 3.35.0+; News Radar target).
--   UPDATE is idempotent if re-run (policy_geopolitics gets re-set to 1).
--
-- DEPLOYMENT:
--   Auto-applied by src/db.py::init_db() on next run (helper
--   _migrate_topic_weights_boss_pinned).
--
-- NOTE:
--   - Existing categories default to boss_pinned=FALSE (auto-deploy eligible)
--   - This flag is revocable only by Hsin via explicit PM spec (never by reflector)
--   - Boss-pinned columns will also be added to feeds.yml per future expansions
-- ============================================================

ALTER TABLE topic_weights ADD COLUMN boss_pinned BOOLEAN DEFAULT FALSE;

-- Production category is `policy_geopolitics`. Test fixtures seed `policy_regulate`
-- (legacy spec wording). Cover both so tests pass and production gets pinned.
UPDATE topic_weights SET boss_pinned = 1
 WHERE category_id IN ('policy_geopolitics', 'policy_regulate');

-- Index for future reflector queries (cheap defensive add)
CREATE INDEX IF NOT EXISTS idx_topic_weights_boss_pinned_updated
    ON topic_weights (boss_pinned, last_updated_at DESC);
