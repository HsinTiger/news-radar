"""Phase 9 Item 4 — unit tests for ``src/reflector/harvest.py``.

Coverage:
  A. Pure decision tests (no DB IO) — `evaluate_feed`, `grace_days_for`,
     `derive_expected_cadence`, `feed_age_days`, `confidence_for`.
  B. End-to-end against an on-disk SQLite — schema + views.sql sourced;
     synthetic news_items / drafts / engagement_stats seeded; verifies
     proposal jsonl + reflector_proposal_lineage post-conditions.

The on-disk DB pattern (vs :memory:) is required because
`write_proposal` opens its own sqlite3 connection for the lineage
INSERT — :memory: DBs are connection-private.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.reflector.harvest import (  # noqa: E402
    FeedConfig,
    FeedYieldRow,
    GRACE_PERIOD_DAYS,
    HIGH_CONFIDENCE_SAMPLE_FACTOR,
    LOW_CADENCE_GRACE_DAYS,
    LOW_CADENCE_PUBLISHES_PER_WEEK,
    MIN_SAMPLES_THRESHOLD,
    SUNSET_YIELD_THRESHOLD,
    confidence_for,
    derive_expected_cadence,
    evaluate_feed,
    feed_age_days,
    format_markdown_report,
    grace_days_for,
    run_harvest,
)


_SCHEMA_PATH = _ROOT / "data" / "01_harvest" / "schema.sql"
_VIEWS_PATH = _ROOT / "data" / "01_harvest" / "views.sql"


# ======================================================================
# A. Pure helper tests
# ======================================================================

def test_derive_cadence_official_tier():
    cfg = FeedConfig(feed_name="fed_press", feed_added_at=None,
                     source_tier="official")
    assert derive_expected_cadence(cfg) == 1.0


def test_derive_cadence_official_class():
    cfg = FeedConfig(feed_name="x", feed_added_at=None, source_class="official")
    assert derive_expected_cadence(cfg) == 1.0


def test_derive_cadence_explicit_overrides_tier():
    cfg = FeedConfig(feed_name="x", feed_added_at=None,
                     source_tier="primary", expected_cadence_per_week=10.0)
    assert derive_expected_cadence(cfg) == 10.0


def test_derive_cadence_unknown_returns_none():
    cfg = FeedConfig(feed_name="x", feed_added_at=None, source_tier="primary")
    assert derive_expected_cadence(cfg) is None


def test_grace_days_unknown_cadence_uses_8w():
    """Conservative default: unknown cadence → 8-week grace."""
    cfg = FeedConfig(feed_name="x", feed_added_at=None)
    assert grace_days_for(cfg) == LOW_CADENCE_GRACE_DAYS == 56


def test_grace_days_low_cadence_official_uses_8w():
    cfg = FeedConfig(feed_name="fed", feed_added_at=None,
                     source_tier="official")
    assert grace_days_for(cfg) == LOW_CADENCE_GRACE_DAYS == 56


def test_grace_days_high_cadence_uses_4w():
    cfg = FeedConfig(feed_name="x", feed_added_at=None,
                     expected_cadence_per_week=10.0)
    assert grace_days_for(cfg) == GRACE_PERIOD_DAYS == 28


def test_feed_age_days_basic():
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    cfg = FeedConfig(
        feed_name="x",
        feed_added_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    assert feed_age_days(cfg, now=now) == 30


def test_feed_age_days_no_added_at():
    cfg = FeedConfig(feed_name="x", feed_added_at=None)
    assert feed_age_days(cfg) is None


def test_feed_age_days_naive_datetime_treated_as_utc():
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    cfg = FeedConfig(
        feed_name="x",
        feed_added_at=datetime(2026, 4, 1),  # naive
    )
    assert feed_age_days(cfg, now=now) == 30


def test_confidence_high_when_above_factor():
    threshold = MIN_SAMPLES_THRESHOLD  # 3
    assert confidence_for(threshold * HIGH_CONFIDENCE_SAMPLE_FACTOR, threshold) == "HIGH"
    assert confidence_for(threshold * HIGH_CONFIDENCE_SAMPLE_FACTOR + 5, threshold) == "HIGH"


def test_confidence_med_below_factor():
    threshold = MIN_SAMPLES_THRESHOLD
    assert confidence_for(threshold, threshold) == "MED"
    assert confidence_for(threshold + 1, threshold) == "MED"
    assert confidence_for(0, threshold) == "MED"


# ----- evaluate_feed direct tests (acceptance criteria proxies) -----

NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)


def _young_feed(days_old: int, source_tier: str = "primary") -> FeedConfig:
    return FeedConfig(
        feed_name="testfeed",
        feed_added_at=NOW - timedelta(days=days_old),
        source_tier=source_tier,
        # Force "high cadence" lane so 4-week grace applies — the
        # cadence-aware-tests below override this explicitly.
        expected_cadence_per_week=(
            10.0 if source_tier != "official" else None
        ),
    )


def test_grace_period_blocks_young_feed_sunset():
    """Acceptance: feed_added_at < 4 weeks old, low yield → NO sunset."""
    cfg = _young_feed(days_old=10)
    yield_row = FeedYieldRow(
        feed_name="testfeed",
        publish_count_7d=10,
        fetch_count_7d=200,
        avg_score_7d=0.4,
        engagement_yield_ratio=0.01,  # well below SUNSET_YIELD_THRESHOLD
    )
    v = evaluate_feed(yield_row, cfg, has_history=False, is_pinned=False, now=NOW)
    assert v.verdict == "skip:grace"


def test_sample_threshold_blocks_low_publish_sunset():
    """Acceptance: publish_count_7d < 3 → NO sunset proposal."""
    cfg = _young_feed(days_old=60)  # past 4-week grace
    yield_row = FeedYieldRow(
        feed_name="testfeed",
        publish_count_7d=2,  # below MIN_SAMPLES_THRESHOLD=3
        fetch_count_7d=200,
        avg_score_7d=0.4,
        engagement_yield_ratio=0.01,
    )
    v = evaluate_feed(yield_row, cfg, has_history=False, is_pinned=False, now=NOW)
    assert v.verdict == "skip:samples"


def test_cadence_aware_low_cadence_blocks_sunset_inside_8w():
    """Acceptance: low-cadence feed (< 4/week) age > 4w but < 8w, low yield
    → NO sunset proposal (8-week rule)."""
    cfg = FeedConfig(
        feed_name="fed_press_releases",
        feed_added_at=NOW - timedelta(days=35),  # > 28d but < 56d
        source_tier="official",
    )
    yield_row = FeedYieldRow(
        feed_name="fed_press_releases",
        publish_count_7d=4,
        fetch_count_7d=10,
        avg_score_7d=0.3,
        engagement_yield_ratio=0.02,  # low yield
    )
    v = evaluate_feed(yield_row, cfg, has_history=False, is_pinned=False, now=NOW)
    assert v.verdict == "skip:grace", (
        f"expected 8-week rule to gate official feed at 35d; got {v.verdict} ({v.reason})"
    )


def test_cadence_aware_low_cadence_allows_sunset_after_8w():
    """Sanity: same low-cadence feed past 8 weeks DOES sunset."""
    cfg = FeedConfig(
        feed_name="fed_press_releases",
        feed_added_at=NOW - timedelta(days=70),  # > 56d
        source_tier="official",
    )
    yield_row = FeedYieldRow(
        feed_name="fed_press_releases",
        publish_count_7d=4,
        fetch_count_7d=10,
        avg_score_7d=0.3,
        engagement_yield_ratio=0.02,
    )
    v = evaluate_feed(yield_row, cfg, has_history=False, is_pinned=False, now=NOW)
    assert v.verdict == "sunset"


def test_sunset_path_all_gates_passed():
    """Acceptance: yield<0.05 + age>4w + samples≥3 + not pinned → sunset."""
    cfg = _young_feed(days_old=60)  # past 28d grace, high cadence
    yield_row = FeedYieldRow(
        feed_name="testfeed",
        publish_count_7d=8,
        fetch_count_7d=300,
        avg_score_7d=0.4,
        engagement_yield_ratio=0.01,
    )
    v = evaluate_feed(yield_row, cfg, has_history=False, is_pinned=False, now=NOW)
    assert v.verdict == "sunset"
    assert "0.0100" in v.reason or "0.01" in v.reason


def test_investigation_path_zero_publish_with_history():
    """Acceptance: publish_count_7d == 0 + has historical → investigation."""
    cfg = _young_feed(days_old=60)
    yield_row = FeedYieldRow(
        feed_name="testfeed",
        publish_count_7d=0,
        fetch_count_7d=20,
        avg_score_7d=None,
        engagement_yield_ratio=0.0,
    )
    v = evaluate_feed(yield_row, cfg, has_history=True, is_pinned=False, now=NOW)
    assert v.verdict == "investigation"


def test_zero_publish_no_history_skips_quietly():
    """Symmetric: zero publish + no history = brand new feed; leave alone."""
    cfg = _young_feed(days_old=5)
    yield_row = FeedYieldRow(
        feed_name="newfeed",
        publish_count_7d=0,
        fetch_count_7d=5,
        avg_score_7d=None,
        engagement_yield_ratio=0.0,
    )
    v = evaluate_feed(yield_row, cfg, has_history=False, is_pinned=False, now=NOW)
    assert v.verdict == "skip:zero_no_history"


def test_null_avg_score_skip_emits_debug_log(caplog):
    """Acceptance + Cowork ruling: NULL avg_score_7d → skip + debug log."""
    cfg = _young_feed(days_old=60)
    yield_row = FeedYieldRow(
        feed_name="testfeed",
        publish_count_7d=5,
        fetch_count_7d=100,
        avg_score_7d=None,  # the Cowork case
        engagement_yield_ratio=0.01,
    )
    with caplog.at_level(logging.DEBUG, logger="src.reflector.harvest"):
        v = evaluate_feed(yield_row, cfg, has_history=False, is_pinned=False, now=NOW)
    assert v.verdict == "skip:null_score"
    # Debug line MUST be emitted (silently skipping is the failure mode
    # the Cowork ruling explicitly forbids).
    assert any("null_score" in rec.message or "NULL" in rec.message
               for rec in caplog.records), (
        f"expected debug log line for NULL avg_score skip; "
        f"got {[r.message for r in caplog.records]}"
    )


def test_boss_pinned_skip():
    """Acceptance: pinned feeds skip sunset even when all other gates pass.
    Currently the production gate returns False uniformly (Item 8 not shipped);
    this test passes is_pinned=True directly to exercise the eventual lane."""
    cfg = _young_feed(days_old=60)
    yield_row = FeedYieldRow(
        feed_name="testfeed",
        publish_count_7d=8,
        fetch_count_7d=300,
        avg_score_7d=0.4,
        engagement_yield_ratio=0.01,
    )
    v = evaluate_feed(yield_row, cfg, has_history=False, is_pinned=True, now=NOW)
    assert v.verdict == "skip:pinned"


def test_healthy_feed_not_proposed():
    cfg = _young_feed(days_old=60)
    yield_row = FeedYieldRow(
        feed_name="goodfeed",
        publish_count_7d=10,
        fetch_count_7d=20,
        avg_score_7d=0.7,
        engagement_yield_ratio=0.4,  # well above 0.05
    )
    v = evaluate_feed(yield_row, cfg, has_history=False, is_pinned=False, now=NOW)
    assert v.verdict == "skip:ok"


def test_unconfigured_feed_skipped():
    """Feed appears in v_feed_yield_7d but isn't in feeds config → can't
    evaluate grace → skip:unconfigured (NOT proposed)."""
    yield_row = FeedYieldRow(
        feed_name="orphan_feed",
        publish_count_7d=5,
        fetch_count_7d=200,
        avg_score_7d=0.3,
        engagement_yield_ratio=0.01,
    )
    # Pass FeedConfig with no feed_added_at to simulate "feed in view but
    # not in config".
    cfg = FeedConfig(feed_name="orphan_feed", feed_added_at=None,
                     expected_cadence_per_week=10.0)
    v = evaluate_feed(yield_row, cfg, has_history=False, is_pinned=False, now=NOW)
    assert v.verdict == "skip:unconfigured"


# ======================================================================
# B. End-to-end with synthetic SQLite
# ======================================================================

def _build_db(tmp_path) -> tuple[sqlite3.Connection, Path]:
    """Build an on-disk DB with schema + views, return (conn, db_path).

    Use on-disk vs :memory: because write_proposal opens its own
    sqlite3.connect for the lineage INSERT.
    """
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(_VIEWS_PATH.read_text(encoding="utf-8"))
    # Phase 8.18 / 8.20 column migrations (mirrors test_reflector_topic.py).
    for col_ddl in [("publish_at", "TEXT"), ("queue_status", "TEXT")]:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(drafts)").fetchall()]
        if col_ddl[0] not in cols:
            conn.execute(f"ALTER TABLE drafts ADD COLUMN {col_ddl[0]} {col_ddl[1]}")
    for col, ddl in [("topic_category", "TEXT"), ("topic_confidence", "REAL"),
                     ("topic_rationale", "TEXT"), ("weighted_score", "REAL")]:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(news_items)").fetchall()]
        if col not in cols:
            conn.execute(f"ALTER TABLE news_items ADD COLUMN {col} {ddl}")
    conn.commit()
    return conn, db_path


def _seed_news_item(
    conn: sqlite3.Connection,
    *,
    news_id: str,
    feed_name: str,
    days_ago: int,
    status: str = "published",
    weighted_score: float | None = 0.4,
):
    pub = (datetime.now(timezone.utc) - timedelta(days=days_ago)
           ).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO news_items
           (id, feed_name, feed_tier, url, title, published_at, fetched_at,
            status, weighted_score)
           VALUES (?, ?, 'primary', ?, ?, ?, ?, ?, ?)""",
        (news_id, feed_name, f"https://example/{news_id}",
         f"title {news_id}", pub, pub, status, weighted_score),
    )


