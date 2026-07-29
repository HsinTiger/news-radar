from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.schedule_policy import decide_schedule, load_policy


POLICY = Path(__file__).resolve().parents[2] / "config/social_automation_policy.json"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE publish_log(platform TEXT, posted_at TEXT, success INTEGER)"
    )
    return conn


def test_morning_slot_dispatches_threads_only() -> None:
    decision = decide_schedule(
        _conn(), load_policy(POLICY), datetime(2026, 7, 23, 0, 10, tzinfo=timezone.utc)
    )
    assert decision.dispatch is True
    assert decision.platforms == ["threads"]


def test_evening_slots_are_platform_native() -> None:
    facebook = decide_schedule(
        _conn(), load_policy(POLICY), datetime(2026, 7, 23, 10, 10, tzinfo=timezone.utc)
    )
    instagram = decide_schedule(
        _conn(), load_policy(POLICY), datetime(2026, 7, 23, 12, 10, tzinfo=timezone.utc)
    )
    assert facebook.platforms == ["facebook"]
    assert instagram.platforms == ["instagram"]


def test_live_policy_is_one_post_per_platform_per_day() -> None:
    decision = decide_schedule(
        _conn(), load_policy(POLICY), datetime(2026, 7, 23, 12, 10, tzinfo=timezone.utc)
    )
    assert all(item.target_posts_per_day == 1 for item in decision.platform_decisions)


def test_daily_quota_prevents_duplicate_dispatch() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO publish_log VALUES ('facebook', '2026-07-23T01:00:00+00:00', 1)"
    )
    decision = decide_schedule(
        conn, load_policy(POLICY), datetime(2026, 7, 23, 12, 10, tzinfo=timezone.utc)
    )
    assert "facebook" not in decision.platforms
    fb = next(item for item in decision.platform_decisions if item.platform == "facebook")
    assert "daily_quota_reached" in fb.reason


def test_recovery_allows_one_bounded_same_day_quality_retry() -> None:
    conn = _conn()
    conn.execute(
        """CREATE TABLE content_quality_evaluations(
        draft_id TEXT,platform TEXT,stage TEXT,checked_at TEXT,guard_version TEXT)"""
    )
    conn.executemany(
        "INSERT INTO content_quality_evaluations VALUES(?,?,?,?,?)",
        [
            (
                "held-draft",
                "instagram",
                "compose",
                "2026-07-23T12:02:00+00:00",
                "2026-07-23.taiwan-daily-v6",
            ),
            (
                "held-draft",
                "instagram",
                "compose",
                "2026-07-23T12:03:00+00:00",
                "2026-07-23.taiwan-daily-v6",
            ),
        ],
    )

    decision = decide_schedule(
        conn,
        load_policy(POLICY),
        datetime(2026, 7, 23, 12, 22, tzinfo=timezone.utc),
        mode="recovery",
    )
    instagram = next(
        item for item in decision.platform_decisions if item.platform == "instagram"
    )
    assert decision.platforms == ["instagram"]
    assert instagram.quality_attempts_today == 1
    assert instagram.max_quality_attempts_per_day == 2
    assert instagram.retryable_queue == 0
    assert "daily_attempt_quota_reached" not in instagram.reason


def test_recovery_stops_after_second_same_day_quality_attempt() -> None:
    conn = _conn()
    conn.execute(
        """CREATE TABLE content_quality_evaluations(
        draft_id TEXT,platform TEXT,stage TEXT,checked_at TEXT,guard_version TEXT)"""
    )
    conn.executemany(
        "INSERT INTO content_quality_evaluations VALUES(?,?,?,?,?)",
        [
            (
                draft_id,
                "instagram",
                "compose",
                checked_at,
                "2026-07-29.taiwan-daily-v38",
            )
            for draft_id, checked_at in (
                ("held-draft-1", "2026-07-23T12:02:00+00:00"),
                ("held-draft-2", "2026-07-23T12:12:00+00:00"),
            )
        ],
    )

    decision = decide_schedule(
        conn,
        load_policy(POLICY),
        datetime(2026, 7, 23, 12, 22, tzinfo=timezone.utc),
        mode="recovery",
    )
    instagram = next(
        item for item in decision.platform_decisions if item.platform == "instagram"
    )

    assert "instagram" not in decision.platforms
    assert instagram.quality_attempts_today == 2
    assert instagram.max_quality_attempts_per_day == 2
    assert "daily_attempt_quota_reached" in instagram.reason


