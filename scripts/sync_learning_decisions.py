#!/usr/bin/env python3
"""Mirror owner decisions from Social Ops and execute approved learning safely.

The service is a decision control plane, while the Release-state SQLite DB and
proposal JSONL files are the durable execution record. Only exact approved
topic-weight and platform-cadence actions are executable here. Publishing is
out of scope.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.reflector import mark_deployed
from src.reflector.platform_policy import (
    DECREASE_RATIO,
    INCREASE_RATIO,
    MIN_NONZERO_RATE,
    effective_cadence,
)
from src.reflector.proposals import read_proposals, update_decision
from src.reflector.topic import (
    GLOBAL_WEIGHT_CEIL,
    GLOBAL_WEIGHT_FLOOR,
    MAX_WEEKLY_DELTA,
)
from src.schedule_policy import load_policy


DEFAULT_DB = Path("data/01_harvest/news_radar.db")
DEFAULT_PROPOSALS_DIR = Path("data/05_reflect/proposals")
DEFAULT_LEASE_FILE = Path(".runtime-state-lease.json")
DEFAULT_POLICY = Path("config/social_automation_policy.json")
WEIGHT_TOLERANCE = 1e-6
FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
REMOTE_TO_LOCAL = {"approved": "approve", "rejected": "reject"}


class DecisionSyncError(RuntimeError):
    """Raised when an owner decision cannot be mirrored or executed safely."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DecisionSyncError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionSyncError(f"{field} is not valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise DecisionSyncError(f"{field} must include a timezone")
    return parsed