def _seed_published_draft_with_engagement(
    conn: sqlite3.Connection,
    *,
    news_id: str,
    draft_id: str,
    likes: int = 0,
):
    """Helper: turn a news_item into a published draft and add engagement
    on facebook so it counts toward v_feed_yield_7d's
    engagement_yield_ratio numerator (when likes > 0)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO drafts
           (id, news_id, persona_version, generated_at, status, queue_status)
           VALUES (?, ?, 'v1', ?, 'published', 'published')""",
        (draft_id, news_id, now),
    )
    conn.execute(
        """INSERT INTO engagement_stats
           (draft_id, platform, platform_post_id, fetched_at, likes)
           VALUES (?, 'facebook', ?, ?, ?)""",
        (draft_id, f"fb_{draft_id}", now, likes),
    )


def test_e2e_run_writes_no_proposal_for_healthy_feed(tmp_path):
    conn, db_path = _build_db(tmp_path)
    proposals_dir = tmp_path / "proposals"
    reports_dir = tmp_path / "reports"

    # Seed: 5 published items in last 7d, all with engagement → ratio 1.0
    for i in range(5):
        _seed_news_item(conn, news_id=f"n{i}", feed_name="HealthyFeed",
                        days_ago=2)
        _seed_published_draft_with_engagement(
            conn, news_id=f"n{i}", draft_id=f"d{i}", likes=10
        )
    conn.commit()

    result = run_harvest(
        conn,
        dry_run=False,
        write_proposals=True,
        proposals_db_path=db_path,
        proposals_base_dir=proposals_dir,
        reports_dir=reports_dir,
        write_report=True,
    )

    assert result.feeds_evaluated == 1
    assert result.sunset_count == 0
    assert result.investigation_count == 0
    # No proposals file should be created (or if created, it's empty).
    if proposals_dir.exists():
        for wf in proposals_dir.glob("*.jsonl"):
            assert wf.read_text() == "", (
                f"expected no proposals; got contents in {wf}"
            )
    # Lineage should have no harvest rows.
    rows = conn.execute(
        "SELECT COUNT(*) FROM reflector_proposal_lineage WHERE analyzer = 'harvest'"
    ).fetchone()
    assert rows[0] == 0


