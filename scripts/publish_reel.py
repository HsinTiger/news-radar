#!/usr/bin/env python3
"""Upload and idempotently publish one reel, then persist its platform ID."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from src import db as dbmod
from src.cover_uploader import upload_cover
from src.publisher import publish_to_fb, publish_to_ig, publish_to_threads
from src.schema import PublishResult


PLATFORMS = {
    "ig": ("instagram", "instagram_reel", publish_to_ig, "IG Reels"),
    "fb": ("facebook", "facebook_reel", publish_to_fb, "FB Reels"),
    "threads": ("threads", "threads_reel", publish_to_threads, "Threads Video"),
}


def _caption(conn, draft_id: str, feed_platform: str, supplied: str) -> str:
    if supplied.strip():
        return supplied.strip()
    row = conn.execute(
        """
        SELECT COALESCE(NULLIF(final_text, ''), NULLIF(full_text, ''), '') AS text
          FROM platform_drafts
         WHERE draft_id = ? AND platform = ?
         LIMIT 1
        """,
        (draft_id, feed_platform),
    ).fetchone()
    if row and row["text"]:
        return row["text"].strip()
    title = conn.execute("SELECT title FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    return (title["title"] if title else "").strip()


async def publish(args: argparse.Namespace) -> int:
    video_path = args.video
    if not video_path.is_file():
        print(f"ERROR: {video_path} not found", file=sys.stderr)
        return 1
    feed_platform, log_platform, publisher, display_name = PLATFORMS[args.platform]
    conn = dbmod.get_conn()
    try:
        if dbmod.has_successful_publish(conn, args.draft_id, log_platform):
            print(
                f"IDEMPOTENT_SKIP: draft={args.draft_id} platform={log_platform}"
            )
            return 0
        caption = _caption(conn, args.draft_id, feed_platform, args.caption)
        slug = f"reel_{args.draft_id[:20]}"
        print(f"Uploading {video_path} to cover-cdn as {slug}...")
        url = upload_cover(
            local_png=video_path,
            draft_id=slug,
            platform_key=args.platform,
            file_ext="mp4",
        )
        if not url:
            print("ERROR: upload to cover-cdn failed", file=sys.stderr)
            return 1
        print(f"Publishing to {display_name}...")
        result = await publisher(caption[:2200], video_url=url)
        success = bool(result.get("success"))
        dbmod.log_publish(
            conn,
            PublishResult(
                draft_id=args.draft_id,
                platform=log_platform,
                platform_post_id=str(result.get("id")) if success else None,
                posted_at=datetime.now(timezone.utc).isoformat(),
                success=success,
                error_message=None if success else str(result.get("error", "unknown")),
            ),
        )
        if not success:
            print(f"FAILED: {result.get('error', 'unknown error')}", file=sys.stderr)
            return 1
        print(f"SUCCESS: {result.get('id')}")
        return 0
    finally:
        conn.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("platform", choices=sorted(PLATFORMS))
    parser.add_argument("--draft-id", required=True)
    parser.add_argument("--caption", default="")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(publish(_parser().parse_args())))
