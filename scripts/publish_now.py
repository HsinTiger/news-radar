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
    ap = argparse.ArgumentParser(description="Immediate single-URL carousel publish")
    ap.add_argument("--url", required=True)
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

    print(f"[publish_now] 🔗 fetching {args.url}", flush=True)
    try:
        title, text = fetch_article(args.url)
    except Exception as exc:  # noqa: BLE001
        print(f"[publish_now] ❌ fetch failed: {exc}"); return 2
    print(f"[publish_now] 📄 title={title!r}  text={len(text)}字", flush=True)
    if len(text) < 80:
        print("[publish_now] ❌ 內文太短/抓不到 → 放棄"); return 2

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
