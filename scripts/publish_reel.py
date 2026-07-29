#!/usr/bin/env python3
"""Upload and idempotently publish one reel, then persist its platform ID."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PLATFORMS = ("fb", "ig", "threads")


async def publish(args: argparse.Namespace) -> int:
    del args
    print(
        "LIVE_REEL_PUBLISHING_RETIRED: every Meta feed post must use the "
        "governed three-card carousel. Use reels_publish.yml only for render/QC.",
        file=sys.stderr,
    )
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("platform", choices=PLATFORMS)
    parser.add_argument("--draft-id", required=True)
    parser.add_argument("--caption", default="")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(publish(_parser().parse_args())))
