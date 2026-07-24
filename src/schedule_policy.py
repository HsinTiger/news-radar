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
    quality_attempts_today: int
    last_success: str | None
    target_posts_per_day: int
    minimum_interval_hours: float
    local_slots: list[int]
    local_days: list[int]
    policy_source: str


@dataclass(frozen=True)
class ScheduleDecision:
    evaluated_at: str
    timezone: str
    mode: str
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
    if policy.get("schema_version") != 2:
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
    """Accept a delayed scheduler tick, never a pre-slot tick.

    The governed scheduler runs hourly around ``:12``.  A symmetric ±50 minute
    window made 17:12 qualify for an 18:00 commute slot, silently publishing 48
    minutes early.  GitHub delay tolerance is therefore directional: the target
    hour through ``tolerance`` minutes after it.
    """
    minute_of_day = now_local.hour * 60 + now_local.minute
    return any(
        0 <= minute_of_day - hour * 60 <= tolerance
        for hour in hours
    )


def _effective_platform_config(
    conn: sqlite3.Connection,
    platform: str,
    base: dict[str, Any],
    *,
    mode: str,
) -> tuple[dict[str, Any], str]:
    config = dict(base)
    # Recovery cadence is a hard safety envelope. Historical live-mode
    # overrides must not silently raise its frequency.
    if mode == "recovery":
        return config, "recovery_policy"
    try:
        row = conn.execute(
            """
            SELECT target_posts_per_day,minimum_interval_hours,local_slots_json,
                   source_proposal_id
              FROM social_policy_overrides WHERE platform=?
            """,
            (platform,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return config, "bootstrap_policy"
        raise
    if row is None:
        return config, "bootstrap_policy"
    slots = json.loads(row["local_slots_json"])
    target = int(row["target_posts_per_day"])
    interval = float(row["minimum_interval_hours"])
    if target < 0 or target > 4 or interval < 4 or interval > 48:
        raise ValueError(f"invalid runtime cadence bounds for {platform}")
    if not isinstance(slots, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 23
        for value in slots
    ):
        raise ValueError(f"invalid runtime slots for {platform}")
    if len(slots) != target or slots != sorted(set(slots)):
        raise ValueError(f"runtime slot count does not match target for {platform}")
    gaps = [right - left for left, right in zip(slots, slots[1:])]
    if slots:
        gaps.append(24 - slots[-1] + slots[0])
    if any(gap < interval for gap in gaps):
        raise ValueError(f"runtime slots violate minimum spacing for {platform}")
    config.update(
        {
            "target_posts_per_day": target,
            "minimum_interval_hours": interval,
            "local_slots": slots,
        }
    )
    return config, f"proposal:{row['source_proposal_id']}"


def decide_schedule(
    conn: sqlite3.Connection,
    policy: dict[str, Any],
    now: datetime | None = None,
    *,
    mode: str = "live",
) -> ScheduleDecision:
    if mode not in {"live", "recovery"}:
        raise ValueError(f"unsupported automation mode: {mode}")
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

    platform_policy = (
        policy["recovery"]["platforms"]
        if mode == "recovery"
        else policy["platforms"]
    )
    for platform, base_config in platform_policy.items():
        config, policy_source = _effective_platform_config(
            conn, platform, base_config, mode=mode
        )
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
        quality_attempts_today = 0
        if mode == "recovery":
            try:
                attempt_row = conn.execute(
                    """
                    SELECT COUNT(DISTINCT draft_id) AS attempts
                      FROM content_quality_evaluations
                     WHERE platform=? AND stage='compose'
                       AND guard_version LIKE '%.taiwan-daily-%'
                       AND datetime(checked_at) >= datetime(?)
                       AND datetime(checked_at) < datetime(?)
                    """,
                    (platform, start_utc, end_utc),
                ).fetchone()
                quality_attempts_today = int(
                    (attempt_row["attempts"] if attempt_row else 0) or 0
                )
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise
        last = _parse_timestamp(last_value)
        interval = timedelta(hours=float(config["minimum_interval_hours"]))
        slot_ok = _inside_slot(
            local,
            [int(value) for value in config["local_slots"]],
            int(config["slot_tolerance_minutes"]),
        )
        local_days = [int(value) for value in config.get("local_days", range(7))]
        day_ok = local.weekday() in local_days
        post_quota_ok = posts_today < int(config["target_posts_per_day"])
        attempt_quota_ok = mode != "recovery" or quality_attempts_today == 0
        interval_ok = last is None or now_utc - last >= interval
        due = day_ok and slot_ok and post_quota_ok and attempt_quota_ok and interval_ok
        reasons = []
        if not day_ok:
            reasons.append("outside_local_day")
        if not slot_ok:
            reasons.append("outside_local_slot")
        if not post_quota_ok:
            reasons.append("daily_quota_reached")
        if not attempt_quota_ok:
            reasons.append("daily_attempt_quota_reached")
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
                quality_attempts_today=quality_attempts_today,
                last_success=last_value,
                target_posts_per_day=int(config["target_posts_per_day"]),
                minimum_interval_hours=float(config["minimum_interval_hours"]),
                local_slots=[int(value) for value in config["local_slots"]],
                local_days=local_days,
                policy_source=policy_source,
            )
        )

    platforms = [decision.platform for decision in decisions if decision.due]
    return ScheduleDecision(
        evaluated_at=now_utc.isoformat(),
        timezone=tz_name,
        mode=mode,
        dispatch=bool(platforms),
        platforms=platforms,
        platform_decisions=decisions,
    )
