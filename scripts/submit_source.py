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


def _fetch_page_text(url: str) -> Optional[str]:
    """Fetch article text from URL using trafilatura."""
    import httpx
    import trafilatura
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
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
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
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


def process_url(url: str, note: str = "", platforms: list = None) -> dict:
    """Save a URL for pipeline to process."""
    platforms = platforms or ["fb", "ig", "threads"]
    news_id = _make_news_id(url)

    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
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
        tags=["user_submission"] + [f"platform:{p}" for p in platforms],
        status="fetched",
    )
    dbmod.upsert_news(conn, item)
    conn.close()
    _append_processed({"type": "url", "url": url, "platforms": platforms}, "created")
    return {"status": "created", "id": news_id, "title": item.title, "word_count": item.word_count}


def process_text(text: str, note: str = "", platforms: list = None) -> dict:
    """Save user-pasted text body as a news item."""
    platforms = platforms or ["fb", "ig", "threads"]
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
        tags=["user_submission", "user_text"] + [f"platform:{p}" for p in platforms],
        status="fetched",
    )
    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
        conn.close()
        return {"status": "already_exists", "id": news_id}
    dbmod.upsert_news(conn, item)
    conn.close()
    _append_processed({"type": "text", "title": title, "platforms": platforms}, "created")
    return {"status": "created", "id": news_id, "title": title, "word_count": item.word_count}


def process_youtube(url: str, note: str = "", platforms: list = None) -> dict:
    """Extract YouTube transcript and save as news item."""
    platforms = platforms or ["fb", "ig", "threads"]
    result = _extract_yt_transcript(url)
    if not result:
        # Fallback: save URL only, let pipeline handle
        return process_url(url, note=f"YouTube: {note}" if note else "YouTube video", platforms=platforms)

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
        tags=["user_submission", "youtube", "video"] + [f"platform:{p}" for p in platforms],
        status="fetched",
    )
    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
        conn.close()
        return {"status": "already_exists", "id": news_id}
    dbmod.upsert_news(conn, item)
    conn.close()
    _append_processed({"type": "youtube", "video_id": video_id, "title": title, "platforms": platforms}, "created")
    return {"status": "created", "id": news_id, "title": title, "word_count": item.word_count}


def process_image(image_url: str, note: str = "", platforms: list = None) -> dict:
    """Save an image URL for analysis + posting."""
    platforms = platforms or ["fb", "ig", "threads"]
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
        tags=["user_submission", "user_image"] + [f"platform:{p}" for p in platforms],
        status="fetched",
    )
    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
        conn.close()
        return {"status": "already_exists", "id": news_id}
    dbmod.upsert_news(conn, item)
    conn.close()
    _append_processed({"type": "image", "image_url": image_url, "platforms": platforms}, "created")
    return {"status": "created", "id": news_id}


_IMAGE_DIR = SUBMISSIONS_DIR / "uploaded_images"

def process_image_base64(base64_data: str, filename: str = "upload.jpg", caption: str = "", platforms: list = None) -> dict:
    """Save base64 image data as file + news_item."""
    import base64
    platforms = platforms or ["fb", "ig", "threads"]
    
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
        tags=["user_submission", "user_image", "base64_upload"] + [f"platform:{p}" for p in platforms],
        status="fetched",
    )
    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
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


def process_images(paths: list, note: str = "", platforms: list = None) -> dict:
    """One or more uploaded screenshots → ONE Meta source (carousel images)."""
    platforms = platforms or ["fb", "ig", "threads"]
    paths = [p.strip() for p in paths if p.strip()]
    if not paths:
        return {"status": "error", "message": "no image paths"}
    urls = [_RAW_BASE + p for p in paths]
    key = "|".join(sorted(paths))
    news_id = _make_news_id("meta_img_" + hashlib.md5(key.encode()).hexdigest())
    conn = dbmod.get_conn()
    if dbmod.news_exists(conn, news_id):
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
        tags=["user_submission", "user_image", f"images:{len(urls)}"] + [f"platform:{p}" for p in platforms],
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

    args = parser.parse_args()
    platforms = [p.strip() for p in args.platforms.split(",")]

    conn = dbmod.get_conn()
    conn.close()

    if args.url:
        result = process_url(args.url, args.note, platforms)
    elif args.text:
        result = process_text(args.text, args.note, platforms)
    elif args.yt:
        result = process_youtube(args.yt, args.note, platforms)
    elif args.images:
        result = process_images(args.images.split(","), args.note, platforms)
    elif args.image:
        result = process_image(args.image, args.note, platforms)
    elif args.image_base64:
        result = process_image_base64(args.image_base64, args.image_filename, args.image_caption, platforms)
    else:
        result = process_pending()
    
    print(json.dumps(result, ensure_ascii=False, indent=2))


def process_pending() -> dict:
    """Read all pending files from data/submissions/ and process them."""
    results = {"total": 0, "created": 0, "errors": []}
    for file_key, file_path, processor in [
        ("urls", URLS_FILE, lambda e: process_url(e["url"], e.get("note", ""), e.get("platforms"))),
        ("texts", TEXTS_FILE, lambda e: process_text(e["text"], e.get("note", ""), e.get("platforms"))),
        ("youtube", YTS_FILE, lambda e: process_youtube(e["url"], e.get("note", ""), e.get("platforms"))),
        ("images", IMAGES_FILE, lambda e: process_image(e["url"], e.get("note", ""), e.get("platforms"))),
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
