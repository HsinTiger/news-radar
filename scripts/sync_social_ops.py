#!/usr/bin/env python3
"""Sync public operational metadata from the runtime DB to Social Ops D1.

Only post identifiers, metrics, source metadata, and governed proposals cross
this boundary. Article bodies, credentials, and raw API payloads do not.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.content_quality_guard import QUALITY_GUARD_VERSION


PLATFORMS = {"facebook", "instagram", "threads"}
CONTROL_SUBMISSION_PREFIX = "control_submission:"
CONTROL_ROUTE_PREFIX = "control_route:"
CONTROL_SOURCE_URL_PREFIX = "control_source_url:"
DEFAULT_PROPOSALS_DIR = Path("data/05_reflect/proposals")
SOCIAL_POLICY_PATH = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "social_automation_policy.json"
)


def _json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


def _has_error(value: Any) -> bool:
    if isinstance(value, dict):
        if "error" in value and value["error"]:
            return True
        return any(_has_error(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_error(item) for item in value)
    return False


def _error_summary(value: Any) -> dict[str, Any]:
    collector = value.get("_collector") if isinstance(value, dict) else None
    collector_summary = {}
    if isinstance(collector, dict):
        collector_summary = {
            "scheduled_bucket_hours": collector.get("scheduled_bucket_hours"),
            "actual_post_age_hours": collector.get("actual_post_age_hours"),
            "late_by_hours": collector.get("late_by_hours"),
        }
    if not isinstance(value, dict):
        return {"legacy": True, **collector_summary}
    for candidate in (value.get("error"), value.get("insights", {}).get("error") if isinstance(value.get("insights"), dict) else None):
        if isinstance(candidate, dict):
            return {
                "legacy": True,
                "api_error": True,
                "code": candidate.get("code"),
                "message": str(candidate.get("message") or "")[:300],
                **collector_summary,
            }
    return {
        "legacy": True,
        "api_error": _has_error(value),
        **collector_summary,
    }


def _stable_id(prefix: str, *values: object) -> str:
    raw = "|".join(str(value or "") for value in values)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def _tag_values(raw_tags: str | None, prefix: str) -> list[str]:
    tags = _json(raw_tags, [])
    if not isinstance(tags, list):
        return []
    return [
        tag[len(prefix) :]
        for tag in tags
        if isinstance(tag, str) and tag.startswith(prefix) and tag[len(prefix) :]
    ]


def _submission_for_platform(raw_tags: str | None, platform: str) -> str | None:
    aliases = {"facebook": "fb", "instagram": "ig", "threads": "threads"}
    short = aliases.get(platform, platform)
    for route in _tag_values(raw_tags, CONTROL_ROUTE_PREFIX):
        if ":" not in route:
            continue
        submission_id, raw_platforms = route.split(":", 1)
        if short in raw_platforms.split(","):
            return submission_id
    ids = _tag_values(raw_tags, CONTROL_SUBMISSION_PREFIX)
    return ids[0] if ids else None


def build_posts(conn: sqlite3.Connection, *, full: bool = False) -> list[dict[str, Any]]:
    where = "" if full else "WHERE COALESCE(p.posted_at,'') >= datetime('now','-45 day')"
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    format_select = (
        "rx.actual_format AS actual_format"
        if "recovery_experiments" in tables
        else "NULL AS actual_format"
    )
    format_join = (
        "LEFT JOIN recovery_experiments rx "
        "ON rx.draft_id=p.draft_id AND rx.platform=p.platform"
        if "recovery_experiments" in tables
        else ""
    )
    rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT p.*,ROW_NUMBER() OVER(
            PARTITION BY p.platform,p.draft_id
            ORDER BY p.id DESC
          ) AS rn
          FROM publish_log p {where}
        )
        SELECT p.id,p.draft_id,p.platform,p.platform_post_id,p.posted_at,p.success,
               p.error_message,d.title,n.topic_category,n.url,n.tags,d.generated_at,
               {format_select}
        FROM ranked p
        LEFT JOIN drafts d ON d.id=p.draft_id
        LEFT JOIN news_items n ON n.id=d.news_id
        {format_join}
        WHERE p.rn=1 AND p.platform IN ('facebook','instagram','threads')
        ORDER BY COALESCE(p.posted_at,d.generated_at) ASC
        """
    ).fetchall()
    result = []
    for row in rows:
        source_urls = _tag_values(row["tags"], CONTROL_SOURCE_URL_PREFIX)
        result.append(
            {
                # Stable across failed -> published retries.  Using post_id on
                # success and draft_id on failure created two D1 rows for one
                # logical platform publication and polluted dashboard counts.
                "id": f"post_{row['draft_id']}_{row['platform']}_feed",
                "draft_id": row["draft_id"],
                "submission_id": _submission_for_platform(
                    row["tags"], row["platform"]
                ),
                "platform": row["platform"],
                "format": row["actual_format"] or "feed",
                "platform_post_id": row["platform_post_id"] or None,
                "status": "published" if row["success"] else "failed",
                "title": row["title"] or None,
                "topic": row["topic_category"] or None,
                "source_url": source_urls[0] if source_urls else (row["url"] or None),
                "posted_at": row["posted_at"] or None,
                "created_at": row["generated_at"] or row["posted_at"] or datetime.now(timezone.utc).isoformat(),
                "updated_at": row["posted_at"] or row["generated_at"] or datetime.now(timezone.utc).isoformat(),
            }
        )
    return result


