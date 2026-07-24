import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts.sync_social_ops import (
    build_automation_state,
    build_engagement,
    build_health,
    build_knowledge,
    build_posts,
    build_proposals,
    build_quality,
    build_recovery_experiments,
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
          feed_name TEXT,tags TEXT,substack_written_at TEXT,
          substack_draft_id TEXT,substack_drafted_at TEXT
        );
        CREATE TABLE drafts(id TEXT PRIMARY KEY,news_id TEXT,title TEXT,generated_at TEXT);
        CREATE TABLE platform_drafts(draft_id TEXT,platform TEXT,full_text TEXT,final_text TEXT);
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
        CREATE TABLE content_quality_evaluations(
          id INTEGER PRIMARY KEY AUTOINCREMENT,draft_id TEXT,news_id TEXT,
          platform TEXT,stage TEXT,attempt INTEGER,checked_at TEXT,
          guard_version TEXT,text_sha256 TEXT,decision TEXT,block_count INTEGER,
          rewrite_count INTEGER,warn_count INTEGER,issue_codes_json TEXT,
          issues_json TEXT
        );
        CREATE TABLE recovery_experiments(
          id TEXT PRIMARY KEY,draft_id TEXT,platform TEXT,experiment_type TEXT,
          hypothesis TEXT,baseline_followers INTEGER,baseline_primary_metric TEXT,
          baseline_primary_value REAL,baseline_captured_at TEXT,content_format TEXT,
          actual_format TEXT,actual_format_at TEXT,topic TEXT,created_at TEXT
        );
        INSERT INTO news_items VALUES(
          'n1','rss','https://example.com','Useful source','ai_application','scored',
          '2099-01-01T00:00:00Z',800,0.9,'rss','[]',NULL,NULL,NULL
        );
        INSERT INTO drafts VALUES('d1','n1','Useful draft','2099-01-02T00:00:00Z');
        INSERT INTO platform_drafts VALUES('d1','threads','Useful post',NULL);
        INSERT INTO content_quality_evaluations VALUES(
          1,'d1','n1','threads','backfill',1,'2099-01-02T01:00:00Z',
          'test-v1','abc','rewrite',0,1,0,'["uncited_stat"]','[]'
        );
        INSERT INTO publish_log VALUES(
          1,'d1','threads','t-post','2099-01-03T00:00:00Z',1,NULL
        );
        INSERT INTO engagement_stats VALUES(
          'threads','t-post','2099-01-04T00:00:00Z',24,100,0,5,0,0,0,2,1,0,'{}',0
        );
        INSERT INTO recovery_experiments(
          id,draft_id,platform,experiment_type,hypothesis,baseline_followers,
          baseline_primary_metric,baseline_primary_value,baseline_captured_at,
          content_format,topic,created_at
        ) VALUES(
          'rx','d1','threads','utility','Reader utility test',3748,'views',279.5,
          '2026-07-23T00:00:00Z','carousel','ai_application','2099-01-02T00:00:00Z'
        );
        """
    )
    return conn


def test_sync_builders_export_metadata_without_article_body() -> None:
    conn = _db()
    posts = build_posts(conn, full=True)
    engagement = build_engagement(conn, full=True)
    knowledge = build_knowledge(conn, full=True, limit=0)
    quality = build_quality(conn, full=True)
    assert posts[0]["status"] == "published"
    assert posts[0]["id"] == "post_d1_threads_feed"
    assert posts[0]["topic"] == "ai_application"
    assert engagement[0]["metric_status"] == "ok"
    assert engagement[0]["replies"] == 2
    assert engagement[0]["clicks"] == 0
    assert knowledge[0]["use_count"] == 1
    assert "clean_markdown" not in knowledge[0]
    threads_quality = next(row for row in quality if row["platform"] == "threads")
    assert threads_quality["evaluated"] == 1
    assert threads_quality["evidence_coverage"] == 1.0
    assert threads_quality["rewrite_count"] == 1
    assert threads_quality["top_issue_codes"] == [
        {"code": "uncited_stat", "count": 1}
    ]
    experiments = build_recovery_experiments(conn)
    assert experiments[0]["id"] == "rx"
    assert experiments[0]["content_format"] == "carousel"
    assert experiments[0]["actual_format"] is None


def test_post_sync_uses_proven_actual_recovery_format() -> None:
    conn = _db()
    conn.execute(
        "UPDATE recovery_experiments SET actual_format='carousel', "
        "actual_format_at='2099-01-03T00:00:01Z' WHERE draft_id='d1'"
    )
    post = build_posts(conn, full=True)[0]
    assert post["id"] == "post_d1_threads_feed"
    assert post["format"] == "carousel"


def test_post_sync_defaults_to_feed_for_pre_recovery_state() -> None:
    conn = _db()
    conn.execute("DROP TABLE recovery_experiments")
    assert build_posts(conn, full=True)[0]["format"] == "feed"


def test_automation_state_uses_canonical_repository_variables(monkeypatch) -> None:
    monkeypatch.setenv("AUTOMATION_MODE", "recovery")
    monkeypatch.setenv("SUBMISSION_PROCESSOR_MODE", "paused")
    row = build_automation_state()[0]
    assert row["mode"] == "recovery"
    assert row["submission_processor"] == "paused"


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


def test_substack_worker_health_is_degraded_for_stale_pending_source() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "sub-stale", "text", "manual://stale", "Owner source", "ai_application",
            "fetched", "2020-01-01T00:00:00Z", 100, 1.0, "user_substack",
            '["substack_source"]', None, None, None,
        ),
    )
    row = next(
        item
        for item in build_health(conn)
        if item["platform"] == "system"
        and item["metric"] == "substack_draft_worker"
    )
    assert row["status"] == "degraded"
    assert "pending_remote=1" in row["detail"]
    assert "remote_proven=0" in row["detail"]


def test_substack_worker_health_is_healthy_only_with_recent_remote_evidence() -> None:
    conn = _db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "sub-recent", "text", "manual://recent", "Owner source", "ai_application",
            "fetched", now, 100, 1.0, "user_substack", '["substack_source"]',
            now, "draft-123", now,
        ),
    )
    row = next(
        item
        for item in build_health(conn)
        if item["platform"] == "system"
        and item["metric"] == "substack_draft_worker"
    )
    assert row["status"] == "healthy"
    assert "pending_remote=0" in row["detail"]
    assert "remote_proven=1" in row["detail"]


def test_engagement_sync_degrades_legacy_schema_without_clicks_to_zero() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE engagement_stats(
          platform TEXT,platform_post_id TEXT,fetched_at TEXT,post_age_bucket INTEGER,
          views INTEGER,reach INTEGER,likes INTEGER,comments INTEGER,shares INTEGER,
          saves INTEGER,replies INTEGER,reposts INTEGER,quotes INTEGER,raw_json TEXT
        );
        INSERT INTO engagement_stats VALUES(
          'facebook','fb-old','2099-01-01T00:00:00Z',24,
          10,9,1,2,3,4,5,6,7,'{}'
        );
        """
    )
    rows = build_engagement(conn, full=True)
    assert rows[0]["clicks"] == 0
    assert rows[0]["metric_status"] == "ok"
    health = {
        (row["platform"], row["metric"]): row["status"]
        for row in build_health(conn)
    }
    assert health[("facebook", "engagement_api")] == "healthy"


