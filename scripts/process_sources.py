#!/usr/bin/env python3
"""
Process user-submitted sources for the pipeline.
Reads from data/sources/pending.json → creates news_items entries.

Usage:
    python scripts/process_sources.py              # process all pending
    python scripts/process_sources.py --dry-run     # preview only

User submits via dashboard → localStorage → exported JSON → data/sources/pending.json
"""
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from src import db as dbmod
from src.schema import NewsItem

SOURCES_DIR = _HERE / "data" / "sources"
SOURCES_FILE = SOURCES_DIR / "pending.json"
PROCESSED_FILE = SOURCES_DIR / "processed.json"


def load_pending() -> list:
    if not SOURCES_FILE.exists():
        return []
    try:
        return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_pending(sources: list):
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    SOURCES_FILE.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")


def append_processed(entry: dict, status: str):
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    processed = []
    if PROCESSED_FILE.exists():
        try:
            processed = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            pass
    entry["processed_at"] = datetime.now(timezone.utc).isoformat()
    entry["status"] = status
    processed.append(entry)
    PROCESSED_FILE.write_text(json.dumps(processed, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def process_sources(dry_run: bool = False) -> dict:
    pending = load_pending()
    if not pending:
        return {"status": "empty", "message": "無待處理來源"}

    results = {"total": len(pending), "created": 0, "skipped": 0, "errors": []}
    remaining = []
    conn = dbmod.get_conn()

    for entry in pending:
        url = entry.get("url", "").strip()
        if not url:
            remaining.append(entry)
            continue

        # Create a simple news_items entry
        import hashlib
        news_id = hashlib.sha1(url.encode()).hexdigest()

        if dbmod.news_exists(conn, news_id):
            results["skipped"] += 1
            append_processed(entry, "already_exists")
            continue

        title = entry.get("note", "") or url.split("/")[-1][:50] or "User submitted"
        platforms = entry.get("platforms", [])
        # We'll tag the news item so composer knows which platforms to target

        if not dry_run:
            item = NewsItem(
                id=news_id,
                feed_name="user_submission",
                feed_tier="primary",
                source_type="article",
                url=url,
                title=title,
                published_at=datetime.now(timezone.utc).isoformat(),
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="fetched",
                tags=["user_submission"] + [f"platform:{p}" for p in platforms],
            )
            dbmod.upsert_news(conn, item)
            results["created"] += 1
            append_processed(entry, "created")
        else:
            results["created"] += 1
            print(f"  [DRY-RUN] would create: {title[:40]} → {', '.join(platforms)}")

    conn.close()

    if not dry_run:
        save_pending(remaining)

    results["remaining"] = len(remaining)
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Process user-submitted sources")
    parser.add_argument("--dry-run", action="store_true", help="預覽不寫入")
    args = parser.parse_args()

    result = process_sources(dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("errors") == [] else 1


if __name__ == "__main__":
    sys.exit(main())