def test_recovery_retries_publish_ready_queue_inside_same_slot() -> None:
    conn = _conn()
    conn.execute("ALTER TABLE publish_log ADD COLUMN draft_id TEXT")
    conn.executescript(
        """
        CREATE TABLE content_quality_evaluations(
          draft_id TEXT,platform TEXT,stage TEXT,checked_at TEXT,guard_version TEXT
        );
        CREATE TABLE drafts(
          id TEXT PRIMARY KEY,status TEXT,queue_status TEXT
        );
        CREATE TABLE platform_drafts(draft_id TEXT,platform TEXT);
        CREATE TABLE recovery_experiments(draft_id TEXT,platform TEXT);
        INSERT INTO content_quality_evaluations VALUES(
          'ready-draft','threads','compose','2026-07-23T00:09:00+00:00',
          '2026-07-24.taiwan-daily-v10'
        );
        INSERT INTO drafts VALUES('ready-draft','auto_approved','queued');
        INSERT INTO platform_drafts VALUES('ready-draft','threads');
        INSERT INTO recovery_experiments VALUES('ready-draft','threads');
        """
    )

    decision = decide_schedule(
        conn,
        load_policy(POLICY),
        datetime(2026, 7, 23, 0, 22, tzinfo=timezone.utc),
        mode="recovery",
    )
    threads = next(
        item for item in decision.platform_decisions if item.platform == "threads"
    )

    assert decision.platforms == ["threads"]
    assert threads.quality_attempts_today == 1
    assert threads.retryable_queue == 1
    assert "daily_attempt_quota_reached" not in threads.reason


def test_recovery_does_not_retry_queue_after_platform_success() -> None:
    conn = _conn()
    conn.execute("ALTER TABLE publish_log ADD COLUMN draft_id TEXT")
    conn.executescript(
        """
        CREATE TABLE content_quality_evaluations(
          draft_id TEXT,platform TEXT,stage TEXT,checked_at TEXT,guard_version TEXT
        );
        CREATE TABLE drafts(id TEXT PRIMARY KEY,status TEXT,queue_status TEXT);
        CREATE TABLE platform_drafts(draft_id TEXT,platform TEXT);
        CREATE TABLE recovery_experiments(draft_id TEXT,platform TEXT);
        INSERT INTO content_quality_evaluations VALUES(
          'done-draft','threads','compose','2026-07-23T00:09:00+00:00',
          '2026-07-24.taiwan-daily-v10'
        );
        INSERT INTO drafts VALUES('done-draft','auto_approved','queued');
        INSERT INTO platform_drafts VALUES('done-draft','threads');
        INSERT INTO recovery_experiments VALUES('done-draft','threads');
        INSERT INTO publish_log(platform,posted_at,success,draft_id)
        VALUES('threads','2026-07-23T00:10:00+00:00',1,'done-draft');
        """
    )

    decision = decide_schedule(
        conn,
        load_policy(POLICY),
        datetime(2026, 7, 23, 0, 22, tzinfo=timezone.utc),
        mode="recovery",
    )
    threads = next(
        item for item in decision.platform_decisions if item.platform == "threads"
    )

    assert "threads" not in decision.platforms
    assert threads.retryable_queue == 0
    assert "daily_quota_reached" in threads.reason


