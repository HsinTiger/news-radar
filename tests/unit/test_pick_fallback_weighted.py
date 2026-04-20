"""Phase 8.20 Step 3：驗證 pick_fallback_any_approved 會依 weighted_score 優先排序。

不動 pick_freshest_queued（那條仍然純 published_at DESC，Phase 8.18 契約）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _fresh_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parents[2] / "data" / "01_harvest" / "schema.sql"
    conn.executescript(schema.read_text(encoding="utf-8"))
    # Phase 8.18 migration manually
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drafts)").fetchall()}
    if "queue_status" not in cols:
        conn.execute("ALTER TABLE drafts ADD COLUMN queue_status TEXT")
    if "publish_at" not in cols:
        conn.execute("ALTER TABLE drafts ADD COLUMN publish_at TEXT")
    return conn


def _seed(conn, *, news_id, draft_id, published_at, weighted):
    conn.execute(
        """INSERT INTO news_items
             (id, feed_name, feed_tier, url, title, published_at, fetched_at,
              status, weighted_score, topic_category)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (news_id, "t", "primary", f"https://a/{news_id}",
         f"title-{news_id}", published_at,
         datetime.now(timezone.utc).isoformat(), "drafted",
         weighted, "ai_model"),
    )
    conn.execute(
        """INSERT INTO drafts
             (id, news_id, persona_version, generated_at,
              status, queue_status, confidence_score)
           VALUES (?,?,?,?,?,?,?)""",
        (draft_id, news_id, "1.1",
         datetime.now(timezone.utc).isoformat(),
         "auto_approved", "stale", 0.85),
    )
    conn.commit()


def test_pick_fallback_prefers_higher_weighted_score_over_freshness():
    from src import db as dbmod
    conn = _fresh_conn()
    # newer but low-weight
    _seed(conn, news_id="n_new", draft_id="d_new",
          published_at="2026-04-21T10:00:00+00:00", weighted=0.7)
    # older but high-weight (AI model 1.7 × score 0.85)
    _seed(conn, news_id="n_old", draft_id="d_old",
          published_at="2026-04-20T10:00:00+00:00", weighted=1.445)

    row = dbmod.pick_fallback_any_approved(conn)
    assert row is not None
    assert row["id"] == "d_old", (
        f"fallback should pick higher weighted_score first, got {row['id']}"
    )


def test_pick_fallback_tiebreak_on_freshness_when_weights_equal():
    from src import db as dbmod
    conn = _fresh_conn()
    _seed(conn, news_id="n_new", draft_id="d_new",
          published_at="2026-04-21T10:00:00+00:00", weighted=1.0)
    _seed(conn, news_id="n_old", draft_id="d_old",
          published_at="2026-04-20T10:00:00+00:00", weighted=1.0)

    row = dbmod.pick_fallback_any_approved(conn)
    assert row["id"] == "d_new", (
        "equal weighted_score should tie-break to fresher published_at"
    )


def test_pick_fallback_handles_null_weighted_score():
    """舊 draft 沒 weighted_score 時也該被撿到（COALESCE→0）。"""
    from src import db as dbmod
    conn = _fresh_conn()
    # Insert without weighted_score (NULL)
    conn.execute(
        """INSERT INTO news_items
             (id, feed_name, feed_tier, url, title, published_at, fetched_at, status)
           VALUES ('n1','t','primary','https://a/n1','pre-820',
                   '2026-04-18T00:00:00+00:00',
                   '2026-04-18T00:00:00+00:00','drafted')""",
    )
    conn.execute(
        """INSERT INTO drafts (id, news_id, persona_version, generated_at,
                               status, queue_status, confidence_score)
           VALUES ('d1','n1','1.1','2026-04-18T00:00:00+00:00',
                   'auto_approved','stale',0.85)"""
    )
    conn.commit()
    row = dbmod.pick_fallback_any_approved(conn)
    assert row is not None
    assert row["id"] == "d1"