def test_e2e_run_writes_investigation_for_zero_publish_with_history(tmp_path):
    """Acceptance: zero publish in 7d but old publish exists → investigation
    proposal lands in jsonl + lineage."""
    conn, db_path = _build_db(tmp_path)
    proposals_dir = tmp_path / "proposals"

    # Historical publish (35 days ago — outside 7d window)
    _seed_news_item(conn, news_id="old_n1", feed_name="DormantFeed",
                    days_ago=35, status="published")
    # No recent items at all → publish_count_7d = 0, fetch_count_7d = 0,
    # so the feed won't appear in v_feed_yield_7d (which filters
    # `published_at > now-7d`). Add at least one fetched-but-not-
    # published recent row so the feed surfaces in the view.
    _seed_news_item(conn, news_id="recent_fetch", feed_name="DormantFeed",
                    days_ago=2, status="fetched", weighted_score=None)
    conn.commit()

    # Sanity: confirm the view sees the feed with publish_count_7d=0.
    view_row = conn.execute(
        "SELECT publish_count_7d, fetch_count_7d FROM v_feed_yield_7d "
        "WHERE feed_name = 'DormantFeed'"
    ).fetchone()
    assert view_row is not None
    assert view_row["publish_count_7d"] == 0
    assert view_row["fetch_count_7d"] == 1

    result = run_harvest(
        conn,
        dry_run=False,
        write_proposals=True,
        proposals_db_path=db_path,
        proposals_base_dir=proposals_dir,
        reports_dir=tmp_path / "reports",
        write_report=False,  # avoid touching cwd's reports/
    )
    assert result.investigation_count == 1
    assert result.sunset_count == 0

    # Verify jsonl + lineage post-condition
    week_files = list(proposals_dir.glob("*.jsonl"))
    assert week_files
    records = []
    for wf in week_files:
        for line in wf.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))
    assert len(records) == 1
    rec = records[0]
    assert rec["analyzer"] == "harvest"
    assert rec["proposal_type"] == "sunset_feed"
    assert rec["action"]["target_config"] == "feeds.yml"
    assert rec["action"]["field"] == "DormantFeed"
    assert rec["action"]["proposed_value"] == "investigate"
    assert rec["evidence"]["metrics"]["signal"] == "zero_publish_with_history"
    assert rec["boss_attention_required"] is True
    assert rec["deployed_at"] is None

    # Lineage post-condition (scoped-vdd: SELECT asserts the side-effect)
    fire_id = rec["fire_id"]
    lineage = conn.execute(
        "SELECT analyzer, target_config, deployed_at "
        "FROM reflector_proposal_lineage WHERE fire_id = ?",
        (fire_id,),
    ).fetchone()
    assert lineage is not None
    assert lineage["analyzer"] == "harvest"
    assert lineage["target_config"] == "feeds.yml"
    assert lineage["deployed_at"] is None  # never auto-deployed


