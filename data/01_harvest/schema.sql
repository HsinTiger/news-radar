-- News Radar · SQLite Schema
-- 沿用 alpha_pipeline 的「id + content + timestamp」基底風格
-- 一張表存原始素材 (news_items)、一張表存 AI 草稿 (drafts)、一張表存發布紀錄 (publish_log)

-- ============ 1. 原始新聞素材 ============
CREATE TABLE IF NOT EXISTS news_items (
    id              TEXT PRIMARY KEY,         -- sha1(url)
    feed_name       TEXT NOT NULL,
    feed_tier       TEXT NOT NULL,            -- primary / secondary
    source_type     TEXT DEFAULT 'article',   -- article / social / video / forum
    url             TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    published_at    TEXT NOT NULL,            -- ISO8601
    fetched_at      TEXT NOT NULL,
    language        TEXT,
    raw_html        TEXT,                     -- 原始 HTML (debug 用，可日後 vacuum)
    clean_markdown  TEXT,                     -- trafilatura 清洗後
    word_count      INTEGER,
    og_image_url    TEXT,
    og_video_url    TEXT,                     -- Phase 8.16：影片/音訊 URL（可能是直鏈或 embed）
    og_video_is_direct INTEGER DEFAULT 0,     -- 1 = .mp4/.mov/.webm 等可直丟 Meta Graph API
    tags            TEXT,                     -- JSON array
    status          TEXT DEFAULT 'fetched',   -- fetched / scored / drafted / published / dropped
    drop_reason     TEXT,
    substack_written_at TEXT,                  -- local/OneDrive article artifact exists
    substack_draft_id TEXT,                    -- Substack API returned draft id (never publish id)
    substack_drafted_at TEXT,                  -- truthful remote draft-created evidence
    -- Phase 8.20：主題分類 + 加權分數
    topic_category     TEXT,                  -- 見 src/topic_taxonomy.py 的 category_id（snake_case）
    topic_confidence   REAL,                  -- 0..1，classifier 回傳的信心
    topic_rationale    TEXT,                  -- classifier 附的一句話理由（給人工檢查用）
    weighted_score     REAL                   -- scorer 結果 × topic_weights.weight，clip 0..2；排序實際用這個
);

CREATE INDEX IF NOT EXISTS idx_news_status ON news_items(status);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_items(published_at);
-- Phase 8.20 的 idx_news_topic / idx_news_weighted_score 故意不寫在這：
-- topic_category 與 weighted_score 是 Phase 8.20 才加的欄位，對舊 DB 而言
-- CREATE TABLE IF NOT EXISTS 不會補欄位，CREATE INDEX 會因為該欄位不存在而炸。
-- 改在 src/db.py::init_db 的 ALTER TABLE migration 之後執行（同 idx_drafts_queue_status 模式）。


-- ============ 2. AI 產出草稿 ============
CREATE TABLE IF NOT EXISTS drafts (
    id                   TEXT PRIMARY KEY,        -- sha1(news_id + persona_version)
    news_id              TEXT NOT NULL,
    persona_version      TEXT NOT NULL,           -- 對應 news_radar_soul.md 的版本
    title                TEXT,
    hook                 TEXT,
    framework            TEXT,
    validation           TEXT,
    macro_insight        TEXT,
    ending_question      TEXT,
    hashtags             TEXT,                    -- JSON array
    image_url            TEXT,
    full_text            TEXT,                    -- 組裝後的完整貼文
    confidence_score     REAL,                    -- 0.0 ~ 1.0
    score_breakdown      TEXT,                    -- JSON (各維度分數)
    llm_provider         TEXT,
    llm_model            TEXT,
    input_tokens         INTEGER,
    output_tokens        INTEGER,
    cached_tokens        INTEGER,
    cost_usd             REAL,
    generated_at         TEXT NOT NULL,
    status               TEXT DEFAULT 'pending_review',
                          -- pending_review / approved / rejected / auto_approved / published
    reviewer_action      TEXT,                    -- approved_as_is / edited / rejected
    final_text           TEXT,                    -- 若有編輯則記錄最終版（reflector 用）
    -- Phase 8.18：雲本混合架構 —— publish queue 的兩個欄位（與 status 正交）
    publish_at           TEXT,                    -- composer 寫入的預期發佈時間（ISO8601，freshness-first 下只當 stale 判定用）
    queue_status         TEXT,                    -- NULL / queued / published / stale / failed (publisher 獨占改動)
    FOREIGN KEY (news_id) REFERENCES news_items(id)
);

CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_drafts_score ON drafts(confidence_score);
-- idx_drafts_queue_status 故意不寫在這：queue_status 欄位是 Phase 8.18 才加的,
-- 對舊 DB 而言該欄位要等 src/db.py::init_db 跑 _migrate_add_column_if_missing 才會存在。
-- 這條 index 的 CREATE 改到 db.py 在 migration 之後執行 (見 init_db 第 70-74 行的 CREATE INDEX IF NOT EXISTS)。


-- ============ 3. 發布紀錄 ============
CREATE TABLE IF NOT EXISTS publish_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id        TEXT NOT NULL,
    platform        TEXT NOT NULL,            -- facebook / threads
    platform_post_id TEXT,                    -- 平台回傳的 ID
    posted_at       TEXT NOT NULL,
    success         INTEGER NOT NULL,         -- 1 / 0
    error_message   TEXT,
    FOREIGN KEY (draft_id) REFERENCES drafts(id)
);

CREATE INDEX IF NOT EXISTS idx_publish_draft ON publish_log(draft_id);
-- Exactly one successful external publication is allowed per draft/platform.
-- Failed attempts remain append-only and may be retried.
CREATE UNIQUE INDEX IF NOT EXISTS uq_publish_success
    ON publish_log(draft_id, platform)
    WHERE success = 1;


-- ============ 4. Token 用量追蹤（每日聚合）============
CREATE TABLE IF NOT EXISTS token_usage_daily (
    date             TEXT PRIMARY KEY,        -- YYYY-MM-DD
    provider         TEXT,
    model            TEXT,
    total_input      INTEGER DEFAULT 0,
    total_output     INTEGER DEFAULT 0,
    total_cached     INTEGER DEFAULT 0,
    total_cost_usd   REAL DEFAULT 0.0,
    call_count       INTEGER DEFAULT 0
);


-- ============ 5. 發布後互動數據（Milestone 3 Reflector）============
-- 每次 run_reflect.py 都會抓一次 FB / IG / Threads 最新互動數，append-only
-- Reflector 會取每個貼文「最新一筆」作為學習訊號
CREATE TABLE IF NOT EXISTS engagement_stats (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id         TEXT NOT NULL,
    platform         TEXT NOT NULL,            -- facebook / threads / instagram
    platform_post_id TEXT NOT NULL,
    fetched_at       TEXT NOT NULL,            -- ISO8601
    likes            INTEGER DEFAULT 0,
    comments         INTEGER DEFAULT 0,
    shares           INTEGER DEFAULT 0,        -- FB/IG shares
    saves            INTEGER DEFAULT 0,        -- IG saves
    reposts          INTEGER DEFAULT 0,        -- Threads reposts
    quotes           INTEGER DEFAULT 0,        -- Threads quotes
    replies          INTEGER DEFAULT 0,        -- Threads replies
    views            INTEGER DEFAULT 0,        -- Threads / IG native views
    reach            INTEGER DEFAULT 0,        -- IG native reach
    clicks           INTEGER DEFAULT 0,        -- FB post_clicks
    raw_json         TEXT,                      -- 原始 API 回傳
    FOREIGN KEY (draft_id) REFERENCES drafts(id)
);

