#!/usr/bin/env python3
"""Carousel live end-to-end test (2026-06-02).

Replicates run_pipeline._publish_platform's Phase-10 carousel path WITHOUT the
news/LLM pipeline: build_cards → render_cards(aspect=platform) → upload_cards
(real push to cover-cdn) → publish_{platform}_carousel (REAL public post).

⚠️ This posts REAL public carousels to IG + FB + Threads. Authorized by the
account owner on 2026-06-02. Each post must be deleted manually afterward.

CRITICAL: .env must be loaded BEFORE importing src.publisher, because that
module reads IG/FB/Threads tokens at import time (module-level os.getenv).
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # repo root on path when run as scripts/<file>

# --- load env FIRST (publisher reads tokens at import) -------------------------
from dotenv import load_dotenv  # noqa: E402

assert load_dotenv(REPO / ".env"), "failed to load .env"

from substack_radar.cards import build_cards, render_cards  # noqa: E402
from src.cover_uploader import upload_cards  # noqa: E402
from src.publisher import (  # noqa: E402
    publish_ig_carousel,
    publish_threads_carousel,
    publish_fb_carousel,
)

# --- sample carousel content (stand-in for the composer's distilled cards) -----
class _Carousel:
    insight_statement = "主力連三日站上五日線，籌碼正在換手"
    insight_support = "外資買超 1.2 萬張，投信同步轉買，散戶卻在賣"
    stat_number = "+38%"
    stat_caption = "近一個月主力成本區累積漲幅"
    takeaways = ["跌破月線前不輕易下車", "量縮回測才是上車點", "別追高、等回踩"]


TITLE = "主力爸爸我錯了：這檔我看走眼了"
TOPIC = "stock"
CAPTION = (
    "主力連三日站上五日線，籌碼正在換手。\n\n"
    "外資買超 1.2 萬張，投信同步轉買，散戶卻在賣——近一個月主力成本區累積 +38%。\n\n"
    "帶走的判斷：跌破月線前不輕易下車、量縮回測才是上車點、別追高等回踩。\n\n"
    "（carousel live test — 主力爸爸我錯了）"
)

PUBLISHERS = {
    "ig": publish_ig_carousel,
    "threads": publish_threads_carousel,
    "fb": publish_fb_carousel,
}


async def run_platform(platform: str) -> dict:
    rec: dict = {"platform": platform}
    draft_id = f"clt{datetime.now(timezone.utc).strftime('%m%d%H%M%S')}"
    cards = build_cards(title=TITLE, subtitle="", carousel=_Carousel())
    rec["card_types"] = [c["type"] for c in cards]
    if len(cards) < 2:
        rec["error"] = "build_cards produced <2 cards"
        return rec

    cdir = Path(tempfile.mkdtemp(prefix=f"clt_{platform}_"))
    card_paths = render_cards(cards=cards, topic_category=TOPIC, aspect=platform, output_dir=cdir)
    rec["rendered"] = [p.name for p in card_paths]
    rec["render_dir"] = str(cdir)

    slug = re.sub(r"[^A-Za-z0-9_]", "", f"{draft_id}_{platform}")[:40]
    card_urls = upload_cards(card_paths, slug)
    rec["card_urls"] = card_urls
    if len(card_urls) < 2:
        rec["error"] = "upload_cards returned <2 urls (CDN push failed)"
        return rec

    result = await PUBLISHERS[platform](card_urls, CAPTION)
    rec["publish_success"] = bool(result.get("success"))
    rec["post_id"] = result.get("id")
    if not result.get("success"):
        rec["error"] = str(result.get("error"))[:400]
    return rec


async def main() -> None:
    results = []
    for platform in ("threads", "ig", "fb"):  # threads first (lowest stakes)
        print(f"\n===== {platform.upper()} =====", flush=True)
        try:
            rec = await run_platform(platform)
        except Exception as exc:  # noqa: BLE001
            rec = {"platform": platform, "error": f"exception: {exc!r}"}
        print(json.dumps(rec, ensure_ascii=False, indent=2), flush=True)
        results.append(rec)

    print("\n===== SUMMARY =====", flush=True)
    for r in results:
        status = "✅" if r.get("publish_success") else "❌"
        print(f"{status} {r['platform']:8} post_id={r.get('post_id')} "
              f"urls={len(r.get('card_urls') or [])} err={r.get('error')}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
