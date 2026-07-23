"""
News Radar · Submit Source (unified entry for user-submitted content)
======================================================================
Handles 4 source types that user submits via Dashboard:
  - URL: fetch article content
  - Text: directly paste body text
  - YouTube: extract transcript via youtube-transcript-api
  - Image: accept image URL / analysis only (no CV yet)

Usage:
    python scripts/submit_source.py --url "https://..."
    python scripts/submit_source.py --text "文章內容..."
    python scripts/submit_source.py --yt "https://youtube.com/watch?v=..."
    python scripts/submit_source.py --image "https://..."
"""
from __future__ import annotations
import hashlib, json, os, re, sys, tempfile, shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))
from src import db as dbmod
from src.schema import NewsItem

SUBMISSIONS_DIR = _HERE / "data" / "submissions"
URLS_FILE = SUBMISSIONS_DIR / "pending_urls.json"
TEXTS_FILE = SUBMISSIONS_DIR / "pending_texts.json"
YTS_FILE = SUBMISSIONS_DIR / "pending_youtube.json"
IMAGES_FILE = SUBMISSIONS_DIR / "pending_images.json"
PROCESSED_FILE = SUBMISSIONS_DIR / "processed.json"

YT_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)
PLATFORM_ALIASES = {
    "fb": "fb",
    "facebook": "fb",
    "ig": "ig",
    "instagram": "ig",
    "threads": "threads",
}


def _ensure_dirs():
    SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def _save_json(path: Path, data: list):
    _ensure_dirs()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_processed(entry: dict, status: str):
    _ensure_dirs()
    processed = _load_json(PROCESSED_FILE)
    entry["processed_at"] = datetime.now(timezone.utc).isoformat()
    entry["status"] = status
    processed.append(entry)
    _save_json(PROCESSED_FILE, processed)


def _make_news_id(url_or_text: str) -> str:
    return hashlib.sha1(url_or_text.encode()).hexdigest()


def _normalize_platforms(platforms: Optional[list]) -> list[str]:
    values = platforms or ["fb", "ig", "threads"]
    normalized: list[str] = []
    invalid: list[str] = []
    for raw in values:
        value = PLATFORM_ALIASES.get(str(raw).strip().lower())
        if value is None:
            invalid.append(str(raw))
        elif value not in normalized:
            normalized.append(value)
    if invalid:
        raise ValueError(f"unknown platforms: {','.join(invalid)}")
    if not normalized:
        raise ValueError("at least one Meta platform is required")
    return normalized


def _submission_tags(
    platforms: list,
    *extra: str,
    submission_id: str = "",
) -> list[str]:
    """Build governed routing/lineage tags for one owner submission."""
    tags = ["user_submission", *extra]
    tags.extend(f"platform:{platform}" for platform in platforms)
    if submission_id:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", submission_id):
            raise ValueError("invalid submission_id")
        tags.append(f"control_submission:{submission_id}")
        tags.append(f"control_route:{submission_id}:{','.join(platforms)}")
    return tags


