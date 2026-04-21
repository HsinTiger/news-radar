"""Unit tests for scripts/flush_legacy_drafts.

覆蓋：
  1. `evaluate` 把會被 guard 擋的 draft 放進 would_flush
  2. 乾淨的 draft 留在 would_keep（不會被誤殺）
  3. orphan draft（沒 platform_drafts）也會 flush（reason=no_platform_drafts）
  4. 已經 failed 的 draft 不在 candidate 內
  5. --apply 之後 queue_status='failed' 真的寫入
  6. 重跑 dry-run 不會再找到東西（idempotent）

不需 pydantic / httpx——只用 sqlite3 + src.content_quality_guard（純函式）。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.flush_legacy_drafts import evaluate, _mark_failed  # type: ignore


LEGACY_TEMPLATE_TEXT = (
    "🚀 測試貼文\n\n"
    "【系統代班速報】\n\n"
    "科技格局正在發生結構性位移。\n\n"
    "#科技戰略 #商業洞察 #數據驅動"
)

CLEAN_TEXT = (
    "Anthropic 今日發佈 Claude Opus 4.7，SWE-bench Verified 拿下 78.2%，"
    "較前一代提升 6.1 個百分點。Agent 場景上單次任務平均呼叫工具 12.4 次。\n\n#Claude"
)


def _build_min_db() -> sqlite3.Connection:
    """In-memory DB，只建 flush_legacy_drafts 真的 query 的欄位。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE news_items (
          id TEXT PRIMARY KEY,
          title TEXT,
          published_at TEXT
        );
        CREATE TABLE drafts (
          id TEXT PRIMARY KEY,
          news_id TEXT,
          queue_status TEXT,
          generated_at TEXT
        );
        CREATE TABLE platform_drafts (
          draft_id TEXT,
          platform TEXT,
          final_text TEXT,
          full_text TEXT,
          PRIMARY KEY (draft_id, platform)
        );
        """
    )
    return conn


def _seed(conn, draft_id, queue_status, text_per_platform, *, title="t", news_id=None):
    """Helper: 插 news + draft + platform_drafts 三平台。"""
    news_id = news_id or f"n_{draft_id}"
    conn.execute(
        "INSERT OR IGNORE INTO news_items VALUES (?, ?, ?)",
        (news_id, title, "2026-04-20T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO drafts VALUES (?, ?, ?, ?)",
        (draft_id, news_id, queue_status, "2026-04-20T12:00:00Z"),
    )
    if text_per_platform is not None:
        for p in ("facebook", "instagram", "threads"):
            conn.execute(
                "INSERT INTO platform_drafts VALUES (?, ?, ?, ?)",
                (draft_id, p, None, text_per_platform),
            )
    conn.commit()


def test_evaluate_flags_legacy_template_drafts():
    conn = _build_min_db()
    _seed(conn, "d1", None, LEGACY_TEMPLATE_TEXT, title="Legacy 1")
    would_flush, would_keep = evaluate(conn, ["null", "queued", "stale"])
    assert len(would_flush) == 1
    assert would_flush[0]["draft_id"] == "d1"
    assert "templated_fallback_marker" in would_flush[0]["reason"]
    assert len(would_keep) == 0


def test_evaluate_keeps_clean_drafts():
    conn = _build_min_db()
    _seed(conn, "d_clean", None, CLEAN_TEXT, title="Anthropic 4.7")
    would_flush, would_keep = evaluate(conn, ["null"])
    assert len(would_flush) == 0
    assert len(would_keep) == 1
    assert would_keep[0]["draft_id"] == "d_clean"


def test_evaluate_flags_orphan_drafts():
    conn = _build_min_db()
    _seed(conn, "d_orphan", None, None, title="Orphan")  # 沒 platform_drafts
    would_flush, would_keep = evaluate(conn, ["null"])
    assert len(would_flush) == 1
    assert would_flush[0]["draft_id"] == "d_orphan"
    assert would_flush[0]["reason"] == "no_platform_drafts"


def test_evaluate_excludes_already_failed():
    conn = _build_min_db()
    _seed(conn, "d_failed", "failed", LEGACY_TEMPLATE_TEXT, title="Already failed")
    would_flush, would_keep = evaluate(conn, ["null", "queued", "stale"])
    assert len(would_flush) == 0
    assert len(would_keep) == 0


def test_apply_mutates_queue_status():
    conn = _build_min_db()
    _seed(conn, "d1", None, LEGACY_TEMPLATE_TEXT, title="L1")
    _seed(conn, "d2", "queued", LEGACY_TEMPLATE_TEXT, title="L2")
    _seed(conn, "d3", None, CLEAN_TEXT, title="Clean")

    would_flush, would_keep = evaluate(conn, ["null", "queued", "stale"])
    assert len(would_flush) == 2
    assert len(would_keep) == 1

    # Apply
    for d in would_flush:
        _mark_failed(conn, d["draft_id"], d["reason"])
    conn.commit()

    # Re-evaluate: legacy ones now excluded; clean one remains
    would_flush2, would_keep2 = evaluate(conn, ["null", "queued", "stale"])
    assert len(would_flush2) == 0
    assert len(would_keep2) == 1
    assert would_keep2[0]["draft_id"] == "d3"

    # d1, d2 are now 'failed' (not in candidate set)
    statuses = dict(conn.execute("SELECT id, queue_status FROM drafts").fetchall())
    assert statuses["d1"] == "failed"
    assert statuses["d2"] == "failed"
    assert statuses["d3"] is None  # clean one untouched


def test_evaluate_respects_include_filter():
    """include=['queued'] 不應該碰 NULL 狀態的 draft。"""
    conn = _build_min_db()
    _seed(conn, "d_null", None, LEGACY_TEMPLATE_TEXT, title="Null legacy")
    _seed(conn, "d_queued", "queued", LEGACY_TEMPLATE_TEXT, title="Queued legacy")

    # Only include 'queued' → NULL one should be ignored
    would_flush, would_keep = evaluate(conn, ["queued"])
    assert len(would_flush) == 1
    assert would_flush[0]["draft_id"] == "d_queued"

    # Only include 'null' → queued one should be ignored
    would_flush2, would_keep2 = evaluate(conn, ["null"])
    assert len(would_flush2) == 1
    assert would_flush2[0]["draft_id"] == "d_null"


def test_evaluate_empty_include_returns_empty():
    conn = _build_min_db()
    _seed(conn, "d1", None, LEGACY_TEMPLATE_TEXT)
    would_flush, would_keep = evaluate(conn, [])
    assert would_flush == []
    assert would_keep == []
