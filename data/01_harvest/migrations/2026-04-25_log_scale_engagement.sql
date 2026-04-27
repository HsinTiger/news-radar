-- ============================================================
-- News Radar · Migration · 2026-04-25
-- log-scale time-series engagement polling
-- ============================================================
--
-- WHY:
--   Dashboard wants to render EngagementGrowthChart per platform,
--   x-axis = post age (1h / 24h / 168h), y-axis = likes / views / reach.
--   Uniform 4h polling lost the time-axis signal — every row was
--   "current state" without an aligned post-age coordinate.
--
-- WHAT:
--   1. Add column engagement_stats.post_age_bucket (INTEGER, NULL OK).
--      Canonical values: 1, 24, 168 (hours since publish).
--      NULL on the 137 legacy rows (pre-2026-04-25 polling).
--   2. CHECK trigger restricts new INSERTs to NULL or canonical buckets.
--      (CHECK constraint cannot be added to existing column in SQLite
--      via ALTER TABLE; trigger gives equivalent enforcement.)
--   3. Partial UNIQUE INDEX prevents double-polling same bucket.
--      WHERE post_age_bucket IS NOT NULL excludes legacy rows.
--   4. Helper INDEX on (draft_id, platform, fetched_at DESC) accelerates
--      the engagement_stats_latest VIEW's correlated subquery.
--   5. VIEW engagement_stats_latest exposes the most-recent row per
--      (draft, platform). Dashboard swaps `FROM engagement_stats` →
--      `FROM engagement_stats_latest` (one-line PR on dashboard side).
--
-- IDEMPOTENCY:
--   All statements use IF NOT EXISTS / DROP IF EXISTS or check column
--   presence before adding. Safe to run multiple times.
--
-- DEPLOYMENT:
--   Auto-applied by src/db.py::init_db() on next run.
--   This SQL file is the canonical record; the Python helper in db.py
--   re-implements the same statements idempotently.
-- ============================================================

-- 1. Add column (only if missing) — Python wrapper handles "if missing"
ALTER TABLE engagement_stats ADD COLUMN post_age_bucket INTEGER;

-- 2. CHECK trigger — enforces canonical bucket values on INSERT
CREATE TRIGGER IF NOT EXISTS engagement_stats_bucket_check
BEFORE INSERT ON engagement_stats
FOR EACH ROW
WHEN NEW.post_age_bucket IS NOT NULL
     AND NEW.post_age_bucket NOT IN (1, 24, 168)
BEGIN
    SELECT RAISE(ABORT, 'post_age_bucket must be NULL or one of (1, 24, 168)');
END;

-- 3. Partial unique index — prevent double-polling of same canonical bucket.
--    Legacy rows (post_age_bucket IS NULL) are excluded from uniqueness check.
CREATE UNIQUE INDEX IF NOT EXISTS idx_engagement_stats_bucket
  ON engagement_stats (draft_id, platform, post_age_bucket)
  WHERE post_age_bucket IS NOT NULL;

-- 4. Lookup index for VIEW's correlated subquery on MAX(fetched_at)
CREATE INDEX IF NOT EXISTS idx_engagement_stats_lookup
  ON engagement_stats (draft_id, platform, fetched_at DESC);

-- 5. VIEW engagement_stats_latest — most recent row per (draft, platform).
--    Form chosen: correlated subquery on MAX(fetched_at).
--    Why not ROW_NUMBER():
--      Both forms work in SQLite ≥ 3.25. For this read-heavy snapshot
--      use case, correlated subquery + index lookup performs equivalently
--      and is more readable when debugging via sqlite3 CLI (no window
--      function syntax noise). The lookup index above optimizes it.
CREATE VIEW IF NOT EXISTS engagement_stats_latest AS
SELECT * FROM engagement_stats e
WHERE fetched_at = (
    SELECT MAX(fetched_at)
    FROM engagement_stats e2
    WHERE e2.draft_id = e.draft_id
      AND e2.platform = e.platform
);
