-- News Radar · SQL view layer (Phase 9 Item 1 + Item 1.5 fold-in + Item 1.6)
-- =====================================================================
-- Substrate views consumed by Phase 9 unified-reflector sub-analyzers.
-- Spec source : PM_Radar/specs/phase_9_implementation_plan.md §3 Item 1
-- Canonical   : PM_Radar/roadmap/phase_9_unified_reflector.md §0..§8
-- Item 1.5    : 2026-04-27 fold-in approved during Item 2 dispatch —
--               adds drafts.confidence_score onto v_post_engagement_aggregated
--               and a new v_draft_hook_by_platform view.
-- Item 1.6    : 2026-04-28 Cowork-ruling option (a) on view-coverage gap
--               raised by Item 3's audit. Extends v_topic_engagement_x_platform
--               with the full per-platform AVG metric set (shares / reach /
--               saves / reposts / quotes / views) so analyzers no longer have
--               to re-derive them off the base tables. The per-row columns
--               on v_post_engagement_aggregated were already complete since
--               Item 1; no changes needed there. See:
--                 audits/2026-04-28_phase9_item1_6_substrate_extension.md
--                 audits/2026-04-28_phase9_item3_reflector_topic_refactor.md
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
DROP VIEW IF EXISTS v_post_engagement_aggregated;
CREATE VIEW v_post_engagement_aggregated AS
SELECT
    d.id              AS draft_id,
    d.news_id         AS news_id,
    ni.title          AS title,
    ni.feed_name      AS feed_name,
    ni.topic_category AS topic_category,
    ni.weighted_score AS weighted_score,
    ni.published_at   AS published_at,
    d.confidence_score AS confidence_score,  -- Item 1.5: surfaces composer
                                              -- self-rating so Item 5 / Item 6
                                              -- analyzers can correlate
                                              -- pre-publish confidence vs
                                              -- realized engagement.

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
    (SELECT clicks   FROM engagement_stats e
       WHERE e.draft_id = d.id AND e.platform = 'facebook'
       ORDER BY e.fetched_at DESC LIMIT 1) AS fb_clicks,

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
--
-- Item 1.6 (2026-04-28, Cowork-ruling option a on the view-coverage gap
-- raised by Item 3's audit, see audits/2026-04-28_phase9_item3_*.md):
-- expanded to cover the full per-platform metric set the Hsin-pinned
-- engagement formula requires. The base view v_post_engagement_aggregated
-- already exposed the per-row columns since Item 1; this view now
-- aggregates them into 30-day averages so analyzers don't have to
-- re-derive them. Existing AVG columns + sample_count + GROUP BY
-- intentionally untouched. DROP-then-CREATE so existing DBs pick up
-- the new columns on next init_db (CREATE VIEW IF NOT EXISTS would
-- otherwise leave the older shape in place).
-- ======================================================================
DROP VIEW IF EXISTS v_topic_engagement_x_platform;
CREATE VIEW v_topic_engagement_x_platform AS
SELECT
    base.topic_category AS topic_category,
    -- likes (since Item 1)
    AVG(base.fb_likes) AS fb_avg_likes_30d,
    AVG(base.ig_likes) AS ig_avg_likes_30d,
    AVG(base.th_likes) AS th_avg_likes_30d,
    -- existing comments / replies (since Item 1)
    AVG(base.fb_comments) AS fb_avg_comments_30d,
    AVG(base.ig_comments) AS ig_avg_comments_30d,
    AVG(base.th_replies)  AS th_avg_replies_30d,
    -- Item 1.6: facebook full per-platform set
    AVG(base.fb_shares)   AS fb_avg_shares_30d,
    AVG(base.fb_reach)    AS fb_avg_reach_30d,
    AVG(base.fb_clicks)   AS fb_avg_clicks_30d,
    -- Item 1.6: instagram full per-platform set
    AVG(base.ig_shares)   AS ig_avg_shares_30d,
    AVG(base.ig_saves)    AS ig_avg_saves_30d,
    AVG(base.ig_reach)    AS ig_avg_reach_30d,
    -- Item 1.6: threads full per-platform set
    AVG(base.th_reposts)  AS th_avg_reposts_30d,
    AVG(base.th_quotes)   AS th_avg_quotes_30d,
    AVG(base.th_views)    AS th_avg_views_30d,
    COUNT(*) AS sample_count
FROM v_post_engagement_aggregated AS base
WHERE base.published_at > datetime('now', '-30 days')
GROUP BY base.topic_category;


-- ============ v_draft_hook_by_platform (Item 1.5) ====================
-- Per-platform-per-draft "hook" extraction joined to engagement metadata.
-- The hook is the substring that the reader sees BEFORE having to expand
-- / scroll, and differs per platform:
--   facebook  : first 100 chars of full_text (covers the in-feed preview).
--   instagram : substring before the first newline (caption pre-fold);
--               if no newline, the whole full_text counts as hook.
--   threads   : first 30 chars (Threads in-feed crop is shorter).
-- LEFT JOIN to v_post_engagement_aggregated → engagement columns are NULL
-- for drafts not yet (or never) published; that's intentional and lets
-- Item 5 / Item 6 analyzers filter on `latest_engagement_at IS NOT NULL`
-- when they need engagement-correlated samples.
-- ======================================================================
CREATE VIEW IF NOT EXISTS v_draft_hook_by_platform AS
SELECT
    pd.draft_id  AS draft_id,
    pd.platform  AS platform,
    CASE pd.platform
        WHEN 'facebook'  THEN substr(pd.full_text, 1, 100)
        WHEN 'instagram' THEN
            CASE
                WHEN instr(pd.full_text, char(10)) > 0
                    THEN substr(pd.full_text, 1, instr(pd.full_text, char(10)) - 1)
                ELSE pd.full_text
            END
        WHEN 'threads'   THEN substr(pd.full_text, 1, 30)
        ELSE pd.full_text
    END AS hook,
    -- engagement metadata via v_post_engagement_aggregated (LEFT JOIN so
    -- unpublished drafts still appear with NULL engagement columns).
    agg.title                AS title,
    agg.topic_category       AS topic_category,
    agg.fb_likes             AS fb_likes,
    agg.ig_likes             AS ig_likes,
    agg.th_likes             AS th_likes,
    agg.latest_engagement_at AS latest_engagement_at,
    agg.confidence_score     AS confidence_score
FROM platform_drafts pd
LEFT JOIN v_post_engagement_aggregated agg ON pd.draft_id = agg.draft_id;

-- ============================================================
-- 分析引擎 Views (2026-06-02)
-- ============================================================

-- 1. v_engagement_growth: 同一篇貼文三時間點 1h/24h/168h 的互動變化
CREATE VIEW IF NOT EXISTS v_engagement_growth AS
SELECT
    draft_id,
    platform,
    MAX(CASE WHEN post_age_bucket=1   THEN likes END)    AS likes_1h,
    MAX(CASE WHEN post_age_bucket=24  THEN likes END)    AS likes_24h,
    MAX(CASE WHEN post_age_bucket=168 THEN likes END)    AS likes_168h,
    MAX(CASE WHEN post_age_bucket=1   THEN reach END)    AS reach_1h,
    MAX(CASE WHEN post_age_bucket=24  THEN reach END)    AS reach_24h,
    MAX(CASE WHEN post_age_bucket=168 THEN reach END)    AS reach_168h,
    MAX(CASE WHEN post_age_bucket=1   THEN views END)    AS views_1h,
    MAX(CASE WHEN post_age_bucket=24  THEN views END)    AS views_24h,
    MAX(CASE WHEN post_age_bucket=168 THEN views END)    AS views_168h,
    MAX(CASE WHEN post_age_bucket=1   THEN comments END) AS comments_1h,
    MAX(CASE WHEN post_age_bucket=24  THEN comments END) AS comments_24h,
    COALESCE(MAX(CASE WHEN post_age_bucket=24  THEN likes END), 0) -
        COALESCE(MAX(CASE WHEN post_age_bucket=1   THEN likes END), 0) AS growth_likes_24h,
    COALESCE(MAX(CASE WHEN post_age_bucket=168 THEN likes END), 0) -
        COALESCE(MAX(CASE WHEN post_age_bucket=24  THEN likes END), 0) AS growth_likes_168h
FROM engagement_stats
WHERE post_age_bucket IS NOT NULL
GROUP BY draft_id, platform;

-- 2. v_engagement_summary: 每篇貼文最終互動總覽 (取最新 bucket)
CREATE VIEW IF NOT EXISTS v_engagement_summary AS
SELECT DISTINCT
    e.draft_id,
    e.platform,
    e.likes            AS finalized_likes,
    e.comments         AS finalized_comments,
    e.shares           AS finalized_shares,
    e.saves            AS finalized_saves,
    e.reposts          AS finalized_reposts,
    e.quotes           AS finalized_quotes,
    e.replies          AS finalized_replies,
    e.views            AS finalized_views,
    e.reach            AS finalized_reach,
    e.post_age_bucket  AS post_age_hours,
    -- 加權互動率（多型公式）
    CASE e.platform
        WHEN 'facebook'   THEN 1.0 * (e.likes + 2*e.comments + 3*e.shares) / MAX(e.reach, 1)
        WHEN 'instagram'  THEN 1.0 * (e.likes + 2*e.comments + 3*e.shares + 3*e.saves) / MAX(e.reach, 1)
        WHEN 'threads'    THEN 1.0 * (e.likes + 2*e.replies + 3*e.reposts + 3*e.quotes/2) / MAX(e.views, 1)
        ELSE 0.0
    END AS engagement_rate,
    d.title            AS draft_title,
    d.confidence_score AS confidence_score,
    d.generated_at
FROM engagement_stats e
JOIN drafts d ON d.id = e.draft_id
WHERE e.id IN (
    SELECT MAX(id) FROM engagement_stats GROUP BY draft_id, platform
);

-- 3. v_topic_performance_30d: 近30天主題 + 平台表現
CREATE VIEW IF NOT EXISTS v_topic_performance_30d AS
SELECT
    n.topic_category,
    e.platform,
    AVG(e.likes)     AS avg_likes,
    AVG(e.comments)  AS avg_comments,
    AVG(e.views)     AS avg_views,
    AVG(e.reach)     AS avg_reach,
    COUNT(*)         AS post_count,
    CASE e.platform
        WHEN 'facebook'   THEN AVG(1.0 * (e.likes + 2*e.comments + 3*e.shares) / MAX(e.reach, 1))
        WHEN 'instagram'  THEN AVG(1.0 * (e.likes + 2*e.comments + 3*e.shares + 3*e.saves) / MAX(e.reach, 1))
        WHEN 'threads'    THEN AVG(1.0 * (e.likes + 2*e.replies + 3*e.reposts + 3*e.quotes/2) / MAX(e.views, 1))
        ELSE 0.0
    END AS avg_engagement_rate
FROM engagement_stats e
JOIN drafts d ON d.id = e.draft_id
JOIN news_items n ON n.id = d.news_id
WHERE n.topic_category IS NOT NULL
  AND e.fetched_at >= datetime('now', '-30 days', 'localtime')
GROUP BY n.topic_category, e.platform
HAVING COUNT(*) >= 3;

-- 4. v_account_daily: 每日帳號總覽
CREATE VIEW IF NOT EXISTS v_account_daily AS
SELECT
    DATE(e.fetched_at) AS day,
    e.platform,
    COUNT(DISTINCT e.draft_id) AS post_count,
    SUM(e.likes)    AS total_likes,
    SUM(e.comments) AS total_comments,
    SUM(e.shares)   AS total_shares,
    MAX(e.reach)    AS total_reach,
    MAX(e.views)    AS total_views,
    CASE e.platform
        WHEN 'facebook'   THEN 1.0 * SUM(e.likes + 2*e.comments + 3*e.shares) / MAX(SUM(e.reach), 1)
        WHEN 'instagram'  THEN 1.0 * SUM(e.likes + 2*e.comments + 3*e.shares + 3*e.saves) / MAX(SUM(e.reach), 1)
        WHEN 'threads'    THEN 1.0 * SUM(e.likes + 2*e.replies + 3*e.reposts + 3*e.quotes/2) / MAX(SUM(e.views), 1)
        ELSE 0.0
    END AS avg_engagement_rate
FROM engagement_stats e
GROUP BY DATE(e.fetched_at), e.platform;

-- 5. v_publish_cadence: 發布節奏分析
CREATE VIEW IF NOT EXISTS v_publish_cadence AS
SELECT
    DATE(p.posted_at) AS day,
    p.platform,
    COUNT(*) AS post_count,
    -- 與上次發布的間隔秒數
    CAST(
        JULIANDAY(p.posted_at) - JULIANDAY(
            LAG(p.posted_at) OVER (PARTITION BY p.platform ORDER BY p.posted_at)
        )
    * 86400 AS INTEGER) AS seconds_since_last,
    CASE
        WHEN CAST(
            JULIANDAY(p.posted_at) - JULIANDAY(
                LAG(p.posted_at) OVER (PARTITION BY p.platform ORDER BY p.posted_at)
            )
        * 86400 AS INTEGER) BETWEEN 3300 AND 7500 THEN 'within_window'
        WHEN CAST(
            JULIANDAY(p.posted_at) - JULIANDAY(
                LAG(p.posted_at) OVER (PARTITION BY p.platform ORDER BY p.posted_at)
            )
        * 86400 AS INTEGER) IS NULL THEN 'first_post'
        ELSE 'outside_window'
    END AS cadence_status
FROM publish_log p
WHERE p.success = 1
GROUP BY DATE(p.posted_at), p.platform;
