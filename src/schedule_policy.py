"""Deterministic, bounded publication scheduling policy."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class PlatformDecision:
    platform: str
    due: bool
    reason: str
    posts_today: int
    last_success: str | None


@dataclass(frozen=True)
class ScheduleDecision:
    evaluated_at: str
    timezone: str
    dispatch: bool
    platforms: list[str]
    platform_decisions: list[PlatformDecision]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["platform_decisions"] = [
            asdict(decision) for decision in self.platform_decisions
        ]
        return payload


def load_policy(path: Path) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1:
        raise ValueError("unsupported social automation policy schema")
    return policy


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _inside_slot(now_local: datetime, hours: list[int], tolerance: int) -> bool:
    minute_of_day = now_local.hour * 60 + now_local.minute
    return any(abs(minute_of_day - hour * 60) <= tolerance for hour in hours)


def decide_schedule(
    conn: sqlite3.Connection,
    policy: dict[str, Any],
    now: datetime | None = None,
) -> ScheduleDecision:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    tz_name = policy["timezone"]
    local = now_utc.astimezone(ZoneInfo(tz_name))
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_local.astimezone(timezone.utc).isoformat()
    end_utc = (start_local + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    decisions: list[PlatformDecision] = []

    for platform, config in policy["platforms"].items():
        row = conn.execute(
            """
            SELECT MAX(CASE WHEN success=1 THEN posted_at END) AS last_success,
                   SUM(CASE WHEN success=1 AND posted_at >= ? AND posted_at < ? THEN 1 ELSE 0 END) AS posts_today
              FROM publish_log
             WHERE platform = ?
            """,
            (start_utc, end_utc, platform),
        ).fetchone()
        last_value = row["last_success"] if row else None
        posts_today = int((row["posts_today"] if row else 0) or 0)
        last = _parse_timestamp(last_value)
        interval = timedelta(hours=float(config["minimum_interval_hours"]))
        slot_ok = _inside_slot(
            local,
            [int(value) for value in config["local_slots"]],
            int(config["slot_tolerance_minutes"]),
        )
        quota_ok = posts_today < int(config["target_posts_per_day"])
        interval_ok = last is None or now_utc - last >= interval
        due = slot_ok and quota_ok and interval_ok
        reasons = []
        if not slot_ok:
            reasons.append("outside_local_slot")
        if not quota_ok:
            reasons.append("daily_quota_reached")
        if not interval_ok:
            reasons.append("minimum_interval_not_reached")
        if due:
            reasons.append("due")
        decisions.append(
            PlatformDecision(
                platform=platform,
                due=due,
                reason=",".join(reasons),
                posts_today=posts_today,
                last_success=last_value,
            )
        )

    platforms = [decision.platform for decision in decisions if decision.due]
    return ScheduleDecision(
        evaluated_at=now_utc.isoformat(),
        timezone=tz_name,
        dispatch=bool(platforms),
        platforms=platforms,
        platform_decisions=decisions,
    )