def test_only_remote_substack_draft_evidence_becomes_terminal_update() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "sub1", "text", "manual://1", "Owner source", "ai_application",
            "fetched", "2099-01-01T00:00:00Z", 100, 1.0, "user_substack",
            '["substack_source","control_submission:12345678-abcd"]',
            "2099-01-02T00:00:00Z", "draft-123", "2099-01-03T00:00:00Z",
        ),
    )
    assert build_submission_updates(conn) == [
        {
            "submission_id": "12345678-abcd",
            "status": "draft_created",
            "observed_at": "2099-01-03T00:00:00Z",
        }
    ]


def test_local_substack_article_alone_is_not_reported_as_remote_draft() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "sub-local", "text", "manual://local", "Local only", "ai_application",
            "fetched", "2099-01-01T00:00:00Z", 100, 1.0, "user_substack",
            '["substack_source","control_submission:local-only-001"]',
            "2099-01-02T00:00:00Z", None, None,
        ),
    )
    assert build_submission_updates(conn) == []


def test_meta_lineage_exports_submission_and_partial_then_published() -> None:
    conn = _db()
    tags = json.dumps(
        [
            "user_submission",
            "platform:fb",
            "platform:ig",
            "platform:threads",
            "control_submission:meta-submit-001",
            "control_route:meta-submit-001:fb,ig,threads",
            "control_submission:meta-submit-fb-001",
            "control_route:meta-submit-fb-001:fb",
            "control_source_url:https://source.example/item",
        ]
    )
    conn.execute(
        "UPDATE news_items SET feed_name='user_submission',tags=? WHERE id='n1'",
        (tags,),
    )
    conn.execute(
        "INSERT INTO publish_log VALUES(2,'d1','facebook','fb-post','2099-01-03T01:00:00Z',1,NULL)"
    )
    conn.execute(
        "INSERT INTO publish_log VALUES(3,'d1','instagram','ig-post','2099-01-03T02:00:00Z',1,NULL)"
    )

    posts = build_posts(conn, full=True)
    threads = next(row for row in posts if row["platform"] == "threads")
    assert threads["submission_id"] == "meta-submit-001"
    assert threads["source_url"] == "https://source.example/item"
    assert build_submission_updates(conn) == [
        {
            "submission_id": "meta-submit-001",
            "status": "published",
            "observed_at": "2099-01-03T02:00:00Z",
        },
        {
            "submission_id": "meta-submit-fb-001",
            "status": "published",
            "observed_at": "2099-01-03T01:00:00Z",
        },
    ]

    conn.execute("DELETE FROM publish_log WHERE platform='threads'")
    assert build_submission_updates(conn) == [
        {
            "submission_id": "meta-submit-001",
            "status": "partial",
            "observed_at": "2099-01-03T02:00:00Z",
        },
        {
            "submission_id": "meta-submit-fb-001",
            "status": "published",
            "observed_at": "2099-01-03T01:00:00Z",
        },
    ]


