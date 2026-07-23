from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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


def test_evening_slot_dispatches_all_due_platforms() -> None:
    decision = decide_schedule(
        _conn(), load_policy(POLICY), datetime(2026, 7, 23, 12, 10, tzinfo=timezone.utc)
    )
    assert set(decision.platforms) == {"threads", "facebook", "instagram"}


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


def test_outside_slot_is_noop() -> None:
    decision = decide_schedule(
        _conn(), load_policy(POLICY), datetime(2026, 7, 23, 4, 0, tzinfo=timezone.utc)
    )
    assert decision.dispatch is False
    assert decision.platforms == []
