"""
News Radar · SQLite 初始化與 CRUD
沿用 alpha_pipeline 的「print [Module N]」log 風格
"""
from __future__ import annotations
import json
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


def get_conn() -> sqlite3.Connection:
    """取得連線，row_factory 設為 dict-like。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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
    with get_conn() as conn:
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
        conn.commit()
    print("[DB]  ↳ schema 套用完成")


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
        INSERT OR REPLACE INTO drafts (
            id, news_id, persona_version, title, hook, framework,
            validation, macro_insight, ending_question, hashtags,
            image_url, full_text, confidence_score, score_breakdown,
            llm_provider, llm_model, input_tokens, output_tokens,
            cached_tokens, cost_usd, generated_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def log_publish(conn: sqlite3.Connection, result: PublishResult) -> None:
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


def pick_freshest_queued(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """Cloud publisher 的主選稿邏輯：挑 news_items.published_at 最新的那筆 queued draft。

    回傳單一 Row（包含 drafts + news_items 所有欄位）或 None。
    只回「可直接發」的——要求 queue_status='queued' 且人類審核已過（approved / auto_approved）。
    """
    return conn.execute(
        """
        SELECT d.*,
               n.published_at AS news_published_at,
               n.title        AS news_title,
               n.url          AS news_url,
               n.og_image_url,
               n.og_video_url,
               n.og_video_is_direct
        FROM drafts d
        JOIN news_items n ON d.news_id = n.id
        WHERE d.queue_status = 'queued'
          AND d.status IN ('approved', 'auto_approved')
        ORDER BY n.published_at DESC
        LIMIT 1
        """
    ).fetchone()


def pick_fallback_any_approved(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """2h lower bound degradation：過了兩小時還沒發，queue 又空，
    放寬條件挑 status='approved' 的（含 queue_status='stale' 的）硬發一則，
    避免頻道沉默超過 2h。

    Phase 8.20 Step 3：freshness-first 為主，但若有多筆同樣等候中，優先挑
    `weighted_score` 高的（例如 AI 新品 1.70 * 0.85 = 1.445 > 非 AI 1.20 * 0.85 = 1.02）。
    `pick_freshest_queued` 維持純 published_at DESC，不動（Phase 8.18 契約）——
    只有在『queue 空、走 fallback』的情境才讓 topic weight 發聲。
    """
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
        ORDER BY COALESCE(n.weighted_score, 0) DESC,
                 n.published_at DESC
        LIMIT 1
        """
    ).fetchone()


def mark_queue_published(conn: sqlite3.Connection, draft_id: str) -> None:
    """Publisher 成功發文後呼叫。同時更新 drafts.status='published'。"""
    conn.execute(
        """
        UPDATE drafts
           SET queue_status = 'published',
               status       = 'published'
         WHERE id = ?
        """,
        (draft_id,),
    )
    conn.commit()


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


def last_successful_publish_at(conn: sqlite3.Connection) -> Optional[str]:
    """查 publish_log 最後一筆 success 的 posted_at（ISO8601）。
    Cadence 計算用：若距今 < 1h 跳過；若距今 > 2h 就算 queue 空也要發。"""
    row = conn.execute(
        """
        SELECT posted_at
          FROM publish_log
         WHERE success = 1
         ORDER BY posted_at DESC
         LIMIT 1
        """
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
    raw_json: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO engagement_stats
          (draft_id, platform, platform_post_id, fetched_at,
           likes, comments, shares, saves, reposts, quotes, replies,
           views, reach, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            raw_json,
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
