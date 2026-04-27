-- News Radar · SQL view layer (Phase 9 Item 1)
-- =====================================================================
-- Substrate views consumed by Phase 9 unified-reflector sub-analyzers.
-- Spec source : PM_Radar/specs/phase_9_implementation_plan.md §3 Item 1
-- Canonical   : PM_Radar/roadmap/phase_9_unified_reflector.md §0..§8
--
-- Init order  : sourced AFTER schema.sql by src/db.py::init_db, so all
--               base tables + columns (incl. Phase 8.18 queue_status,
--               Phase 8.20 topic_category/weighted_score) exist already.
--
-- Idempotent  : every CREATE VIEW uses IF NOT EXISTS. SQLite views are
--               evaluated at SELECT time; no materialization, no index.
-- =====================================================================


-- ============ v_post_engagement_aggregated ===========================
-- Foundation view. One row per published draft with the latest per-
-- platform engagement snapshot pulled from engagement_stats. Reflector
-- composer/scorer analyzers + derived views v_drafts_with_outcome and
-- v_topic_engagement_x_platform all read from here.
--
-- Per-platform columns are correlated subqueries on engagement_stats —
-- if a platform has no row for a draft they return NULL (not an error).
-- ======================================================================
CREATE VIEW IF NOT EXISTS v_post_engagement_aggregated AS
SELECT
    d.id              AS draft_id,
    d.news_id         AS news_id,
    ni.title          AS title,
    ni.feed_name      AS feed_name,
    ni.topic_category AS topic_category,
    ni.weighted_score AS weighted_score,
    ni.published_at   AS published_at,

    -- facebook latest snapshot
    (SELECT likes    FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'facebook'
       ORDER BY e.fetched_at DESC LIMIT 1) AS fb_likes,
    (SELECT comments FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'facebook'
       ORDER BY e.fetched_at DESC LIMIT 1) AS fb_comments,
    (SELECT shares   FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'facebook'
       ORDER BY e.fetched_at DESC LIMIT 1) AS fb_shares,
    (SELECT views    FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'facebook'
       ORDER BY e.fetched_at DESC LIMIT 1) AS fb_views,
    (SELECT reach    FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'facebook'
       ORDER BY e.fetched_at DESC LIMIT 1) AS fb_reach,

    -- instagram latest snapshot
    (SELECT likes    FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'instagram'
       ORDER BY e.fetched_at DESC LIMIT 1) AS ig_likes,
    (SELECT comments FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'instagram'
       ORDER BY e.fetched_at DESC LIMIT 1) AS ig_comments,
    (SELECT saves    FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'instagram'
       ORDER BY e.fetched_at DESC LIMIT 1) AS ig_saves,
    (SELECT shares   FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'instagram'
       ORDER BY e.fetched_at DESC LIMIT 1) AS ig_shares,
    (SELECT reach    FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'instagram'
       ORDER BY e.fetched_at DESC LIMIT 1) AS ig_reach,

    -- threads latest snapshot
    (SELECT likes    FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'threads'
       ORDER BY e.fetched_at DESC LIMIT 1) AS th_likes,
    (SELECT replies  FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'threads'
       ORDER BY e.fetched_at DESC LIMIT 1) AS th_replies,
    (SELECT reposts  FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'threads'
       ORDER BY e.fetched_at DESC LIMIT 1) AS th_reposts,
    (SELECT quotes   FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'threads'
       ORDER BY e.fetched_at DESC LIMIT 1) AS th_quotes,
    (SELECT views    FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'threads'
       ORDER BY e.fetched_at DESC LIMIT 1) AS th_views,

    -- aggregate freshness marker (NULL when no engagement_stats row exists)
    (SELECT MAX(fetched_at) FROM engagement_stats e
       WHERE e.draft_id = d.id) AS latest_engagement_at
FROM drafts d
JOIN news_items ni ON ni.id = d.news_id
WHERE d.queue_status = 'published' OR d.status = 'published';


-- ============ v_drafts_with_outcome ==================================
-- Derived from v_post_engagement_aggregated. Adds engagement_quartile
-- per topic_category (NTILE 4) over a 14-day window. Used by composer
-- analyzer's top-Q vs bot-Q sibling sampler (canonical §8.3 row 4).
--
-- 14-day window is intentionally tighter than v_post_engagement's
-- "all published" footprint — the sampler should compare drafts that
-- competed in roughly the same algorithmic conditions.
-- ======================================================================
CREATE VIEW IF NOT EXISTS v_drafts_with_outcome AS
SELECT
    base.*,
    NTILE(4) OVER (
        PARTITION BY base.topic_category
        ORDER BY (
            COALESCE(base.fb_likes, 0)
          + COALESCE(base.ig_likes, 0)
          + COALESCE(base.th_likes, 0)
        )
    ) AS engagement_quartile
FROM v_post_engagement_aggregated AS base
WHERE base.latest_engagement_at IS NOT NULL
  AND base.latest_engagement_at > datetime('now', '-14 days');


-- ============ v_feed_yield_7d ========================================
-- Per-feed yield over the last 7 days. Powers harvest analyzer's feed
-- sunset/boost proposals (canonical §8.3 row 1). engagement_yield_ratio
-- is the share of fetched items that ended up published with non-zero
-- engagement. Reads from v_post_engagement_aggregated for the
-- engagement-presence test so the join shape stays consistent across
-- analyzers.
-- ======================================================================
CREATE VIEW IF NOT EXISTS v_feed_yield_7d AS
SELECT
    ni.feed_name AS feed_name,
    COUNT(DISTINCT CASE WHEN ni.status = 'published' THEN ni.id END)
        AS publish_count_7d,
    COUNT(DISTINCT ni.id) AS fetch_count_7d,
    AVG(ni.weighted_score) AS avg_score_7d,
    CAST(COUNT(DISTINCT CASE
            WHEN ni.id IN (
                SELECT v.news_id FROM v_post_engagement_aggregated v
                WHERE COALESCE(v.fb_likes, 0)
                    + COALESCE(v.ig_likes, 0)
                    + COALESCE(v.th_likes, 0) > 0
            )
            THEN ni.id
        END) AS REAL)
        / NULLIF(COUNT(DISTINCT ni.id), 0) AS engagement_yield_ratio
FROM news_items ni
WHERE ni.published_at > datetime('now', '-7 days')
GROUP BY ni.feed_name;


-- ============ v_topic_engagement_x_platform ==========================
-- Per-topic × platform engagement averages over the last 30 days.
-- Powers topic_weight analyzer (canonical §8.3 row 2) and feeds the
-- per-platform topic-multiplier discussion in §8.3. sample_count is
-- the gate the analyzer uses against the §1.2 sample-size rule
-- ("cross-platform total < 5 = skip").
-- ======================================================================
CREATE VIEW IF NOT EXISTS v_topic_engagement_x_platform AS
SELECT
    base.topic_category AS topic_category,
    AVG(base.fb_likes) AS fb_avg_likes_30d,
    AVG(base.ig_likes) AS ig_avg_likes_30d,
    AVG(base.th_likes) AS th_avg_likes_30d,
    AVG(base.fb_comments) AS fb_avg_comments_30d,
    AVG(base.ig_comments) AS ig_avg_comments_30d,
    AVG(base.th_replies)  AS th_avg_replies_30d,
    COUNT(*) AS sample_count
FROM v_post_engagement_aggregated AS base
WHERE base.published_at > datetime('now', '-30 days')
GROUP BY base.topic_category;