def test_e2e_run_writes_no_sunset_for_unconfigured_feed_inside_view(tmp_path):
    """A feed appearing in v_feed_yield_7d but absent from config.yaml
    should be skipped (skip:unconfigured), NOT sunset-proposed.

    This guards the "we can't read feed_added_at" path; sunset proposals
    must always carry a defensible age claim.
    """
    conn, db_path = _build_db(tmp_path)
    proposals_dir = tmp_path / "proposals"

    # Seed 5 published items with low engagement (likes=0 → ratio=0.0)
    for i in range(5):
        _seed_news_item(conn, news_id=f"n{i}", feed_name="OrphanFeed",
                        days_ago=2)
        _seed_published_draft_with_engagement(
            conn, news_id=f"n{i}", draft_id=f"d{i}", likes=0
        )
    conn.commit()

    # Don't supply a config_path → load_feed_configs reads production
    # config.yaml which doesn't contain "OrphanFeed". The analyzer
    # should treat it as unconfigured.
    # To make this test deterministic regardless of prod config state,
    # point at an empty tmp config file.
    empty_cfg = tmp_path / "empty_config.yaml"
    empty_cfg.write_text("feeds: []\n")

    result = run_harvest(
        conn,
        dry_run=False,
        write_proposals=True,
        proposals_db_path=db_path,
        proposals_base_dir=proposals_dir,
        config_path=empty_cfg,
        write_report=False,
    )
    assert result.sunset_count == 0
    # The feed should appear as skip:unconfigured.
    matched = [v for v in result.verdicts if v.feed_name == "OrphanFeed"]
    assert matched and matched[0].verdict == "skip:unconfigured"