def validate_local_lease(
    lease_file: Path,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    """Fail closed unless a non-expired state-write lease is present locally."""
    try:
        lease = json.loads(lease_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionSyncError(f"missing or invalid state lease: {exc}") from exc
    owner = lease.get("owner")
    token = lease.get("token")
    if lease.get("schema_version") != 1 or not isinstance(owner, str) or not owner:
        raise DecisionSyncError("state lease identity is invalid")
    if not isinstance(token, str) or len(token) < 16:
        raise DecisionSyncError("state lease token is invalid")
    expires = _parse_timestamp(lease.get("expires_at"), "lease.expires_at")
    current = now or datetime.now(timezone.utc)
    if expires <= current:
        raise DecisionSyncError("state lease expired before learning execution")
    return {"owner": owner, "expires_at": expires.isoformat()}


def fetch_decisions(
    client: httpx.Client,
    *,
    api_url: str,
    token: str,
) -> list[dict[str, Any]]:
    response = client.get(
        f"{api_url.rstrip('/')}/api/service/learning-proposals/decisions",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    payload = response.json()
    decisions = payload.get("decisions") if isinstance(payload, dict) else None
    if not isinstance(decisions, list):
        raise DecisionSyncError("decision service returned an invalid payload")
    return decisions


def _proposal_index(proposals_dir: Path) -> dict[str, dict[str, Any]]:
    records = read_proposals(base_dir=proposals_dir)
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        fire_id = record.get("fire_id") if isinstance(record, dict) else None
        if not isinstance(fire_id, str) or not fire_id:
            raise DecisionSyncError("proposal JSONL contains a record without fire_id")
        if fire_id in indexed:
            raise DecisionSyncError(f"duplicate proposal fire_id: {fire_id}")
        indexed[fire_id] = record
    return indexed


def _lineage(db_path: Path, fire_id: str) -> sqlite3.Row:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT fire_id,analyzer,proposal_type,target_config,hsin_decision,
                   hsin_decision_at,deployed_at
              FROM reflector_proposal_lineage WHERE fire_id=?
            """,
            (fire_id,),
        ).fetchone()
    if row is None:
        raise DecisionSyncError(f"proposal {fire_id} exists in JSONL but not lineage")
    return row


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionSyncError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise DecisionSyncError(f"{field} must be finite")
    return result


def _validate_topic_action(
    db_path: Path,
    record: dict[str, Any],
    lineage: sqlite3.Row,
) -> dict[str, Any]:
    fire_id = record["fire_id"]
    action = record.get("action")
    if not isinstance(action, dict):
        raise DecisionSyncError(f"proposal {fire_id} has no action object")
    if record.get("analyzer") != "topic" or record.get("proposal_type") != "adjust_weight":
        raise DecisionSyncError(f"proposal {fire_id} is not a topic-weight action")
    if lineage["analyzer"] != "topic" or lineage["proposal_type"] != "adjust_weight":
        raise DecisionSyncError(f"proposal {fire_id} JSONL/lineage identity mismatch")
    if action.get("target_config") != "topic_weights" or lineage["target_config"] != "topic_weights":
        raise DecisionSyncError(f"proposal {fire_id} target_config mismatch")
    if record.get("boss_attention_required") is not True:
        raise DecisionSyncError(f"proposal {fire_id} did not require owner attention")

    field = action.get("field")
    if not isinstance(field, str) or not FIELD_RE.fullmatch(field):
        raise DecisionSyncError(f"proposal {fire_id} has an invalid topic field")
    current = _number(action.get("current_value"), "action.current_value")
    proposed = _number(action.get("proposed_value"), "action.proposed_value")
    if not (GLOBAL_WEIGHT_FLOOR <= current <= GLOBAL_WEIGHT_CEIL):
        raise DecisionSyncError(f"proposal {fire_id} current value is outside the weight range")
    if not (GLOBAL_WEIGHT_FLOOR <= proposed <= GLOBAL_WEIGHT_CEIL):
        raise DecisionSyncError(f"proposal {fire_id} proposed value is outside the weight range")
    delta = proposed - current
    if abs(delta) <= WEIGHT_TOLERANCE:
        raise DecisionSyncError(f"proposal {fire_id} is a no-op")
    if abs(delta) > MAX_WEEKLY_DELTA + WEIGHT_TOLERANCE:
        raise DecisionSyncError(f"proposal {fire_id} exceeds the weekly delta gate")

    evidence = record.get("evidence")
    metrics = evidence.get("metrics", {}) if isinstance(evidence, dict) else {}
    if isinstance(metrics, dict):
        for key, expected in (
            ("old_weight", current),
            ("new_weight", proposed),
            ("applied_delta", delta),
        ):
            if key in metrics and not math.isclose(
                _number(metrics[key], f"evidence.metrics.{key}"),
                expected,
                abs_tol=WEIGHT_TOLERANCE,
            ):
                raise DecisionSyncError(f"proposal {fire_id} evidence/action mismatch for {key}")

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT weight FROM topic_weights WHERE category_id=?",
            (field,),
        ).fetchone()
    if row is None:
        raise DecisionSyncError(f"proposal {fire_id} references unknown topic {field}")
    observed = float(row[0])
    deployed_at = lineage["deployed_at"]
    expected = proposed if deployed_at else current
    if not math.isclose(observed, expected, abs_tol=WEIGHT_TOLERANCE):
        raise DecisionSyncError(
            f"proposal {fire_id} drift gate failed for {field}: "
            f"expected={expected:.6f} observed={observed:.6f}"
        )
    samples = 0
    if isinstance(metrics, dict) and "total_samples" in metrics:
        raw_samples = metrics["total_samples"]
        if isinstance(raw_samples, bool) or not isinstance(raw_samples, (int, float)):
            raise DecisionSyncError(f"proposal {fire_id} has invalid total_samples")
        samples = max(0, int(raw_samples))
    return {
        "type": "topic_weight",
        "field": field,
        "current": current,
        "proposed": proposed,
        "delta": delta,
        "samples": samples,
    }


def _validate_cadence_value(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DecisionSyncError(f"{field} must be an object")
    target = value.get("target_posts_per_day")
    interval = value.get("minimum_interval_hours")
    slots = value.get("local_slots")
    if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target <= 4:
        raise DecisionSyncError(f"{field}.target_posts_per_day is invalid")
    interval_value = _number(interval, f"{field}.minimum_interval_hours")
    if not 4 <= interval_value <= 48:
        raise DecisionSyncError(f"{field}.minimum_interval_hours is outside 4..48")
    if not isinstance(slots, list) or len(slots) != target:
        raise DecisionSyncError(f"{field}.local_slots must match the daily target")
    if any(
        isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot <= 23
        for slot in slots
    ):
        raise DecisionSyncError(f"{field}.local_slots contains an invalid hour")
    if slots != sorted(set(slots)):
        raise DecisionSyncError(f"{field}.local_slots must be sorted and unique")
    circular_gaps = [right - left for left, right in zip(slots, slots[1:])]
    if slots:
        circular_gaps.append(24 - slots[-1] + slots[0])
    if any(gap < interval_value for gap in circular_gaps):
        raise DecisionSyncError(f"{field}.local_slots violate minimum spacing")
    return {
        "target_posts_per_day": target,
        "minimum_interval_hours": interval_value,
        "local_slots": slots,
    }


def _validate_schedule_action(
    db_path: Path,
    record: dict[str, Any],
    lineage: sqlite3.Row,
    *,
    policy_path: Path = DEFAULT_POLICY,
) -> dict[str, Any]:
    fire_id = record["fire_id"]
    action = record.get("action")
    if not isinstance(action, dict):
        raise DecisionSyncError(f"proposal {fire_id} has no action object")
    if (
        record.get("analyzer") != "platform_policy"
        or record.get("proposal_type") != "adjust_cadence"
        or lineage["analyzer"] != "platform_policy"
        or lineage["proposal_type"] != "adjust_cadence"
    ):
        raise DecisionSyncError(f"proposal {fire_id} is not a cadence action")
    if (
        action.get("target_config") != "social_schedule"
        or lineage["target_config"] != "social_schedule"
    ):
        raise DecisionSyncError(f"proposal {fire_id} target_config mismatch")
    if record.get("boss_attention_required") is not True:
        raise DecisionSyncError(f"proposal {fire_id} did not require owner attention")
    platform = record.get("platform")
    field = action.get("field")
    if platform not in {"facebook", "instagram", "threads"}:
        raise DecisionSyncError(f"proposal {fire_id} has an invalid platform")
    if field != f"{platform}.cadence":
        raise DecisionSyncError(f"proposal {fire_id} cadence identity mismatch")

    current = _validate_cadence_value(action.get("current_value"), "action.current_value")
    proposed = _validate_cadence_value(action.get("proposed_value"), "action.proposed_value")
    target_delta = proposed["target_posts_per_day"] - current["target_posts_per_day"]
    if abs(target_delta) != 1:
        raise DecisionSyncError(f"proposal {fire_id} must change cadence by exactly one post/day")

    evidence = record.get("evidence")
    metrics = evidence.get("metrics") if isinstance(evidence, dict) else None
    if not isinstance(metrics, dict):
        raise DecisionSyncError(f"proposal {fire_id} has no cadence evidence")
    current_metrics = metrics.get("current")
    baseline_metrics = metrics.get("baseline")
    if not isinstance(current_metrics, dict) or not isinstance(baseline_metrics, dict):
        raise DecisionSyncError(f"proposal {fire_id} has malformed cadence windows")
    policy = load_policy(policy_path)
    adaptation = policy["adaptation"]
    minimum_posts = int(adaptation["minimum_posts_per_platform"])
    minimum_coverage = float(adaptation["minimum_metric_coverage"])
    for name, window in (("current", current_metrics), ("baseline", baseline_metrics)):
        posts = _number(window.get("posts"), f"evidence.{name}.posts")
        coverage = _number(
            window.get("metric_coverage"), f"evidence.{name}.metric_coverage"
        )
        nonzero = _number(window.get("nonzero_rate"), f"evidence.{name}.nonzero_rate")
        if posts < minimum_posts:
            raise DecisionSyncError(f"proposal {fire_id} fails the sample gate")
        if coverage < minimum_coverage:
            raise DecisionSyncError(f"proposal {fire_id} fails the metric coverage gate")
        if nonzero < MIN_NONZERO_RATE:
            raise DecisionSyncError(f"proposal {fire_id} fails the nonzero signal gate")
    ratio = _number(metrics.get("score_ratio"), "evidence.score_ratio")
    if target_delta > 0 and ratio < INCREASE_RATIO:
        raise DecisionSyncError(f"proposal {fire_id} fails the increase ratio gate")
    if target_delta < 0 and ratio > DECREASE_RATIO:
        raise DecisionSyncError(f"proposal {fire_id} fails the decrease ratio gate")

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        observed = effective_cadence(conn, policy, platform)
    expected = proposed if lineage["deployed_at"] else current
    if observed != expected:
        raise DecisionSyncError(
            f"proposal {fire_id} cadence drift gate failed for {platform}: "
            f"expected={expected} observed={observed}"
        )
    return {
        "type": "social_schedule",
        "field": field,
        "platform": platform,
        "current": current,
        "proposed": proposed,
        "score_ratio": ratio,
    }


def _apply_topic_action(
    db_path: Path,
    proposals_dir: Path,
    record: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    fire_id = record["fire_id"]
    lineage = _lineage(db_path, fire_id)
    if lineage["hsin_decision"] not in {"approve", "approved"}:
        raise DecisionSyncError(f"proposal {fire_id} is not locally approved")
    if lineage["deployed_at"]:
        mark_deployed(
            fire_id,
            deployed_at=lineage["deployed_at"],
            db_path=db_path,
            base_dir=proposals_dir,
        )
        return {"id": fire_id, "outcome": "already_applied", "field": action["field"]}

    applied_at = _utcnow()
    reason = f"owner_approved:{fire_id}"
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                """
                SELECT l.hsin_decision,l.deployed_at,t.weight
                  FROM reflector_proposal_lineage l
                  JOIN topic_weights t ON t.category_id=?
                 WHERE l.fire_id=?
                """,
                (action["field"], fire_id),
            ).fetchone()
            if current is None or current["hsin_decision"] not in {"approve", "approved"}:
                raise DecisionSyncError(f"proposal {fire_id} approval changed before execution")
            if current["deployed_at"]:
                raise DecisionSyncError(f"proposal {fire_id} deployment raced another worker")
            if not math.isclose(float(current["weight"]), action["current"], abs_tol=WEIGHT_TOLERANCE):
                raise DecisionSyncError(f"proposal {fire_id} drifted before execution")

            changed = conn.execute(
                """
                UPDATE topic_weights
                   SET weight=?,last_updated_at=?,update_reason=?,last_delta=?,
                       sample_count=sample_count+?
                 WHERE category_id=? AND ABS(weight-?)<=?
                """,
                (
                    action["proposed"],
                    applied_at,
                    reason,
                    action["delta"],
                    action["samples"],
                    action["field"],
                    action["current"],
                    WEIGHT_TOLERANCE,
                ),
            )
            if changed.rowcount != 1:
                raise DecisionSyncError(f"proposal {fire_id} compare-and-swap failed")
            conn.execute(
                """
                INSERT INTO topic_weight_history(
                  category_id,recorded_at,weight_before,weight_after,update_reason,
                  delta,samples_in_window,rationale
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    action["field"],
                    applied_at,
                    action["current"],
                    action["proposed"],
                    "owner_approved_proposal",
                    action["delta"],
                    action["samples"],
                    f"Applied exact owner-approved proposal {fire_id}",
                ),
            )
            deployed = conn.execute(
                "UPDATE reflector_proposal_lineage SET deployed_at=? WHERE fire_id=? AND deployed_at IS NULL",
                (applied_at, fire_id),
            )
            if deployed.rowcount != 1:
                raise DecisionSyncError(f"proposal {fire_id} deployment marker compare-and-swap failed")
            readback = conn.execute(
                "SELECT weight FROM topic_weights WHERE category_id=?",
                (action["field"],),
            ).fetchone()
            if readback is None or not math.isclose(
                float(readback["weight"]), action["proposed"], abs_tol=WEIGHT_TOLERANCE
            ):
                raise DecisionSyncError(f"proposal {fire_id} pre-commit readback failed")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    mark_deployed(
        fire_id,
        deployed_at=applied_at,
        db_path=db_path,
        base_dir=proposals_dir,
    )
    with sqlite3.connect(str(db_path)) as conn:
        observed = conn.execute(
            "SELECT weight FROM topic_weights WHERE category_id=?",
            (action["field"],),
        ).fetchone()
    if observed is None or not math.isclose(float(observed[0]), action["proposed"], abs_tol=WEIGHT_TOLERANCE):
        raise DecisionSyncError(f"proposal {fire_id} post-write readback failed")
    return {
        "id": fire_id,
        "outcome": "applied",
        "field": action["field"],
        "before": action["current"],
        "after": action["proposed"],
        "applied_at": applied_at,
    }


def _apply_schedule_action(
    db_path: Path,
    proposals_dir: Path,
    record: dict[str, Any],
    action: dict[str, Any],
    *,
    policy_path: Path = DEFAULT_POLICY,
) -> dict[str, Any]:
    fire_id = record["fire_id"]
    lineage = _lineage(db_path, fire_id)
    if lineage["hsin_decision"] not in {"approve", "approved"}:
        raise DecisionSyncError(f"proposal {fire_id} is not locally approved")
    if lineage["deployed_at"]:
        mark_deployed(
            fire_id,
            deployed_at=lineage["deployed_at"],
            db_path=db_path,
            base_dir=proposals_dir,
        )
        return {"id": fire_id, "outcome": "already_applied", "field": action["field"]}

    policy = load_policy(policy_path)
    applied_at = _utcnow()
    platform = action["platform"]
    before_json = json.dumps(action["current"], ensure_ascii=False, sort_keys=True)
    after_json = json.dumps(action["proposed"], ensure_ascii=False, sort_keys=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            decision = conn.execute(
                "SELECT hsin_decision,deployed_at FROM reflector_proposal_lineage WHERE fire_id=?",
                (fire_id,),
            ).fetchone()
            if decision is None or decision["hsin_decision"] not in {"approve", "approved"}:
                raise DecisionSyncError(f"proposal {fire_id} approval changed before execution")
            if decision["deployed_at"]:
                raise DecisionSyncError(f"proposal {fire_id} deployment raced another worker")
            observed = effective_cadence(conn, policy, platform)
            if observed != action["current"]:
                raise DecisionSyncError(f"proposal {fire_id} cadence drifted before execution")
            proposed = action["proposed"]
            conn.execute(
                """
                INSERT INTO social_policy_overrides(
                  platform,target_posts_per_day,minimum_interval_hours,
                  local_slots_json,source_proposal_id,updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(platform) DO UPDATE SET
                  target_posts_per_day=excluded.target_posts_per_day,
                  minimum_interval_hours=excluded.minimum_interval_hours,
                  local_slots_json=excluded.local_slots_json,
                  source_proposal_id=excluded.source_proposal_id,
                  updated_at=excluded.updated_at
                """,
                (
                    platform,
                    proposed["target_posts_per_day"],
                    proposed["minimum_interval_hours"],
                    json.dumps(proposed["local_slots"]),
                    fire_id,
                    applied_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO social_policy_history(
                  platform,recorded_at,cadence_before_json,cadence_after_json,
                  source_proposal_id,rationale
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    platform,
                    applied_at,
                    before_json,
                    after_json,
                    fire_id,
                    f"Owner-approved cadence; score_ratio={action['score_ratio']:.6f}",
                ),
            )
            deployed = conn.execute(
                "UPDATE reflector_proposal_lineage SET deployed_at=? WHERE fire_id=? AND deployed_at IS NULL",
                (applied_at, fire_id),
            )
            if deployed.rowcount != 1:
                raise DecisionSyncError(f"proposal {fire_id} deployment marker compare-and-swap failed")
            if effective_cadence(conn, policy, platform) != proposed:
                raise DecisionSyncError(f"proposal {fire_id} pre-commit cadence readback failed")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    mark_deployed(
        fire_id,
        deployed_at=applied_at,
        db_path=db_path,
        base_dir=proposals_dir,
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        observed = effective_cadence(conn, policy, platform)
    if observed != action["proposed"]:
        raise DecisionSyncError(f"proposal {fire_id} post-write cadence readback failed")
    return {
        "id": fire_id,
        "outcome": "applied",
        "field": action["field"],
        "before": action["current"],
        "after": action["proposed"],
        "applied_at": applied_at,
    }


def process_decisions(
    db_path: Path,
    proposals_dir: Path,
    decisions: Sequence[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate the whole batch, then mirror and execute it deterministically."""
    proposal_index = _proposal_index(proposals_dir)
    prepared: list[tuple[dict[str, Any], dict[str, Any], sqlite3.Row, dict[str, Any] | None]] = []
    seen_ids: set[str] = set()
    pending_fields: dict[str, str] = {}

    for decision in decisions:
        fire_id = decision.get("id") if isinstance(decision, dict) else None
        status = decision.get("status") if isinstance(decision, dict) else None
        if not isinstance(fire_id, str) or not fire_id or status not in REMOTE_TO_LOCAL:
            raise DecisionSyncError("decision payload contains an invalid id or status")
        if fire_id in seen_ids:
            raise DecisionSyncError(f"duplicate remote decision id: {fire_id}")
        seen_ids.add(fire_id)
        _parse_timestamp(decision.get("decided_at"), f"decision {fire_id}.decided_at")
        record = proposal_index.get(fire_id)
        if record is None:
            raise DecisionSyncError(f"remote decision references unknown proposal {fire_id}")
        lineage = _lineage(db_path, fire_id)
        local = lineage["hsin_decision"]
        expected_local = REMOTE_TO_LOCAL[status]
        normalized_local = {"approved": "approve", "rejected": "reject"}.get(local, local)
        if normalized_local not in {None, expected_local}:
            raise DecisionSyncError(f"proposal {fire_id} has a conflicting local decision")
        record_local = record.get("hsin_decision")
        normalized_record = {
            "approved": "approve",
            "rejected": "reject",
        }.get(record_local, record_local)
        if normalized_record not in {None, expected_local}:
            raise DecisionSyncError(f"proposal {fire_id} has a conflicting JSONL decision")
        if status == "rejected" and lineage["deployed_at"]:
            raise DecisionSyncError(f"proposal {fire_id} was rejected after deployment")

        action: dict[str, Any] | None = None
        action_record = record.get("action")
        target_config = (
            action_record.get("target_config")
            if isinstance(action_record, dict)
            else None
        )
        if status == "approved" and target_config == "topic_weights":
            action = _validate_topic_action(db_path, record, lineage)
        elif status == "approved" and target_config == "social_schedule":
            action = _validate_schedule_action(db_path, record, lineage)
        if action is not None:
            if not lineage["deployed_at"]:
                previous = pending_fields.setdefault(action["field"], fire_id)
                if previous != fire_id:
                    raise DecisionSyncError(
                        f"approved proposals {previous} and {fire_id} target the same field"
                    )
        prepared.append((decision, record, lineage, action))

    outcomes: list[dict[str, Any]] = []
    for decision, record, lineage, action in prepared:
        fire_id = record["fire_id"]
        status = decision["status"]
        if dry_run:
            outcomes.append(
                {
                    "id": fire_id,
                    "outcome": "would_apply" if action else f"would_mirror_{status}",
                }
            )
            continue

        local_decision = REMOTE_TO_LOCAL[status]
        record_decision = record.get("hsin_decision")
        remote_comment = decision.get("decision_comment") or None
        needs_mirror = (
            lineage["hsin_decision"] not in {local_decision, status}
            or record_decision not in {local_decision, status}
            or lineage["hsin_decision_at"] != decision["decided_at"]
            or record.get("hsin_decision_at") != decision["decided_at"]
            or record.get("hsin_decision_comment") != remote_comment
        )
        if needs_mirror:
            update_decision(
                fire_id,
                local_decision,
                remote_comment,
                decision_at=decision["decided_at"],
                db_path=db_path,
                base_dir=proposals_dir,
            )
        if status == "rejected":
            outcomes.append({"id": fire_id, "outcome": "rejected_mirrored"})
        elif action is None:
            outcomes.append({"id": fire_id, "outcome": "approved_unsupported_pending"})
        elif action["type"] == "topic_weight":
            outcomes.append(_apply_topic_action(db_path, proposals_dir, record, action))
        elif action["type"] == "social_schedule":
            outcomes.append(_apply_schedule_action(db_path, proposals_dir, record, action))
        else:  # pragma: no cover - validated action types are exhaustive
            raise DecisionSyncError(f"proposal {fire_id} has an unsupported action type")

    counts: dict[str, int] = {}
    for row in outcomes:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    return {"ok": True, "dry_run": dry_run, "counts": counts, "outcomes": outcomes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--proposals-dir", type=Path, default=DEFAULT_PROPOSALS_DIR)
    parser.add_argument("--lease-file", type=Path, default=DEFAULT_LEASE_FILE)
    parser.add_argument("--api-url", default=os.environ.get("SOCIAL_OPS_API_URL", ""))
    parser.add_argument("--token", default=os.environ.get("SOCIAL_OPS_SERVICE_TOKEN", ""))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        if not args.api_url or not args.token:
            raise DecisionSyncError("SOCIAL_OPS_API_URL and SOCIAL_OPS_SERVICE_TOKEN are required")
        lease = None if args.dry_run else validate_local_lease(args.lease_file)
        with httpx.Client(timeout=45) as client:
            decisions = fetch_decisions(
                client,
                api_url=args.api_url,
                token=args.token,
            )
        result = process_decisions(
            args.db,
            args.proposals_dir,
            decisions,
            dry_run=args.dry_run,
        )
        result["decision_count"] = len(decisions)
        if lease:
            result["lease"] = lease
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        DecisionSyncError,
        httpx.HTTPError,
        sqlite3.Error,
        OSError,
        LookupError,
        ValueError,
    ) as exc:
        print(f"[learning-decisions] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