def build_engagement(conn: sqlite3.Connection, *, full: bool = False) -> list[dict[str, Any]]:
    where = "" if full else "AND COALESCE(fetched_at,'') >= datetime('now','-45 day')"
    columns = {row[1] for row in conn.execute("PRAGMA table_info(engagement_stats)")}
    # Old Release-state snapshots predate the Facebook clicks migration.  Zero
    # is the only truthful degraded value; never fail the entire metadata sync.
    clicks_sql = "clicks" if "clicks" in columns else "0 AS clicks"
    rows = conn.execute(
        f"""
        SELECT platform,platform_post_id,fetched_at,post_age_bucket,views,reach,
               {clicks_sql},likes,comments,shares,saves,replies,reposts,
               quotes,raw_json
        FROM engagement_stats
        WHERE platform IN ('facebook','instagram','threads')
          AND platform_post_id IS NOT NULL {where}
        ORDER BY fetched_at ASC
        """
    ).fetchall()
    result = []
    for row in rows:
        raw = _json(row["raw_json"], {})
        result.append(
            {
                "platform": row["platform"],
                "platform_post_id": row["platform_post_id"],
                "captured_at": row["fetched_at"],
                "post_age_hours": row["post_age_bucket"],
                "views": row["views"] or 0,
                "reach": row["reach"] or 0,
                "clicks": row["clicks"] or 0,
                "likes": row["likes"] or 0,
                "comments": row["comments"] or 0,
                "shares": row["shares"] or 0,
                "saves": row["saves"] or 0,
                "replies": row["replies"] or 0,
                "reposts": row["reposts"] or 0,
                "quotes": row["quotes"] or 0,
                "metric_status": "degraded" if _has_error(raw) else "ok",
                "raw_summary": _error_summary(raw),
            }
        )
    return result


