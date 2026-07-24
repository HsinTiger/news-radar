#!/usr/bin/env python3
"""Record the owner-approved Taiwan daily commute schedule.

The executable schedule lives in ``config/social_automation_policy.json``.
This script persists the exact before/after decision and its evidence limits;
it never publishes content by itself.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.reflector.proposals import PROPOSALS_DIR, write_proposal
from src.schedule_policy import load_policy


FIRE_ID = "meta-recovery-taiwan-daily-schedule-v2"
CURRENT = {
    "threads": {
        "target_posts_per_day": 1,
        "minimum_interval_hours": 20.0,
        "local_slots": [16],
        "local_days": [0, 1, 2, 3, 4, 5, 6],
    },
    "facebook": {
        "target_posts_per_day": 1,
        "minimum_interval_hours": 60.0,
        "local_slots": [20],
        "local_days": [1, 4],
    },
    "instagram": {
        "target_posts_per_day": 1,
        "minimum_interval_hours": 60.0,
        "local_slots": [20],
        "local_days": [2, 5],
    },
}
EXPECTED_SLOTS = {"threads": [8], "facebook": [18], "instagram": [20]}


def _cadence(platform_cfg: dict) -> dict:
    return {
        "target_posts_per_day": int(platform_cfg["target_posts_per_day"]),
        "minimum_interval_hours": float(platform_cfg["minimum_interval_hours"]),
        "local_slots": [int(value) for value in platform_cfg["local_slots"]],
        "local_days": [int(value) for value in platform_cfg["local_days"]],
    }


def activate(
    db_path: Path,
    proposals_dir: Path,
    policy: dict,
    *,
    apply: bool,
    decided_at: str,
) -> dict:
    proposed = {
        platform: _cadence(policy["recovery"]["platforms"][platform])
        for platform in ("threads", "facebook", "instagram")
    }
    for platform, expected_slots in EXPECTED_SLOTS.items():
        cfg = proposed[platform]
        if (
            cfg["target_posts_per_day"] != 1
            or cfg["minimum_interval_hours"] != 20.0
            or cfg["local_slots"] != expected_slots
            or cfg["local_days"] != list(range(7))
        ):
            raise ValueError(
                f"{platform} Taiwan daily activation expects 1/day, 20h spacing, "
                f"slot={expected_slots}, all local days"
            )

    result = {
        "fire_id": FIRE_ID,
        "apply": apply,
        "decided_at": decided_at,
        "current": CURRENT,
        "proposed": proposed,
    }
    with sqlite3.connect(str(db_path)) as conn:
        existing = conn.execute(
            "SELECT deployed_at FROM reflector_proposal_lineage WHERE fire_id=?",
            (FIRE_ID,),
        ).fetchone()
    if existing:
        result["already_applied"] = bool(existing[0])
        result["applied_at"] = existing[0]
        return result
    if not apply:
        return result

    applied_at = datetime.now(timezone.utc).isoformat()
    write_proposal(
        {
            "fire_id": FIRE_ID,
            "fire_at": decided_at,
            "analyzer": "platform_policy",
            "platform": "all",
            "proposal_type": "adjust_cadence",
            "evidence": {
                "sample_ids": [],
                "metrics": {
                    "owner_requirement": "one post per platform per day",
                    "threads_legacy_metric_coverage": "usable_but_high_cadence_confounded",
                    "facebook_legacy_metric_status": "126_of_127_error_markers",
                    "instagram_legacy_metric_status": "median_reach_zero",
                    "timing_causality": "ASSUMED; platform slots are bounded commute-window experiments",
                    "exit_gate": "7 posts per platform with complete 168h evidence",
                },
                "confidence": "LOW",
            },
            "action": {
                "target_config": "social_schedule",
                "field": "recovery.taiwan_daily_cadence",
                "current_value": CURRENT,
                "proposed_value": proposed,
            },
            "boss_attention_required": True,
            "hsin_decision": "approve",
            "hsin_decision_at": decided_at,
            "hsin_decision_comment": (
                "Owner requires one accurate Taiwan public-interest post per platform daily; "
                "platform timing is delegated as a measured commute-window experiment"
            ),
            "deployed_at": applied_at,
        },
        db_path=db_path,
        base_dir=proposals_dir,
    )
    result["applied_at"] = applied_at
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path, default=Path("data/01_harvest/news_radar.db")
    )
    parser.add_argument(
        "--policy", type=Path, default=Path("config/social_automation_policy.json")
    )
    parser.add_argument("--proposals-dir", type=Path, default=PROPOSALS_DIR)
    parser.add_argument("--decided-at", default="2026-07-24T15:09:14+08:00")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = activate(
        args.db,
        args.proposals_dir,
        load_policy(args.policy),
        apply=args.apply,
        decided_at=args.decided_at,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