def test_e2e_dry_run_writes_no_proposals(tmp_path):
    conn, db_path = _build_db(tmp_path)
    proposals_dir = tmp_path / "proposals"

    _seed_news_item(conn, news_id="old", feed_name="DormantFeed",
                    days_ago=40, status="published")
    _seed_news_item(conn, news_id="recent", feed_name="DormantFeed",
                    days_ago=1, status="fetched")
    conn.commit()

    result = run_harvest(
        conn,
        dry_run=True,
        write_proposals=True,
        proposals_db_path=db_path,
        proposals_base_dir=proposals_dir,
        write_report=False,
    )
    # dry-run still surfaces investigation in result, but writes nothing.
    assert result.dry_run is True
    assert result.investigation_count == 1
    assert not list(proposals_dir.glob("*.jsonl"))
    rows = conn.execute(
        "SELECT COUNT(*) FROM reflector_proposal_lineage"
    ).fetchone()
    assert rows[0] == 0


def test_format_markdown_report_lists_verdicts():
    from src.reflector.harvest import HarvestResult, FeedVerdict
    result = HarvestResult(
        ran_at="2026-04-28T03:00:00+00:00",
        dry_run=False,
        feeds_evaluated=2,
        sunset_count=1,
        investigation_count=0,
        skipped_count=1,
        verdicts=[
            FeedVerdict(feed_name="BadFeed", verdict="sunset",
                        reason="yield=0.01 <0.05, age=60d ≥28d, samples=8 ≥3"),
            FeedVerdict(feed_name="GoodFeed", verdict="skip:ok",
                        reason="healthy"),
        ],
    )
    md = format_markdown_report(result)
    assert "Harvest Analyzer" in md
    assert "BadFeed" in md
    assert "GoodFeed" in md
    assert "Sunset candidates" in md
