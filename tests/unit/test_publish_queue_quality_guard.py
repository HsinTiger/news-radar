"""Integration-ish test: 驗證 run_publish_queue._publish_one 會被 QualityGuard 攔下。

這邊不碰真實 Meta API（mock publisher）、也不碰 launch_notification_center
（靠 local_notify 在 Linux sandbox 預設就 no-op），只驗證：
    給一筆『platform_drafts 內容是 emergency_template』的 draft，
    _publish_one 會在 call publisher 前就 return False，並標 failed。
"""
from __future__ import annotations

import asyncio
import sqlite3
import types
from datetime import datetime, timezone
from pathlib import Path

from src import content_quality_guard as guard_mod


# ---------- 共用 in-memory DB + schema ----------
def _fresh_memory_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema_path = Path(__file__).resolve().parents[2] / "data" / "01_harvest" / "schema.sql"
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    # 手動補 Phase 8.18 的 queue_status 欄位（schema.sql 是舊版）
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drafts)").fetchall()}
    if "queue_status" not in cols:
        conn.execute("ALTER TABLE drafts ADD COLUMN queue_status TEXT")
    if "publish_at" not in cols:
        conn.execute("ALTER TABLE drafts ADD COLUMN publish_at TEXT")
    return conn


def _seed_one_templated_draft(conn):
    """寫一筆『news_items + drafts + platform_drafts』，
    platform_drafts 三列都是 emergency_template 文字。"""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO news_items
             (id, feed_name, feed_tier, url, title, published_at, fetched_at)
           VALUES ('n1','test','primary','https://a.example/b',
                   'Zero-Copy GPU Inference from WebAssembly on Apple Silicon',?,?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO drafts
             (id, news_id, persona_version, generated_at, status,
              confidence_score, queue_status, publish_at)
           VALUES ('d1','n1','1.1',?, 'auto_approved', 1.0, 'queued', ?)""",
        (now, now),
    )
    tmpl = (
        "🚀 Zero-Copy GPU Inference from WebAssembly on Apple Silicon\n\n"
        "【系統代班速報】\n\n"
        "科技格局正在發生結構性位移，護城河的定義已從產品轉向生態數據。\n\n"
        "#科技戰略 #商業洞察 #數據驅動"
    )
    for pl in ("facebook", "instagram", "threads"):
        conn.execute(
            """INSERT INTO platform_drafts
                 (draft_id, platform, title, body, full_text, char_count, created_at)
               VALUES ('d1', ?, ?, ?, ?, ?, ?)""",
            ("d1", pl, "🚀 Zero-Copy GPU Inference", tmpl, tmpl, len(tmpl), now),
        )
    conn.commit()


def test_publish_queue_blocks_templated_draft(monkeypatch):
    """_publish_one 對 emergency_template 內容必須 return False 且 mark failed。"""
    import run_publish_queue as rpq

    conn = _fresh_memory_conn()
    _seed_one_templated_draft(conn)

    # Mock publisher to explode if called (proves guard short-circuits before network)
    async def _never_called(*a, **kw):
        raise AssertionError("publisher must NOT be called when guard blocks")
    monkeypatch.setattr(rpq, "publish_to_fb", _never_called)
    monkeypatch.setattr(rpq, "publish_to_ig", _never_called)
    monkeypatch.setattr(rpq, "publish_to_threads", _never_called)

    # Build a fake 'row' like pick_freshest_queued returns (SELECT joined columns)
    row = conn.execute(
        """SELECT d.id, d.news_id, n.title AS news_title,
                  n.published_at AS news_published_at, n.og_image_url
             FROM drafts d JOIN news_items n ON d.news_id = n.id
            WHERE d.id='d1'"""
    ).fetchone()

    ok = asyncio.run(rpq._publish_one(conn, row, dry_run=False))
    assert ok is False

    # After block, queue_status='failed'
    qs = conn.execute("SELECT queue_status FROM drafts WHERE id='d1'").fetchone()[0]
    assert qs == "failed", f"expected queue_status=failed, got {qs}"


def test_publish_queue_lets_healthy_draft_through(monkeypatch):
    """對照組：healthy 內容不該被 guard 攔下（publisher 會被呼叫）。"""
    import run_publish_queue as rpq

    conn = _fresh_memory_conn()
    now = datetime.now(timezone.utc).isoformat()
    healthy = (
        "Anthropic 今日發佈 Claude Opus 4.7，SWE-bench Verified 拿下 78.2%，"
        "較前一代提升 6.1 個百分點。Agent 場景上單次任務平均呼叫工具 12.4 次、"
        "錯誤重試率較 4.6 下降 31%。\n\n#Claude"
    )
    conn.execute(
        """INSERT INTO news_items
             (id, feed_name, feed_tier, url, title, published_at, fetched_at)
           VALUES ('n2','test','primary','https://a.example/c','Anthropic 發表 Claude Opus 4.7',?,?)""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO drafts
             (id, news_id, persona_version, generated_at, status,
              confidence_score, queue_status, publish_at)
           VALUES ('d2','n2','1.1',?, 'auto_approved', 1.0, 'queued', ?)""",
        (now, now),
    )
    for pl in ("facebook", "instagram", "threads"):
        conn.execute(
            """INSERT INTO platform_drafts
                 (draft_id, platform, full_text, created_at)
               VALUES ('d2', ?, ?, ?)""",
            (pl, healthy, now),
        )
    conn.commit()

    # Track publisher calls
    call_log = []
    async def _fake_ok(*a, **kw):
        call_log.append(1)
        return {"success": True, "id": f"post_{len(call_log)}"}
    monkeypatch.setattr(rpq, "publish_to_fb", _fake_ok)
    monkeypatch.setattr(rpq, "publish_to_ig", _fake_ok)
    monkeypatch.setattr(rpq, "publish_to_threads", _fake_ok)

    row = conn.execute(
        """SELECT d.id, d.news_id, n.title AS news_title,
                  n.published_at AS news_published_at, n.og_image_url
             FROM drafts d JOIN news_items n ON d.news_id = n.id
            WHERE d.id='d2'"""
    ).fetchone()

    # seed an og_image_url so IG/Threads don't early-reject
    conn.execute("UPDATE news_items SET og_image_url='https://example.com/x.jpg' WHERE id='n2'")
    conn.commit()
    row = conn.execute(
        """SELECT d.id, d.news_id, n.title AS news_title,
                  n.published_at AS news_published_at, n.og_image_url
             FROM drafts d JOIN news_items n ON d.news_id = n.id
            WHERE d.id='d2'"""
    ).fetchone()

    ok = asyncio.run(rpq._publish_one(conn, row, dry_run=False))
    assert ok is True
    assert len(call_log) == 3, f"expected 3 publisher calls, got {len(call_log)}"
