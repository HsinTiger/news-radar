"""
News Radar · SQLite 初始化與 CRUD
沿用 alpha_pipeline 的「print [Module N]」log 風格
"""
from __future__ import annotations
import json
import hashlib
import sqlite3
from pathlib import Path
from typing import Optional, List
from .schema import NewsItem, Draft, PublishResult

# 平台 key → DB 中的平台命名
PLATFORM_DB_NAME = {"fb": "facebook", "ig": "instagram", "threads": "threads"}

# 平台 key → UI 顯示用的標籤
PLATFORM_LABEL = {"fb": "📘 FB", "ig": "📸 IG", "threads": "🧵 Threads"}

# 計算絕對路徑（不管從哪個 cwd 執行都能找到 DB）
_BASE = Path(__file__).resolve().parent.parent
DB_PATH = _BASE / "data" / "01_harvest" / "news_radar.db"
SCHEMA_PATH = _BASE / "data" / "01_harvest" / "schema.sql"
VIEWS_PATH = _BASE / "data" / "01_harvest" / "views.sql"


def get_conn() -> sqlite3.Connection:
    """取得連線，row_factory 設為 dict-like。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def record_token_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    cost_usd: float = 0.0,
    date: Optional[str] = None,
) -> None:
    """Accumulate one LLM call's usage into token_usage_daily (UPSERT by date).

    2026-05-30 (Optimization D): the schema for this table existed but nothing
    wrote to it, so before/after token savings were unmeasurable. The substack
    composer now calls this after every Claude CLI / Gemini call. PK is `date`,
    so we accumulate totals into a single daily row (latest provider/model wins
    the label columns — fine for a single-writer daily pipeline).
    """
    from datetime import datetime, timezone

    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT INTO token_usage_daily
                    (date, provider, model, total_input, total_output,
                     total_cached, total_cost_usd, call_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(date) DO UPDATE SET
                    provider       = excluded.provider,
                    model          = excluded.model,
                    total_input    = total_input    + excluded.total_input,
                    total_output   = total_output   + excluded.total_output,
                    total_cached   = total_cached   + excluded.total_cached,
                    total_cost_usd = total_cost_usd + excluded.total_cost_usd,
                    call_count     = call_count     + 1
                """,
                (day, provider, model, int(input_tokens or 0),
                 int(output_tokens or 0), int(cached_tokens or 0),
                 float(cost_usd or 0.0)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # metering must never break the pipeline
        print(f"[DB] ⚠️ record_token_usage failed (non-fatal): {exc}")


def _migrate_add_column_if_missing(conn: sqlite3.Connection, table: str,
                                   column: str, ddl_suffix: str) -> None:
    """Idempotent migration 輔助：若 column 不存在就 ALTER TABLE ADD COLUMN。"""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_suffix}")
        print(f"[DB]  ↳ migration：{table}.{column} 已加入")


