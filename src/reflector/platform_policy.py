"""Platform-specific cadence learning with owner-gated execution.

This analyzer compares two adjacent evidence windows independently for
Facebook, Instagram, and Threads. It may write an exact cadence proposal, but
it never changes runtime scheduling policy itself.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from src.reflector.proposals import PROPOSALS_DIR, read_proposals, write_proposal
from src.schedule_policy import load_policy


PLATFORMS = ("facebook", "instagram", "threads")
INCREASE_RATIO = 1.25
DECREASE_RATIO = 0.75
MIN_NONZERO_RATE = 0.50


@dataclass(frozen=True)
class WindowStats:
    posts: int
    valid_metrics: int
    metric_coverage: float
    nonzero_posts: int
    nonzero_rate: float
    median_action_score: float


@dataclass(frozen=True)
class PlatformReview:
    platform: str
    status: str
    reason: str
    current: WindowStats
    baseline: WindowStats
    score_ratio: Optional[float]
    proposal_id: Optional[str] = None


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_has_error(value: str | None) -> bool:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return True

    def visit(item: Any) -> bool:
        if isinstance(item, dict):
            if item.get("error"):
                return True
            return any(visit(child) for child in item.values())
        if isinstance(item, list):
            return any(visit(child) for child in item)
        return False

    return visit(payload)


def action_score(platform: str, row: sqlite3.Row) -> float:
    if platform == "facebook":
        return (
            float(row["likes"] or 0)
            + 2 * float(row["comments"] or 0)
            + 3 * float(row["shares"] or 0)
            + 0.25 * float(row["clicks"] or 0)
        )
    if platform == "instagram":
        return (
            float(row["likes"] or 0)
            + 2 * float(row["comments"] or 0)
            + 3 * float(row["shares"] or 0)
            + 1.5 * float(row["saves"] or 0)
            + 0.01 * float(row["reach"] or 0)
        )
    if platform == "threads":
        return (
            float(row["likes"] or 0)
            + 2 * float(row["replies"] or 0)
            + 3 * float(row["reposts"] or 0)
            + 1.5 * float(row["quotes"] or 0)
            + 0.005 * float(row["views"] or 0)
        )
    raise ValueError(f"unsupported platform: {platform}")


def _window(rows: list[sqlite3.Row], start: datetime, end: datetime) -> WindowStats:
    selected = [
        row for row in rows
        if start <= _parse_timestamp(row["posted_at"]) < end
    ]
    valid = [
        row for row in selected
        if row["fetched_at"] and not _json_has_error(row["raw_json"])
    ]
    scores = [action_score(row["platform"], row) for row in valid]
    nonzero = sum(score > 0 for score in scores)
    total = len(selected)
    valid_count = len(valid)
    return WindowStats(
        posts=total,
        valid_metrics=valid_count,
        metric_coverage=round(valid_count / total, 6) if total else 0.0,
        nonzero_posts=nonzero,
        nonzero_rate=round(nonzero / valid_count, 6) if valid_count else 0.0,
        median_action_score=round(float(statistics.median(scores)), 6) if scores else 0.0,
    )


def collect_stats(
    conn: sqlite3.Connection,
    platform: str,
    *,
    now: datetime,
    window_days: int,
) -> tuple[WindowStats, WindowStats]:
    earliest = (now - timedelta(days=window_days * 2)).isoformat()
    rows = conn.execute(
        """
        WITH published AS (
          SELECT *,ROW_NUMBER() OVER(
            PARTITION BY platform,platform_post_id ORDER BY id DESC
          ) AS rn
          FROM publish_log
          WHERE platform=? AND success=1 AND platform_post_id IS NOT NULL
            AND datetime(posted_at)>=datetime(?)
        ), latest_metric AS (
          SELECT *,ROW_NUMBER() OVER(
            PARTITION BY platform,platform_post_id ORDER BY fetched_at DESC
          ) AS rn
          FROM engagement_stats WHERE platform=?
        )
        SELECT p.platform,p.platform_post_id,p.posted_at,
               e.fetched_at,e.raw_json,e.views,e.reach,e.clicks,e.likes,
               e.comments,e.shares,e.saves,e.replies,e.reposts,e.quotes
          FROM published p
          LEFT JOIN latest_metric e
            ON e.platform=p.platform AND e.platform_post_id=p.platform_post_id
           AND e.rn=1
         WHERE p.rn=1
        """,
        (platform, earliest, platform),
    ).fetchall()
    current_start = now - timedelta(days=window_days)
    baseline_start = now - timedelta(days=window_days * 2)
    return (
        _window(rows, current_start, now),
        _window(rows, baseline_start, current_start),
    )


def cadence_for_target(target: int) -> dict[str, Any]:
    if target < 0 or target > 4:
        raise ValueError("target cadence must be between 0 and 4")
    if target == 0:
        return {
            "target_posts_per_day": 0,
            "minimum_interval_hours": 48.0,
            "local_slots": [],
        }
    if target == 1:
        return {
            "target_posts_per_day": 1,
            "minimum_interval_hours": 20.0,
            "local_slots": [20],
        }
    slots = [round(8 + index * 12 / (target - 1)) for index in range(target)]
    minimum_interval = max(4.0, math.floor(12 / (target - 1)))
    return {
        "target_posts_per_day": target,
        "minimum_interval_hours": minimum_interval,
        "local_slots": slots,
    }


def effective_cadence(
    conn: sqlite3.Connection,
    policy: dict[str, Any],
    platform: str,
) -> dict[str, Any]:
    base = policy["platforms"][platform]
    cadence = {
        "target_posts_per_day": int(base["target_posts_per_day"]),
        "minimum_interval_hours": float(base["minimum_interval_hours"]),
        "local_slots": [int(value) for value in base["local_slots"]],
    }
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "social_policy_overrides" not in tables:
        return cadence
    row = conn.execute(
        """
        SELECT target_posts_per_day,minimum_interval_hours,local_slots_json
          FROM social_policy_overrides WHERE platform=?
        """,
        (platform,),
    ).fetchone()
    if row is None:
        return cadence
    return {
        "target_posts_per_day": int(row["target_posts_per_day"]),
        "minimum_interval_hours": float(row["minimum_interval_hours"]),
        "local_slots": [int(value) for value in json.loads(row["local_slots_json"])],
    }


def recommend_target(
    current: WindowStats,
    baseline: WindowStats,
    *,
    current_target: int,
    minimum_posts: int,
    minimum_coverage: float,
    floor: int,
    ceiling: int,
) -> tuple[str, str, Optional[int], Optional[float]]:
    if current.posts < minimum_posts or baseline.posts < minimum_posts:
        return "insufficient", "insufficient_posts", None, None
    if (
        current.metric_coverage < minimum_coverage
        or baseline.metric_coverage < minimum_coverage
    ):
        return "insufficient", "insufficient_metric_coverage", None, None
    if (
        current.nonzero_rate < MIN_NONZERO_RATE
        or baseline.nonzero_rate < MIN_NONZERO_RATE
        or current.median_action_score <= 0
        or baseline.median_action_score <= 0
    ):
        return "insufficient", "insufficient_nonzero_signal", None, None
    ratio = current.median_action_score / baseline.median_action_score
    if ratio >= INCREASE_RATIO and current_target < ceiling:
        return "propose", "performance_up", current_target + 1, ratio
    if ratio <= DECREASE_RATIO and current_target > floor:
        return "propose", "performance_down", current_target - 1, ratio
    return "stable", "within_stability_band", None, ratio


def _pending_fields(proposals_dir: Path) -> set[str]:
    pending: set[str] = set()
    for proposal in read_proposals(base_dir=proposals_dir):
        action = proposal.get("action")
        if (
            isinstance(action, dict)
            and action.get("target_config") == "social_schedule"
            and not proposal.get("hsin_decision")
            and not proposal.get("deployed_at")
        ):
            field = action.get("field")
            if isinstance(field, str):
                pending.add(field)
    return pending


def run_review(
    conn: sqlite3.Connection,
    policy: dict[str, Any],
    *,
    now: datetime,
    proposals_db_path: Path,
    proposals_dir: Path,
    write_proposals: bool = True,
) -> list[PlatformReview]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    adaptation = policy["adaptation"]
    window_days = int(adaptation["evaluation_window_days"])
    minimum_posts = int(adaptation["minimum_posts_per_platform"])
    minimum_coverage = float(adaptation["minimum_metric_coverage"])
    floor = int(adaptation["frequency_floor_per_day"])
    ceiling = int(adaptation["frequency_ceiling_per_day"])
    pending = _pending_fields(proposals_dir)
    reviews: list[PlatformReview] = []

    for platform in PLATFORMS:
        current, baseline = collect_stats(
            conn, platform, now=now, window_days=window_days
        )
        cadence = effective_cadence(conn, policy, platform)
        status, reason, target, ratio = recommend_target(
            current,
            baseline,
            current_target=int(cadence["target_posts_per_day"]),
            minimum_posts=minimum_posts,
            minimum_coverage=minimum_coverage,
            floor=floor,
            ceiling=ceiling,
        )
        proposal_id: Optional[str] = None
        field = f"{platform}.cadence"
        if status == "propose" and target is not None:
            if field in pending:
                status = "pending"
                reason = "existing_owner_decision_pending"
            elif write_proposals:
                proposed = cadence_for_target(target)
                confidence = (
                    "HIGH"
                    if min(current.valid_metrics, baseline.valid_metrics) >= minimum_posts * 2
                    else "MED"
                )
                proposal_id = write_proposal(
                    {
                        "analyzer": "platform_policy",
                        "platform": platform,
                        "proposal_type": "adjust_cadence",
                        "evidence": {
                            "sample_ids": [],
                            "metrics": {
                                "window_days": window_days,
                                "current": asdict(current),
                                "baseline": asdict(baseline),
                                "score_ratio": round(float(ratio), 6),
                                "increase_ratio_gate": INCREASE_RATIO,
                                "decrease_ratio_gate": DECREASE_RATIO,
                                "minimum_nonzero_rate": MIN_NONZERO_RATE,
                            },
                            "confidence": confidence,
                        },
                        "action": {
                            "target_config": "social_schedule",
                            "field": field,
                            "current_value": cadence,
                            "proposed_value": proposed,
                        },
                        "boss_attention_required": True,
                    },
                    db_path=proposals_db_path,
                    base_dir=proposals_dir,
                )
        reviews.append(
            PlatformReview(
                platform=platform,
                status=status,
                reason=reason,
                current=current,
                baseline=baseline,
                score_ratio=round(float(ratio), 6) if ratio is not None else None,
                proposal_id=proposal_id,
            )
        )
    return reviews


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path, default=Path("data/01_harvest/news_radar.db")
    )
    parser.add_argument(
        "--policy", type=Path, default=Path("config/social_automation_policy.json")
    )
    parser.add_argument("--proposals-dir", type=Path, default=PROPOSALS_DIR)
    parser.add_argument("--now", help="ISO-8601 replay time")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from src import db as dbmod

    if args.db.resolve() == Path(dbmod.DB_PATH).resolve():
        dbmod.init_db()
        conn = dbmod.get_conn()
    else:
        conn = sqlite3.connect(str(args.db))
        conn.row_factory = sqlite3.Row
    try:
        reviews = run_review(
            conn,
            load_policy(args.policy),
            now=_parse_timestamp(args.now) if args.now else datetime.now(timezone.utc),
            proposals_db_path=args.db,
            proposals_dir=args.proposals_dir,
            write_proposals=not args.dry_run,
        )
    finally:
        conn.close()
    print(
        json.dumps(
            {"ok": True, "dry_run": args.dry_run, "reviews": [asdict(row) for row in reviews]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
