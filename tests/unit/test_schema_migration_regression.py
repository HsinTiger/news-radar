"""Regression test：schema.sql 對舊 DB 的向下相容性。

Phase 8.20 Step 4 之前曾發生過一次 bug：
  schema.sql 裡把 `CREATE INDEX idx_news_topic ON news_items(topic_category)`
  直接寫在建表區塊（會被 conn.executescript 一次跑完），但 `topic_category`
  是 Phase 8.20 才加的欄位，對舊 DB 而言 ALTER TABLE migration 還沒補上，
  導致 init_db 對舊 DB 會炸出 `no such column: topic_category`。

這個測試模擬：
  1. 建一個 Phase 8.20 *之前* 樣貌的 DB（news_items 無 topic_category 欄位）
  2. 讀當前 repo 的 schema.sql，用 executescript 套用
  3. 確認沒炸 + news_items 表的 CREATE TABLE IF NOT EXISTS 不會補欄位
     （這是 SQLite 的正確行為），ALTER TABLE 會由 db.py::init_db 接手

如果未來有人把索引 *或* 任何需要新欄位的語句加回 schema.sql 的
CREATE 區塊，這個測試會立刻失敗。

設計上不 import src.db（避免 pydantic 相依），只用 sqlite3 stdlib。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = _ROOT / "data" / "01_harvest" / "schema.sql"


def _make_pre_phase_820_db() -> sqlite3.Connection:
    """建一個 Phase 8.20 之前樣貌的 minimal DB。"""
    conn = sqlite3.connect(":memory:")
    # 這是 Phase 8.20 之前的 news_items 定義——故意不含 topic_category、
    # weighted_score 等新欄位
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS news_items (
            id TEXT PRIMARY KEY,
            feed_name TEXT NOT NULL,
            feed_tier TEXT NOT NULL,
            source_type TEXT DEFAULT 'article',
            url TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            published_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            language TEXT,
            raw_html TEXT,
            clean_markdown TEXT,
            word_count INTEGER,
            og_image_url TEXT,
            og_video_url TEXT,
            og_video_is_direct INTEGER DEFAULT 0,
            tags TEXT,
            status TEXT DEFAULT 'fetched',
            drop_reason TEXT
            -- 故意不含 topic_category / topic_confidence / weighted_score
        );
        """
    )
    return conn


def test_schema_sql_is_idempotent_on_old_db():
    """套 schema.sql 到『缺 Phase 8.20 欄位的 DB』不能炸。
    等價於：init_db 對舊 DB 呼叫 executescript(schema) 這一步要安全。
    """
    conn = _make_pre_phase_820_db()
    try:
        schema = _SCHEMA.read_text(encoding="utf-8")
        # 這就是 src.db.init_db 第 56 行的行為
        conn.executescript(schema)
        # 若 schema.sql 又偷放了 CREATE INDEX 引用新欄位，這裡會炸
        # OperationalError: no such column: topic_category
        conn.commit()
    finally:
        conn.close()


def test_schema_sql_has_no_phase820_index_in_create_block():
    """防呆：文字層面掃 schema.sql，確保沒有引用新欄位的 CREATE INDEX。

    之所以加這條：executescript 不會丟出『正在測的欄位不存在』這種
    error message 很難在 CI log 裡 debug；這條測試直接在字串層把
    regression pattern 擋下。
    """
    schema_text = _SCHEMA.read_text(encoding="utf-8")
    # 抓每一行 CREATE INDEX ...
    offending: list = []
    for line in schema_text.splitlines():
        stripped = line.strip().lower()
        if not stripped.startswith("create index"):
            continue
        # Phase 8.20 的三個欄位
        for col in ("topic_category", "weighted_score"):
            if col in stripped:
                offending.append(line.strip())
    assert not offending, (
        f"schema.sql 的 CREATE INDEX 不可引用 Phase 8.20 才加的欄位 "
        f"(old DB 還沒 ALTER TABLE 會炸)。這些要搬到 db.py::init_db 的"
        f" migration 之後：\n  " + "\n  ".join(offending)
    )


def test_schema_sql_has_no_phase818_index_in_create_block():
    """同上：queue_status 是 Phase 8.18 才加的，index 也不能寫在 CREATE 區塊。"""
    schema_text = _SCHEMA.read_text(encoding="utf-8")
    offending: list = []
    for line in schema_text.splitlines():
        stripped = line.strip().lower()
        if not stripped.startswith("create index"):
            continue
        if "queue_status" in stripped:
            offending.append(line.strip())
    assert not offending, (
        "schema.sql 不可含 queue_status 的 CREATE INDEX（必須留給 db.py 在 "
        "migration 之後執行）：\n  " + "\n  ".join(offending)
    )


def test_fresh_db_schema_has_all_phase820_tables():
    """正向測試：fresh DB 套 schema.sql 後該有的表都在。"""
    conn = sqlite3.connect(":memory:")
    try:
        schema = _SCHEMA.read_text(encoding="utf-8")
        conn.executescript(schema)
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        # Phase 8.20 引入的兩張表必須存在
        assert "topic_weights" in tables
        assert "topic_weight_history" in tables
        # 核心表
        for t in ("news_items", "drafts", "publish_log", "engagement_stats",
                  "platform_drafts", "reflection_events"):
            assert t in tables, f"missing table: {t}"
        engagement_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(engagement_stats)")
        }
        assert "clicks" in engagement_columns
    finally:
        conn.close()
