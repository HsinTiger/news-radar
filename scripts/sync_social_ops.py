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
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


PLATFORMS = {"facebook", "instagram", "threads"}
CONTROL_SUBMISSION_PREFIX = "control_submission:"


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
    if not isinstance(value, dict):
        return {"legacy": True}
    for candidate in (value.get("error"), value.get("insights", {}).get("error") if isinstance(value.get("insights"), dict) else None):
        if isinstance(candidate, dict):
            return {
                "legacy": True,
                "api_error": True,
                "code": candidate.get("code"),
                "message": str(candidate.get("message") or "")[:300],
            }
    return {"legacy": True, "api_error": _has_error(value)}


def _stable_id(prefix: str, *values: object) -> str:
    raw = "|".join(str(value or "") for value in values)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def build_posts(conn: sqlite3.Connection, *, full: bool = False) -> list[dict[str, Any]]:
    where = "" if full else "WHERE COALESCE(p.posted_at,'') >= datetime('now','-45 day')"
    rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT p.*,ROW_NUMBER() OVER(
            PARTITION BY p.platform,COALESCE(NULLIF(p.platform_post_id,''),p.draft_id)
            ORDER BY p.id DESC
          ) AS rn
          FROM publish_log p {where}
        )
        SELECT p.id,p.draft_id,p.platform,p.platform_post_id,p.posted_at,p.success,
               p.error_message,d.title,n.topic_category,n.url,d.generated_at
        FROM ranked p
        LEFT JOIN drafts d ON d.id=p.draft_id
        LEFT JOIN news_items n ON n.id=d.news_id
        WHERE p.rn=1 AND p.platform IN ('facebook','instagram','threads')
        ORDER BY COALESCE(p.posted_at,d.generated_at) ASC
        """
    ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "id": _stable_id("post", row["platform"], row["platform_post_id"] or row["draft_id"]),
                "draft_id": row["draft_id"],
                "submission_id": None,
                "platform": row["platform"],
                "format": "feed",
                "platform_post_id": row["platform_post_id"] or None,
                "status": "published" if row["success"] else "failed",
                "title": row["title"] or None,
                "topic": row["topic_category"] or None,
                "source_url": row["url"] or None,
                "posted_at": row["posted_at"] or None,
                "created_at": row["generated_at"] or row["posted_at"] or datetime.now(timezone.utc).isoformat(),
                "updated_at": row["posted_at"] or row["generated_at"] or datetime.now(timezone.utc).isoformat(),
            }
        )
    return result


def build_engagement(conn: sqlite3.Connection, *, full: bool = False) -> list[dict[str, Any]]:
    where = "" if full else "AND COALESCE(fetched_at,'') >= datetime('now','-45 day')"
    rows = conn.execute(
        f"""
        SELECT platform,platform_post_id,fetched_at,post_age_bucket,views,reach,
               engaged_users,clicks,likes,comments,shares,saves,replies,reposts,
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
                "engaged_users": row["engaged_users"] or 0,
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


def build_proposals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT fire_id,fire_at,proposal_type,target_config,hsin_decision,
               hsin_decision_at,deployed_at,evidence_json
        FROM reflector_proposal_lineage ORDER BY fire_at
        """
    ).fetchall()
    result = []
    for row in rows:
        if row["deployed_at"]:
            status = "applied"
        elif row["hsin_decision"] in {"approved", "rejected"}:
            status = row["hsin_decision"]
        else:
            status = "proposed"
        result.append(
            {
                "id": row["fire_id"],
                "kind": row["proposal_type"] or "unknown",
                "status": status,
                "summary": f"{row['proposal_type'] or 'proposal'} → {row['target_config'] or 'unknown target'}",
                "evidence": _json(row["evidence_json"], {}),
                "proposed_change": {"target_config": row["target_config"]},
                "created_at": row["fire_at"] or datetime.now(timezone.utc).isoformat(),
                "decided_at": row["hsin_decision_at"] or None,
                "applied_at": row["deployed_at"] or None,
            }
        )
    return result


def build_health(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """
        SELECT platform,COUNT(*) AS samples,MAX(fetched_at) AS latest,
               SUM(CASE WHEN raw_json LIKE '%\"error\"%' THEN 1 ELSE 0 END) AS error_samples,
               SUM(CASE WHEN COALESCE(views,0)+COALESCE(reach,0)+
                 COALESCE(engaged_users,0)+COALESCE(clicks,0)+COALESCE(likes,0)+
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
    return result


def build_submission_updates(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Find Substack sources that the Mac has proven were turned into drafts."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(news_items)")}
    if "substack_written_at" not in columns:
        return []
    rows = conn.execute(
        """
        SELECT tags,substack_written_at FROM news_items
        WHERE feed_name='user_substack' AND substack_written_at IS NOT NULL
          AND tags LIKE '%control_submission:%'
        """
    ).fetchall()
    updates: dict[str, str] = {}
    for row in rows:
        tags = _json(row["tags"], [])
        if not isinstance(tags, list):
            continue
        for tag in tags:
            if isinstance(tag, str) and tag.startswith(CONTROL_SUBMISSION_PREFIX):
                submission_id = tag[len(CONTROL_SUBMISSION_PREFIX) :]
                if submission_id:
                    updates[submission_id] = row["substack_written_at"]
    return [
        {"submission_id": submission_id, "status": "draft_created", "observed_at": observed_at}
        for submission_id, observed_at in sorted(updates.items())
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
        response.raise_for_status()
        reported += 1
    return reported


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/01_harvest/news_radar.db"))
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
            "posts": build_posts(conn, full=args.full),
            "engagement": build_engagement(conn, full=args.full),
            "knowledge": build_knowledge(conn, full=args.full, limit=args.knowledge_limit),
            "proposals": build_proposals(conn),
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