CREATE INDEX IF NOT EXISTS idx_engagement_draft ON engagement_stats(draft_id);
CREATE INDEX IF NOT EXISTS idx_engagement_post  ON engagement_stats(platform, platform_post_id);


-- ============ 5.1 平台專屬變體（Milestone 3.1）============
-- 每個 draft_id 會展開成 3 列（facebook / instagram / threads），
-- publisher 會直接拿對應 row 的 full_text 發文，不再做字數截斷。
CREATE TABLE IF NOT EXISTS platform_drafts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id          TEXT NOT NULL,
    platform          TEXT NOT NULL,          -- facebook / instagram / threads
    title             TEXT,
    body              TEXT,                   -- 正文（不含 hashtag）
    hashtags          TEXT,                   -- JSON array
    full_text         TEXT NOT NULL,          -- 組好、可直接發佈的完整文字（含 hashtag）
    final_text        TEXT,                   -- 人工編輯後的版本（給 Reflector）
    reviewer_action   TEXT,                   -- approved_as_is / edited / rejected
    char_count        INTEGER,
    appendix_version  TEXT,                   -- 對應 platform appendix 的版本
    created_at        TEXT NOT NULL,
    UNIQUE (draft_id, platform),
    FOREIGN KEY (draft_id) REFERENCES drafts(id)
);

CREATE INDEX IF NOT EXISTS idx_platform_drafts_draft ON platform_drafts(draft_id);
CREATE INDEX IF NOT EXISTS idx_platform_drafts_platform ON platform_drafts(platform);


-- ============ 5.2 內容品質證據（不保存全文）============
-- 每次 compose / pre-publish / historical backfill 都只留下規則命中、
-- severity 與文字雜湊。rewrite 是「需再寫或人工看」；只有 block 是拒發。
CREATE TABLE IF NOT EXISTS content_quality_evaluations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id          TEXT NOT NULL,
    news_id           TEXT,
    platform          TEXT NOT NULL CHECK(platform IN ('facebook','instagram','threads')),
    stage             TEXT NOT NULL CHECK(stage IN ('compose','pre_publish','backfill')),
    attempt           INTEGER NOT NULL DEFAULT 1 CHECK(attempt BETWEEN 1 AND 9),
    checked_at        TEXT NOT NULL,
    guard_version     TEXT NOT NULL,
    text_sha256       TEXT NOT NULL,
    decision          TEXT NOT NULL CHECK(decision IN ('pass','warn','rewrite','block')),
    block_count       INTEGER NOT NULL DEFAULT 0,
    rewrite_count     INTEGER NOT NULL DEFAULT 0,
    warn_count        INTEGER NOT NULL DEFAULT 0,
    issue_codes_json  TEXT NOT NULL DEFAULT '[]',
    issues_json       TEXT NOT NULL DEFAULT '[]',
    UNIQUE(draft_id, platform, stage, attempt, text_sha256)
);

CREATE INDEX IF NOT EXISTS idx_quality_platform_checked
    ON content_quality_evaluations(platform, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_quality_draft_platform
    ON content_quality_evaluations(draft_id, platform, checked_at DESC);


-- ============ 6. 反思紀錄（Reflector 每次執行留痕）============
CREATE TABLE IF NOT EXISTS reflection_events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at                TEXT NOT NULL,
    signals_summary       TEXT,               -- JSON 統計：{"edits":n,"csv":m,"engagement":k}
    samples_used          INTEGER DEFAULT 0,
    soul_version_before   TEXT,
    soul_version_after    TEXT,
    patch_markdown        TEXT,               -- 追加到 soul.md Ⅸ. Iteration Log 的內容
    rules_added_json      TEXT,               -- JSON array
    rationale             TEXT,
    input_tokens          INTEGER DEFAULT 0,
    output_tokens         INTEGER DEFAULT 0,
    cost_usd              REAL DEFAULT 0.0,
    status                TEXT DEFAULT 'completed'  -- completed / skipped_low_samples / failed
);

