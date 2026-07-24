#!/usr/bin/env python3
"""Compose against a disposable canonical-state copy without publishing.

This is the only supported out-of-slot editorial canary. It never receives
Meta credentials, never pushes runtime state, disables token refresh and
preview writes, and replaces the publisher with a hard failure.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db as dbmod  # noqa: E402
from src.content_quality_guard import QUALITY_GUARD_VERSION  # noqa: E402


PLATFORMS = {"facebook", "instagram", "threads"}
PRIMARY_FRESHNESS_HOURS = 36


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _source_current_at_run(published_at: str | None, started_at: str) -> bool:
    published = _parse_iso(published_at)
    started = _parse_iso(started_at)
    if published is None or started is None:
        return False
    return started - timedelta(hours=PRIMARY_FRESHNESS_HOURS) <= published <= started


def _eligible_current_primary_rows(
    rows: list[dict[str, Any]], started_at: str
) -> list[dict[str, Any]]:
    """Return unpublished public-primary rows inside the freshness window."""

    return [
        row
        for row in rows
        if row.get("source_status") == "fetched"
        and _source_current_at_run(row.get("source_published_at"), started_at)
    ]


def _filter_pending_items(
    rows: list[Any], allowed_news_ids: set[str]
) -> list[Any]:
    """Limit a disposable canary run to its reverified source allowlist."""

    return [row for row in rows if row["id"] in allowed_news_ids]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _latest_quality(
    conn: sqlite3.Connection,
    *,
    platform: str,
    since_iso: str,
    freshly_harvested_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    freshly_harvested_ids = freshly_harvested_ids or set()
    rows = conn.execute(
        """
        WITH ranked AS (
          SELECT q.*,
                 ROW_NUMBER() OVER(
                   PARTITION BY q.draft_id,q.platform ORDER BY q.id DESC
                 ) AS rn
            FROM content_quality_evaluations q
           WHERE q.platform=? AND q.guard_version=?
             AND datetime(q.checked_at) >= datetime(?)
        )
        SELECT q.draft_id,COALESCE(q.news_id,d.news_id) AS news_id,
               q.platform,q.decision,q.attempt,
               q.guard_version,q.issue_codes_json,
               n.feed_name,n.url,n.published_at,n.fetched_at,n.tags
          FROM ranked q
          LEFT JOIN drafts d ON d.id=q.draft_id
          LEFT JOIN news_items n ON n.id=COALESCE(q.news_id,d.news_id)
         WHERE q.rn=1 ORDER BY q.id
        """,
        (platform, QUALITY_GUARD_VERSION, since_iso),
    ).fetchall()
    result = []
    for row in rows:
        try:
            tags = json.loads(row["tags"] or "[]")
        except (TypeError, json.JSONDecodeError):
            tags = []
        news_id = row["news_id"]
        result.append({
            "draft_id": row["draft_id"],
            "news_id": news_id,
            "platform": row["platform"],
            "decision": row["decision"],
            "attempt": row["attempt"],
            "guard_version": row["guard_version"],
            "issue_codes": json.loads(row["issue_codes_json"] or "[]"),
            "source_feed": row["feed_name"],
            "source_url": row["url"],
            "source_published_at": row["published_at"],
            "source_fetched_at": row["fetched_at"],
            "source_tags": tags,
            "source_is_primary_record": "primary-record" in tags,
            "harvested_this_run": news_id in freshly_harvested_ids,
            "source_current_at_run": _source_current_at_run(
                row["published_at"], since_iso
            ),
        })
    return result


def _primary_source_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id,feed_name,url,published_at,fetched_at,tags,status,drop_reason
          FROM news_items
         WHERE COALESCE(tags,'') LIKE '%"primary-record"%'
        """
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            tags = json.loads(row["tags"] or "[]")
        except (TypeError, json.JSONDecodeError):
            tags = []
        if "primary-record" not in tags:
            continue
        result[row["id"]] = {
            "news_id": row["id"],
            "source_feed": row["feed_name"],
            "source_url": row["url"],
            "source_published_at": row["published_at"],
            "source_fetched_at": row["fetched_at"],
            "source_tags": tags,
            "source_status": row["status"],
            "source_drop_reason": row["drop_reason"],
        }
    return result


def _all_configured_feeds_healthy(harvest_report: dict[str, Any] | None) -> bool:
    report = harvest_report or {}
    feed_results = report.get("feed_results") or {}
    return bool(
        feed_results
        and len(feed_results) == int(report.get("feeds_checked") or 0)
        and all(
            result.get("status") == "ok"
            for result in feed_results.values()
        )
    )


