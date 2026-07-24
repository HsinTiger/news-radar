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


FIRE_ID = "meta-recovery-topic-policy-v3"


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
        "source": "owner-approved 2026-07-24 Meta recovery mode",
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
            "robust topic medians plus source-tier and format evidence; avoid "
            "outlier-led ranking; no automatic frequency increase"
        ),
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
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--decided-at", default="2026-07-24T00:00:00+08:00")
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
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
