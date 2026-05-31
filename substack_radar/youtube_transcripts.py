"""
News Radar · YouTube Transcript Harvester (token-free)
======================================================
2026-05-30 (Optimization C / inspiration fetch).

The existing RSS fetcher (`src/fetcher.py`) only captures a YouTube video's
*description* — too thin to write a long-form essay from. This module pulls the
full **transcript** (auto-captions or manual subs) with the `yt-dlp` binary, with
**zero LLM tokens**, and inserts it into `news_items` as `source_type='video'` so
the Substack composer can use it as supplied material instead of doing paid
agentic web research.

Flow (all deterministic, no API keys):
    config/substack_youtube_sources.yaml
        → resolve each source (channel / playlist / video URL) to recent video IDs
        → for each video: yt-dlp --skip-download --write-(auto-)sub → .vtt
        → parse VTT → clean plain text
        → build NewsItem(clean_markdown=transcript) → db.upsert_news

Usage:
    python -m src.youtube_transcripts            # harvest per yaml, write to DB
    python -m src.youtube_transcripts --dry-run  # print what it would do, no DB write
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.fetcher import make_news_id  # noqa: E402
from src.schema import NewsItem  # noqa: E402

YT_SOURCES_PATH = Path(__file__).resolve().parent / "config" / "substack_youtube_sources.yaml"

# Defaults; overridable per-source in the yaml.
DEFAULT_MAX_VIDEOS = 3
DEFAULT_MAX_AGE_DAYS = 21
# Glob patterns so we catch auto-caption variants (en-orig / en-US / zh-Hant-…).
DEFAULT_SUB_LANGS = ["zh-Hant", "zh-Hant.*", "zh-TW", "zh.*", "en", "en.*", "en-orig"]
MIN_TRANSCRIPT_CHARS = 200  # below this it's not worth composing from


def _yt_dlp_bin() -> Optional[str]:
    """Locate the yt-dlp binary (PATH first, then known OneDrive copy)."""
    found = shutil.which("yt-dlp")
    if found:
        return found
    fallback = Path(
        "/Users/hsin/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/"
        "文件/antigravity_workspace/substack/yt-dlp"
    )
    return str(fallback) if fallback.exists() else None


# ---------------------------------------------------------------------------
# Minimal YAML parser (reuse the dependency-free style already in this repo)
# ---------------------------------------------------------------------------

def _parse_sources_yaml(text: str) -> List[Dict[str, str]]:
    """Parse a simple `sources:` list. Each item:

        sources:
          - url: https://www.youtube.com/@channel/videos
            topic_category: ai_model        # optional
            max_videos: 2                    # optional
    """
    sources: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.strip() == "sources:":
            continue
        stripped = raw.lstrip()
        if stripped.startswith("- "):
            if current:
                sources.append(current)
            current = {}
            stripped = stripped[2:]
        if ":" in stripped and current is not None:
            key, _, val = stripped.partition(":")
            current[key.strip()] = val.strip().strip("\"'")
    if current:
        sources.append(current)
    return sources


def load_sources() -> List[Dict[str, str]]:
    if not YT_SOURCES_PATH.exists():
        print(f"[YT] ℹ️ no {YT_SOURCES_PATH.name}; skipping YouTube harvest.")
        return []
    return _parse_sources_yaml(YT_SOURCES_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# yt-dlp helpers
# ---------------------------------------------------------------------------

def _list_recent_video_ids(bin_path: str, url: str, max_videos: int) -> List[str]:
    """Resolve a channel/playlist/video URL to up to `max_videos` recent video IDs.

    Uses --flat-playlist so no media is downloaded — only metadata. A bare video
    URL resolves to itself.
    """
    try:
        proc = subprocess.run(
            [
                bin_path, "--flat-playlist", "--no-warnings",
                "-I", f"1:{max_videos}",      # playlist items 1..max_videos
                "--print", "%(id)s", url,
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, Exception) as exc:  # noqa: BLE001
        print(f"[YT] ⚠️ list failed for {url}: {exc}")
        return []
    ids = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if not ids and proc.stderr:
        print(f"[YT] ⚠️ no ids for {url}: {proc.stderr.strip()[:200]}")
    return ids[:max_videos]


def _fetch_video_meta(bin_path: str, video_id: str) -> Dict[str, str]:
    """Fetch title + upload_date for a single video (no download)."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        proc = subprocess.run(
            [bin_path, "--no-warnings", "--skip-download",
             "--print", "%(title)s\n%(upload_date)s", url],
            capture_output=True, text=True, timeout=90,
        )
        lines = (proc.stdout or "").splitlines()
        return {
            "title": lines[0].strip() if lines else f"YouTube {video_id}",
            "upload_date": lines[1].strip() if len(lines) > 1 else "",
        }
    except Exception:  # noqa: BLE001
        return {"title": f"YouTube {video_id}", "upload_date": ""}