def test_recovery_quality_attempt_resets_on_next_local_day() -> None:
    conn = _conn()
    conn.execute(
        """CREATE TABLE content_quality_evaluations(
        draft_id TEXT,platform TEXT,stage TEXT,checked_at TEXT,guard_version TEXT)"""
    )
    conn.execute(
        "INSERT INTO content_quality_evaluations VALUES(?,?,?,?,?)",
        (
            "yesterday-held",
            "instagram",
            "compose",
            "2026-07-23T12:02:00+00:00",
            "2026-07-23.taiwan-daily-v6",
        ),
    )

    decision = decide_schedule(
        conn,
        load_policy(POLICY),
        datetime(2026, 7, 24, 12, 22, tzinfo=timezone.utc),
        mode="recovery",
    )
    instagram = next(
        item for item in decision.platform_decisions if item.platform == "instagram"
    )
    assert decision.platforms == ["instagram"]
    assert instagram.quality_attempts_today == 0


def test_outside_slot_is_noop() -> None:
    decision = decide_schedule(
        _conn(), load_policy(POLICY), datetime(2026, 7, 23, 4, 0, tzinfo=timezone.utc)
    )
    assert decision.dispatch is False
    assert decision.platforms == []


def test_owner_approved_runtime_override_changes_threads_slot() -> None:
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE social_policy_overrides(
          platform TEXT PRIMARY KEY,target_posts_per_day INTEGER,
          minimum_interval_hours REAL,local_slots_json TEXT,
          source_proposal_id TEXT,updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO social_policy_overrides VALUES(?,?,?,?,?,?)",
        ("threads", 3, 6.0, "[8,14,20]", "proposal-123", "2026-07-22T00:00:00Z"),
    )
    decision = decide_schedule(
        conn,
        load_policy(POLICY),
        datetime(2026, 7, 23, 6, 10, tzinfo=timezone.utc),  # 14:10 Taipei
    )
    threads = next(item for item in decision.platform_decisions if item.platform == "threads")
    assert decision.platforms == ["threads"]
    assert threads.target_posts_per_day == 3
    assert threads.minimum_interval_hours == 6.0
    assert threads.local_slots == [8, 14, 20]
    assert threads.policy_source == "proposal:proposal-123"


def test_runtime_override_rejects_spacing_violation_across_midnight() -> None:
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE social_policy_overrides(
          platform TEXT PRIMARY KEY,target_posts_per_day INTEGER,
          minimum_interval_hours REAL,local_slots_json TEXT,
          source_proposal_id TEXT,updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO social_policy_overrides VALUES(?,?,?,?,?,?)",
        ("threads", 2, 8.0, "[0,20]", "bad-proposal", "2026-07-22T00:00:00Z"),
    )
    with pytest.raises(ValueError, match="minimum spacing"):
        decide_schedule(
            conn,
            load_policy(POLICY),
            datetime(2026, 7, 23, 12, 10, tzinfo=timezone.utc),
        )


def test_recovery_morning_commute_dispatches_threads_only() -> None:
    decision = decide_schedule(
        _conn(),
        load_policy(POLICY),
        datetime(2026, 7, 24, 0, 10, tzinfo=timezone.utc),  # Friday 08:10 Taipei
        mode="recovery",
    )
    assert decision.mode == "recovery"
    assert decision.platforms == ["threads"]
    threads = decision.platform_decisions[0]
    assert threads.target_posts_per_day == 1
    assert threads.policy_source == "recovery_policy"


def test_recovery_daily_evening_slots_separate_facebook_and_instagram() -> None:
    facebook = decide_schedule(
        _conn(),
        load_policy(POLICY),
        datetime(2026, 7, 27, 10, 10, tzinfo=timezone.utc),  # Monday 18:10
        mode="recovery",
    )
    instagram = decide_schedule(
        _conn(),
        load_policy(POLICY),
        datetime(2026, 7, 27, 12, 10, tzinfo=timezone.utc),  # Monday 20:10
        mode="recovery",
    )
    assert facebook.platforms == ["facebook"]
    assert instagram.platforms == ["instagram"]


