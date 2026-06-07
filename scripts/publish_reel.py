#!/usr/bin/env python3
"""Upload a reel MP4 to cover-cdn branch and publish to Meta platform."""
import asyncio, sys, os
from pathlib import Path
_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from src.cover_uploader import upload_cover
from src.publisher import publish_to_ig, publish_to_fb, publish_to_threads

async def main():
    video_path = Path(sys.argv[1])
    platform = sys.argv[2] if len(sys.argv) > 2 else "ig"
    caption = sys.argv[3] if len(sys.argv) > 3 else ""

    if not video_path.exists():
        print(f"ERROR: {video_path} not found")
        sys.exit(1)

    slug = f"reel_{video_path.stem}"
    print(f"Uploading {video_path} to cover-cdn as {slug}...")
    url = upload_cover(local_png=video_path, draft_id=slug, platform_key=platform, file_ext="mp4")
    if not url:
        print("ERROR: upload to cover-cdn failed")
        sys.exit(1)
    print(f"Cover CDN URL: {url}")

    publishers = {
        "ig": (publish_to_ig, "IG Reels"),
        "fb": (publish_to_fb, "FB Reels"),
        "threads": (publish_to_threads, "Threads Video"),
    }
    if platform not in publishers:
        print(f"ERROR: unknown platform {platform}")
        sys.exit(1)

    func, pname = publishers[platform]
    print(f"Publishing to {pname}...")
    result = await func(caption[:2200], video_url=url)
    if result.get("success"):
        print(f"SUCCESS: {result.get('id')}")
    else:
        print(f"FAILED: {result.get('error', 'unknown error')}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
