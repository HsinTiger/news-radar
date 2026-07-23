import json
import sqlite3
from pathlib import Path

from scripts.sync_social_ops import (
    build_engagement,
    build_health,
    build_knowledge,
    build_posts,
    build_proposals,
    build_submission_updates,
)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE news_items(
          id TEXT PRIMARY KEY,source_type TEXT,url TEXT,title TEXT,topic_category TEXT,
          status TEXT,fetched_at TEXT,word_count INTEGER,weighted_score REAL,
          feed_name TEXT,tags TEXT,substack_written_at TEXT
        );
        CREATE TABLE drafts(id TEXT PRIMARY KEY,news_id TEXT,title TEXT,generated_at TEXT);
        CREATE TABLE publish_log(
          id INTEGER PRIMARY KEY,draft_id TEXT,platform TEXT,platform_post_id TEXT,
          posted_at TEXT,success INTEGER,error_message TEXT
        );
        CREATE TABLE engagement_stats(
          platform TEXT,platform_post_id TEXT,fetched_at TEXT,post_age_bucket INTEGER,
          views INTEGER,reach INTEGER,likes INTEGER,comments INTEGER,shares INTEGER,
          saves INTEGER,replies INTEGER,reposts INTEGER,quotes INTEGER,raw_json TEXT,
          clicks INTEGER
        );
        CREATE TABLE reflector_proposal_lineage(
          fire_id TEXT,fire_at TEXT,proposal_type TEXT,target_config TEXT,
          hsin_decision TEXT,hsin_decision_at TEXT,deployed_at TEXT,evidence_json TEXT
        );
        INSERT INTO news_items VALUES(
          'n1','rss','https://example.com','Useful source','ai_application','scored',
          '2099-01-01T00:00:00Z',800,0.9,'rss','[]',NULL
        );
        INSERT INTO drafts VALUES('d1','n1','Useful draft','2099-01-02T00:00:00Z');
        INSERT INTO publish_log VALUES(
          1,'d1','threads','t-post','2099-01-03T00:00:00Z',1,NULL
        );
        INSERT INTO engagement_stats VALUES(
          'threads','t-post','2099-01-04T00:00:00Z',24,100,0,5,0,0,0,2,1,0,'{}',0
        );
        """
    )
    return conn


def test_sync_builders_export_metadata_without_article_body() -> None:
    conn = _db()
    posts = build_posts(conn, full=True)
    engagement = build_engagement(conn, full=True)
    knowledge = build_knowledge(conn, full=True, limit=0)
    assert posts[0]["status"] == "published"
    assert posts[0]["topic"] == "ai_application"
    assert engagement[0]["metric_status"] == "ok"
    assert engagement[0]["replies"] == 2
    assert engagement[0]["clicks"] == 0
    assert knowledge[0]["use_count"] == 1
    assert "clean_markdown" not in knowledge[0]


def test_health_is_unknown_for_missing_platform_and_degraded_for_error() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO engagement_stats VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "facebook", "f-post", "2099-01-04T00:00:00Z", 24,
            0, 0, 0, 0, 0, 0, 0, 0, 0,
            '{"insights":{"error":{"code":100}}}', 0,
        ),
    )
    rows = build_health(conn)
    status = {(row["platform"], row["metric"]): row["status"] for row in rows}
    assert status[("facebook", "engagement_api")] == "degraded"
    assert status[("instagram", "engagement_api")] == "unknown"
    assert status[("threads", "engagement_api")] == "healthy"
    assert status[("instagram", "signal_coverage")] == "unknown"
    assert status[("threads", "signal_coverage")] == "healthy"
    assert not any(row["metric"] == "audience" for row in rows)


def test_substack_written_marker_becomes_truthful_terminal_update() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "sub1", "text", "manual://1", "Owner source", "ai_application",
            "fetched", "2099-01-01T00:00:00Z", 100, 1.0, "user_substack",
            '["substack_source","control_submission:12345678-abcd"]',
            "2099-01-02T00:00:00Z",
        ),
    )
    assert build_submission_updates(conn) == [
        {
            "submission_id": "12345678-abcd",
            "status": "draft_created",
            "observed_at": "2099-01-02T00:00:00Z",
        }
    ]


def test_proposal_sync_maps_legacy_decision_and_exports_exact_action(
    tmp_path: Path,
) -> None:
    conn = _db()
    fire_id = "proposal-owner-gate-01"
    evidence = {"sample_ids": [], "metrics": {"total_samples": 12}, "confidence": "HIGH"}
    conn.execute(
        "INSERT INTO reflector_proposal_lineage VALUES(?,?,?,?,?,?,?,?)",
        (
            fire_id,
            "2099-01-01T00:00:00Z",
            "adjust_weight",
            "topic_weights",
            "approve",
            "2099-01-02T00:00:00Z",
            None,
            json.dumps(evidence),
        ),
    )
    proposals_dir = tmp_path / "proposals"
    proposals_dir.mkdir()
    action = {
        "target_config": "topic_weights",
        "field": "ai_model",
        "current_value": 1.0,
        "proposed_value": 1.1,
    }
    (proposals_dir / "2099-W01.jsonl").write_text(
        json.dumps(
            {
                "fire_id": fire_id,
                "action": action,
                "hsin_decision_comment": "owner approved",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rows = build_proposals(conn, proposals_dir=proposals_dir)
    assert rows == [
        {
            "id": fire_id,
            "kind": "adjust_weight",
            "status": "approved",
            "owner_decision": "approved",
            "summary": "adjust_weight → topic_weights.ai_model",
            "evidence": evidence,
            "proposed_change": action,
            "created_at": "2099-01-01T00:00:00Z",
            "decision_comment": "owner approved",
            "decided_at": "2099-01-02T00:00:00Z",
            "applied_at": None,
        }
    ]
