#!/usr/bin/env python3
"""Submit a source destined for a SUBSTACK draft (not Meta).

This is the Substack counterpart to scripts/submit_source.py. It is kept as a
SEPARATE script so the two manual-submit paths stay cleanly divided:

    submit_source.py    → Meta (fb / ig / threads)  → tags platform:*  feed_name=user_submission
    submit_substack.py  → Substack draft            → tags substack_source  feed_name=user_substack

A Substack submission is written into the SAME news_items table (so it rides the
existing state-branch DB sync), but with:
  - feed_name = "user_substack"          → Meta pipeline keys on user_submission, so it skips this
  - tags      = ["substack_source", ...] → NO platform:* tags, so Meta publish never targets it

The local Mac then composes the draft from this row, token-free, via:
    substack_radar/compose.py morning --news-id <id>
(see scripts/drain_substack.py / the launchd drain job).

Usage (same shape as submit_source.py):
    python scripts/submit_substack.py --url "https://..."        --note "..."
    python scripts/submit_substack.py --text "full article body" --note "title"
    python scripts/submit_substack.py --yt  "https://youtu.be/.." --note "..."
"""
from __future__ import annotations
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from src import db as dbmod
from src.schema import NewsItem

# Reuse the proven fetch/transcript helpers from the Meta submit script —
# single source of truth, no duplication of the scraping logic.
from scripts.submit_source import (
    _fetch_page_text,
    _extract_yt_transcript,
    _make_news_id,
)

SUBSTACK_TAG = "substack_source"
SUBSTACK_FEED = "user_substack"


def _save_substack_item(news_id: str, *, url: str | None, title: str,
                        body: str, source_type: str, extra_tags: list[str]) -> dict:
    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
        conn.close()
        return {"status": "already_exists", "id": news_id}
    now = datetime.now(timezone.utc).isoformat()
    item = NewsItem(
        id=news_id,
        feed_name=SUBSTACK_FEED,
        feed_tier="primary",
        source_type=source_type,
        url=url,
        title=title[:120] or "Substack submission",
        published_at=now,
        fetched_at=now,
        clean_markdown=body or "",
        word_count=len((body or "").split()),
        og_image_url=None,
        tags=[SUBSTACK_TAG, "user_submission_substack", *extra_tags],
        status="fetched",
    )
    dbmod.upsert_news(conn, item)
    conn.close()
    return {"status": "created", "id": news_id, "title": item.title,
            "word_count": item.word_count, "target": "substack"}


def process_url(url: str, note: str = "") -> dict:
    news_id = _make_news_id("substack_" + url)
    body = _fetch_page_text(url) or ""
    title = note or (url.split("/")[-1][:80] or "Substack submission")
    return _save_substack_item(news_id, url=url, title=title, body=body,
                               source_type="article", extra_tags=[])


def process_text(text: str, note: str = "") -> dict:
    h = hashlib.md5(text.encode()).hexdigest()
    news_id = _make_news_id(f"substack_text_{h}")
    title = note or (text[:60] + ("..." if len(text) > 60 else ""))
    return _save_substack_item(news_id, url=None, title=title, body=text,
                               source_type="text", extra_tags=["user_text"])


def process_youtube(url: str, note: str = "") -> dict:
    info = _extract_yt_transcript(url)
    if not info:
        # No transcript — fall back to treating it as a URL source.
        return process_url(url, note=note or "YouTube (no transcript)")
    news_id = _make_news_id("substack_yt_" + info["video_id"])
    title = note or info["title"]
    body = f"# {info['title']}\n\n(YouTube transcript, lang={info['language']})\n\n{info['transcript']}"
    return _save_substack_item(news_id, url=url, title=title, body=body,
                               source_type="youtube", extra_tags=["youtube", "video"])


def main():
    import argparse
    p = argparse.ArgumentParser(description="Submit a source for a SUBSTACK draft")
    p.add_argument("--url", type=str)
    p.add_argument("--text", type=str)
    p.add_argument("--yt", type=str)
    p.add_argument("--note", type=str, default="")
    args = p.parse_args()

    if args.url:
        result = process_url(args.url, args.note)
    elif args.text:
        result = process_text(args.text, args.note)
    elif args.yt:
        result = process_youtube(args.yt, args.note)
    else:
        result = {"status": "error", "error": "one of --url / --text / --yt required"}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in ("created", "already_exists") else 1


if __name__ == "__main__":
    sys.exit(main())
