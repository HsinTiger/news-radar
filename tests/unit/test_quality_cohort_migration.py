import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_quality_cohort_migration_preserves_existing_snapshots() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        (ROOT / "cloudflare-worker/migrations/0005_content_quality.sql").read_text(
            encoding="utf-8"
        )
    )
    conn.execute(
        """INSERT INTO content_quality_snapshots(
             platform,captured_at,window_days,candidates,evaluated,evidence_coverage,
             pass_count,warn_count,rewrite_count,block_count,publish_ready_count,
             top_issue_codes_json,guard_version
           ) VALUES('instagram','2099-01-01T00:00:00Z',45,1,1,1,1,0,0,0,1,'[]','legacy')"""
    )
    conn.executescript(
        (ROOT / "cloudflare-worker/migrations/0008_quality_guard_cohort.sql").read_text(
            encoding="utf-8"
        )
    )

    assert conn.execute(
        "SELECT legacy_excluded_count FROM content_quality_snapshots"
    ).fetchone()[0] == 0
