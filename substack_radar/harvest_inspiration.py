"""
News Radar · Substack Inspiration Harvester (token-free)
========================================================
2026-05-30. Runs BEFORE each Substack compose slot to refresh the pool of raw
material the writer draws from — replacing the old paid agentic WebSearch/WebFetch
research with deterministic, zero-LLM-token harvesting that reuses existing infra:

    1. RSS / article harvest  → run_harvest.run_harvest_once()  (src/fetcher.py + trafilatura)
       (now includes good-news + tech/business "inspiration" feeds added to config.yaml)
    2. YouTube transcripts     → src.youtube_transcripts.harvest_youtube_transcripts()
       (yt-dlp auto/manual subtitles → news_items, source_type='video')

Both write into the same news_items table the morning slot already reads from, and
that evening's pick_evening_inspiration() ranks deterministically.

Usage:
    python tools/substack_harvest_inspiration.py            # full harvest
    python tools/substack_harvest_inspiration.py --no-youtube
    python tools/substack_harvest_inspiration.py --dry-run  # YouTube dry-run; RSS still writes
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except Exception:
    pass


async def _run(args: argparse.Namespace) -> int:
    print("=" * 60)
    print("Substack inspiration harvest (token-free)")
    print("=" * 60)

    # 1) RSS / article harvest (reuses the main deterministic harvester)
    try:
        from run_harvest import run_harvest_once

        report = await run_harvest_once()
        print(f"[Harvest] RSS new={report.items_new} dropped={report.items_dropped}")
    except Exception as exc:  # noqa: BLE001
        print(f"[Harvest] ⚠️ RSS harvest failed (continuing): {exc}")

    # 2) YouTube transcripts (yt-dlp; skipped cleanly if no binary / no sources)
    if args.no_youtube:
        print("[YT] skipped (--no-youtube)")
    else:
        try:
            from substack_radar.youtube_transcripts import harvest_youtube_transcripts

            items = harvest_youtube_transcripts(dry_run=args.dry_run)
            print(f"[YT] transcripts harvested: {len(items)}")
        except Exception as exc:  # noqa: BLE001
            print(f"[YT] ⚠️ transcript harvest failed (continuing): {exc}")

    print("\n✅ Inspiration pool refreshed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Token-free Substack inspiration harvest.")
    ap.add_argument("--no-youtube", action="store_true", help="skip YouTube transcript harvest")
    ap.add_argument("--dry-run", action="store_true", help="YouTube dry-run (no DB write for YT)")
    args = ap.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