def test_meta_quality_block_without_draft_becomes_quality_held() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "n-held", "text", "manual://held", "Owner source", "ai_application",
            "dropped", "2099-02-01T00:00:00Z", 100, 1.0, "user_submission",
            '["user_submission","platform:threads",'
            '"control_submission:meta-held-001",'
            '"control_route:meta-held-001:threads"]',
            None, None, None,
        ),
    )
    conn.execute(
        """INSERT INTO content_quality_evaluations(
             draft_id,news_id,platform,stage,attempt,checked_at,guard_version,
             text_sha256,decision,block_count,rewrite_count,warn_count,
             issue_codes_json,issues_json
           ) VALUES('d-held','n-held','threads','compose',1,
             '2099-02-02T00:00:00Z','v1','sha','block',1,0,0,'[]','[]')"""
    )
    assert build_submission_updates(conn) == [
        {
            "submission_id": "meta-held-001",
            "status": "quality_held",
            "observed_at": "2099-02-02T00:00:00Z",
        }
    ]


def test_quality_hold_is_platform_scoped_and_not_masked_by_later_pass() -> None:
    conn = _db()
    tags = json.dumps(
        [
            "user_submission",
            "platform:fb",
            "platform:threads",
            "control_submission:meta-held-threads",
            "control_route:meta-held-threads:threads",
            "control_submission:meta-clean-fb",
            "control_route:meta-clean-fb:fb",
        ]
    )
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "n-platform-held", "text", "manual://platform-held", "Owner source",
            "ai_application", "dropped", "2099-02-01T00:00:00Z", 100, 1.0,
            "user_submission", tags, None, None, None,
        ),
    )
    conn.execute(
        """INSERT INTO content_quality_evaluations(
             draft_id,news_id,platform,stage,attempt,checked_at,guard_version,
             text_sha256,decision,block_count,rewrite_count,warn_count,
             issue_codes_json,issues_json
           ) VALUES('d-platform-held','n-platform-held','threads','compose',1,
             '2099-02-02T00:00:00Z','v1','sha-t','block',1,0,0,'[]','[]')"""
    )
    conn.execute(
        """INSERT INTO content_quality_evaluations(
             draft_id,news_id,platform,stage,attempt,checked_at,guard_version,
             text_sha256,decision,block_count,rewrite_count,warn_count,
             issue_codes_json,issues_json
           ) VALUES('d-platform-held','n-platform-held','facebook','compose',1,
             '2099-02-02T00:01:00Z','v1','sha-f','pass',0,0,0,'[]','[]')"""
    )
    assert build_submission_updates(conn) == [
        {
            "submission_id": "meta-held-threads",
            "status": "quality_held",
            "observed_at": "2099-02-02T00:00:00Z",
        }
    ]


def test_successful_quality_rewrite_is_not_reported_as_held() -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "n-rewrite-resolved", "text", "manual://rewrite-resolved", "Owner source",
            "ai_application", "queued", "2099-02-01T00:00:00Z", 100, 1.0,
            "user_submission",
            '["user_submission","platform:threads",'
            '"control_submission:meta-rewrite-resolved",'
            '"control_route:meta-rewrite-resolved:threads"]',
            None, None, None,
        ),
    )
    conn.execute(
        "INSERT INTO drafts VALUES('d-rewrite-resolved','n-rewrite-resolved','Draft','2099-02-02T00:00:00Z')"
    )
    conn.execute(
        """INSERT INTO content_quality_evaluations(
             draft_id,news_id,platform,stage,attempt,checked_at,guard_version,
             text_sha256,decision,block_count,rewrite_count,warn_count,
             issue_codes_json,issues_json
           ) VALUES('d-rewrite-resolved','n-rewrite-resolved','threads','compose',1,
             '2099-02-02T00:00:00Z','v1','sha-old','rewrite',0,1,0,'[]','[]')"""
    )
    conn.execute(
        """INSERT INTO content_quality_evaluations(
             draft_id,news_id,platform,stage,attempt,checked_at,guard_version,
             text_sha256,decision,block_count,rewrite_count,warn_count,
             issue_codes_json,issues_json
           ) VALUES('d-rewrite-resolved','n-rewrite-resolved','threads','compose',2,
             '2099-02-02T00:01:00Z','v1','sha-new','pass',0,0,0,'[]','[]')"""
    )
    assert build_submission_updates(conn) == []


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
