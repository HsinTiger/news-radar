import re
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_worker_upsert_refreshes_proven_post_format() -> None:
    source = (ROOT / "cloudflare-worker" / "worker.js").read_text(encoding="utf-8")
    assert "format=excluded.format,platform_post_id=excluded.platform_post_id" in source
    assert 'const API_VERSION = "2026-07-24.recovery-v8";' in source


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