def _copy_previews(
    conn: sqlite3.Connection,
    quality: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Export only fresh or current public-primary copy for editorial audit."""

    previews: list[dict[str, Any]] = []
    for evidence in quality:
        if not evidence.get("source_is_primary_record") or not (
            evidence.get("harvested_this_run")
            or evidence.get("source_current_at_run")
        ):
            continue
        row = conn.execute(
            """
            SELECT pd.title,pd.full_text,d.carousel_json
              FROM platform_drafts pd
              JOIN drafts d ON d.id=pd.draft_id
             WHERE pd.draft_id=? AND pd.platform=?
            """,
            (evidence["draft_id"], evidence["platform"]),
        ).fetchone()
        if row is None:
            continue
        try:
            carousel = json.loads(row["carousel_json"]) if row["carousel_json"] else None
        except (TypeError, json.JSONDecodeError):
            carousel = {"invalid_json": True}
        previews.append({
            "draft_id": evidence["draft_id"],
            "platform": evidence["platform"],
            "source_feed": evidence["source_feed"],
            "source_url": evidence["source_url"],
            "title": row["title"],
            "full_text": row["full_text"],
            "carousel": carousel,
        })
    return previews


async def run_setup_canary(
    *,
    source_db: Path,
    platform: str,
    report_path: Path,
    refresh_primary_sources: bool = False,
    include_copy: bool = False,
) -> dict[str, Any]:
    # The script name is a runtime contract, not a suggestion.  Lock the
    # canary to the same Recovery/editorial path as the production workflow so
    # a local invocation cannot accidentally exercise the legacy long prompt
    # and report misleading evidence.
    os.environ["AUTOMATION_MODE"] = "recovery"
    os.environ["EDITORIAL_MODE"] = "1"
    os.environ["EDITOR_ENFORCE"] = "1"
    if platform not in PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    if not source_db.is_file():
        raise FileNotFoundError(source_db)

    canonical_before = _sha256(source_db)
    with tempfile.TemporaryDirectory(prefix="news-radar-recovery-setup-") as temp_dir:
        temp_db = Path(temp_dir) / "news_radar.db"
        shutil.copy2(source_db, temp_db)

        import run_harvest
        import run_pipeline

        dbmod.DB_PATH = temp_db
        run_harvest.dbmod.DB_PATH = temp_db
        run_pipeline.dbmod.DB_PATH = temp_db
        run_pipeline.refresh_threads_token = lambda: None
        run_pipeline.save_md_draft = lambda *args, **kwargs: None
        run_pipeline.save_archive_md = lambda *args, **kwargs: None

        async def forbidden_publish(*args, **kwargs):
            raise AssertionError("publisher invoked during setup-only canary")

        run_pipeline._publish_platform = forbidden_publish

        started_at = datetime.now(timezone.utc).isoformat()
        conn = dbmod.get_conn()
        publish_before = conn.execute("SELECT COUNT(*) FROM publish_log").fetchone()[0]
        queued_before = dbmod.count_queued_pending_for_platforms(
            conn,
            {platform},
            recovery_only=True,
        )
        primary_before = _primary_source_rows(conn)
        conn.close()

        harvest_report: dict[str, Any] | None = None
        if refresh_primary_sources:
            harvested = await run_harvest.run_harvest_once(
                feed_tag="primary-record",
                write_log=False,
            )
            harvest_report = harvested.model_dump(mode="json")

        conn = dbmod.get_conn()
        primary_after = _primary_source_rows(conn)
        conn.close()
        freshly_harvested_ids = set(primary_after) - set(primary_before)
        fresh_primary_sources = [
            primary_after[news_id]
            for news_id in sorted(freshly_harvested_ids)
        ]
        current_primary_sources = [
            row
            for row in primary_after.values()
            if row["source_status"] != "dropped"
            and _source_current_at_run(row["source_published_at"], started_at)
        ]
        eligible_current_primary_sources = _eligible_current_primary_rows(
            list(primary_after.values()), started_at
        )
        eligible_current_ids = {
            row["news_id"] for row in eligible_current_primary_sources
        }
        eligible_fresh_primary_sources = [
            row
            for row in fresh_primary_sources
            if row["news_id"] in eligible_current_ids
        ]

        # Deduplication is not staleness.  A reverified official record remains
        # eligible while its publication timestamp is inside the freshness
        # window.  The disposable pipeline is allowlisted to current,
        # unpublished primary rows so its normal topic ranking cannot silently
        # select a secondary-media item instead.
        should_compose = (
            not refresh_primary_sources
            or bool(eligible_current_primary_sources)
        )
        if should_compose:
            original_argv = sys.argv[:]
            original_get_pending_items = dbmod.get_pending_items

            def canary_get_pending_items(conn: sqlite3.Connection) -> list[Any]:
                rows = original_get_pending_items(conn)
                if not refresh_primary_sources:
                    return rows
                return _filter_pending_items(rows, eligible_current_ids)

            dbmod.get_pending_items = canary_get_pending_items
            try:
                sys.argv = [
                    "run_pipeline.py",
                    "--compose-only",
                    "--buffer-target",
                    str(queued_before + 1),
                    "--platforms",
                    platform,
                ]
                await run_pipeline.main()
            finally:
                sys.argv = original_argv
                dbmod.get_pending_items = original_get_pending_items

        conn = dbmod.get_conn()
        publish_after = conn.execute("SELECT COUNT(*) FROM publish_log").fetchone()[0]
        quality = _latest_quality(
            conn,
            platform=platform,
            since_iso=started_at,
            freshly_harvested_ids=freshly_harvested_ids,
        )
        copy_previews = _copy_previews(conn, quality) if include_copy else []
        queued_after = dbmod.count_queued_pending_for_platforms(
            conn,
            {platform},
            recovery_only=True,
        )
        experiments = conn.execute(
            """
            SELECT COUNT(*) FROM recovery_experiments
             WHERE platform=? AND datetime(created_at) >= datetime(?)
            """,
            (platform, started_at),
        ).fetchone()[0]
        conn.close()

    canonical_after = _sha256(source_db)
    publish_unchanged = publish_before == publish_after
    canonical_unchanged = canonical_before == canonical_after
    publish_ready = any(
        row["decision"] in {"pass", "warn"} for row in quality
    )
    fresh_primary_quality = [
        row
        for row in quality
        if row["harvested_this_run"]
        and row["source_is_primary_record"]
        and row["source_current_at_run"]
    ]
    fresh_primary_ready = any(
        row["decision"] in {"pass", "warn"}
        for row in fresh_primary_quality
    )
    current_primary_quality = [
        row
        for row in quality
        if row["source_is_primary_record"] and row["source_current_at_run"]
    ]
    current_primary_ready = any(
        row["decision"] in {"pass", "warn"}
        for row in current_primary_quality
    )
    primary_harvest_complete = (
        not refresh_primary_sources
        or _all_configured_feeds_healthy(harvest_report)
    )
    source_gate_ready = (
        (fresh_primary_ready or current_primary_ready) and primary_harvest_complete
        if refresh_primary_sources
        else publish_ready
    )
    status = (
        "pass"
        if publish_unchanged
        and canonical_unchanged
        and source_gate_ready
        and experiments > 0
        and queued_after > queued_before
        else "held"
    )
    if not canonical_unchanged or not publish_unchanged:
        hold_reason = "state_invariant_failed"
    elif refresh_primary_sources and not primary_harvest_complete:
        hold_reason = "primary_harvest_incomplete"
    elif refresh_primary_sources and not eligible_current_primary_sources:
        hold_reason = "no_eligible_current_primary_sources"
    elif not quality:
        hold_reason = "no_current_guard_evaluation"
    elif refresh_primary_sources and not (
        fresh_primary_quality or current_primary_quality
    ):
        hold_reason = "current_primary_not_selected"
    elif refresh_primary_sources and not (
        fresh_primary_ready or current_primary_ready
    ):
        hold_reason = "current_primary_quality_held"
    elif not publish_ready:
        hold_reason = "quality_held"
    elif experiments <= 0:
        hold_reason = "missing_recovery_experiment"
    elif queued_after <= queued_before:
        hold_reason = "queue_not_advanced"
    else:
        hold_reason = "ready"
    report = {
        "status": status,
        "hold_reason": hold_reason,
        "setup_only": True,
        "automation_mode": os.environ["AUTOMATION_MODE"],
        "platform": platform,
        "started_at": started_at,
        "guard_version": QUALITY_GUARD_VERSION,
        "canonical_db_unchanged": canonical_unchanged,
        "publish_log_unchanged": publish_unchanged,
        "publish_log_before": publish_before,
        "publish_log_after": publish_after,
        "queued_before": queued_before,
        "queued_after": queued_after,
        "new_experiments": experiments,
        "primary_source_gate_required": refresh_primary_sources,
        "primary_source_gate_ready": source_gate_ready,
        "fresh_primary_quality_ready": fresh_primary_ready,
        "current_primary_quality_ready": current_primary_ready,
        "source_gate_mode": (
            "fresh_primary"
            if fresh_primary_ready
            else "current_primary" if current_primary_ready else "none"
        ),
        "primary_refresh": {
            "requested": refresh_primary_sources,
            "all_configured_feeds_healthy": primary_harvest_complete,
            "harvest_report": harvest_report,
            "fresh_source_count": len(fresh_primary_sources),
            "eligible_fresh_source_count": len(eligible_fresh_primary_sources),
            "current_source_count": len(current_primary_sources),
            "eligible_current_source_count": len(
                eligible_current_primary_sources
            ),
            "fresh_sources": fresh_primary_sources,
        },
        "quality": quality,
        "copy_previews": copy_previews,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument(
        "--source-db",
        type=Path,
        default=ROOT / "data" / "01_harvest" / "news_radar.db",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "reports" / "recovery_setup_canary.json",
    )
    parser.add_argument(
        "--refresh-primary-sources",
        action="store_true",
        help=(
            "harvest configured primary-record feeds into the disposable DB "
            "and require a newly fetched or 36-hour current source to pass "
            "the current guard"
        ),
    )
    parser.add_argument(
        "--include-copy",
        action="store_true",
        help=(
            "include only fresh or current public-primary generated copy in the "
            "short-lived Actions report for owner editorial audit"
        ),
    )
    args = parser.parse_args()
    report = asyncio.run(
        run_setup_canary(
            source_db=args.source_db.resolve(),
            platform=args.platform,
            report_path=args.report.resolve(),
            refresh_primary_sources=args.refresh_primary_sources,
            include_copy=args.include_copy,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