def init_db() -> None:
    """首次執行：建立資料庫與所有表。安全重跑。
    也順便跑 idempotent migration，讓舊 DB 自動補缺漏欄位。
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"[DB] 初始化 {DB_PATH.name}")
    conn = get_conn()
    try:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema_sql)
        # 針對舊 DB 補欄位（SQLite 的 CREATE TABLE IF NOT EXISTS 不會補欄位）
        _migrate_add_column_if_missing(
            conn, "news_items", "source_type", "TEXT DEFAULT 'article'"
        )
        # Phase 8.16：影片 URL 提取
        _migrate_add_column_if_missing(
            conn, "news_items", "og_video_url", "TEXT"
        )
        _migrate_add_column_if_missing(
            conn, "news_items", "og_video_is_direct", "INTEGER DEFAULT 0"
        )
        # Phase 8.18：雲本混合架構 publish queue（drafts 表）
        _migrate_add_column_if_missing(
            conn, "drafts", "publish_at", "TEXT"
        )
        _migrate_add_column_if_missing(
            conn, "drafts", "queue_status", "TEXT"
        )
        # Phase 10 (2026-06-03)：把 carousel 圖卡內容（CarouselCards JSON）存在 draft 層，
        # 讓雲端發文的 run_publish_queue 能 render+發 carousel（render 非 LLM，不破壞防火牆）。
        _migrate_add_column_if_missing(
            conn, "drafts", "carousel_json", "TEXT"
        )
        # index 可能首次建立時已存在、若是 migration 情境要補
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_drafts_queue_status ON drafts(queue_status)"
            )
        except sqlite3.OperationalError:
            pass
        # Phase 8.20：主題分類 + 加權分數（news_items 新欄位）
        _migrate_add_column_if_missing(
            conn, "news_items", "topic_category", "TEXT"
        )
        _migrate_add_column_if_missing(
            conn, "news_items", "topic_confidence", "REAL"
        )
        _migrate_add_column_if_missing(
            conn, "news_items", "topic_rationale", "TEXT"
        )
        _migrate_add_column_if_missing(
            conn, "news_items", "weighted_score", "REAL"
        )
        # Substack evidence split: a local article is not proof that the remote
        # draft inbox accepted it.  Only substack_drafted_at + draft_id prove that.
        _migrate_add_column_if_missing(
            conn, "news_items", "substack_written_at", "TEXT"
        )
        _migrate_add_column_if_missing(
            conn, "news_items", "substack_draft_id", "TEXT"
        )
        _migrate_add_column_if_missing(
            conn, "news_items", "substack_drafted_at", "TEXT"
        )
        _migrate_add_column_if_missing(
            conn, "news_items", "substack_post_id", "TEXT"
        )
        _migrate_add_column_if_missing(
            conn, "news_items", "substack_post_url", "TEXT"
        )
        _migrate_add_column_if_missing(
            conn, "news_items", "substack_published_at", "TEXT"
        )
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_topic ON news_items(topic_category)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_weighted_score "
                "ON news_items(weighted_score)"
            )
        except sqlite3.OperationalError:
            pass
        # Phase 8.20：seed topic_weights（僅插入尚未存在的 category）
        _seed_topic_weights(conn)
        # 2026-04-25: log-scale time-series engagement polling
        # （詳見 data/01_harvest/migrations/2026-04-25_log_scale_engagement.sql）
        _migrate_log_scale_engagement(conn)
        # Phase 9 Item 2 (2026-04-27): reflector_proposal_lineage table is
        # defined inline in schema.sql §9 with IF NOT EXISTS guards (pure new
        # table — no ALTER needed). Re-execute the migration SQL file too so
        # the canonical migration record is the source of truth on disk; the
        # statements are idempotent. See:
        #   data/01_harvest/migrations/2026-04-27_phase9_proposal_lineage.sql
        _migrate_proposal_lineage(conn)
        # Phase 9 Item 1 (2026-04-28): backfill news_items.status='published'
        # historic rows where mark_queue_published was called before the fix.
        # See: data/01_harvest/migrations/2026-04-28_backfill_news_items_status.sql
        _migrate_backfill_news_items_status(conn)
        # Phase 9 Item 1 (2026-04-27): substrate views for unified reflector.
        # Sourced AFTER schema.sql + all column migrations so views can rely
        # on Phase 8.18 queue_status / Phase 8.20 topic_category etc.
        # Idempotent (CREATE VIEW IF NOT EXISTS); cheap to re-run on every init.
        if VIEWS_PATH.exists():
            views_sql = VIEWS_PATH.read_text(encoding="utf-8")
            conn.executescript(views_sql)
        conn.commit()
    finally:
        # sqlite3.Connection context exit does not close the handle. Explicit
        # close is required for Windows temp DB cleanup and deterministic IO.
        conn.close()
    print("[DB]  ↳ schema 套用完成")


_MIGRATION_PROPOSAL_LINEAGE_PATH = (
    _BASE / "data" / "01_harvest" / "migrations"
    / "2026-04-27_phase9_proposal_lineage.sql"
)


def _migrate_proposal_lineage(conn: sqlite3.Connection) -> None:
    """Idempotent migration for Phase 9 Item 2 reflector_proposal_lineage.

    The CREATE TABLE / CREATE INDEX statements live in schema.sql §9 (already
    sourced earlier in init_db) AND in the canonical migration file.
    Re-running the migration script here is a belt-and-suspenders idempotent
    no-op on fresh DBs and an effective replay on older DBs that pre-date
    the §9 amendment. All statements use IF NOT EXISTS.
    """
    if not _MIGRATION_PROPOSAL_LINEAGE_PATH.exists():
        return
    sql = _MIGRATION_PROPOSAL_LINEAGE_PATH.read_text(encoding="utf-8")
    try:
        conn.executescript(sql)
    except sqlite3.OperationalError:
        # IF NOT EXISTS should make this unreachable, but defend the
        # init path against future migration-file edits.
        pass


_MIGRATION_BACKFILL_NEWS_ITEMS_STATUS_PATH = (
    _BASE / "data" / "01_harvest" / "migrations"
    / "2026-04-28_backfill_news_items_status.sql"
)


def _migrate_backfill_news_items_status(conn: sqlite3.Connection) -> None:
    """Idempotent migration for Phase 9 Item 1 backfill.

    Backfill missing news_items.status='published' promotions that should have
    occurred when mark_queue_published was called (but didn't, due to defect
    in src/db.py lines 699-710 before Phase 9 Item 1 fix).

    Safe to re-run (WHERE clause excludes already-published rows).
    """
    if not _MIGRATION_BACKFILL_NEWS_ITEMS_STATUS_PATH.exists():
        return
    sql = _MIGRATION_BACKFILL_NEWS_ITEMS_STATUS_PATH.read_text(encoding="utf-8")
    try:
        conn.executescript(sql)
        # Verify: count published news_items before and log if changed.
        result = conn.execute(
            "SELECT COUNT(*) as count FROM news_items WHERE status='published'"
        ).fetchone()
        if result:
            print(f"[DB]  ↳ backfill：news_items.status='published' 共 {result['count']} 筆")
    except sqlite3.OperationalError:
        pass


def _migrate_log_scale_engagement(conn: sqlite3.Connection) -> None:
    """Idempotent migration for log-scale engagement polling (2026-04-25).

    Adds:
      - engagement_stats.post_age_bucket (INTEGER, NULL OK; canonical 1/24/168)
      - engagement_stats.clicks for the current Facebook metric contract
      - CHECK trigger restricting bucket to NULL or {1, 24, 168}
      - Partial UNIQUE INDEX on (draft_id, platform, post_age_bucket)
        WHERE post_age_bucket IS NOT NULL — prevents double-polling same bucket
        without blocking legacy NULL rows
      - INDEX (draft_id, platform, fetched_at DESC) — accelerates the
        engagement_stats_latest VIEW's correlated subquery
      - VIEW engagement_stats_latest — most recent row per (draft, platform);
        dashboard reads from this for snapshot queries

    All statements are idempotent (IF NOT EXISTS / column-existence check).
    Safe to re-run.
    """
    # 1. ADD COLUMN (only if missing)
    _migrate_add_column_if_missing(
        conn, "engagement_stats", "post_age_bucket", "INTEGER"
    )
    _migrate_add_column_if_missing(
        conn, "engagement_stats", "clicks", "INTEGER DEFAULT 0"
    )

    # 2. CHECK trigger — SQLite can't add CHECK to existing column via ALTER,
    #    but BEFORE INSERT trigger gives equivalent enforcement.
    try:
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS engagement_stats_bucket_check
            BEFORE INSERT ON engagement_stats
            FOR EACH ROW
            WHEN NEW.post_age_bucket IS NOT NULL
                 AND NEW.post_age_bucket NOT IN (1, 24, 168)
            BEGIN
                SELECT RAISE(ABORT, 'post_age_bucket must be NULL or one of (1, 24, 168)');
            END
        """)
    except sqlite3.OperationalError:
        pass

    # 3. Partial unique index — legacy NULL rows excluded
    try:
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_engagement_stats_bucket
            ON engagement_stats (draft_id, platform, post_age_bucket)
            WHERE post_age_bucket IS NOT NULL
        """)
    except sqlite3.OperationalError:
        pass

    # 4. Lookup index for VIEW's correlated subquery on MAX(fetched_at)
    try:
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_engagement_stats_lookup
            ON engagement_stats (draft_id, platform, fetched_at DESC)
        """)
    except sqlite3.OperationalError:
        pass

    # 5. VIEW for dashboard snapshot reads.
    #    Form: correlated subquery (not ROW_NUMBER) — SQLite supports both
    #    since 3.25, but correlated subquery is more readable in sqlite3 CLI
    #    debugging and equally fast given the lookup index above.
    try:
        conn.execute("""
            CREATE VIEW IF NOT EXISTS engagement_stats_latest AS
            SELECT * FROM engagement_stats e
            WHERE fetched_at = (
                SELECT MAX(fetched_at)
                FROM engagement_stats e2
                WHERE e2.draft_id = e.draft_id
                  AND e2.platform = e.platform
            )
        """)
    except sqlite3.OperationalError:
        pass

    # 6. Phase 9 Item 1 (2026-04-28): engagement_per_post view for dashboard
    try:
        conn.execute("""
            CREATE VIEW IF NOT EXISTS engagement_per_post AS
            SELECT e.*, d.title AS draft_title
            FROM engagement_stats_latest e
            JOIN drafts d ON d.id = e.draft_id
            ORDER BY e.fetched_at DESC
        """)
    except sqlite3.OperationalError:
        pass


