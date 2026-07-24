#!/usr/bin/env python3
"""Fail-closed verification of recent Meta publication evidence."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from src import db as dbmod


PLATFORMS = {"facebook", "instagram", "threads"}


def verify(
    since_minutes: int,
    require_attempt: bool,
    platforms: set[str] | None = None,
    *,
    conn=None,
) -> int:
    owns_conn = conn is None
    if conn is None:
        conn = dbmod.get_conn()
    failures: list[str] = []
    try:
        targets = sorted(platforms or [])
        platform_sql = ""
        params: list[object] = [f"-{since_minutes} minutes"]
        if targets:
            platform_sql = f" AND p.platform IN ({','.join('?' * len(targets))})"
            params.extend(targets)
        recent = conn.execute(
            """
            SELECT p.id, p.draft_id, p.platform, p.platform_post_id,
                   p.posted_at, p.success, p.error_message
              FROM publish_log p
             WHERE datetime(p.posted_at) >= datetime('now', ?)
             """ + platform_sql + """
             ORDER BY p.posted_at DESC
            """,
            tuple(params),
        ).fetchall()
        successes = [row for row in recent if row["success"]]
        failed = [row for row in recent if not row["success"]]
        print(
            f"[Verify:Publish] window_minutes={since_minutes} "
            f"attempts={len(recent)} successes={len(successes)} failures={len(failed)}"
        )
        if require_attempt and not recent:
            failures.append("no_attempt_in_verification_window")
        if require_attempt and targets:
            attempted_platforms = {row["platform"] for row in recent}
            successful_platforms = {row["platform"] for row in successes}
            for platform in sorted(set(targets) - attempted_platforms):
                failures.append(f"missing_platform_attempt:{platform}")
            for platform in sorted(set(targets) - successful_platforms):
                failures.append(f"missing_platform_success:{platform}")
        for row in successes:
            if not row["platform_post_id"]:
                failures.append(
                    f"missing_platform_post_id:{row['draft_id'][:12]}:{row['platform']}"
                )
        for row in failed[:10]:
            failures.append(
                f"publish_failed:{row['draft_id'][:12]}:{row['platform']}:"
                f"{row['error_message'] or 'unknown'}"
            )
        duplicates = conn.execute(
            """
            SELECT draft_id, platform, COUNT(*) AS count
              FROM publish_log
             WHERE success = 1
             GROUP BY draft_id, platform
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for row in duplicates:
            failures.append(
                f"duplicate_success:{row['draft_id'][:12]}:{row['platform']}:"
                f"{row['count']}"
            )
        total_success = conn.execute(
            "SELECT COUNT(*) FROM publish_log WHERE success=1"
        ).fetchone()[0]
        total_failed = conn.execute(
            "SELECT COUNT(*) FROM publish_log WHERE success=0"
        ).fetchone()[0]
        print(
            f"[Verify:Publish] lifetime_success={total_success} "
            f"lifetime_failed={total_failed}"
        )
        if failures:
            for failure in failures:
                print(f"❌ [Verify:Publish] {failure}")
            print("❌ [Verify:Publish] FAIL")
            return 1
        print("✅ [Verify:Publish] PASS")
        return 0
    finally:
        if owns_conn:
            conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-minutes", type=int, default=30)
    parser.add_argument("--require-attempt", action="store_true")
    parser.add_argument("--platforms", default="")
    args = parser.parse_args()
    if args.since_minutes <= 0:
        parser.error("--since-minutes must be positive")
    platforms = {value.strip() for value in args.platforms.split(",") if value.strip()}
    if not platforms <= PLATFORMS:
        parser.error("--platforms must contain only facebook,instagram,threads")
    return verify(args.since_minutes, args.require_attempt, platforms or None)


if __name__ == "__main__":
    raise SystemExit(main())
