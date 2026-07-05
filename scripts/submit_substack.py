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
                        body: str, source_type: str, extra_tags: list[str],
                        immediate: bool = False) -> dict:
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
        # `immediate` 標籤讓 Mac 端的快速 drain（每 5 分鐘）優先挑出這篇立刻寫稿，
        # 不必等每小時的常規 drain。見 scripts/drain_substack.py --only-immediate。
        tags=[SUBSTACK_TAG, "user_submission_substack", *extra_tags,
              *(["immediate"] if immediate else [])],
        status="fetched",
    )
    dbmod.upsert_news(conn, item)
    conn.close()
    return {"status": "created", "id": news_id, "title": item.title,
            "word_count": item.word_count, "target": "substack"}


def process_url(url: str, note: str = "", immediate: bool = False) -> dict:
    news_id = _make_news_id("substack_" + url)
    body = _fetch_page_text(url) or ""
    title = note or (url.split("/")[-1][:80] or "Substack submission")
    return _save_substack_item(news_id, url=url, title=title, body=body,
                               source_type="article", extra_tags=[], immediate=immediate)


def process_text(text: str, note: str = "", immediate: bool = False) -> dict:
    h = hashlib.md5(text.encode()).hexdigest()
    news_id = _make_news_id(f"substack_text_{h}")
    title = note or (text[:60] + ("..." if len(text) > 60 else ""))
    # news_items.url 是 UNIQUE NOT NULL：全文投稿沒有來源網址，但不能用空字串——
    # 第二篇 url='' 會撞 UNIQUE constraint 整個 submit 崩掉（2026-07-05 修）。
    # 改用每篇唯一的合成 url（非 http scheme，下游不會去抓）＝內容雜湊，天然去重。
    synthetic_url = f"manual-text://{h}"
    return _save_substack_item(news_id, url=synthetic_url, title=title, body=text,
                               source_type="text", extra_tags=["user_text"], immediate=immediate)


def process_youtube(url: str, note: str = "", immediate: bool = False) -> dict:
    info = _extract_yt_transcript(url)
    if not info:
        # No transcript — fall back to treating it as a URL source.
        # 仍標 youtube + enrich_yt：Mac 端 drain 會用 Whisper 把無字幕影片轉出來。
        news_id = _make_news_id("substack_yt_nocap_" + _make_news_id(url))
        body = f"# {note or 'YouTube'}\n\n（無字幕，Mac 端會用 Whisper 轉逐字稿）\n\n## 種子來源\n{url}\n"
        return _save_substack_item(news_id, url=url, title=note or "YouTube (no transcript)",
                                   body=body, source_type="youtube",
                                   extra_tags=["youtube", "video", "enrich_yt", "no_caption"],
                                   immediate=immediate)
    news_id = _make_news_id("substack_yt_" + info["video_id"])
    title = note or info["title"]
    body = f"# {info['title']}\n\n(YouTube transcript, lang={info['language']})\n\n{info['transcript']}"
    return _save_substack_item(news_id, url=url, title=title, body=body,
                               source_type="youtube", extra_tags=["youtube", "video", "enrich_yt"],
                               immediate=immediate)


def process_youtube_multi(urls: list[str], note: str = "", immediate: bool = False) -> dict:
    """多支 YouTube 種子（巨人之聲多源）：把全部網址寫進 body，讓 Mac 端 drain
    觸發 enrich_youtube_sources.py 建『一主題 × 多一手源 + 書面深度報告』素材包。"""
    urls = [u.strip() for u in urls if u.strip()]
    if not urls:
        return {"status": "error", "error": "no youtube urls"}
    if len(urls) == 1:
        return process_youtube(urls[0], note, immediate=immediate)
    key = hashlib.md5("|".join(sorted(urls)).encode()).hexdigest()
    news_id = _make_news_id("substack_ytmulti_" + key)
    seeds = "\n".join(urls)
    title = note or "巨人之聲 · 多源 YouTube"
    body = (f"# {title}\n\n（{len(urls)} 支 YouTube 種子；Mac 端會自動建深度素材包："
            f"全逐字稿（無字幕走 Whisper）＋自動搜尋對應書面深度報告）\n\n## 種子來源\n{seeds}\n")
    return _save_substack_item(news_id, url=urls[0], title=title, body=body,
                               source_type="youtube",
                               extra_tags=["youtube", "video", "enrich_yt", "multi_source"],
                               immediate=immediate)


_RAW_BASE = "https://raw.githubusercontent.com/HsinTiger/news-radar/main/"


def process_images(paths: list[str], note: str = "", immediate: bool = False) -> dict:
    """One or more uploaded screenshots → ONE Substack draft seed.

    There is no OCR in the cloud, so `note` is REQUIRED and used as the textual
    seed (what the images are about / your angle). The images are embedded as
    markdown refs so compose.py can reference them in the draft.
    """
    paths = [p.strip() for p in paths if p.strip()]
    if not paths:
        return {"status": "error", "error": "no image paths"}
    if not note.strip():
        return {"status": "error", "error": "note required for image submission (no OCR — describe the topic/angle)"}

    key = "|".join(sorted(paths))
    news_id = _make_news_id("substack_img_" + hashlib.md5(key.encode()).hexdigest())
    refs = "\n".join(f"![screenshot]({_RAW_BASE}{p})" for p in paths)
    body = f"# {note}\n\n（使用者上傳 {len(paths)} 張截圖作為素材，主題：{note}）\n\n{refs}"
    return _save_substack_item(news_id, url=_RAW_BASE + paths[0], title=note, body=body,
                               source_type="image", extra_tags=["user_image", f"images:{len(paths)}"],
                               immediate=immediate)


def main():
    import argparse
    p = argparse.ArgumentParser(description="Submit a source for a SUBSTACK draft")
    p.add_argument("--url", type=str)
    p.add_argument("--text", type=str)
    p.add_argument("--yt", type=str, help="YouTube 網址；可逗號分隔多支（巨人之聲多源種子）")
    p.add_argument("--images", type=str, help="comma-separated repo-relative image paths")
    p.add_argument("--note", type=str, default="")
    p.add_argument("--immediate", action="store_true",
                   help="標記 immediate → Mac 端快速 drain（每 5 分鐘）優先立刻寫稿，不等每小時排程")
    args = p.parse_args()

    if args.url:
        result = process_url(args.url, args.note, immediate=args.immediate)
    elif args.text:
        result = process_text(args.text, args.note, immediate=args.immediate)
    elif args.yt:
        result = process_youtube_multi(args.yt.split(","), args.note, immediate=args.immediate)
    elif args.images:
        result = process_images(args.images.split(","), args.note, immediate=args.immediate)
    else:
        result = {"status": "error", "error": "one of --url / --text / --yt / --images required"}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in ("created", "already_exists") else 1


if __name__ == "__main__":
    sys.exit(main())