def _seed_topic_weights(conn: sqlite3.Connection) -> None:
    """把 src.topic_taxonomy.TOPIC_CATEGORIES 的初始權重寫進 topic_weights 表。
    已存在的 category_id 完全不動（避免覆蓋 back-prop 已調整過的權重）；
    只補新增的類別。
    """
    # 延後 import 避免循環依賴（db.py 極基層，taxonomy 也不依賴 db）
    from datetime import datetime, timezone
    from .topic_taxonomy import TOPIC_CATEGORIES

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = {
        r[0] for r in conn.execute(
            "SELECT category_id FROM topic_weights"
        ).fetchall()
    }
    inserted = 0
    for c in TOPIC_CATEGORIES:
        if c.id in existing:
            continue
        conn.execute(
            "INSERT INTO topic_weights "
            "(category_id, display_name, weight, last_updated_at, "
            "update_reason, sample_count) "
            "VALUES (?, ?, ?, ?, 'initial_seed', 0)",
            (c.id, c.display_name, c.seed_weight, now_iso),
        )
        inserted += 1
    if inserted:
        print(f"[DB]  ↳ 主題權重 seed：補入 {inserted} 個新類別")


# ---------- news_items CRUD ----------

def news_exists(conn: sqlite3.Connection, news_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM news_items WHERE id = ? LIMIT 1", (news_id,)
    ).fetchone()
    return row is not None


