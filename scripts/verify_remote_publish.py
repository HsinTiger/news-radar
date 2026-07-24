#!/usr/bin/env python3
"""Verify that Meta can read back every just-published requested post.

Local ``publish_log`` evidence proves that the publisher returned an ID.  This
independent verifier makes the next authority jump: it sends that exact ID back
to the platform insights API and requires a readable response.  Zero metrics
are valid; an unreadable ID, expired token, or API error is not.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db as dbmod  # noqa: E402
from src.engagement import PLATFORM_FETCHERS  # noqa: E402


PLATFORMS = {"facebook", "instagram", "threads"}
Fetcher = Callable[[httpx.AsyncClient, str], Awaitable[dict[str, Any]]]


def recent_post_ids(
    conn,
    *,
    since_minutes: int,
    platforms: set[str],
) -> tuple[dict[str, str], list[str]]:
    placeholders = ",".join("?" for _ in platforms)
    rows = conn.execute(
        f"""
        SELECT platform,platform_post_id,posted_at,id
          FROM publish_log
         WHERE success=1
           AND platform IN ({placeholders})
           AND datetime(posted_at) >= datetime('now', ?)
         ORDER BY datetime(posted_at) DESC,id DESC
        """,
        (*sorted(platforms), f"-{since_minutes} minutes"),
    ).fetchall()
    post_ids: dict[str, str] = {}
    failures: list[str] = []
    for row in rows:
        platform = row["platform"]
        post_id = str(row["platform_post_id"] or "").strip()
        if platform not in post_ids and post_id:
            post_ids[platform] = post_id
    for platform in sorted(platforms):
        if platform not in post_ids:
            failures.append(f"missing_recent_platform_post_id:{platform}")
    return post_ids, failures


async def verify(
    since_minutes: int,
    platforms: set[str],
    *,
    conn=None,
    fetchers: Mapping[str, Fetcher] | None = None,
) -> int:
    owns_conn = conn is None
    if conn is None:
        conn = dbmod.get_conn()
    failures: list[str] = []
    try:
        post_ids, local_failures = recent_post_ids(
            conn,
            since_minutes=since_minutes,
            platforms=platforms,
        )
        failures.extend(local_failures)
        active_fetchers = fetchers or PLATFORM_FETCHERS
        async with httpx.AsyncClient(timeout=45.0) as client:
            for platform in sorted(platforms):
                post_id = post_ids.get(platform)
                if not post_id:
                    continue
                fetcher = active_fetchers.get(platform)
                if fetcher is None:
                    failures.append(f"missing_fetcher:{platform}")
                    continue
                try:
                    result = await fetcher(client, post_id)
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        f"remote_readback_exception:{platform}:{type(exc).__name__}"
                    )
                    continue
                if not result.get("ok"):
                    failures.append(f"remote_readback_failed:{platform}")
                    continue
                nonzero = sorted(
                    name
                    for name in (
                        "views", "reach", "clicks", "likes", "comments",
                        "shares", "saves", "replies", "reposts", "quotes",
                    )
                    if int(result.get(name) or 0) > 0
                )
                print(
                    f"✅ [RemoteReadback] {platform} id={post_id[:18]}… "
                    f"readable=true nonzero={','.join(nonzero) or 'none'}"
                )
        if failures:
            for failure in failures:
                print(f"❌ [RemoteReadback] {failure}")
            print("❌ [RemoteReadback] FAIL")
            return 1
        print("✅ [RemoteReadback] PASS")
        return 0
    finally:
        if owns_conn:
            conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-minutes", type=int, default=30)
    parser.add_argument("--platforms", required=True)
    args = parser.parse_args()
    if args.since_minutes <= 0:
        parser.error("--since-minutes must be positive")
    platforms = {
        value.strip() for value in args.platforms.split(",") if value.strip()
    }
    if not platforms or not platforms <= PLATFORMS:
        parser.error("--platforms must contain only facebook,instagram,threads")
    return asyncio.run(verify(args.since_minutes, platforms))


if __name__ == "__main__":
    raise SystemExit(main())