def _merge_existing_tags(conn, news_id: str, tags: list[str]) -> None:
    """Attach a new control submission to a deduplicated source row."""
    row = conn.execute("SELECT tags FROM news_items WHERE id=?", (news_id,)).fetchone()
    if row is None:
        return
    try:
        existing = json.loads(row["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        existing = []
    merged = list(dict.fromkeys([*existing, *tags]))
    conn.execute(
        "UPDATE news_items SET tags=? WHERE id=?",
        (json.dumps(merged, ensure_ascii=False), news_id),
    )
    conn.commit()


def _resolve_google_news(url: str) -> str:
    """Google News /read/ & /articles/ URLs wrap the real article in a base64
    token. Resolve to the destination via Google's batchexecute API. Returns the
    real URL, or the original url on any failure."""
    import re, json
    import httpx
    if "news.google.com" not in url:
        return url
    m = re.search(r"/(?:read|articles|rss/articles)/([A-Za-z0-9_\-]+)", url)
    if not m:
        return url
    H = {"User-Agent": "Mozilla/5.0"}
    try:
        r = httpx.get(f"https://news.google.com/rss/articles/{m.group(1)}",
                      headers=H, timeout=20, follow_redirects=True)
        sig = re.search(r'data-n-a-sg="([^"]+)"', r.text)
        ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
        gid = re.search(r'data-n-a-id="([^"]+)"', r.text)
        if not (sig and ts and gid):
            return url
        inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
                 'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
                 '"%s",%s,"%s"]') % (gid.group(1), ts.group(1), sig.group(1))
        freq = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        resp = httpx.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",
            headers={**H, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            data={"f.req": freq}, timeout=20)
        # response embeds: ["garturlres","<REAL_URL>",1,"<AMP_URL>"]
        mm = re.search(r'\\"garturlres\\",\\"(https?://[^\\"]+)\\"', resp.text)
        if mm:
            return mm.group(1).encode().decode("unicode_escape")
    except Exception:
        pass
    return url


def _fetch_page_text(url: str) -> Optional[str]:
    """Fetch article text from URL using trafilatura. Resolves Google News
    wrapper URLs to the real article first."""
    import httpx
    import trafilatura
    url = _resolve_google_news(url)
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            text = trafilatura.extract(resp.text)
            return text if text else None
    except Exception:
        pass
    return None


def _extract_yt_transcript(url: str) -> Optional[Dict]:
    """Extract YouTube transcript using youtube-transcript-api.
    Returns {video_id, title, transcript, language} or None."""
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.formatters import TextFormatter

    match = YT_VIDEO_ID_RE.search(url)
    if not match:
        return None
    video_id = match.group(1)

    try:
        # Try manual subs first, fall back to auto-generated
        # youtube-transcript-api 1.x：class method list_transcripts() 改成 instance .list()。
        # 0.6.x 的 .fetch() 因 YouTube 端點改版會 ParseError，故升級到 1.x（見 requirements.txt）。
        transcript_list = YouTubeTranscriptApi().list(video_id)
        transcript = None
        lang = "zh-TW"
        try:
            # Prefer Traditional Chinese
            tr = transcript_list.find_transcript(["zh-TW", "zh-Hant", "zh", "en"])
            transcript = tr.fetch()
            lang = tr.language_code
        except Exception:
            # Try any manual transcript
            for tr in transcript_list:
                if not tr.is_generated:
                    transcript = tr.fetch()
                    lang = tr.language_code
                    break
            if not transcript:
                # Fall back to auto-generated English
                tr = transcript_list.find_generated_transcript(["en"])
                transcript = tr.fetch()
                lang = "en"

        if not transcript:
            return None

        formatter = TextFormatter()
        text = formatter.format_transcript(transcript)

        # Get video title via a simple fetch
        title = f"YouTube {video_id}"
        try:
            import httpx
            resp = httpx.get(f"https://www.youtube.com/watch?v={video_id}", timeout=10)
            if resp.status_code == 200:
                m = re.search(r'<title>(.+?)</title>', resp.text)
                if m:
                    title = m.group(1).replace(" - YouTube", "")
        except Exception:
            pass

        return {
            "video_id": video_id,
            "title": title.strip(),
            "transcript": text,
            "language": lang,
            "length_min": round(len(text.split()) / 150),  # rough estimate
        }
    except Exception as e:
        return None


def process_url(
    url: str,
    note: str = "",
    platforms: list = None,
    submission_id: str = "",
) -> dict:
    """Save a URL for pipeline to process."""
    platforms = _normalize_platforms(platforms)
    news_id = _make_news_id(url)

    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
        _merge_existing_tags(
            conn,
            news_id,
            _submission_tags(platforms, submission_id=submission_id),
        )
        conn.close()
        return {"status": "already_exists", "id": news_id}

    # Try to pre-fetch article text
    article_text = _fetch_page_text(url)
    item = NewsItem(
        id=news_id,
        feed_name="user_submission",
        feed_tier="primary",
        source_type="article",
        url=url,
        title=note or url.split("/")[-1][:50] or "User submitted",
        published_at=datetime.now(timezone.utc).isoformat(),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        clean_markdown=article_text or "",
        word_count=len(article_text or ""),
        og_image_url=None,
        tags=_submission_tags(platforms, submission_id=submission_id),
        status="fetched",
    )
    dbmod.upsert_news(conn, item)
    conn.close()
    _append_processed({"type": "url", "url": url, "platforms": platforms}, "created")
    return {"status": "created", "id": news_id, "title": item.title, "word_count": item.word_count}


def process_text(
    text: str,
    note: str = "",
    platforms: list = None,
    submission_id: str = "",
) -> dict:
    """Save user-pasted text body as a news item."""
    platforms = _normalize_platforms(platforms)
    content_hash = hashlib.md5(text.encode()).hexdigest()
    news_id = _make_news_id(f"user_text_{content_hash}")

    title = note or text[:40] + ("..." if len(text) > 40 else "")
    item = NewsItem(
        id=news_id,
        feed_name="user_submission",
        feed_tier="primary",
        source_type="article",
        url=f"user:text:{content_hash}",
        title=title,
        published_at=datetime.now(timezone.utc).isoformat(),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        clean_markdown=text,
        word_count=len(text.split()),
        tags=_submission_tags(platforms, "user_text", submission_id=submission_id),
        status="fetched",
    )
    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
        _merge_existing_tags(conn, news_id, item.tags)
        conn.close()
        return {"status": "already_exists", "id": news_id}
    dbmod.upsert_news(conn, item)
    conn.close()
    _append_processed({"type": "text", "title": title, "platforms": platforms}, "created")
    return {"status": "created", "id": news_id, "title": title, "word_count": item.word_count}


def process_youtube(
    url: str,
    note: str = "",
    platforms: list = None,
    submission_id: str = "",
) -> dict:
    """Extract YouTube transcript and save as news item."""
    platforms = _normalize_platforms(platforms)
    result = _extract_yt_transcript(url)
    if not result:
        # Fallback: save URL only, let pipeline handle
        return process_url(
            url,
            note=f"YouTube: {note}" if note else "YouTube video",
            platforms=platforms,
            submission_id=submission_id,
        )

    news_id = _make_news_id(url)
    title = result["title"]
    transcript = result["transcript"]
    video_id = result["video_id"]

    item = NewsItem(
        id=news_id,
        feed_name="user_submission",
        feed_tier="primary",
        source_type="video",
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=title,
        published_at=datetime.now(timezone.utc).isoformat(),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        clean_markdown=f"YouTube Transcript: {title}\n\n{transcript}",
        word_count=len(transcript.split()),
        tags=_submission_tags(
            platforms, "youtube", "video", submission_id=submission_id
        ),
        status="fetched",
    )
    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
        _merge_existing_tags(conn, news_id, item.tags)
        conn.close()
        return {"status": "already_exists", "id": news_id}
    dbmod.upsert_news(conn, item)
    conn.close()
    _append_processed({"type": "youtube", "video_id": video_id, "title": title, "platforms": platforms}, "created")
    return {"status": "created", "id": news_id, "title": title, "word_count": item.word_count}


def process_image(
    image_url: str,
    note: str = "",
    platforms: list = None,
    submission_id: str = "",
) -> dict:
    """Save an image URL for analysis + posting."""
    platforms = _normalize_platforms(platforms)
    news_id = _make_news_id(image_url)
    title = note or "Image submission"
    item = NewsItem(
        id=news_id,
        feed_name="user_submission",
        feed_tier="primary",
        source_type="article",
        url=image_url,
        title=title,
        published_at=datetime.now(timezone.utc).isoformat(),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        clean_markdown=f"User submitted image: {image_url}\n\nNote: {note}" if note else f"User submitted image: {image_url}",
        word_count=len(note or "") + 20,
        og_image_url=image_url,
        tags=_submission_tags(
            platforms, "user_image", submission_id=submission_id
        ),
        status="fetched",
    )
    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
        _merge_existing_tags(conn, news_id, item.tags)
        conn.close()
        return {"status": "already_exists", "id": news_id}
    dbmod.upsert_news(conn, item)
    conn.close()
    _append_processed({"type": "image", "image_url": image_url, "platforms": platforms}, "created")
    return {"status": "created", "id": news_id}


_IMAGE_DIR = SUBMISSIONS_DIR / "uploaded_images"

def process_image_base64(
    base64_data: str,
    filename: str = "upload.jpg",
    caption: str = "",
    platforms: list = None,
    submission_id: str = "",
) -> dict:
    """Save base64 image data as file + news_item."""
    import base64
    platforms = _normalize_platforms(platforms)
    
    # Parse base64 data URL (e.g. data:image/jpeg;base64,/9j...)
    if "," in base64_data:
        header, b64 = base64_data.split(",", 1)
    else:
        b64 = base64_data
        header = ""
    
    # Determine extension
    ext = "jpg"
    if "png" in header:
        ext = "png"
    elif "heic" in header or "heif" in header:
        ext = "heic"
    
    _ensure_dirs()
    _IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    
    safe_name = hashlib.md5(b64.encode()).hexdigest()[:12]
    img_filename = f"{safe_name}.{ext}"
    img_path = _IMAGE_DIR / img_filename
    
    try:
        img_bytes = base64.b64decode(b64)
        img_path.write_bytes(img_bytes)
    except Exception as e:
        return {"status": "error", "message": f"Base64 decode failed: {e}"}
    
    news_id = hashlib.sha1(f"user_image_{safe_name}".encode()).hexdigest()
    title = caption or filename
    
    item = NewsItem(
        id=news_id,
        feed_name="user_submission",
        feed_tier="primary",
        source_type="article",
        url=f"file://{img_path}",
        title=title,
        published_at=datetime.now(timezone.utc).isoformat(),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        clean_markdown=f"User submitted image: {filename}\n\nCaption: {caption}" if caption else f"User submitted image: {filename}",
        word_count=len(caption or "") + 5,
        og_image_url=str(img_path),
        tags=_submission_tags(
            platforms,
            "user_image",
            "base64_upload",
            submission_id=submission_id,
        ),
        status="fetched",
    )
    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
        _merge_existing_tags(conn, news_id, item.tags)
        conn.close()
        return {"status": "already_exists", "id": news_id, "path": str(img_path)}
    dbmod.upsert_news(conn, item)
    conn.close()
    _append_processed({"type": "image_base64", "filename": filename, "platforms": platforms}, "created")
    return {"status": "created", "id": news_id, "path": str(img_path), "size_bytes": len(img_bytes)}


# ====================================================================
# CLI
# ====================================================================
_RAW_BASE = "https://raw.githubusercontent.com/HsinTiger/news-radar/main/"


def process_images(
    paths: list,
    note: str = "",
    platforms: list = None,
    submission_id: str = "",
) -> dict:
    """One or more uploaded screenshots → ONE Meta source (carousel images)."""
    platforms = _normalize_platforms(platforms)
    paths = [p.strip() for p in paths if p.strip()]
    if not paths:
        return {"status": "error", "message": "no image paths"}
    urls = [_RAW_BASE + p for p in paths]
    key = "|".join(sorted(paths))
    news_id = _make_news_id("meta_img_" + hashlib.md5(key.encode()).hexdigest())
    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
        _merge_existing_tags(
            conn,
            news_id,
            _submission_tags(
                platforms,
                "user_image",
                f"images:{len(urls)}",
                submission_id=submission_id,
            ),
        )
        conn.close()
        return {"status": "already_exists", "id": news_id}
    refs = "\n".join(f"![screenshot]({u})" for u in urls)
    item = NewsItem(
        id=news_id, feed_name="user_submission", feed_tier="primary",
        source_type="article", url=urls[0],
        title=note or f"{len(urls)} 張截圖",
        published_at=datetime.now(timezone.utc).isoformat(),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        clean_markdown=(f"User submitted {len(urls)} image(s). Note: {note}\n\n{refs}"),
        word_count=len(note or "") + 20, og_image_url=urls[0],
        tags=_submission_tags(
            platforms,
            "user_image",
            f"images:{len(urls)}",
            submission_id=submission_id,
        ),
        status="fetched",
    )
    dbmod.upsert_news(conn, item)
    conn.close()
    _append_processed({"type": "images", "paths": paths, "platforms": platforms}, "created")
    return {"status": "created", "id": news_id, "count": len(urls)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Submit source for News Radar")
    parser.add_argument("--url", type=str, help="Article URL")
    parser.add_argument("--text", type=str, help="Article text body")
    parser.add_argument("--yt", type=str, help="YouTube video URL")
    parser.add_argument("--image", type=str, help="Image URL")
    parser.add_argument("--images", type=str, help="Comma-separated repo-relative image paths")
    parser.add_argument("--image-base64", type=str, help="Base64 image data (from mobile upload)")
    parser.add_argument("--image-filename", type=str, default="upload.jpg", help="Original filename")
    parser.add_argument("--image-caption", type=str, default="", help="Image caption")
    parser.add_argument("--from-pending", action="store_true", help="Process all pending submission files")
    parser.add_argument("--note", type=str, default="", help="Editorial note")
    parser.add_argument("--platforms", type=str, default="fb,ig,threads",
                       help="Comma-separated platforms")
    parser.add_argument(
        "--submission-id",
        type=str,
        default="",
        help="Control-plane submission ID used for end-to-end lineage",
    )

    args = parser.parse_args()
    platforms = [p.strip() for p in args.platforms.split(",")]

    conn = dbmod.get_conn()
    conn.close()

    if args.url:
        result = process_url(args.url, args.note, platforms, args.submission_id)
    elif args.text:
        result = process_text(args.text, args.note, platforms, args.submission_id)
    elif args.yt:
        result = process_youtube(args.yt, args.note, platforms, args.submission_id)
    elif args.images:
        result = process_images(
            args.images.split(","), args.note, platforms, args.submission_id
        )
    elif args.image:
        result = process_image(args.image, args.note, platforms, args.submission_id)
    elif args.image_base64:
        result = process_image_base64(
            args.image_base64,
            args.image_filename,
            args.image_caption,
            platforms,
            args.submission_id,
        )
    else:
        result = process_pending()
    
    print(json.dumps(result, ensure_ascii=False, indent=2))


def process_pending() -> dict:
    """Read all pending files from data/submissions/ and process them."""
    results = {"total": 0, "created": 0, "errors": []}
    for file_key, file_path, processor in [
        (
            "urls",
            URLS_FILE,
            lambda e: process_url(
                e["url"], e.get("note", ""), e.get("platforms"), e.get("submission_id", "")
            ),
        ),
        (
            "texts",
            TEXTS_FILE,
            lambda e: process_text(
                e["text"], e.get("note", ""), e.get("platforms"), e.get("submission_id", "")
            ),
        ),
        (
            "youtube",
            YTS_FILE,
            lambda e: process_youtube(
                e["url"], e.get("note", ""), e.get("platforms"), e.get("submission_id", "")
            ),
        ),
        (
            "images",
            IMAGES_FILE,
            lambda e: process_image(
                e["url"], e.get("note", ""), e.get("platforms"), e.get("submission_id", "")
            ),
        ),
    ]:
        entries = _load_json(file_path)
        results["total"] += len(entries)
        remaining = []
        for entry in entries:
            try:
                r = processor(entry)
                if r.get("status") in ("created", "already_exists"):
                    results["created"] += 1
                else:
                    remaining.append(entry)
            except Exception as e:
                results["errors"].append(str(e))
                remaining.append(entry)
        _save_json(file_path, remaining)

    return results


if __name__ == "__main__":
    main()