def upsert_news(conn: sqlite3.Connection, item: NewsItem) -> bool:
    """
    寫入新聞素材。回傳 True 表示「新插入」，False 表示「已存在，跳過」。
    """
    if news_exists(conn, item.id):
        return False

    conn.execute(
        """
        INSERT INTO news_items (
            id, feed_name, feed_tier, source_type, url, title,
            published_at, fetched_at, language, raw_html, clean_markdown,
            word_count, og_image_url, og_video_url, og_video_is_direct,
            tags, status, drop_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item.id,
            item.feed_name,
            item.feed_tier,
            item.source_type,
            item.url,
            item.title,
            item.published_at,
            item.fetched_at,
            item.language,
            item.raw_html,
            item.clean_markdown,
            item.word_count,
            item.og_image_url,
            item.og_video_url,
            1 if item.og_video_is_direct else 0,
            json.dumps(item.tags, ensure_ascii=False),
            item.status,
            item.drop_reason,
        ),
    )
    conn.commit()
    return True


def prune_old_source_text(conn: sqlite3.Connection, keep_days: int = 14) -> int:
    """把超過 keep_days 天的 news_items 的 clean_markdown 清空（保留 row 供去重）。

    2026-07-05 加：DB 長到 100MB 就再也 push 不上 state branch（GitHub 硬限制），
    整條 state 同步（雲端 full_pipeline persist、submit persist、Mac compose push）全掛，
    投稿與排程一起死。clean_markdown（全文）是唯一大宗，且合成稿後就沒用了
    （drafts 有自己的 full_text）。每次 harvest 後跑一次，把 DB 體積 bound 在約 14 天內。
    只清舊列的正文、不刪列（id/url 去重鍵留著），對現行流程零影響。
    回傳被清空的列數。VACUUM 交給偶發維護（每次跑太重）。"""
    cur = conn.execute(
        "UPDATE news_items SET clean_markdown='' "
        "WHERE fetched_at < datetime('now', ?) AND clean_markdown IS NOT NULL "
        "AND clean_markdown <> ''",
        (f"-{int(keep_days)} days",),
    )
    conn.commit()
    n = cur.rowcount if cur.rowcount is not None else 0
    if n:
        print(f"[DB]  ↳ prune：清空 {n} 篇 >{keep_days}天 舊素材正文（bound DB 體積）")
    return n


def mark_dropped(conn: sqlite3.Connection, news_id: str, reason: str) -> None:
    conn.execute(
        "UPDATE news_items SET status='dropped', drop_reason=? WHERE id=?",
        (reason, news_id),
    )
    conn.commit()


def update_status(conn: sqlite3.Connection, news_id: str, status: str) -> None:
    conn.execute(
        "UPDATE news_items SET status=? WHERE id=?",
        (status, news_id),
    )
    conn.commit()


# ---------- Phase 8.20：topic 分類 + 加權分數 ----------

def set_news_topic(
    conn: sqlite3.Connection,
    news_id: str,
    category_id: str,
    confidence: float,
    rationale: str,
    weighted_score: float,
) -> None:
    """scorer / run_pipeline 算完 topic 後寫這四個欄位。與 update_status 解耦，
    因為即便 status 已經是 'scored' / 'drafted'，主題分類還是可以補寫。"""
    conn.execute(
        """
        UPDATE news_items
           SET topic_category  = ?,
               topic_confidence = ?,
               topic_rationale  = ?,
               weighted_score   = ?
         WHERE id = ?
        """,
        (category_id, confidence, rationale, weighted_score, news_id),
    )
    conn.commit()


def get_topic_weight(
    conn: sqlite3.Connection, category_id: str, default: float = 1.0
) -> float:
    """讀 topic_weights 表的當前 weight。查不到或壞資料 → 回 default（1.0）。
    Back-prop 會持續 UPDATE 這個值，所以每次 pipeline 都應該現讀現算。"""
    row = conn.execute(
        "SELECT weight FROM topic_weights WHERE category_id = ?",
        (category_id,),
    ).fetchone()
    if row is None:
        return default
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return default


def bump_topic_sample_count(
    conn: sqlite3.Connection, category_id: str, delta: int = 1
) -> None:
    """每次該類別有 draft 寫進 DB，就 +1。供 back-prop 判斷『樣本夠不夠』。"""
    conn.execute(
        "UPDATE topic_weights "
        "SET sample_count = COALESCE(sample_count, 0) + ? "
        "WHERE category_id = ?",
        (delta, category_id),
    )
    conn.commit()


def insert_draft(conn: sqlite3.Connection, draft: Draft) -> None:
    conn.execute(
        """
        INSERT INTO drafts (
            id, news_id, persona_version, title, hook, framework,
            validation, macro_insight, ending_question, hashtags,
            image_url, full_text, confidence_score, score_breakdown,
            llm_provider, llm_model, input_tokens, output_tokens,
            cached_tokens, cost_usd, generated_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            news_id = excluded.news_id,
            persona_version = excluded.persona_version,
            title = excluded.title,
            hook = excluded.hook,
            framework = excluded.framework,
            validation = excluded.validation,
            macro_insight = excluded.macro_insight,
            ending_question = excluded.ending_question,
            hashtags = excluded.hashtags,
            image_url = excluded.image_url,
            full_text = excluded.full_text,
            confidence_score = excluded.confidence_score,
            score_breakdown = excluded.score_breakdown,
            llm_provider = excluded.llm_provider,
            llm_model = excluded.llm_model,
            input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens,
            cached_tokens = excluded.cached_tokens,
            cost_usd = excluded.cost_usd,
            generated_at = excluded.generated_at,
            status = CASE
                WHEN drafts.status = 'published' THEN drafts.status
                ELSE excluded.status
            END
        """,
        (
            draft.id,
            draft.news_id,
            draft.persona_version,
            draft.content.title,
            draft.content.hook,
            draft.content.framework,
            draft.content.validation,
            draft.content.macro_insight,
            draft.content.ending_question,
            json.dumps(draft.content.hashtags, ensure_ascii=False),
            draft.content.image_url,
            draft.full_text,
            draft.confidence_score,
            json.dumps(draft.score_breakdown.model_dump(), ensure_ascii=False),
            draft.llm_provider,
            draft.llm_model,
            draft.input_tokens,
            draft.output_tokens,
            draft.cached_tokens,
            draft.cost_usd,
            draft.generated_at,
            draft.status,
        ),
    )
    conn.commit()


def set_carousel_json(conn: sqlite3.Connection, draft_id: str, carousel_json: Optional[str]) -> None:
    """Persist governed three-card content on the draft row.

    Stored at draft level (shared by all platforms). Called by run_pipeline right
    after insert_draft so the cloud publisher (run_publish_queue) can render+post
    carousels. NULL/empty is publish-blocking; no Meta path may degrade to a
    single image or text post.
    """
    conn.execute(
        "UPDATE drafts SET carousel_json = ? WHERE id = ?",
        (carousel_json, draft_id),
    )
    conn.commit()


def log_publish(conn: sqlite3.Connection, result: PublishResult) -> None:
    try:
        conn.execute(
            """
            INSERT INTO publish_log (
                draft_id, platform, platform_post_id, posted_at, success, error_message
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                result.draft_id,
                result.platform,
                result.platform_post_id,
                result.posted_at,
                1 if result.success else 0,
                result.error_message,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # The partial unique index is the final race guard.  A second writer
        # that lost the race observes the durable success and treats its DB
        # write as idempotent; all other constraint errors remain fatal.
        if result.success and has_successful_publish(
            conn, result.draft_id, result.platform
        ):
            conn.rollback()
            return
        conn.rollback()
        raise


def list_recent_titles(conn: sqlite3.Connection, limit: int = 30) -> List[str]:
    """給 scorer.py 比對相似度用的『最近 N 筆標題』清單"""
    rows = conn.execute(
        "SELECT title FROM news_items ORDER BY fetched_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [r["title"] for r in rows]


def get_pending_items(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Milestone 2 用：取所有還沒進 AI 處理的項目。

    Phase 8.20 Step 3：排序規則改為「topic weight desc → freshness desc」。
      - 第一輪 pending items 的 weighted_score 皆為 NULL（還沒 classify），
        SQLite COALESCE 會退回 0 → 排序純粹依 published_at DESC，與舊行為一致。
      - 若腳本中斷後重跑、或 backfill 腳本先跑過一輪，已分類的 item 會被先挑，
        把高權重類別（AI 三兄弟、供應鏈、財報）推前面處理。
    """
    return conn.execute(
        """
        SELECT * FROM news_items
         WHERE status='fetched'
           AND COALESCE(feed_name,'') <> 'user_substack'
           AND COALESCE(tags,'') NOT LIKE '%"substack_source"%'
         ORDER BY COALESCE(weighted_score, 0) DESC,
                  published_at DESC
        """
    ).fetchall()


# Phase 8.16：給影片發文測試用 —— 撈出「已抓到可上傳影片 URL」的素材
def list_items_with_direct_video(
    conn: sqlite3.Connection, limit: int = 20
) -> List[sqlite3.Row]:
    """回傳 og_video_is_direct=1 且 og_video_url 非空的 news_items，依 published_at 由新到舊。
    publisher 的短影片測試 path 可直接拿這個列表做輸入來源。
    """
    return conn.execute(
        """
        SELECT * FROM news_items
        WHERE og_video_is_direct = 1
          AND og_video_url IS NOT NULL
          AND TRIM(og_video_url) <> ''
          AND status <> 'dropped'
        ORDER BY published_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def count_video_coverage(conn: sqlite3.Connection) -> dict:
    """給 diagnose 用的快速盤點：有多少素材帶了影片 URL、其中多少是 direct。"""
    total = conn.execute("SELECT COUNT(*) AS c FROM news_items").fetchone()["c"]
    any_video = conn.execute(
        "SELECT COUNT(*) AS c FROM news_items "
        "WHERE og_video_url IS NOT NULL AND TRIM(og_video_url) <> ''"
    ).fetchone()["c"]
    direct_video = conn.execute(
        "SELECT COUNT(*) AS c FROM news_items "
        "WHERE og_video_is_direct = 1 "
        "AND og_video_url IS NOT NULL AND TRIM(og_video_url) <> ''"
    ).fetchone()["c"]
    return {
        "total": total,
        "with_any_video_url": any_video,
        "with_direct_video_url": direct_video,
    }


# ---------- Milestone 3.1 · Platform Drafts (三平台變體) ----------

def upsert_platform_draft(
    conn: sqlite3.Connection,
    draft_id: str,
    platform: str,
    title: str,
    body: str,
    hashtags: list,
    full_text: str,
    char_count: int,
    appendix_version: str,
    created_at: str,
) -> None:
    """寫入單一平台變體。UNIQUE(draft_id, platform) 保證每平台一列。"""
    conn.execute(
        """
        INSERT INTO platform_drafts
            (draft_id, platform, title, body, hashtags,
             full_text, char_count, appendix_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(draft_id, platform) DO UPDATE SET
            title = excluded.title,
            body = excluded.body,
            hashtags = excluded.hashtags,
            full_text = excluded.full_text,
            char_count = excluded.char_count,
            appendix_version = excluded.appendix_version,
            created_at = excluded.created_at
        """,
        (
            draft_id,
            platform,
            title,
            body,
            json.dumps(hashtags, ensure_ascii=False),
            full_text,
            int(char_count or 0),
            appendix_version,
            created_at,
        ),
    )
    conn.commit()


def get_platform_drafts(conn: sqlite3.Connection, draft_id: str) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM platform_drafts WHERE draft_id = ? ORDER BY platform",
        (draft_id,),
    ).fetchall()


def record_quality_evaluation(
    conn: sqlite3.Connection,
    *,
    draft_id: str,
    news_id: Optional[str],
    platform: str,
    stage: str,
    attempt: int,
    full_text: str,
    issues: list,
    checked_at: Optional[str] = None,
    commit: bool = True,
) -> tuple[str, bool]:
    """Persist one deterministic quality observation without storing content.

    Evidence is append-only by text hash. Re-running a backfill over unchanged
    text is idempotent. ``rewrite`` is intentionally distinct from ``block``:
    the composer gets one retry, while historical rewrite findings remain
    observations and never become retroactive publish failures.
    """
    from datetime import datetime, timezone
    from .content_quality_guard import QUALITY_GUARD_VERSION

    if platform not in {"facebook", "instagram", "threads"}:
        raise ValueError(f"unsupported quality platform: {platform}")
    if stage not in {"compose", "pre_publish", "backfill"}:
        raise ValueError(f"unsupported quality stage: {stage}")
    payload = [
        {
            "code": str(issue.code),
            "severity": str(issue.severity),
            "message": str(issue.message),
        }
        for issue in issues
    ]
    severities = [item["severity"] for item in payload]
    if "block" in severities:
        decision = "block"
    elif "rewrite" in severities:
        decision = "rewrite"
    elif "warn" in severities:
        decision = "warn"
    else:
        decision = "pass"
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO content_quality_evaluations(
          draft_id,news_id,platform,stage,attempt,checked_at,guard_version,
          text_sha256,decision,block_count,rewrite_count,warn_count,
          issue_codes_json,issues_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            draft_id,
            news_id,
            platform,
            stage,
            int(attempt),
            checked_at or datetime.now(timezone.utc).isoformat(),
            QUALITY_GUARD_VERSION,
            hashlib.sha256((full_text or "").encode("utf-8")).hexdigest(),
            decision,
            severities.count("block"),
            severities.count("rewrite"),
            severities.count("warn"),
            json.dumps(sorted({item["code"] for item in payload}), ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    if commit:
        conn.commit()
    return decision, cursor.rowcount == 1


def update_platform_draft_final_text(
    conn: sqlite3.Connection,
    draft_id: str,
    platform: str,
    final_text: str,
    reviewer_action: str = "edited",
) -> None:
    conn.execute(
        """
        UPDATE platform_drafts
           SET final_text = ?, reviewer_action = ?
         WHERE draft_id = ? AND platform = ?
        """,
        (final_text, reviewer_action, draft_id, platform),
    )
    conn.commit()


def list_platform_drafts_with_edits(conn: sqlite3.Connection, limit: int = 50) -> List[sqlite3.Row]:
    """給 Reflector 用：取所有被人工改過的平台變體。"""
    return conn.execute(
        """
        SELECT pd.*, d.title AS draft_title
          FROM platform_drafts pd
          JOIN drafts d ON d.id = pd.draft_id
         WHERE pd.final_text IS NOT NULL
           AND TRIM(pd.final_text) <> ''
           AND TRIM(pd.final_text) <> TRIM(pd.full_text)
         ORDER BY pd.created_at DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()


# ---------- Phase 8.18 · Publish Queue (Cloud publisher 用) ----------
#
# 語意備忘：
#   drafts.status        = composer / 審核維度  (pending_review / approved / auto_approved / published / rejected)
#   drafts.queue_status  = publisher 佇列維度  (NULL / queued / published / stale / failed)
#   drafts.publish_at    = composer 寫入的『預期發佈時間』(ISO8601)——freshness-first 下只當 stale 判定用
#
# Cloud publisher 的選稿契約（freshness-first）：
#   1. WHERE queue_status='queued' 且 status IN ('approved','auto_approved')
#   2. ORDER BY news_items.published_at DESC 挑最新一筆
#   3. 發出後 mark_queue_published(...)；剩下比它舊的全部 mark_queue_stale_older_than(...)

def enqueue_draft(
    conn: sqlite3.Connection,
    draft_id: str,
    publish_at: Optional[str] = None,
) -> None:
    """composer 寫稿完呼叫：把 draft 標為 queued。
    publish_at 是 ISO8601 預期發佈時間（可選；freshness-first 下主要當 stale 判定用）。
    Idempotent：同一個 draft_id 重複 enqueue 只會更新 publish_at，不會重複入庫。
    """
    conn.execute(
        """
        UPDATE drafts
           SET queue_status = 'queued',
               publish_at = COALESCE(?, publish_at)
         WHERE id = ?
        """,
        (publish_at, draft_id),
    )
    conn.commit()


def mark_recovery_actual_format(
    conn: sqlite3.Connection,
    draft_id: str,
    platform: str,
    actual_format: str,
    observed_at: str,
) -> None:
    """Record the API path that actually succeeded, not the intended format."""
    if actual_format not in {"feed", "carousel", "reel"}:
        raise ValueError(f"unsupported recovery format: {actual_format}")
    conn.execute(
        """
        UPDATE recovery_experiments
           SET actual_format=?,actual_format_at=?
         WHERE draft_id=? AND platform=?
        """,
        (actual_format, observed_at, draft_id, platform),
    )
    conn.commit()


def _pending_platform_clause(platforms) -> tuple[str, list[str]]:
    targets = sorted({str(value) for value in (platforms or []) if value})
    if not targets:
        return "", []
    placeholders = ",".join("?" * len(targets))
    return (
        f"""
          AND EXISTS (
            SELECT 1 FROM platform_drafts pd
             WHERE pd.draft_id=d.id
               AND pd.platform IN ({placeholders})
               AND NOT EXISTS (
                 SELECT 1 FROM publish_log p
                  WHERE p.draft_id=d.id AND p.platform=pd.platform AND p.success=1
               )
          )
        """,
        targets,
    )


def _recovery_experiment_clause(
    recovery_only: bool,
    platforms,
) -> tuple[str, list[str]]:
    if not recovery_only:
        return "", []
    from .content_quality_guard import QUALITY_GUARD_VERSION

    targets = sorted({str(value) for value in (platforms or []) if value})
    platform_sql = ""
    params: list[str] = []
    if targets:
        placeholders = ",".join("?" * len(targets))
        platform_sql = f" AND rx.platform IN ({placeholders})"
        params.extend(targets)
    params.append(QUALITY_GUARD_VERSION)
    return (
        f"""
          AND EXISTS (
            SELECT 1 FROM recovery_experiments rx
            JOIN content_quality_evaluations q
              ON q.draft_id=rx.draft_id AND q.platform=rx.platform
             WHERE rx.draft_id=d.id
               {platform_sql}
               AND q.stage='compose'
               AND q.guard_version=?
               AND q.id=(
                 SELECT MAX(q2.id)
                   FROM content_quality_evaluations q2
                  WHERE q2.draft_id=q.draft_id
                    AND q2.platform=q.platform
                    AND q2.stage='compose'
                    AND q2.guard_version=q.guard_version
               )
               AND q.decision IN ('pass','warn')
          )
        """,
        params,
    )


def pick_freshest_queued(
    conn: sqlite3.Connection,
    prefer_categories=None,
    platforms=None,
    recovery_only: bool = False,
) -> Optional[sqlite3.Row]:
    """Cloud publisher 的主選稿邏輯：挑 news_items.published_at 最新的那筆 queued draft。

    回傳單一 Row（包含 drafts + news_items 所有欄位）或 None。
    只回「可直接發」的——要求 queue_status='queued' 且人類審核已過（approved / auto_approved）。

    Phase 8.18 freshness order + platform-aware pending filter
    ------------------------------------------------
    這個 picker 的排序鍵是 `news_items.published_at`，不是 `drafts.publish_at`。
    意思是：被 enqueue 當下寫到 drafts.publish_at 的「預計時間」對發稿順序完全
    沒影響——publisher 永遠挑「最新的新聞」發，而不是「最早排進 queue」的。

    這個設計是有意的——頻道定位是「最新資訊」，不是「時段排程」。新鮮度
    永遠勝過 FIFO。drafts.publish_at 欄位留著只是 migration 成本太高，
    實際上是 dead field（production 程式碼沒有 READ 點，只有 logging）。

    未來若要 timezone-optimized scheduling（例如「早上 9 點一定發某類」），
    正確做法是：
        1. 加 `scheduled_at` 欄位（別重用 publish_at，避免語意混淆）
        2. 改 picker 為 `ORDER BY scheduled_at ASC`（然後新鮮度變排程的輸入）
        3. 同步砍掉 dashboard 的 freshness-first 說明

    ``platforms`` 不改 freshness 排序，只排除本次不該發或已成功的平台：
    Threads cycle 不會拿到 FB-only draft；同一 draft 已成功的 Threads 也不會
    阻止尚未成功的 Facebook / Instagram 之後重試。
    """
    _base = """
        SELECT d.*,
               n.published_at AS news_published_at,
               n.title        AS news_title,
               n.url          AS news_url,
               n.og_image_url,
               n.og_video_url,
               n.og_video_is_direct,
               n.topic_category
        FROM drafts d
        JOIN news_items n ON d.news_id = n.id
        WHERE d.queue_status = 'queued'
          AND d.status IN ('approved', 'auto_approved')
    """
    platform_sql, platform_params = _pending_platform_clause(platforms)
    recovery_sql, recovery_params = _recovery_experiment_clause(
        recovery_only, platforms
    )
    # EDITORIAL_MODE 時段路由：先試「該 slot 桶」裡最新的 queued draft；桶內沒料就
    # 落到下面原本的 freshness-first（Phase 8.18 契約，預設 prefer_categories=None 完全不變）。
    if prefer_categories:
        cats = [c for c in prefer_categories if c]
        if cats:
            ph = ",".join("?" * len(cats))
            row = conn.execute(
                _base + platform_sql + recovery_sql
                + f" AND n.topic_category IN ({ph}) ORDER BY n.published_at DESC LIMIT 1",
                tuple(platform_params + recovery_params + cats),
            ).fetchone()
            if row is not None:
                return row
    return conn.execute(
        _base + platform_sql + recovery_sql + " ORDER BY n.published_at DESC LIMIT 1",
        tuple(platform_params + recovery_params),
    ).fetchone()


def count_queued_pending_for_platforms(
    conn: sqlite3.Connection,
    platforms,
    *,
    recovery_only: bool = False,
) -> int:
    """Count queued drafts that still owe at least one requested platform."""
    platform_sql, platform_params = _pending_platform_clause(platforms)
    recovery_sql, recovery_params = _recovery_experiment_clause(
        recovery_only, platforms
    )
    row = conn.execute(
        """SELECT COUNT(DISTINCT d.id)
             FROM drafts d
            WHERE d.queue_status='queued'
              AND d.status IN ('approved','auto_approved')
        """
        + platform_sql
        + recovery_sql,
        tuple(platform_params + recovery_params),
    ).fetchone()
    return int(row[0]) if row else 0


def count_queued_in_categories(
    conn: sqlite3.Connection,
    categories,
    platforms=None,
    recovery_only: bool = False,
) -> int:
    """數 queued（可直接發）draft 中、topic_category 落在 categories 的筆數。
    給 EDITORIAL_MODE 的時段 buffer 用：某 slot 桶還沒料就該 compose，即使總 buffer 已被
    別桶（Mac 端非 slot-aware 的填充）塞滿。"""
    cats = [c for c in categories if c]
    if not cats:
        return 0
    ph = ",".join("?" * len(cats))
    platform_sql, platform_params = _pending_platform_clause(platforms)
    recovery_sql, recovery_params = _recovery_experiment_clause(
        recovery_only, platforms
    )
    row = conn.execute(
        f"""SELECT COUNT(*) FROM drafts d JOIN news_items n ON d.news_id = n.id
            WHERE d.queue_status = 'queued' AND d.status IN ('approved','auto_approved')
              {platform_sql} {recovery_sql}
              AND n.topic_category IN ({ph})""",
        tuple(platform_params + recovery_params + cats),
    ).fetchone()
    return int(row[0]) if row else 0


def pick_fallback_any_approved(
    conn: sqlite3.Connection,
    platforms=None,
    recovery_only: bool = False,
) -> Optional[sqlite3.Row]:
    """2h lower bound degradation：過了兩小時還沒發，queue 又空，
    放寬條件挑 status='approved' 的（含 queue_status='stale' 的）硬發一則，
    避免頻道沉默超過 2h。

    Phase 8.20 Step 3：freshness-first 為主，但若有多筆同樣等候中，優先挑
    `weighted_score` 高的（例如 AI 新品 1.70 * 0.85 = 1.445 > 非 AI 1.20 * 0.85 = 1.02）。
    `pick_freshest_queued` 維持純 published_at DESC，不動（Phase 8.18 契約）——
    只有在『queue 空、走 fallback』的情境才讓 topic weight 發聲。
    """
    platform_sql, platform_params = _pending_platform_clause(platforms)
    recovery_sql, recovery_params = _recovery_experiment_clause(
        recovery_only, platforms
    )
    return conn.execute(
        """
        SELECT d.*,
               n.published_at AS news_published_at,
               n.title        AS news_title,
               n.url          AS news_url,
               n.og_image_url,
               n.og_video_url,
               n.og_video_is_direct,
               n.topic_category,
               n.weighted_score
        FROM drafts d
        JOIN news_items n ON d.news_id = n.id
        WHERE d.status IN ('approved', 'auto_approved')
          AND (d.queue_status IS NULL OR d.queue_status IN ('queued', 'stale'))
        """ + platform_sql + recovery_sql + """
        ORDER BY COALESCE(n.weighted_score, 0) DESC,
                 n.published_at DESC
        LIMIT 1
        """,
        tuple(platform_params + recovery_params),
    ).fetchone()


def has_successful_publish(
    conn: sqlite3.Connection,
    draft_id: str,
    platform: str,
) -> bool:
    """Idempotency guard for the publisher.

    Returns True iff ``publish_log`` already contains a ``success=1`` row
    for this (draft_id, platform) pair. Callers MUST treat True as
    "publish has already happened — do NOT call the platform API again".

    Why this exists (2026-05-02 incident)
    -------------------------------------
    Mac shutdown during a publish cycle ended up posting the same draft
    3-4 times to the platforms after recovery: the publisher had no
    memory of "did we already do this", so each restart re-tried the
    queued draft and the API actually accepted the duplicate posts.

    With this guard wired into ``run_pipeline._publish_platform`` and
    ``run_publish_queue._publish_one``:

      * publish_log row with success=1 already exists → publisher skips
        the API call, increments any_success counter (since the post IS
        live), does NOT write a duplicate row.
      * publish_log only has success=0 (prior failure) → guard returns
        False; publisher retries normally. The new attempt's row stacks
        on top of the failed one, and a future success=1 row blocks
        further retries.

    The platform argument MUST use a canonical publish-log identity:
    ``facebook`` / ``instagram`` / ``threads`` for feed posts, or the
    corresponding ``*_reel`` identity for short video.  Short aliases such
    as ``fb`` and ``ig`` are never stored.

    Limitations (deliberate, documented):
      * Does NOT defend against two concurrent publishers reading the
        same "no success row" state simultaneously and racing to write
        their own success rows. That requires DB-level advisory lock or
        application-side mutex; out of scope for the 2026-05-02 fix
        which targets the much more common single-publisher restart
        case.
      * Idempotency is platform-scoped: the same draft can still be
        legitimately published to FB then IG then Threads in sequence;
        each (draft, platform) tuple is independently guarded.
    """
    row = conn.execute(
        """
        SELECT 1 FROM publish_log
         WHERE draft_id = ?
           AND platform = ?
           AND success  = 1
         LIMIT 1
        """,
        (draft_id, platform),
    ).fetchone()
    return row is not None


def pending_publish_platforms(
    conn: sqlite3.Connection,
    draft_id: str,
) -> set[str]:
    """Return intended platform variants that still lack success evidence."""
    rows = conn.execute(
        """
        SELECT pd.platform
          FROM platform_drafts pd
         WHERE pd.draft_id=?
           AND NOT EXISTS (
             SELECT 1 FROM publish_log p
              WHERE p.draft_id=pd.draft_id
                AND p.platform=pd.platform
                AND p.success=1
           )
        """,
        (draft_id,),
    ).fetchall()
    return {str(row[0]) for row in rows}


def mark_queue_published(conn: sqlite3.Connection, draft_id: str) -> None:
    """Publisher 成功發文後呼叫。同時更新 drafts.status='published' 和 news_items.status='published'。

    Atomic transaction: both tables updated together, or neither.
    Resolves Phase 9 Item 1 backfill: news_items.status promotion was missing from regular publish path.
    """
    try:
        conn.execute(
            """
            UPDATE drafts
               SET queue_status = 'published',
                   status       = 'published'
             WHERE id = ?
            """,
            (draft_id,),
        )
        conn.execute(
            """
            UPDATE news_items
               SET status = 'published'
             WHERE id = (SELECT news_id FROM drafts WHERE id = ?)
            """,
            (draft_id,),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e


def mark_queue_failed(
    conn: sqlite3.Connection,
    draft_id: str,
    reason: Optional[str] = None,
) -> None:
    """Publisher 發文失敗（三平台全軍覆沒、或品質守門員攔下）呼叫——
    標 failed 讓它不會無限輪迴被挑中。人工後續可改回 queued 重試。

    reason 只會被 print 出來給 log 用，不寫 DB（避免為 Phase 8.20 附帶這條
    守門員去改 schema；真要審計細節可去 content_quality_guard 那邊的 format_issues）。
    """
    conn.execute(
        "UPDATE drafts SET queue_status = 'failed' WHERE id = ?",
        (draft_id,),
    )
    conn.commit()
    if reason:
        print(f"[DB] mark_queue_failed draft_id={draft_id[:16]}… reason={reason}")


def mark_queue_stale_except(
    conn: sqlite3.Connection,
    keep_draft_id: str,
) -> int:
    """publisher 挑中一筆發之後呼叫：把 news_items.published_at 比它舊的 queued draft
    全部標 'stale'。freshness-first 的核心動作——舊的就別再等了。
    回傳 affected rows 數。"""
    cur = conn.execute(
        """
        UPDATE drafts
           SET queue_status = 'stale'
         WHERE queue_status = 'queued'
           AND id <> ?
           AND news_id IN (
               SELECT n2.id
                 FROM news_items n2
                 JOIN drafts d2 ON d2.news_id = n2.id
                WHERE d2.id <> ?
                  AND n2.published_at < (
                      SELECT n3.published_at
                        FROM news_items n3
                        JOIN drafts d3 ON d3.news_id = n3.id
                       WHERE d3.id = ?
                  )
           )
        """,
        (keep_draft_id, keep_draft_id, keep_draft_id),
    )
    conn.commit()
    return cur.rowcount or 0


def last_successful_publish_at(
    conn: sqlite3.Connection,
    platforms=None,
) -> Optional[str]:
    """查指定平台最後一筆 success 的 posted_at（未指定則查全域）。

    Cadence 必須跟 scheduler 的 platform scope 一致，避免剛發 FB 就錯誤
    壓掉已到期的 Threads cycle。
    """
    targets = sorted({str(value) for value in (platforms or []) if value})
    platform_sql = ""
    params: tuple[str, ...] = ()
    if targets:
        platform_sql = f" AND platform IN ({','.join('?' * len(targets))})"
        params = tuple(targets)
    row = conn.execute(
        "SELECT posted_at FROM publish_log WHERE success=1"
        + platform_sql
        + " ORDER BY posted_at DESC LIMIT 1",
        params,
    ).fetchone()
    return row["posted_at"] if row else None


def count_queue_status(conn: sqlite3.Connection) -> dict:
    """diagnose 用：盤點 queue 裡各狀態多少筆。"""
    rows = conn.execute(
        """
        SELECT COALESCE(queue_status, 'null') AS qs, COUNT(*) AS c
          FROM drafts
         GROUP BY COALESCE(queue_status, 'null')
        """
    ).fetchall()
    return {r["qs"]: r["c"] for r in rows}


# ---------- Milestone 3 · Reflector 相關 ----------

def list_successful_posts(conn: sqlite3.Connection, limit: int = 50) -> List[sqlite3.Row]:
    """取最近 N 筆成功發布、且有 platform_post_id 可抓互動的紀錄。"""
    return conn.execute(
        """
        SELECT p.draft_id, p.platform, p.platform_post_id, p.posted_at,
               d.title, d.full_text, d.final_text, d.status AS draft_status,
               d.confidence_score
        FROM publish_log p
        JOIN drafts d ON d.id = p.draft_id
        WHERE p.success = 1 AND p.platform_post_id IS NOT NULL
        ORDER BY p.posted_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def insert_engagement(
    conn: sqlite3.Connection,
    draft_id: str,
    platform: str,
    platform_post_id: str,
    fetched_at: str,
    likes: int = 0,
    comments: int = 0,
    shares: int = 0,
    saves: int = 0,
    reposts: int = 0,
    quotes: int = 0,
    replies: int = 0,
    views: int = 0,
    reach: int = 0,
    clicks: int = 0,
    raw_json: Optional[str] = None,
    post_age_bucket: Optional[int] = None,
) -> None:
    """Append a row to engagement_stats.

    post_age_bucket: NULL for legacy / one-off backfills (Phase pre-8.23);
        canonical 1 / 24 / 168 (hours) for log-scale time-series polls.
        CHECK trigger enforces NULL or canonical values; partial UNIQUE INDEX
        on (draft_id, platform, post_age_bucket) prevents double-poll of
        same bucket.
    """
    conn.execute(
        """
        INSERT INTO engagement_stats
          (draft_id, platform, platform_post_id, fetched_at,
           likes, comments, shares, saves, reposts, quotes, replies,
           views, reach, clicks, raw_json, post_age_bucket)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            platform,
            platform_post_id,
            fetched_at,
            int(likes or 0),
            int(comments or 0),
            int(shares or 0),
            int(saves or 0),
            int(reposts or 0),
            int(quotes or 0),
            int(replies or 0),
            int(views or 0),
            int(reach or 0),
            int(clicks or 0),
            raw_json,
            post_age_bucket if post_age_bucket is None else int(post_age_bucket),
        ),
    )
    conn.commit()


def latest_engagement_per_post(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """每個 (draft_id, platform) 只取最新一筆互動數，附上 draft 基本資料。"""
    return conn.execute(
        """
        SELECT e.*, d.title AS draft_title, d.full_text AS ai_version,
               d.final_text AS human_version, d.reviewer_action
        FROM engagement_stats e
        JOIN drafts d ON d.id = e.draft_id
        WHERE e.id IN (
            SELECT MAX(id) FROM engagement_stats
            GROUP BY draft_id, platform
        )
        ORDER BY e.fetched_at DESC
        """
    ).fetchall()


def get_draft_full_text(conn: sqlite3.Connection, draft_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT id, full_text, final_text, title, reviewer_action FROM drafts WHERE id = ?",
        (draft_id,),
    ).fetchone()


def list_drafts_with_final_text(conn: sqlite3.Connection, limit: int = 50) -> List[sqlite3.Row]:
    """取所有已被人工編輯 (final_text 與 full_text 不同) 的 draft。"""
    return conn.execute(
        """
        SELECT id, news_id, title, full_text, final_text,
               confidence_score, reviewer_action, generated_at
        FROM drafts
        WHERE final_text IS NOT NULL
          AND TRIM(final_text) <> ''
          AND TRIM(final_text) <> TRIM(full_text)
        ORDER BY generated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def update_draft_final_text(
    conn: sqlite3.Connection,
    draft_id: str,
    final_text: str,
    reviewer_action: str = "edited",
) -> None:
    conn.execute(
        "UPDATE drafts SET final_text = ?, reviewer_action = ? WHERE id = ?",
        (final_text, reviewer_action, draft_id),
    )
    conn.commit()


def log_reflection_event(
    conn: sqlite3.Connection,
    ran_at: str,
    signals_summary: dict,
    samples_used: int,
    soul_version_before: Optional[str],
    soul_version_after: Optional[str],
    patch_markdown: Optional[str],
    rules_added: Optional[list],
    rationale: Optional[str],
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    status: str = "completed",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO reflection_events
            (ran_at, signals_summary, samples_used,
             soul_version_before, soul_version_after,
             patch_markdown, rules_added_json, rationale,
             input_tokens, output_tokens, cost_usd, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ran_at,
            json.dumps(signals_summary, ensure_ascii=False),
            samples_used,
            soul_version_before,
            soul_version_after,
            patch_markdown,
            json.dumps(rules_added or [], ensure_ascii=False),
            rationale,
            input_tokens,
            output_tokens,
            cost_usd,
            status,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ---------- 當做 module 執行時直接 init ----------
if __name__ == "__main__":
    init_db()