def _download_subs(bin_path: str, video_id: str, langs: List[str], out_dir: Path) -> Optional[Path]:
    """Download (manual first, else auto) subtitles as VTT. Returns the .vtt path."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    tmpl = str(out_dir / "%(id)s.%(ext)s")
    base_args = [
        bin_path, "--no-warnings", "--skip-download",
        "--sub-format", "vtt", "--sub-langs", ",".join(langs),
        "-o", tmpl, url,
    ]
    # Try manual subs, then auto-generated.
    for extra in (["--write-sub"], ["--write-auto-sub"]):
        try:
            subprocess.run(base_args[:1] + extra + base_args[1:], capture_output=True,
                           text=True, timeout=120)
        except Exception:  # noqa: BLE001
            continue
        vtts = sorted(out_dir.glob(f"{video_id}*.vtt"))
        if vtts:
            return vtts[0]
    return None


# ---------------------------------------------------------------------------
# VTT → plain text
# ---------------------------------------------------------------------------

_VTT_TS = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->")
_VTT_TAG = re.compile(r"<[^>]+>")
_VTT_INLINE_TS = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")


def vtt_to_text(vtt: str) -> str:
    """Strip cue numbers / timestamps / tags and dedupe the rolling-window
    repetition that YouTube auto-captions produce."""
    out: List[str] = []
    for line in vtt.splitlines():
        s = line.strip()
        if not s or s == "WEBVTT" or s.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if _VTT_TS.search(s) or s.isdigit():
            continue
        s = _VTT_INLINE_TS.sub("", _VTT_TAG.sub("", s)).strip()
        if not s:
            continue
        if out and out[-1] == s:        # consecutive duplicate
            continue
        out.append(s)
    # Auto-captions often repeat a line one cue later; collapse near-dups.
    deduped: List[str] = []
    for s in out:
        if deduped and (s in deduped[-1] or deduped[-1] in s):
            if len(s) > len(deduped[-1]):
                deduped[-1] = s
            continue
        deduped.append(s)
    return " ".join(deduped).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def harvest_youtube_transcripts(*, dry_run: bool = False) -> List[NewsItem]:
    """Harvest transcripts per yaml → NewsItem list (also upserts to DB unless dry_run)."""
    bin_path = _yt_dlp_bin()
    if not bin_path:
        print("[YT] ⚠️ yt-dlp not found on PATH; skipping YouTube harvest.")
        return []

    sources = load_sources()
    if not sources:
        return []

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    items: List[NewsItem] = []

    for src in sources:
        url = src.get("url", "").strip()
        if not url:
            continue
        topic = src.get("topic_category", "other")
        max_videos = int(src.get("max_videos", DEFAULT_MAX_VIDEOS) or DEFAULT_MAX_VIDEOS)
        print(f"[YT] source: {url} (topic={topic}, max={max_videos})")

        video_ids = _list_recent_video_ids(bin_path, url, max_videos)
        for vid in video_ids:
            video_url = f"https://www.youtube.com/watch?v={vid}"
            news_id = make_news_id(video_url)

            with tempfile.TemporaryDirectory() as td:
                vtt_path = _download_subs(bin_path, vid, DEFAULT_SUB_LANGS, Path(td))
                if vtt_path is None:
                    print(f"[YT]   - {vid}: no subtitles, skip")
                    continue
                transcript = vtt_to_text(vtt_path.read_text(encoding="utf-8", errors="replace"))

            if len(transcript) < MIN_TRANSCRIPT_CHARS:
                print(f"[YT]   - {vid}: transcript too short ({len(transcript)} chars), skip")
                continue

            meta = _fetch_video_meta(bin_path, vid)
            up = meta.get("upload_date", "")
            published = (
                f"{up[:4]}-{up[4:6]}-{up[6:8]}T00:00:00+00:00"
                if len(up) == 8 else now_iso
            )
            item = NewsItem(
                id=news_id,
                feed_name="YouTube",
                feed_tier="secondary",
                source_type="video",
                url=video_url,
                title=meta.get("title", f"YouTube {vid}"),
                published_at=published,
                fetched_at=now_iso,
                language="zh" if any("一" <= c <= "鿿" for c in transcript[:200]) else "en",
                clean_markdown=transcript,
                word_count=len(transcript),
                tags=["youtube", "video"],
                status="fetched",
            )
            item.__dict__["topic_category"] = topic  # tolerated extra attr for downstream
            items.append(item)
            print(f"[YT]   + {vid}: {item.title[:50]!r} ({len(transcript)} chars)")

    if not dry_run and items:
        from src import db as dbmod

        dbmod.init_db()
        conn = dbmod.get_conn()
        try:
            written = 0
            for it in items:
                if dbmod.news_exists(conn, it.id):
                    continue
                # Persist topic_category alongside the row if column exists.
                if dbmod.upsert_news(conn, it):
                    written += 1
                    tc = it.__dict__.get("topic_category")
                    if tc:
                        try:
                            conn.execute(
                                "UPDATE news_items SET topic_category=? WHERE id=?",
                                (tc, it.id),
                            )
                        except Exception:  # noqa: BLE001
                            pass
            conn.commit()
            print(f"[YT] ✅ wrote {written} new transcript item(s) to DB.")
        finally:
            conn.close()

    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="Harvest YouTube transcripts (token-free).")
    ap.add_argument("--dry-run", action="store_true", help="don't write to DB")
    args = ap.parse_args()
    items = harvest_youtube_transcripts(dry_run=args.dry_run)
    print(f"\n[YT] done: {len(items)} transcript item(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