def test_scheduler_never_dispatches_before_slot_and_catches_up_after_delay() -> None:
    before = decide_schedule(
        _conn(),
        load_policy(POLICY),
        datetime(2026, 7, 27, 9, 12, tzinfo=timezone.utc),  # 17:12 Taipei
        mode="recovery",
    )
    delayed_ok = decide_schedule(
        _conn(),
        load_policy(POLICY),
        datetime(2026, 7, 27, 10, 50, tzinfo=timezone.utc),  # 18:50 Taipei
        mode="recovery",
    )
    delayed_catch_up = decide_schedule(
        _conn(),
        load_policy(POLICY),
        datetime(2026, 7, 27, 11, 25, tzinfo=timezone.utc),  # 19:25 Taipei
        mode="recovery",
    )
    assert "facebook" not in before.platforms
    assert delayed_ok.platforms == ["facebook"]
    assert delayed_catch_up.platforms == ["facebook"]
    facebook = next(
        item
        for item in delayed_catch_up.platform_decisions
        if item.platform == "facebook"
    )
    assert facebook.reason == "catch_up_due"


def test_observed_github_delivery_delays_still_reach_each_recovery_slot() -> None:
    policy = load_policy(POLICY)

    late_morning = decide_schedule(
        _conn(),
        policy,
        datetime(2026, 7, 27, 3, 47, tzinfo=timezone.utc),  # 11:47 Taipei
        mode="recovery",
    )
    late_facebook = decide_schedule(
        _conn(),
        policy,
        datetime(2026, 7, 27, 11, 25, tzinfo=timezone.utc),  # 19:25 Taipei
        mode="recovery",
    )
    late_instagram = decide_schedule(
        _conn(),
        policy,
        datetime(2026, 7, 27, 13, 47, tzinfo=timezone.utc),  # 21:47 Taipei
        mode="recovery",
    )

    assert late_morning.platforms == ["threads"]
    assert late_facebook.platforms == ["facebook"]
    assert late_instagram.platforms == ["instagram"]


def test_recovery_catch_up_expires_at_local_midnight() -> None:
    decision = decide_schedule(
        _conn(),
        load_policy(POLICY),
        datetime(2026, 7, 27, 16, 5, tzinfo=timezone.utc),  # Tuesday 00:05 Taipei
        mode="recovery",
    )

    assert decision.platforms == []


def test_recovery_ignores_live_runtime_override() -> None:
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE social_policy_overrides(
          platform TEXT PRIMARY KEY,target_posts_per_day INTEGER,
          minimum_interval_hours REAL,local_slots_json TEXT,
          source_proposal_id TEXT,updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO social_policy_overrides VALUES(?,?,?,?,?,?)",
        ("threads", 4, 4.0, "[0,6,12,18]", "legacy-fast", "2026-07-23T00:00:00Z"),
    )
    decision = decide_schedule(
        conn,
        load_policy(POLICY),
        datetime(2026, 7, 24, 0, 10, tzinfo=timezone.utc),
        mode="recovery",
    )
    threads = next(item for item in decision.platform_decisions if item.platform == "threads")
    assert threads.target_posts_per_day == 1
    assert threads.local_slots == [8]
    assert threads.policy_source == "recovery_policy"


def test_recovery_noon_is_a_delayed_morning_catch_up() -> None:
    decision = decide_schedule(
        _conn(),
        load_policy(POLICY),
        datetime(2026, 7, 24, 4, 10, tzinfo=timezone.utc),
        mode="recovery",
    )
    assert decision.platforms == ["threads"]
    threads = next(
        item for item in decision.platform_decisions if item.platform == "threads"
    )
    assert threads.reason == "catch_up_due"