def build_quality(conn: sqlite3.Connection, *, full: bool = False) -> list[dict[str, Any]]:
    """Aggregate the current guard cohort without exporting content."""
    now = datetime.now(timezone.utc).isoformat()
    window_days = 0 if full else 45
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "content_quality_evaluations" not in tables:
        return [
            {
                "platform": platform,
                "captured_at": now,
                "window_days": window_days,
                "candidates": 0,
                "evaluated": 0,
                "evidence_coverage": 0.0,
                "pass_count": 0,
                "warn_count": 0,
                "rewrite_count": 0,
                "block_count": 0,
                "publish_ready_count": 0,
                "top_issue_codes": [],
                "guard_version": QUALITY_GUARD_VERSION,
                "legacy_excluded_count": 0,
            }
            for platform in sorted(PLATFORMS)
        ]

    recent = "" if full else "AND datetime(COALESCE(d.generated_at,q.checked_at)) >= datetime('now','-45 day')"
    rows = conn.execute(
        f"""
        SELECT q.*,d.generated_at
          FROM content_quality_evaluations q
          LEFT JOIN drafts d ON d.id=q.draft_id
         WHERE q.platform IN ('facebook','instagram','threads') {recent}
         ORDER BY q.checked_at,q.id
        """
    ).fetchall()
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        latest[(row["draft_id"], row["platform"])] = row

    draft_recent = "" if full else "AND datetime(d.generated_at) >= datetime('now','-45 day')"
    candidate_rows = conn.execute(
        f"""
        SELECT pd.draft_id,pd.platform
          FROM platform_drafts pd JOIN drafts d ON d.id=pd.draft_id
         WHERE pd.platform IN ('facebook','instagram','threads') {draft_recent}
        """
    ).fetchall()
    candidates = {
        platform: {
            (row["draft_id"], row["platform"])
            for row in candidate_rows if row["platform"] == platform
        }
        for platform in PLATFORMS
    }
    for key in latest:
        candidates[key[1]].add(key)

    result: list[dict[str, Any]] = []
    for platform in sorted(PLATFORMS):
        latest_platform_rows = [
            row for (_draft, p), row in latest.items() if p == platform
        ]
        platform_rows = [
            row
            for row in latest_platform_rows
            if row["guard_version"] == QUALITY_GUARD_VERSION
        ]
        legacy_excluded_count = len(latest_platform_rows) - len(platform_rows)
        decisions = Counter(row["decision"] for row in platform_rows)
        codes: Counter[str] = Counter()
        for row in platform_rows:
            codes.update(_json(row["issue_codes_json"], []))
        candidate_count = len(candidates[platform])
        evaluated = len(platform_rows)
        result.append(
            {
                "platform": platform,
                "captured_at": now,
                "window_days": window_days,
                "candidates": candidate_count,
                "evaluated": evaluated,
                "evidence_coverage": round(evaluated / candidate_count, 6) if candidate_count else 0.0,
                "pass_count": decisions["pass"],
                "warn_count": decisions["warn"],
                "rewrite_count": decisions["rewrite"],
                "block_count": decisions["block"],
                "publish_ready_count": decisions["pass"] + decisions["warn"],
                "top_issue_codes": [
                    {"code": code, "count": count}
                    for code, count in codes.most_common(5)
                ],
                "guard_version": QUALITY_GUARD_VERSION,
                "legacy_excluded_count": legacy_excluded_count,
            }
        )
    return result


