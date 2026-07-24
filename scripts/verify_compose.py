#!/usr/bin/env python3
"""Fail-closed verification of the exact platform-scoped compose release."""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

import json
from src import db as dbmod
from src.content_quality_guard import (
    check_quality,
    has_blocking_issues,
    should_request_rewrite,
)


PLATFORMS = {"facebook", "instagram", "threads"}


def verify_compose(
    conn,
    *,
    expected_platforms: set[str],
    since_minutes: int,
    recovery: bool,
    since_iso: str | None = None,
) -> int:
    failures = []
    recovery_ready_drafts: set[str] = set()
    # 1. Limit evidence to this exact compose release.  A rolling window is a
    # compatibility fallback only: it can accidentally import held drafts from
    # an earlier run and create a false FAIL for a healthy new release.
    if since_iso:
        recent = conn.execute("""
            SELECT d.id, d.title, d.confidence_score, d.status, d.queue_status,
                   d.generated_at, d.news_id
            FROM drafts d
            WHERE datetime(d.generated_at) >= datetime(?)
            ORDER BY d.generated_at DESC
        """, (since_iso,)).fetchall()
        boundary = f"since_iso={since_iso}"
    else:
        recent = conn.execute("""
            SELECT d.id, d.title, d.confidence_score, d.status, d.queue_status,
                   d.generated_at, d.news_id
            FROM drafts d
            WHERE datetime(d.generated_at) >= datetime('now', ?)
            ORDER BY d.generated_at DESC
        """, (f"-{since_minutes} minutes",)).fetchall()
        boundary = f"window_minutes={since_minutes}"
    print(
        f"[Verify:Compose] {boundary} "
        f"expected={sorted(expected_platforms)} recent_drafts={len(recent)}"
    )

    if len(recent) == 0:
        print("❌ [Verify:Compose] No draft created in verification window")
        failures.append("no_recent_draft")

    # 2. For each release draft, check the exact requested platform set.
    for draft in recent:
        draft_id = draft["id"]
        draft_quality_ready = True
        platforms = conn.execute(
            "SELECT platform, char_count, full_text FROM platform_drafts WHERE draft_id=?",
            (draft_id,)
        ).fetchall()
        found_platforms = {p["platform"]: p for p in platforms}

        missing = sorted(expected_platforms - set(found_platforms))
        unexpected = sorted(set(found_platforms) - expected_platforms)

        if missing:
            print(f"❌ [Verify:Compose] draft={draft_id[:12]} missing platforms: {missing}")
            failures.append(f"missing_platforms:{draft_id[:12]}")
        if unexpected:
            print(f"❌ [Verify:Compose] draft={draft_id[:12]} unexpected platforms: {unexpected}")
            failures.append(f"unexpected_platforms:{draft_id[:12]}")

        # 3. Quality guard check on the requested release scope.
        for plat in sorted(expected_platforms & set(found_platforms)):
            pd_data = found_platforms[plat]
            text = pd_data["full_text"] or ""
            issues = check_quality(
                text,
                title=draft["title"] or "",
                recovery=recovery,
            )
            quality_failed = has_blocking_issues(issues) or (
                recovery and should_request_rewrite(issues)
            )
            if quality_failed:
                draft_quality_ready = False
                if recovery:
                    print(
                        f"  ⏸ [Verify:Compose] draft={draft_id[:12]} "
                        f"{plat}: quality held (excluded from release)"
                    )
                else:
                    print(f"❌ [Verify:Compose] draft={draft_id[:12]} {plat}: quality held")
                    failures.append(f"quality_held:{draft_id[:12]}:{plat}")
            else:
                char_count = pd_data["char_count"] or len(text)
                print(f"  ✓ {plat}: {char_count} chars")

            # 4. Advisory style fingerprints remain visible but are not truth gates.
            ai_flags = []
            if "這說明兩件事" in text or "拆解兩層邏輯" in text:
                ai_flags.append("八股條列結構")
            if "總結來說" in text or "總而言之" in text:
                ai_flags.append("總結式收尾")
            if text.count("—") > 3:
                ai_flags.append(f"破折號過多({text.count('—')})")
            if ai_flags:
                print(f"⚠️ [Verify:Compose] draft={draft_id[:12]} {plat}: AI味指紋={ai_flags}")

        # Recovery is allowed to inspect and retain rejected candidates before
        # finding one publish-ready draft.  A held candidate is evidence that
        # the guard worked, not a release failure.  Lineage is required only
        # for a quality-passing draft that is actually queued for publishing.
        if recovery and not missing and not unexpected and draft_quality_ready:
            if draft["queue_status"] != "queued":
                print(
                    f"  ⏸ [Verify:Compose] draft={draft_id[:12]} "
                    f"queue_status={draft['queue_status']!r} (excluded from release)"
                )
                continue
            experiment_platforms = {
                row["platform"]
                for row in conn.execute(
                    "SELECT platform FROM recovery_experiments WHERE draft_id=?",
                    (draft_id,),
                )
            }
            missing_lineage = sorted(expected_platforms - experiment_platforms)
            if missing_lineage:
                failures.append(f"missing_recovery_lineage:{draft_id[:12]}")
                print(
                    f"❌ [Verify:Compose] draft={draft_id[:12]} "
                    f"missing recovery lineage: {missing_lineage}"
                )
            else:
                recovery_ready_drafts.add(draft_id)

    if recovery and not recovery_ready_drafts:
        failures.append("no_publish_ready_recovery_draft")
        print(
            "❌ [Verify:Compose] no current-run Recovery draft passed quality, "
            "queue, scope, and lineage gates"
        )

    # 5. Overall health
    total_drafts = conn.execute("SELECT COUNT(*) as c FROM drafts").fetchone()["c"]
    pending = conn.execute(
        "SELECT COUNT(*) as c FROM drafts WHERE queue_status='queued'"
    ).fetchone()["c"]
    print(f"[Verify:Compose] total_drafts={total_drafts} pending_queue={pending}")

    if failures:
        print(f"❌ [Verify:Compose] FAIL reasons={failures}")
        return 1
    print("✅ [Verify:Compose] PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platforms", default="facebook,instagram,threads")
    parser.add_argument("--since-minutes", type=int, default=1440)
    parser.add_argument(
        "--since-iso",
        default="",
        help="Exact UTC compose boundary; preferred over the rolling window",
    )
    args = parser.parse_args()
    expected = {value.strip() for value in args.platforms.split(",") if value.strip()}
    if not expected or not expected <= PLATFORMS:
        parser.error("--platforms must contain only facebook,instagram,threads")
    if args.since_minutes <= 0:
        parser.error("--since-minutes must be positive")
    since_iso = args.since_iso.strip() or None
    if since_iso:
        try:
            datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
        except ValueError:
            parser.error("--since-iso must be an ISO-8601 timestamp")
    conn = dbmod.get_conn()
    try:
        return verify_compose(
            conn,
            expected_platforms=expected,
            since_minutes=args.since_minutes,
            recovery=os.environ.get("AUTOMATION_MODE", "").strip().lower() == "recovery",
            since_iso=since_iso,
        )
    finally:
        conn.close()

if __name__ == "__main__":
    raise SystemExit(main())
