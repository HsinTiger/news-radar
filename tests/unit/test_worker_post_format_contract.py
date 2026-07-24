import re
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_worker_upsert_refreshes_proven_post_format() -> None:
    source = (ROOT / "cloudflare-worker" / "worker.js").read_text(encoding="utf-8")
    assert "format=excluded.format,platform_post_id=excluded.platform_post_id" in source
    assert 'const API_VERSION = "2026-07-25.recovery-v17";' in source


def test_worker_computes_seven_day_follower_delta_from_d1_history() -> None:
    source = (ROOT / "cloudflare-worker" / "worker.js").read_text(encoding="utf-8")
    match = re.search(r"const LATEST_AUDIENCE_SQL = `(.+?)`;", source, re.DOTALL)
    assert match is not None

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE audience_snapshots(
        platform TEXT,captured_at TEXT,followers INTEGER,
        followers_delta_7d INTEGER,metric_status TEXT)"""
    )
    conn.executemany(
        "INSERT INTO audience_snapshots VALUES(?,?,?,?,?)",
        [
            ("facebook", "2026-07-01T03:00:00+00:00", 28, None, "ok"),
            ("facebook", "2026-07-09T03:00:00+00:00", 31, None, "ok"),
            ("instagram", "2026-07-09T03:00:00+00:00", 9, None, "ok"),
            ("threads", "2026-07-01T03:00:00+00:00", 3700, None, "ok"),
            ("threads", "2026-07-09T03:00:00+00:00", 3749, 40, "ok"),
        ],
    )

    rows = {row["platform"]: row for row in conn.execute(match.group(1))}
    assert rows["facebook"]["followers_delta_7d"] == 3
    assert rows["instagram"]["followers_delta_7d"] is None
    assert rows["threads"]["followers_delta_7d"] == 40


def test_worker_exposes_robust_engagement_metrics() -> None:
    source = (ROOT / "cloudflare-worker" / "worker.js").read_text(encoding="utf-8")
    match = re.search(r"const LATEST_ENGAGEMENT_SQL = `(.+?)`;", source, re.DOTALL)
    assert match is not None

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE engagement_snapshots(
        platform TEXT,platform_post_id TEXT,captured_at TEXT,
        views INTEGER,reach INTEGER,clicks INTEGER,likes INTEGER,comments INTEGER,
        shares INTEGER,saves INTEGER,replies INTEGER,reposts INTEGER,quotes INTEGER,
        metric_status TEXT)"""
    )
    for idx, (views, actions) in enumerate([(1, 0), (10, 2), (100, 5)], 1):
        conn.execute(
            "INSERT INTO engagement_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "threads", f"p{idx}", f"2026-07-0{idx}T03:00:00+00:00",
                views, 0, 0, actions, 0, 0, 0, 0, 0, 0, "ok",
            ),
        )

    row = conn.execute(match.group(1)).fetchone()
    assert row["median_views"] == 10
    assert row["median_actions"] == 2
    assert row["zero_action_rate"] == 33.3
    assert row["avg_views"] == 37.0