def build_recovery_experiments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Export experiment lineage only; no generated post body crosses to D1."""
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "recovery_experiments" not in tables:
        return []
    rows = conn.execute(
        """
        SELECT id,draft_id,platform,experiment_type,hypothesis,
               baseline_followers,baseline_primary_metric,baseline_primary_value,
               baseline_captured_at,content_format,actual_format,actual_format_at,
               topic,created_at
          FROM recovery_experiments
         ORDER BY created_at ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def build_automation_state() -> list[dict[str, Any]]:
    raw_mode = os.environ.get("AUTOMATION_MODE")
    raw_processor = os.environ.get("SUBMISSION_PROCESSOR_MODE")
    # Metadata-only callers must never overwrite durable runtime state merely
    # because their workflow omitted repository variables.
    if raw_mode is None or raw_processor is None:
        return []
    mode = raw_mode.strip().lower()
    processor = raw_processor.strip().lower()
    if mode not in {"paused", "recovery", "live"}:
        raise ValueError(f"invalid AUTOMATION_MODE: {mode!r}")
    if processor not in {"paused", "live"}:
        raise ValueError(f"invalid SUBMISSION_PROCESSOR_MODE: {processor!r}")
    return [
        {
            "id": "runtime",
            "mode": mode,
            "submission_processor": processor,
            "source": "github_repository_variables",
            "detail": "Synced by canonical news-radar operational workflow",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ]


def build_knowledge(
    conn: sqlite3.Connection,
    *,
    full: bool = False,
    limit: int = 500,
) -> list[dict[str, Any]]:
    where = "" if full else "WHERE COALESCE(n.fetched_at,'') >= datetime('now','-14 day') OR d.id IS NOT NULL"
    limit_sql = "" if limit <= 0 else f"LIMIT {int(limit)}"
    rows = conn.execute(
        f"""
        SELECT n.id,n.source_type,n.url,n.title,n.topic_category,n.status,
               n.fetched_at,n.word_count,n.weighted_score,
               MAX(d.generated_at) AS last_used_at,COUNT(d.id) AS use_count
        FROM news_items n LEFT JOIN drafts d ON d.news_id=n.id
        {where}
        GROUP BY n.id
        ORDER BY (MAX(d.generated_at) IS NULL),MAX(d.generated_at) DESC,n.fetched_at DESC
        {limit_sql}
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "source_type": row["source_type"] or "unknown",
            "source_url": row["url"] or None,
            "title": row["title"] or "(untitled)",
            "topic": row["topic_category"] or None,
            "evidence_summary": (
                f"status={row['status'] or 'unknown'}; words={row['word_count'] or 0}; "
                f"weighted_score={row['weighted_score'] if row['weighted_score'] is not None else 'unknown'}"
            ),
            "status": "active",
            "first_seen_at": row["fetched_at"] or datetime.now(timezone.utc).isoformat(),
            "last_used_at": row["last_used_at"] or None,
            "use_count": row["use_count"] or 0,
        }
        for row in rows
    ]


def _proposal_records(proposals_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not proposals_dir.is_dir():
        return records
    for path in sorted(proposals_dir.glob("*.jsonl")):
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid proposal JSON at {path}:{line_number}: {exc}") from exc
            fire_id = row.get("fire_id") if isinstance(row, dict) else None
            if not isinstance(fire_id, str) or not fire_id:
                raise ValueError(f"proposal without fire_id at {path}:{line_number}")
            if fire_id in records:
                raise ValueError(f"duplicate proposal fire_id in JSONL: {fire_id}")
            records[fire_id] = row
    return records


def build_proposals(
    conn: sqlite3.Connection,
    *,
    proposals_dir: Path = DEFAULT_PROPOSALS_DIR,
) -> list[dict[str, Any]]:
    records = _proposal_records(proposals_dir)
    rows = conn.execute(
        """
        SELECT fire_id,fire_at,proposal_type,target_config,hsin_decision,
               hsin_decision_at,deployed_at,evidence_json
        FROM reflector_proposal_lineage ORDER BY fire_at
        """
    ).fetchall()
    result = []
    for row in rows:
        record = records.get(row["fire_id"], {})
        action = record.get("action") if isinstance(record.get("action"), dict) else {}
        if row["hsin_decision"] in {"approve", "approved"}:
            owner_decision = "approved"
        elif row["hsin_decision"] in {"reject", "rejected"}:
            owner_decision = "rejected"
        else:
            owner_decision = None
        if row["deployed_at"]:
            status = "applied"
        elif owner_decision == "approved":
            status = "approved"
        elif owner_decision == "rejected":
            status = "rejected"
        elif row["hsin_decision"] == "amend":
            status = "superseded"
        else:
            status = "proposed"
        target = action.get("target_config") or row["target_config"] or "unknown target"
        field = action.get("field")
        result.append(
            {
                "id": row["fire_id"],
                "kind": row["proposal_type"] or "unknown",
                "status": status,
                "owner_decision": owner_decision,
                "summary": f"{row['proposal_type'] or 'proposal'} → {target}{'.' + field if field else ''}",
                "evidence": _json(row["evidence_json"], {}),
                "proposed_change": action or {"target_config": target},
                "created_at": row["fire_at"] or datetime.now(timezone.utc).isoformat(),
                "decision_comment": record.get("hsin_decision_comment"),
                "decided_at": row["hsin_decision_at"] or None,
                "applied_at": row["deployed_at"] or None,
            }
        )
    return result


def _age_hours(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 3600)
    except (TypeError, ValueError):
        return None


def _build_substack_scope_health(
    conn: sqlite3.Connection,
    *,
    captured_at: str,
    current_control: bool,
) -> dict[str, Any]:
    """Expose current and legacy draft evidence without owner article content."""
    metric = (
        "substack_draft_worker"
        if current_control
        else "substack_legacy_backlog"
    )
    scope = "current" if current_control else "legacy"
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "news_items" not in tables:
        return {
            "platform": "system",
            "metric": metric,
            "status": "unknown",
            "detail": f"news_items=missing; {scope} draft evidence unavailable",
            "captured_at": captured_at,
        }
    columns = {row[1] for row in conn.execute("PRAGMA table_info(news_items)")}
    required = {"tags", "substack_draft_id", "substack_drafted_at"}
    if "tags" not in columns:
        return {
            "platform": "system",
            "metric": metric,
            "status": "unknown",
            "detail": f"schema=legacy; {scope}_classification=unavailable",
            "captured_at": captured_at,
        }
    scope_where = (
        "tags LIKE '%control_submission:%'"
        if current_control
        else "COALESCE(tags,'') NOT LIKE '%control_submission:%'"
    )
    total_row = conn.execute(
        f"""
        SELECT COUNT(*) AS total,MIN(fetched_at) AS oldest,MAX(fetched_at) AS newest
          FROM news_items
         WHERE feed_name='user_substack' AND {scope_where}
        """
    ).fetchone()
    total = int(total_row["total"] or 0)
    if not required <= columns:
        return {
            "platform": "system",
            "metric": metric,
            "status": "degraded" if total else "unknown",
            "detail": (
                f"schema=legacy; {scope}_submissions={total}; "
                "remote_evidence=unavailable; "
                f"oldest={total_row['oldest'] or 'none'}; "
                f"newest={total_row['newest'] or 'none'}"
            ),
            "captured_at": captured_at,
        }

    local_term = (
        "SUM(CASE WHEN substack_written_at IS NOT NULL THEN 1 ELSE 0 END)"
        if "substack_written_at" in columns
        else "0"
    )
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN substack_drafted_at IS NULL OR substack_draft_id IS NULL
                        THEN 1 ELSE 0 END) AS pending,
               {local_term} AS local_written,
               SUM(CASE WHEN substack_drafted_at IS NOT NULL AND substack_draft_id IS NOT NULL
                        THEN 1 ELSE 0 END) AS remote_proven,
               MIN(CASE WHEN substack_drafted_at IS NULL OR substack_draft_id IS NULL
                        THEN fetched_at END) AS oldest_pending,
               MAX(substack_drafted_at) AS latest_remote
          FROM news_items
         WHERE feed_name='user_substack' AND {scope_where}
        """
    ).fetchone()
    total = int(row["total"] or 0)
    pending = int(row["pending"] or 0)
    local_written = int(row["local_written"] or 0)
    remote_proven = int(row["remote_proven"] or 0)
    now = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    oldest_pending_hours = _age_hours(row["oldest_pending"], now)
    latest_remote_hours = _age_hours(row["latest_remote"], now)
    remote_fresh_hours = 24
    if total == 0:
        status = "unknown"
    elif not current_control and pending:
        status = "degraded"
    elif pending:
        status = "degraded" if oldest_pending_hours is None or oldest_pending_hours >= 6 else "unknown"
    elif not current_control:
        status = "healthy" if remote_proven == total else "unknown"
    else:
        status = (
            "healthy"
            if remote_proven == total
            and latest_remote_hours is not None
            and latest_remote_hours <= remote_fresh_hours
            else "unknown"
        )
    return {
        "platform": "system",
        "metric": metric,
        "status": status,
        "detail": (
            f"{scope}_submissions={total}; {scope}_pending_remote={pending}; "
            f"{scope}_local_written={local_written}; {scope}_remote_proven={remote_proven}; "
            f"oldest_pending={row['oldest_pending'] or 'none'}; "
            f"latest_remote={row['latest_remote'] or 'none'}; "
            + (
                "latest_remote_age_hours="
                f"{latest_remote_hours:.1f}; pending_stale_gate=6h; "
                f"remote_fresh_gate={remote_fresh_hours}h"
                if current_control and latest_remote_hours is not None
                else "latest_remote_age_hours=none; pending_stale_gate=6h; "
                f"remote_fresh_gate={remote_fresh_hours}h"
                if current_control
                else "history_preserved=true"
            )
        ),
        "captured_at": captured_at,
    }


def build_substack_worker_health(
    conn: sqlite3.Connection,
    *,
    captured_at: str,
) -> dict[str, Any]:
    """Current control-plane submissions; legacy rows cannot mask this signal."""
    return _build_substack_scope_health(
        conn,
        captured_at=captured_at,
        current_control=True,
    )


def build_substack_legacy_backlog_health(
    conn: sqlite3.Connection,
    *,
    captured_at: str,
) -> dict[str, Any]:
    """Retain historical unverified backlog as a separate visible risk."""
    return _build_substack_scope_health(
        conn,
        captured_at=captured_at,
        current_control=False,
    )


def build_daily_publish_health(
    conn: sqlite3.Connection,
    *,
    captured_at: str,
    policy: dict[str, Any] | None = None,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    """Classify actual daily Meta delivery, never scheduler process health."""
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if policy is None:
        policy = json.loads(SOCIAL_POLICY_PATH.read_text(encoding="utf-8"))
    tz_name = str(policy["timezone"])
    now = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(ZoneInfo(tz_name))
    today_start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start_local = today_start_local + timedelta(days=1)
    previous_start_local = today_start_local - timedelta(days=1)
    today_start = today_start_local.astimezone(timezone.utc).isoformat()
    tomorrow_start = tomorrow_start_local.astimezone(timezone.utc).isoformat()
    previous_start = previous_start_local.astimezone(timezone.utc).isoformat()
    active_mode = (mode or os.environ.get("AUTOMATION_MODE") or "recovery").strip().lower()
    platform_policy = (
        policy["recovery"]["platforms"]
        if active_mode == "recovery"
        else policy["platforms"]
    )
    recovery_started = None
    if active_mode == "recovery" and policy.get("recovery", {}).get("owner_approved_at"):
        recovery_started = datetime.fromisoformat(
            str(policy["recovery"]["owner_approved_at"]).replace("Z", "+00:00")
        ).astimezone(ZoneInfo(tz_name)).date()
    result: list[dict[str, Any]] = []
    for platform in sorted(PLATFORMS):
        config = platform_policy[platform]
        target = int(config["target_posts_per_day"])
        local_days = [int(value) for value in config.get("local_days", range(7))]
        slots = [int(value) for value in config.get("local_slots", [])]
        deadline = config.get("catch_up_deadline_hour")
        if deadline is None:
            deadline = min(24, (max(slots) + 1) if slots else 24)
        if "publish_log" not in tables:
            today_posts = 0
            previous_posts = 0
            latest_success = None
            status = "unknown"
            reason = "publish_log_missing"
        else:
            row = conn.execute(
                """
                SELECT
                  SUM(CASE WHEN success=1 AND datetime(posted_at) >= datetime(?)
                            AND datetime(posted_at) < datetime(?) THEN 1 ELSE 0 END)
                    AS today_posts,
                  SUM(CASE WHEN success=1 AND datetime(posted_at) >= datetime(?)
                            AND datetime(posted_at) < datetime(?) THEN 1 ELSE 0 END)
                    AS previous_posts,
                  MAX(CASE WHEN success=1 THEN posted_at END) AS latest_success
                FROM publish_log WHERE platform=?
                """,
                (
                    today_start,
                    tomorrow_start,
                    previous_start,
                    today_start,
                    platform,
                ),
            ).fetchone()
            today_posts = int((row["today_posts"] if row else 0) or 0)
            previous_posts = int((row["previous_posts"] if row else 0) or 0)
            latest_success = row["latest_success"] if row else None
            today_expected = local.weekday() in local_days and target > 0
            previous_expected = (
                previous_start_local.weekday() in local_days
                and target > 0
                and (
                    recovery_started is None
                    or previous_start_local.date() >= recovery_started
                )
            )
            minute_of_day = local.hour * 60 + local.minute
            if active_mode == "paused":
                status = "unknown"
                reason = "automation_paused"
            elif previous_expected and previous_posts < target:
                status = "degraded"
                reason = "previous_day_missed"
            elif today_posts >= target:
                status = "healthy"
                reason = "today_target_met"
            elif not today_expected:
                status = "unknown"
                reason = "outside_local_day"
            elif minute_of_day >= int(deadline) * 60:
                status = "degraded"
                reason = "today_deadline_missed"
            else:
                status = "unknown"
                reason = "awaiting_today_deadline"
        result.append(
            {
                "platform": platform,
                "metric": "daily_publish_cadence",
                "status": status,
                "detail": (
                    f"reason={reason}; mode={active_mode}; timezone={tz_name}; "
                    f"today_posts={today_posts}/{target}; "
                    f"previous_day_posts={previous_posts}/{target}; "
                    f"local_deadline_hour={deadline}; "
                    f"latest_success={latest_success or 'none'}"
                ),
                "captured_at": captured_at,
            }
        )
    return result


def build_health(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    engagement_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(engagement_stats)")
    }
    clicks_term = "COALESCE(clicks,0)+" if "clicks" in engagement_columns else ""
    rows = conn.execute(
        f"""
        SELECT platform,COUNT(*) AS samples,MAX(fetched_at) AS latest,
               SUM(CASE WHEN raw_json LIKE '%\"error\"%' THEN 1 ELSE 0 END) AS error_samples,
               SUM(CASE WHEN COALESCE(views,0)+COALESCE(reach,0)+
                 {clicks_term}COALESCE(likes,0)+
                 COALESCE(comments,0)+COALESCE(shares,0)+COALESCE(saves,0)+
                 COALESCE(replies,0)+COALESCE(reposts,0)+COALESCE(quotes,0) > 0
                 THEN 1 ELSE 0 END) AS nonzero_samples
        FROM engagement_stats
        WHERE platform IN ('facebook','instagram','threads')
        GROUP BY platform
        """
    ).fetchall()
    by_platform = {row["platform"]: row for row in rows}
    result = []
    for platform in sorted(PLATFORMS):
        row = by_platform.get(platform)
        samples = int(row["samples"] or 0) if row else 0
        errors = int(row["error_samples"] or 0) if row else 0
        nonzero = int(row["nonzero_samples"] or 0) if row else 0
        if samples == 0:
            status = "unknown"
        elif errors:
            status = "degraded"
        else:
            status = "healthy"
        result.extend(
            [
                {
                    "platform": platform,
                    "metric": "engagement_api",
                    "status": status,
                    "detail": f"legacy samples={samples}; samples_with_error_marker={errors}; latest={row['latest'] if row else 'none'}",
                    "captured_at": now,
                },
                {
                    "platform": platform,
                    "metric": "signal_coverage",
                    "status": (
                        "unknown" if samples == 0 or nonzero / samples < 0.5 else "healthy"
                    ),
                    "detail": (
                        f"legacy nonzero_snapshots={nonzero}/{samples}; "
                        "low coverage can be real low engagement or a data gap and requires canary evidence"
                    ),
                    "captured_at": now,
                },
            ]
        )
    result.extend(
        [
            build_substack_worker_health(conn, captured_at=now),
            build_substack_legacy_backlog_health(conn, captured_at=now),
        ]
    )
    result.extend(build_daily_publish_health(conn, captured_at=now))
    return result


def build_submission_updates(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Derive truthful terminal/progress states from canonical runtime evidence."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(news_items)")}
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    has_quality = "content_quality_evaluations" in tables
    updates: dict[str, dict[str, str]] = {}
    if {"substack_draft_id", "substack_drafted_at"} <= columns:
        rows = conn.execute(
            """
            SELECT tags,substack_drafted_at FROM news_items
            WHERE feed_name='user_substack' AND substack_draft_id IS NOT NULL
              AND substack_drafted_at IS NOT NULL
              AND tags LIKE '%control_submission:%'
            """
        ).fetchall()
        for row in rows:
            for submission_id in _tag_values(
                row["tags"], CONTROL_SUBMISSION_PREFIX
            ):
                updates[submission_id] = {
                    "status": "draft_created",
                    "observed_at": row["substack_drafted_at"],
                }

    meta_rows = conn.execute(
        """
        SELECT n.id AS news_id,n.tags,n.status AS news_status,
               d.id AS draft_id,d.generated_at
          FROM news_items n
          LEFT JOIN drafts d ON d.news_id=n.id
         WHERE n.feed_name='user_submission'
           AND n.tags LIKE '%control_submission:%'
        """
    ).fetchall()
    rank = {"quality_held": 0, "partial": 1, "published": 2}
    aliases = {
        "fb": "facebook",
        "facebook": "facebook",
        "ig": "instagram",
        "instagram": "instagram",
        "threads": "threads",
    }
    for row in meta_rows:
        legacy_requested = {
            aliases[value]
            for value in _tag_values(row["tags"], "platform:")
            if value in aliases
        }
        routes: dict[str, set[str]] = {}
        for route in _tag_values(row["tags"], CONTROL_ROUTE_PREFIX):
            if ":" not in route:
                continue
            submission_id, raw_platforms = route.split(":", 1)
            requested = {
                aliases[value]
                for value in raw_platforms.split(",")
                if value in aliases
            }
            if submission_id and requested:
                routes[submission_id] = requested
        if not routes:
            routes = {
                submission_id: legacy_requested
                for submission_id in _tag_values(
                    row["tags"], CONTROL_SUBMISSION_PREFIX
                )
                if legacy_requested
            }
        if not routes:
            continue
        success_rows = []
        if row["draft_id"]:
            success_rows = conn.execute(
                """
                SELECT platform,MAX(posted_at) AS posted_at
                  FROM publish_log
                 WHERE draft_id=? AND success=1
                 GROUP BY platform
                """,
                (row["draft_id"],),
            ).fetchall()
        all_successes = {item["platform"] for item in success_rows}
        latest_quality: dict[str, sqlite3.Row] = {}
        if not all_successes and has_quality:
            quality_rows = conn.execute(
                """
                SELECT platform,decision,checked_at
                  FROM content_quality_evaluations
                 WHERE id IN (
                   SELECT MAX(id)
                     FROM content_quality_evaluations
                    WHERE news_id=? AND stage='compose'
                    GROUP BY platform
                 )
                """,
                (row["news_id"],),
            ).fetchall()
            latest_quality = {item["platform"]: item for item in quality_rows}
        for submission_id, requested in routes.items():
            successes = all_successes & requested
            if not successes:
                held = [
                    latest_quality[platform]
                    for platform in requested
                    if platform in latest_quality
                    and latest_quality[platform]["decision"] in {"block", "rewrite"}
                ]
                if held:
                    previous = updates.get(submission_id)
                    if not previous or rank.get(previous["status"], 99) < rank["quality_held"]:
                        updates[submission_id] = {
                            "status": "quality_held",
                            "observed_at": max(item["checked_at"] for item in held),
                        }
                continue
            status = "published" if successes == requested else "partial"
            observed_at = max(
                [
                    item["posted_at"]
                    for item in success_rows
                    if item["platform"] in requested and item["posted_at"]
                ]
                or [row["generated_at"]]
            )
            previous = updates.get(submission_id)
            if previous and rank.get(previous["status"], 99) >= rank[status]:
                continue
            updates[submission_id] = {
                "status": status,
                "observed_at": observed_at,
            }
    return [
        {"submission_id": submission_id, **payload}
        for submission_id, payload in sorted(updates.items())
    ]


def batches(rows: Sequence[dict[str, Any]], size: int = 75) -> Iterator[list[dict[str, Any]]]:
    for offset in range(0, len(rows), size):
        yield list(rows[offset : offset + size])


def sync_payloads(
    client: httpx.Client,
    *,
    api_url: str,
    token: str,
    groups: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    sent: dict[str, int] = {}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for name, rows in groups.items():
        sent[name] = 0
        for chunk in batches(rows):
            response = client.post(
                f"{api_url.rstrip('/')}/api/service/sync",
                headers=headers,
                json={name: chunk},
            )
            response.raise_for_status()
            sent[name] += len(chunk)
    return sent


def report_submission_updates(
    client: httpx.Client,
    *,
    api_url: str,
    token: str,
    updates: Sequence[dict[str, str]],
) -> int:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    reported = 0
    for update in updates:
        response = client.post(
            f"{api_url.rstrip('/')}/api/service/submissions/{update['submission_id']}/status",
            headers=headers,
            json={"status": update["status"]},
        )
        if response.status_code == 404:
            print(
                "[sync_social_ops] WARN skipping status for non-control "
                f"submission {update['submission_id']}",
                file=sys.stderr,
            )
            continue
        response.raise_for_status()
        reported += 1
    return reported


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/01_harvest/news_radar.db"))
    parser.add_argument("--proposals-dir", type=Path, default=DEFAULT_PROPOSALS_DIR)
    parser.add_argument("--api-url", default=os.environ.get("SOCIAL_OPS_API_URL", ""))
    parser.add_argument("--token", default=os.environ.get("SOCIAL_OPS_SERVICE_TOKEN", ""))
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--knowledge-limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        groups = {
            "automation": build_automation_state(),
            "posts": build_posts(conn, full=args.full),
            "engagement": build_engagement(conn, full=args.full),
            "quality": build_quality(conn, full=args.full),
            "experiments": build_recovery_experiments(conn),
            "knowledge": build_knowledge(conn, full=args.full, limit=args.knowledge_limit),
            "proposals": build_proposals(conn, proposals_dir=args.proposals_dir),
            "health": build_health(conn),
        }
        submission_updates = build_submission_updates(conn)
    finally:
        conn.close()
    counts = {name: len(rows) for name, rows in groups.items()}
    counts["submission_updates"] = len(submission_updates)
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "counts": counts}, ensure_ascii=False, indent=2))
        return 0
    if not args.api_url or not args.token:
        print("SOCIAL_OPS_API_URL and SOCIAL_OPS_SERVICE_TOKEN are required", file=sys.stderr)
        return 2
    with httpx.Client(timeout=45) as client:
        sent = sync_payloads(client, api_url=args.api_url, token=args.token, groups=groups)
        sent["submission_updates"] = report_submission_updates(
            client,
            api_url=args.api_url,
            token=args.token,
            updates=submission_updates,
        )
    print(json.dumps({"ok": True, "sent": sent}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
