#!/usr/bin/env python3
"""Record the owner-approved Threads Recovery timing experiment.

The executable schedule lives in ``config/social_automation_policy.json``.
This script writes the exact before/after decision into the durable proposal
ledger so the dashboard and future workers can audit why the slot changed.
It does not publish content or increase frequency.
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


FIRE_ID = "meta-recovery-threads-time-v1"
CURRENT = {
    "target_posts_per_day": 1,
    "minimum_interval_hours": 20.0,
    "local_slots": [12],
}


def activate(
    db_path: Path,
    proposals_dir: Path,
    policy: dict,
    *,
    apply: bool,
    decided_at: str,
) -> dict:
    target_cfg = policy["recovery"]["platforms"]["threads"]
    proposed = {
        "target_posts_per_day": int(target_cfg["target_posts_per_day"]),
        "minimum_interval_hours": float(target_cfg["minimum_interval_hours"]),
        "local_slots": [int(value) for value in target_cfg["local_slots"]],
    }
    if proposed["target_posts_per_day"] != 1 or proposed["local_slots"] != [16]:
        raise ValueError("Threads Recovery timing activation expects 1/day at 16:00")

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
            "platform": "threads",
            "proposal_type": "adjust_cadence",
            "evidence": {
                "sample_ids": [],
                "metrics": {
                    "runtime_state_revision": 21,
                    "historical_threads_posts": 104,
                    "target_hour_posts": 5,
                    "target_hour_median_views": 625,
                    "first_recovery_post_1h_views": 6,
                    "first_recovery_post_1h_likes": 2,
                    "frequency_change": 0,
                    "causality": "ASSUMED; timing is a bounded experiment",
                    "exit_gate": "3 Recovery posts with 168h evidence",
                },
                "confidence": "MED",
            },
            "action": {
                "target_config": "social_schedule",
                "field": "threads.recovery_cadence",
                "current_value": CURRENT,
                "proposed_value": proposed,
            },
            "boss_attention_required": True,
            "hsin_decision": "approve",
            "hsin_decision_at": decided_at,
            "hsin_decision_comment": (
                "Owner requested data-oriented Meta optimization deployable within one hour"
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
    parser.add_argument("--decided-at", default="2026-07-24T14:14:00+08:00")
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
