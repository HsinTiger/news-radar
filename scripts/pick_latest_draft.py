#!/usr/bin/env python3
"""Pick the newest draft that has not already produced a reel."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


PLATFORM_LOG_KEY = {
    "ig": "instagram_reel",
    "instagram": "instagram_reel",
    "fb": "facebook_reel",
    "facebook": "facebook_reel",
    "threads": "threads_reel",
}


def pick_latest_unpublished(conn: sqlite3.Connection, platform: str) -> str | None:
    log_key = PLATFORM_LOG_KEY.get(platform)
    if not log_key:
        raise ValueError(f"unsupported reel platform: {platform}")
    row = conn.execute(
        """
        SELECT d.id
          FROM drafts d
         WHERE d.status IN ('auto_approved', 'published')
           AND NOT EXISTS (
               SELECT 1
                 FROM publish_log p
                WHERE p.draft_id = d.id
                  AND p.platform = ?
                  AND p.success = 1
           )
         ORDER BY d.generated_at DESC
         LIMIT 1
        """,
        (log_key,),
    ).fetchone()
    return row["id"] if row else None


def main() -> int:
    from src import db as dbmod

    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", default="ig", choices=sorted(PLATFORM_LOG_KEY))
    args = parser.parse_args()
    conn = dbmod.get_conn()
    try:
        draft_id = pick_latest_unpublished(conn, args.platform)
    finally:
        conn.close()
    if not draft_id:
        return 2
    print(draft_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
