#!/usr/bin/env python3
"""Apply the owner-approved bootstrap topic policy with an audit trail."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schedule_policy import load_policy
from src.reflector.proposals import PROPOSALS_DIR, read_proposals, update_decision


FIRE_ID = "meta-recovery-taiwan-editorial-policy-v4"


def supersede_drifted_topic_proposals(
    db_path: Path,
    proposals_dir: Path,
    *,
    decided_at: str,
) -> list[str]:
    """Retire pre-policy or drifted pending topic proposals."""
    cutoff = datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    cutoff = cutoff.astimezone(timezone.utc)
    with sqlite3.connect(str(db_path)) as conn:
        actual = {
            row[0]: float(row[1])
            for row in conn.execute("SELECT category_id,weight FROM topic_weights")
        }
    superseded: list[str] = []
    for record in read_proposals(base_dir=proposals_dir):
        if record.get("hsin_decision") or record.get("deployed_at"):
            continue
        action = record.get("action")
        if not isinstance(action, dict) or action.get("target_config") != "topic_weights":
            continue
        field = action.get("field")
        current = action.get("current_value")
        if field not in actual or isinstance(current, bool) or not isinstance(
            current, (int, float)
        ):
            continue
        fire_at = datetime.fromisoformat(
            str(record.get("fire_at") or decided_at).replace("Z", "+00:00")
        )
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=timezone.utc)
        older_than_policy = fire_at.astimezone(timezone.utc) < cutoff
        drifted = abs(float(current) - actual[field]) > 1e-9
        if not older_than_policy and not drifted:
            continue
        fire_id = str(record["fire_id"])
        update_decision(
            fire_id,
            "amend",
            f"Superseded by {FIRE_ID}; current {field}={actual[field]:.6f}",
            decision_at=decided_at,
            db_path=db_path,
            base_dir=proposals_dir,
        )
        superseded.append(fire_id)
    return superseded


def apply_policy(
    conn: sqlite3.Connection,
    policy: dict,
    *,
    apply: bool,
    decided_at: str,
) -> dict:
    requested = policy["initial_topic_weights"]
    rows = {
        row["category_id"]: row
        for row in conn.execute(
            "SELECT category_id, weight, sample_count FROM topic_weights"
        ).fetchall()
    }
    missing = sorted(set(requested) - set(rows))
    if missing:
        raise ValueError(f"policy references unknown topic categories: {missing}")
    changes = []
    for category, target in requested.items():
        before = float(rows[category]["weight"])
        after = float(target)
        if abs(before - after) < 1e-9:
            continue
        changes.append(
            {
                "category": category,
                "before": before,
                "after": after,
                "delta": round(after - before, 6),
                "samples": int(rows[category]["sample_count"] or 0),
            }
        )
    result = {
        "fire_id": FIRE_ID,
        "apply": apply,
        "decided_at": decided_at,
        "changes": changes,
    }
    if not apply or not changes:
        return result

    existing = conn.execute(
        "SELECT deployed_at FROM reflector_proposal_lineage WHERE fire_id=?",
        (FIRE_ID,),
    ).fetchone()
    if existing and existing["deployed_at"]:
        result["already_applied"] = True
        return result

    evidence = {
        "source": "owner-approved 2026-07-24 Taiwan daily editorial objective",
        "captured_at": "2026-07-24T03:45:58.359620+00:00",
        "runtime_state_revision": 20,
        "threads_posts": 103,
        "threads_median_views": 277.0,
        "threads_median_actions": 0.0,
        "threads_nonzero_action_posts": 48,
        "threads_robust_topic_medians": {
            "earnings": {"posts": 18, "median_views": 376.0},
            "supply_chain": {"posts": 18, "median_views": 337.0},
            "current_affairs": {"posts": 7, "median_views": 260.0},
            "tech_product_launch": {"posts": 9, "median_views": 206.0},
        },
        "threads_source_tier_medians": {
            "primary": {"posts": 29, "median_views": 379.0},
            "secondary": {"posts": 74, "median_views": 256.0},
        },
        "threads_format_medians": {
            "carousel": {"posts": 94, "median_views": 287.5},
            "feed": {"posts": 9, "median_views": 121.0},
        },
        "facebook_metric_status": "degraded_126_of_127_error_markers",
        "instagram_metric_status": "low_signal_carousel_experiment_required",
        "method": (
            "owner-defined Taiwan public-interest scope plus robust topic medians, "
            "official-source priority, and fail-closed off-scope selection"
        ),
        "owner_editorial_scope": [
            "politics_and_government_accountability",
            "food_safety_and_consumer_protection",
            "national_policy",
            "markets_and_economy",
        ],
    }
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for change in changes:
            conn.execute(
                """
                UPDATE topic_weights
                   SET weight=?, last_updated_at=?, update_reason=?, last_delta=?
                 WHERE category_id=?
                """,
                (
                    change["after"],
                    now,
                    FIRE_ID,
                    change["delta"],
                    change["category"],
                ),
            )
            conn.execute(
                """
                INSERT INTO topic_weight_history(
                    category_id, recorded_at, weight_before, weight_after,
                    update_reason, delta, samples_in_window, rationale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change["category"],
                    now,
                    change["before"],
                    change["after"],
                    FIRE_ID,
                    change["delta"],
                    change["samples"],
                    evidence["method"],
                ),
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO reflector_proposal_lineage(
                fire_id, fire_at, analyzer, proposal_type, target_config,
                hsin_decision, hsin_decision_at, deployed_at, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                FIRE_ID,
                decided_at,
                "bootstrap_social_policy",
                "set_initial_topic_weights",
                "topic_weights",
                "approved",
                decided_at,
                now,
                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.execute(
            """
            INSERT INTO reflection_events(
                ran_at, signals_summary, samples_used, soul_version_before,
                soul_version_after, patch_markdown, rules_added_json,
                rationale, input_tokens, output_tokens, cost_usd, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?)
            """,
            (
                now,
                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                103,
                "unchanged",
                "unchanged",
                json.dumps(changes, ensure_ascii=False, sort_keys=True),
                "[]",
                "Owner-approved conservative bootstrap after runtime incident.",
                "applied_owner_approved",
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    result["applied_at"] = now
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/01_harvest/news_radar.db"))
    parser.add_argument(
        "--policy", type=Path, default=Path("config/social_automation_policy.json")
    )
    parser.add_argument("--proposals-dir", type=Path, default=PROPOSALS_DIR)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--decided-at", default="2026-07-24T15:09:14+08:00")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        result = apply_policy(
            conn,
            load_policy(args.policy),
            apply=args.apply,
            decided_at=args.decided_at,
        )
    finally:
        conn.close()
    result["superseded_proposals"] = (
        supersede_drifted_topic_proposals(
            args.db,
            args.proposals_dir,
            decided_at=args.decided_at,
        )
        if args.apply
        else []
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
