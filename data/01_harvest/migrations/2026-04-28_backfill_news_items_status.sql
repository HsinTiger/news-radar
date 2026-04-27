-- ============================================================
-- News Radar · Migration · 2026-04-28
-- Phase 9 Item 1 · Backfill news_items.status='published'
-- ============================================================
--
-- WHY:
--   Silent feeds spike audit (2026-04-28_silent_feeds_spike.md) diagnosed
--   that mark_queue_published (src/db.py:699-710) updates drafts.status='published'
--   but never updates news_items.status='published'. This causes the harvest_analyzer's
--   "investigation lane" (v_feed_yield_7d) to report publish_count_7d=0 even for feeds
--   actively publishing to platforms (because v_feed_yield_7d counts news_items.status='published').
--
--   Lifetime impact: ~49 drafts marked published, but only 7 news_items rows ever
--   reached 'published' status (mostly from emergency/manual escape hatches).
--   Production state: CoinDesk 11 published drafts + 0 published news_items;
--   Decrypt 6 published drafts + 1 published news_item; TechCrunch AI 2 published drafts + 0;
--   Cointelegraph 0 published (separate issue: freshness-first queue starvation + short RSS bodies).
--
-- WHAT:
--   1. UPDATE news_items SET status='published' for all rows where:
--      - Joined draft exists with status='published'
--      - news_items.status is currently NOT 'published' (idempotent)
--   2. This is a one-time promotion; going forward, mark_queue_published in
--      src/db.py performs both updates atomically (Phase 9 Item 1 fix in same commit).
--
-- IDEMPOTENCY:
--   Safe to re-run. WHERE clause excludes rows already marked 'published'.
--
-- DEPLOYMENT:
--   Auto-applied by src/db.py::_migrate_add_column_if_missing / migration runner.
--   Called by init_db() with other Phase 9 migrations.
--
-- ============================================================

UPDATE news_items
   SET status = 'published'
 WHERE id IN (
     SELECT news_id
       FROM drafts
      WHERE status = 'published'
 )
   AND status != 'published';
