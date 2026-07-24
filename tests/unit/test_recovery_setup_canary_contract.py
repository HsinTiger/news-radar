import json
import sqlite3
from pathlib import Path

from scripts.recovery_setup_canary import (
    _all_configured_feeds_healthy,
    _copy_previews,
    _latest_quality,
)
from src.content_quality_guard import QUALITY_GUARD_VERSION


ROOT = Path(__file__).resolve().parents[2]


def test_setup_canary_script_has_hard_no_publish_contract() -> None:
    source = (ROOT / "scripts/recovery_setup_canary.py").read_text(encoding="utf-8")
    assert "publisher invoked during setup-only canary" in source
    assert '"--compose-only"' in source
    assert "publish_log_unchanged" in source
    assert "canonical_db_unchanged" in source
    assert '"hold_reason": hold_reason' in source
    assert 'os.environ["AUTOMATION_MODE"] = "recovery"' in source
    assert '"automation_mode": os.environ["AUTOMATION_MODE"]' in source
    assert "tempfile.TemporaryDirectory" in source
    assert 'feed_tag="primary-record"' in source
    assert "write_log=False" in source
    assert '"harvested_this_run"' in source
    assert '"primary_source_gate_ready"' in source
    assert '"copy_previews"' in source


def test_setup_canary_workflow_is_read_only_and_has_no_meta_secrets() -> None:
    workflow = (
        ROOT / ".github/workflows/recovery-setup-canary.yml"
    ).read_text(encoding="utf-8")
    assert "contents: read" in workflow
    assert "models: read" in workflow
    assert "GITHUB_TOKEN" in workflow
    assert "state_store.py push" not in workflow
    assert "FB_PAGE_ACCESS_TOKEN" not in workflow
    assert "IG_ACCESS_TOKEN" not in workflow
    assert "THREADS_ACCESS_TOKEN" not in workflow
    assert "scripts/recovery_setup_canary.py" in workflow
    assert "--refresh-primary-sources" in workflow
    assert "--include-copy" in workflow


def test_latest_quality_exposes_fresh_primary_source_lineage() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE content_quality_evaluations(
          id INTEGER PRIMARY KEY,draft_id TEXT,news_id TEXT,platform TEXT,
          decision TEXT,attempt INTEGER,guard_version TEXT,issue_codes_json TEXT,
          checked_at TEXT
        );
        CREATE TABLE drafts(id TEXT PRIMARY KEY,news_id TEXT);
        CREATE TABLE news_items(
          id TEXT PRIMARY KEY,feed_name TEXT,url TEXT,fetched_at TEXT,tags TEXT
        );
        """
    )
    conn.execute("INSERT INTO drafts VALUES('draft-1','news-1')")
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?)",
        (
            "news-1",
            "行政院 本院新聞",
            "https://example.gov.tw/news-1",
            "2026-07-25T00:00:00+00:00",
            json.dumps(["official", "primary-record"]),
        ),
    )
    conn.execute(
        "INSERT INTO content_quality_evaluations VALUES(?,?,?,?,?,?,?,?,?)",
        (
            1,
            "draft-1",
            "news-1",
            "threads",
            "pass",
            1,
            QUALITY_GUARD_VERSION,
            "[]",
            "2026-07-25T00:01:00+00:00",
        ),
    )

    quality = _latest_quality(
        conn,
        platform="threads",
        since_iso="2026-07-25T00:00:00+00:00",
        freshly_harvested_ids={"news-1"},
    )

    assert quality == [
        {
            "draft_id": "draft-1",
            "news_id": "news-1",
            "platform": "threads",
            "decision": "pass",
            "attempt": 1,
            "guard_version": QUALITY_GUARD_VERSION,
            "issue_codes": [],
            "source_feed": "行政院 本院新聞",
            "source_url": "https://example.gov.tw/news-1",
            "source_fetched_at": "2026-07-25T00:00:00+00:00",
            "source_tags": ["official", "primary-record"],
            "source_is_primary_record": True,
            "harvested_this_run": True,
        }
    ]


def test_primary_refresh_requires_a_result_for_every_configured_feed() -> None:
    report = {
        "feeds_checked": 2,
        "feed_results": {
            "source-a": {"status": "ok"},
            "source-b": {"status": "failed"},
        },
    }
    assert _all_configured_feeds_healthy(report) is False

    report["feed_results"]["source-b"] = {"status": "ok"}
    assert _all_configured_feeds_healthy(report) is True


def test_copy_preview_exports_only_fresh_public_primary_copy() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE drafts(id TEXT PRIMARY KEY,carousel_json TEXT);
        CREATE TABLE platform_drafts(
          draft_id TEXT,platform TEXT,title TEXT,full_text TEXT
        );
        INSERT INTO drafts VALUES(
          'draft-primary','{"takeaways":["查核公告"]}'
        );
        INSERT INTO drafts VALUES('draft-private',NULL);
        INSERT INTO platform_drafts VALUES(
          'draft-primary','threads','公共政策','自然文案'
        );
        INSERT INTO platform_drafts VALUES(
          'draft-private','threads','Owner seed','不得輸出'
        );
        """
    )
    quality = [
        {
            "draft_id": "draft-primary",
            "platform": "threads",
            "source_feed": "行政院 本院新聞",
            "source_url": "https://example.gov.tw/record",
            "source_is_primary_record": True,
            "harvested_this_run": True,
        },
        {
            "draft_id": "draft-private",
            "platform": "threads",
            "source_feed": "user_submission",
            "source_url": "manual-text://private",
            "source_is_primary_record": False,
            "harvested_this_run": True,
        },
    ]

    assert _copy_previews(conn, quality) == [
        {
            "draft_id": "draft-primary",
            "platform": "threads",
            "source_feed": "行政院 本院新聞",
            "source_url": "https://example.gov.tw/record",
            "title": "公共政策",
            "full_text": "自然文案",
            "carousel": {"takeaways": ["查核公告"]},
        }
    ]
