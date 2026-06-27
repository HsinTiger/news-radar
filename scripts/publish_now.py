#!/usr/bin/env python3
"""Immediate single-URL publish: fetch → compose → publish carousel NOW.

Bypasses the 2h cron + the publish queue. Given one article URL it:
  1. fetches the page (trafilatura) → title + clean text  (找資料)
  2. compose_multi_platform(title, text) → carousel + per-platform variants (撰文)
  3. per selected platform: build_cards → render → upload → publish_*_carousel (發出)

Used by .github/workflows/publish_now.yml (workflow_dispatch with `url`,
`platforms`) and runnable locally for testing:
    ./.venv/bin/python scripts/publish_now.py --url <URL> --platforms fb,ig,threads
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from src.composer import compose_multi_platform, finalize_variant  # noqa: E402
from substack_radar.cards import build_cards, render_cards  # noqa: E402
from src.cover_uploader import upload_cards  # noqa: E402
from src.publisher import (  # noqa: E402
    publish_ig_carousel, publish_threads_carousel, publish_fb_carousel,
)

_PUB = {"ig": publish_ig_carousel, "threads": publish_threads_carousel, "fb": publish_fb_carousel}
_PLAT_KEY = {"fb": "fb", "facebook": "fb", "ig": "ig", "instagram": "ig", "threads": "threads"}


def fetch_article(url: str) -> tuple[str, str]:
    """Return (title, clean_text) for an article URL."""
    import httpx
    import trafilatura

    r = httpx.get(url, timeout=25, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (compatible; NewsRadar/1.0)"})
    r.raise_for_status()
    title, text = "", ""
    data = trafilatura.extract(r.text, output_format="json", with_metadata=True,
                               include_comments=False, include_tables=False)
    if data:
        d = json.loads(data)
        title = (d.get("title") or "").strip()
        text = (d.get("text") or "").strip()
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
        title = (m.group(1).strip() if m else url.rstrip("/").split("/")[-1])[:80]
    return title, text


async def _publish_platform(pf: str, cover_title: str, carousel, caption: str) -> tuple[bool, str, object]:
    cards = build_cards(title=cover_title or "", subtitle="", carousel=carousel)
    if len(cards) < 2:
        return False, "build_cards <2", None
    cdir = Path(tempfile.mkdtemp(prefix=f"pn_{pf}_"))
    paths = render_cards(cards=cards, topic_category="other", aspect=pf, output_dir=cdir)
    did = "pn" + datetime.now(timezone.utc).strftime("%m%d%H%M%S")
    slug = re.sub(r"[^A-Za-z0-9_]", "", f"{did}_{pf}")[:40]
    urls = upload_cards(paths, slug)
    if len(urls) < 2:
        return False, f"card upload failed ({len(urls)})", None
    res = await _PUB[pf](urls, caption)
    return bool(res.get("success")), str(res.get("error"))[:200], res.get("id")


async def main() -> int:
    ap = argparse.ArgumentParser(description="Immediate single-item carousel publish (url 或 title+text)")
    ap.add_argument("--url", help="文章網址（擇一：--url，或 --title 搭 --text/--file）")
    ap.add_argument("--title", default="", help="標題（不抓 url 時，搭配 --text/--file 使用）")
    ap.add_argument("--text", default="", help="直接給文章內文")
    ap.add_argument("--file", default="", help="從檔案讀文章內文（markdown / 純文字）")
    ap.add_argument("--platforms", default="fb,ig,threads", help="comma list: fb,ig,threads")
    ap.add_argument("--note", default="", help="optional editorial hint fed to the composer")
    args = ap.parse_args()

    plats = []
    for p in args.platforms.split(","):
        k = _PLAT_KEY.get(p.strip().lower())
        if k and k not in plats:
            plats.append(k)
    if not plats:
        print("[publish_now] ❌ no valid platforms"); return 2

    # 取得 (title, text)：有 url → 抓網頁；否則用 --title + --text/--file（直接給文字，
    # 例如從救回的草稿重發、或文字型立即發送，免抓網頁、免進佇列）。
    if args.url:
        # YouTube 走逐字稿：trafilatura 讀影片頁只拿得到 ~78 字 → 必然 < 80 而放棄，
        # 這正是 youtube 發文路徑「出錯」的原因。重用 submit_source 既有的逐字稿擷取。
        # 註：雲端 GitHub runner IP 偶爾被 YouTube 擋；抓不到時給清楚訊息、可改本機或貼全文。
        from scripts.submit_source import _extract_yt_transcript, YT_VIDEO_ID_RE
        if YT_VIDEO_ID_RE.search(args.url):
            print(f"[publish_now] ▶️ YouTube → 抓逐字稿 {args.url}", flush=True)
            info = _extract_yt_transcript(args.url)
            if not info or len((info.get("transcript") or "").strip()) < 80:
                print("[publish_now] ❌ YouTube 抓不到逐字稿（無字幕／被擋）→ 放棄。"
                      "可改用『全文』貼摘要，或在本機跑。"); return 2
            title = (info.get("title") or args.note or "YouTube").strip()
            text = info["transcript"]
        else:
            print(f"[publish_now] 🔗 fetching {args.url}", flush=True)
            try:
                title, text = fetch_article(args.url)
            except Exception as exc:  # noqa: BLE001
                print(f"[publish_now] ❌ fetch failed: {exc}"); return 2
    else:
        title = args.title.strip()
        text = args.text
        if args.file:
            try:
                text = Path(args.file).read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                print(f"[publish_now] ❌ 讀檔失敗: {exc}"); return 2
        text = (text or "").strip()
        if not title or not text:
            print("[publish_now] ❌ 需要 --url，或 --title 搭配 --text/--file"); return 2
    print(f"[publish_now] 📄 title={title!r}  text={len(text)}字", flush=True)
    if len(text) < 80:
        print("[publish_now] ❌ 抓不到足夠內文（可能需登入／JS 渲染／防爬）→ 放棄。"
              "請改用『全文』把內容貼上。"); return 2

    content = (f"{args.note}\n\n" if args.note else "") + text
    print("[publish_now] ✍️ composing…", flush=True)
    draft = await compose_multi_platform(title, content)
    if not draft or draft.carousel is None:
        print("[publish_now] ❌ compose 失敗或無 carousel"); return 3

    order = [p for p in ("threads", "ig", "fb") if p in plats]
    any_ok = False
    for pf in order:
        variant = getattr(draft, pf, None)
        if variant is None:
            print(f"   ⚠️ [{pf}] 無變體，跳過"); continue
        try:
            fv, caption, _ok = finalize_variant(variant, pf)
            ok, err, pid = await _publish_platform(pf, fv.title, draft.carousel, caption)
        except Exception as exc:  # noqa: BLE001 — one platform must not crash the rest
            ok, err, pid = False, f"exception: {exc!r}", None
        print(f"{'✅' if ok else '❌'} [{pf}] id={pid}" + ("" if ok else f"  err={err}"), flush=True)
        any_ok = any_ok or ok
    return 0 if any_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