CREATE INDEX IF NOT EXISTS idx_reflection_ran_at ON reflection_events(ran_at);


-- ============ 7. 主題權重（Phase 8.20，Hsin seed 2026-04-21）============
-- 分類 id（snake_case）對應 src/topic_taxonomy.py 的 TopicCategory.id。
-- seed 寫在 db.py::init_db 裡，以便未來改權重不用碰 SQL；這張表由 back-prop
-- reflector 週期性 UPDATE，人工改動請在 update_reason 記 'manual'。
CREATE TABLE IF NOT EXISTS topic_weights (
    category_id       TEXT PRIMARY KEY,
    display_name      TEXT NOT NULL,
    weight            REAL NOT NULL,          -- 目前使用中的權重，0.3..2.0
    last_updated_at   TEXT NOT NULL,          -- ISO8601
    update_reason     TEXT NOT NULL,          -- 'initial_seed' / 'back_prop' / 'manual'
    sample_count      INTEGER DEFAULT 0,      -- 這類別已累積的發文數（給 back-prop EMA 用）
    last_delta        REAL,                   -- 最近一次 back-prop 的 delta（debug 用）
    notes             TEXT
);


-- ============ 8. 主題權重變動歷史（Phase 8.20，審計用）============
-- 每次 topic_weights 被 UPDATE 就 INSERT 一筆；人工檢查『為什麼 ai_model 從 1.7
-- 跌到 1.3』時可以追溯。append-only。
CREATE TABLE IF NOT EXISTS topic_weight_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id       TEXT NOT NULL,
    recorded_at       TEXT NOT NULL,
    weight_before     REAL,
    weight_after      REAL NOT NULL,
    update_reason     TEXT NOT NULL,
    delta             REAL,                   -- weight_after - weight_before
    samples_in_window INTEGER,                -- 本輪 back-prop 看了多少篇
    rationale         TEXT                    -- 人工或機器留的一段理由
);

CREATE INDEX IF NOT EXISTS idx_topic_weight_history_category
    ON topic_weight_history(category_id, recorded_at);


-- ============ 8.5 分平台發文策略 override（owner-governed）============
-- Git 內的 social_automation_policy.json 是 bootstrap/default；owner 核准的
-- exact cadence proposal 會寫入這張 runtime table。scheduler 只讀已部署值。
CREATE TABLE IF NOT EXISTS social_policy_overrides (
    platform                 TEXT PRIMARY KEY CHECK(platform IN ('facebook','instagram','threads')),
    target_posts_per_day     INTEGER NOT NULL CHECK(target_posts_per_day BETWEEN 0 AND 4),
    minimum_interval_hours   REAL NOT NULL CHECK(minimum_interval_hours BETWEEN 4 AND 48),
    local_slots_json         TEXT NOT NULL,
    source_proposal_id       TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_policy_history (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    platform                 TEXT NOT NULL,
    recorded_at              TEXT NOT NULL,
    cadence_before_json      TEXT NOT NULL,
    cadence_after_json       TEXT NOT NULL,
    source_proposal_id       TEXT NOT NULL,
    rationale                TEXT
);

CREATE INDEX IF NOT EXISTS idx_social_policy_history_platform
    ON social_policy_history(platform, recorded_at);


-- ============ 9. Phase 9 Item 2 · Reflector proposal lineage ============
-- 2026-04-27 · Spec: PM_Radar/specs/phase_9_implementation_plan.md §3 Item 2
--
-- Dual-record substrate for Phase 9 unified reflector. Canonical record
-- is the per-ISO-week jsonl under data/05_reflect/proposals/YYYY-WW.jsonl;
-- this table is the queryable mirror. src/reflector/proposals.py is the
-- only writer (Items 3-7 analyzers call write_proposal()).
--
-- See also: data/01_harvest/migrations/2026-04-27_phase9_proposal_lineage.sql
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
