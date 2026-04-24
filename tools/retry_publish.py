#!/usr/bin/env python3
"""
News Radar · Retry Publisher
=============================

用途：一個 draft 已經 compose 好、`platform_drafts` 三平台 row 都在 DB、但
publish 階段因為圖片 URL 抓不到（Reuters CDN 擋 Meta fetcher / 過期 token /
hot-link 防盜鏈）而全數 FAIL。這支腳本換一個 Meta 抓得到的 og_image URL，
**只重跑 publish 階段**：

    - 讀 platform_drafts → 拿 `full_text`（不重 compose、不重 score）
    - 依序 publish_to_fb / publish_to_threads / publish_to_ig
    - 每個平台寫一筆新的 publish_log
    - 跳過最近一筆 publish_log 已 `success=1` 的平台（避免重發）
    - 至少一平台成功 → drafts.status='published' + news_items.status='published'
    - 把新的 og_image_url 寫回 news_items（audit trail）

不做的事：
    - 不重 fetch、不重 extract、不重 score、不重 compose
    - 不改 platform_drafts.full_text（發的就是當初 compose 好的那份）
    - 不呼叫 push_state.sh（交給 wrapper 或手動跑）

Usage:
    cd ~/news_radar && source .venv/bin/activate

    # 換圖重發（預設跳過已 success=1 的平台）
    python tools/retry_publish.py 7bd26065237708cd \\
        --og-image "https://raw.githubusercontent.com/USER/news_radar/main/assets/xxx.jpg"

    # 指定平台（逗號分隔：fb,ig,threads）
    python tools/retry_publish.py 7bd26065237708cd \\
        --og-image "https://..." --platforms fb,ig

    # 強制重發（即使 publish_log 最後一筆 success=1 也再發一次 — 通常不要）
    python tools/retry_publish.py 7bd26065237708cd \\
        --og-image "https://..." --force

Exit codes:
    0  所有目標平台現在都有至少一筆 success=1 publish_log
    2  argparse / 環境錯
    3  draft_id 找不到 / 沒有 platform_drafts
    7  至少一平台仍然失敗
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---- repo root bootstrap ---------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.publisher import publish_to_fb, publish_to_ig, publish_to_threads  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "01_harvest" / "news_radar.db"

# Same mapping as emergency_oneshot.py / first_batch_publish.py
PLATFORM_TO_DB = {"fb": "facebook", "ig": "instagram", "threads": "threads"}
DB_TO_PLATFORM = {v: k for k, v in PLATFORM_TO_DB.items()}


# ---- helpers ---------------------------------------------------------------
def step(title: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n  {title}\n{bar}")


def fail(code: int, msg: str) -> None:
    print(f"\n❌ {msg}\n", file=sys.stderr)
    sys.exit(code)


def _fetch_draft(draft_id: str) -> dict:
    """Return dict with news_id, platform_drafts list. Raise SystemExit on missing."""
    if not DB_PATH.exists():
        fail(2, f"DB 不存在：{DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    drow = cur.execute(
        "SELECT id, news_id, status FROM drafts WHERE id=?", (draft_id,)
    ).fetchone()
    if not drow:
        conn.close()
        fail(3, f"找不到 draft_id={draft_id}")

    pdrows = cur.execute(
        """
        SELECT platform, full_text, char_count
        FROM platform_drafts
        WHERE draft_id=?
        """,
        (draft_id,),
    ).fetchall()
    if not pdrows:
        conn.close()
        fail(3, f"draft_id={draft_id} 沒有 platform_drafts（沒 compose 過？）")

    nrow = cur.execute(
        "SELECT id, title, url, og_image_url, status FROM news_items WHERE id=?",
        (drow["news_id"],),
    ).fetchone()

    conn.close()
    return {
        "draft_id": drow["id"],
        "news_id": drow["news_id"],
        "draft_status": drow["status"],
        "news_title": nrow["title"] if nrow else None,
        "news_url": nrow["url"] if nrow else None,
        "news_og_image_old": nrow["og_image_url"] if nrow else None,
        "news_status": nrow["status"] if nrow else None,
        "platform_drafts": {row["platform"]: dict(row) for row in pdrows},
    }


def _latest_publish_log_success(draft_id: str, db_platform: str) -> Optional[int]:
    """Return success flag (0/1) of the most recent publish_log row for this
    (draft_id, platform), or None if no row exists."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    row = cur.execute(
        """
        SELECT success FROM publish_log
        WHERE draft_id=? AND platform=?
        ORDER BY id DESC LIMIT 1
        """,
        (draft_id, db_platform),
    ).fetchone()
    conn.close()
    return None if row is None else int(row[0])


def _record_publish_result(draft_id: str, platform_short: str, resp: Optional[dict], err: Optional[str]) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    platform_post_id = None
    if resp and isinstance(resp, dict):
        platform_post_id = str(resp.get("id") or resp.get("post_id") or "")
    cur.execute(
        """
        INSERT INTO publish_log(
            draft_id, platform, platform_post_id,
            posted_at, success, error_message
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            PLATFORM_TO_DB[platform_short],
            platform_post_id,
            datetime.now(timezone.utc).isoformat(),
            1 if err is None else 0,
            err,
        ),
    )
    conn.commit()
    conn.close()


def _mark_published(draft_id: str, news_id: str, new_og_image: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE drafts SET status='published' WHERE id=?", (draft_id,))
    cur.execute(
        "UPDATE news_items SET status='published', og_image_url=? WHERE id=?",
        (new_og_image, news_id),
    )
    conn.commit()
    conn.close()
    print("   ↳ drafts.status='published'")
    print("   ↳ news_items.status='published'")
    print(f"   ↳ news_items.og_image_url ← {new_og_image}")


# ---- core ------------------------------------------------------------------
async def _publish_one(platform_short: str, full_text: str, image_url: str) -> dict:
    """Call the right publisher. Returns the raw publisher dict."""
    if platform_short == "fb":
        return await publish_to_fb(full_text, image_url=image_url, video_url=None)
    if platform_short == "threads":
        return await publish_to_threads(full_text, image_url=image_url, video_url=None)
    if platform_short == "ig":
        return await publish_to_ig(full_text, image_url=image_url, video_url=None)
    raise ValueError(f"unknown platform: {platform_short}")


async def main_async(args) -> int:
    step(f"Retry Publish · draft_id={args.draft_id}")
    print(f"DB = {DB_PATH}")
    print(f"new og_image = {args.og_image}")

    d = _fetch_draft(args.draft_id)
    print(f"\nnews_id = {d['news_id']}")
    print(f"title   = {d['news_title']}")
    print(f"url     = {d['news_url']}")
    print(f"draft.status       = {d['draft_status']}")
    print(f"news.status (old)  = {d['news_status']}")
    print(f"news.og_image (old) = {d['news_og_image_old']}")
    print(f"platforms in DB     = {sorted(d['platform_drafts'].keys())}")

    # Figure out which platforms to retry
    # CLI uses short names (fb/ig/threads); DB uses long names (facebook/instagram/threads).
    if args.platforms:
        targets = [p.strip().lower() for p in args.platforms.split(",") if p.strip()]
    else:
        targets = ["fb", "threads", "ig"]

    for p in targets:
        if p not in PLATFORM_TO_DB:
            fail(2, f"未知的 platform 短名：{p!r}（要 fb / ig / threads）")

    # Skip-rule: if latest publish_log already success=1, skip (unless --force)
    plan: list[tuple[str, str]] = []  # (platform_short, reason)
    for p in targets:
        db_name = PLATFORM_TO_DB[p]
        if db_name not in d["platform_drafts"]:
            print(f"   ⚠ 跳過 {p}：platform_drafts 沒這平台（沒 compose？）")
            continue
        last = _latest_publish_log_success(args.draft_id, db_name)
        if last == 1 and not args.force:
            print(f"   ↷ 跳過 {p}：最近一筆 publish_log success=1（避免重發，加 --force 可強制）")
            continue
        plan.append((p, "retry" if last == 0 else "first-try" if last is None else "forced"))

    if not plan:
        print("\n沒有需要重試的平台。結束。")
        return 0

    print("\n--- retry plan ---")
    for p, why in plan:
        cc = d["platform_drafts"][PLATFORM_TO_DB[p]]["char_count"]
        print(f"  {p:8s}  char_count={cc:4d}  reason={why}")

    # Publish loop
    step("Publish (依序 fb → threads → ig 中，按照 plan 順序)")
    # Keep canonical order for readability
    canonical_order = ["fb", "threads", "ig"]
    ordered_plan = [(p, w) for p in canonical_order for (pp, w) in plan if pp == p]

    results: dict[str, dict] = {}
    for p, why in ordered_plan:
        pd = d["platform_drafts"][PLATFORM_TO_DB[p]]
        full_text = pd["full_text"]
        cc = pd["char_count"]
        print(f"\n>>> [{p.upper()}] publishing ({cc} 字) [{why}] ...")
        t0 = time.time()
        try:
            r = await _publish_one(p, full_text, args.og_image)
            ok_flag = bool(r.get("success")) if isinstance(r, dict) else False
            if ok_flag:
                print(f"    ✔ 成功，耗時 {time.time() - t0:.1f}s")
                print(f"    回傳：{json.dumps(r, ensure_ascii=False)[:400]}")
                results[p] = {"ok": True, "resp": r, "err": None}
                _record_publish_result(args.draft_id, p, r, None)
            else:
                err = json.dumps(r, ensure_ascii=False)[:500] if isinstance(r, dict) else str(r)
                print(f"    ✗ 失敗(publisher 回報)：{err}")
                results[p] = {"ok": False, "resp": r, "err": err}
                _record_publish_result(args.draft_id, p, r, err)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"    ✗ 失敗(例外)：{err}")
            results[p] = {"ok": False, "resp": None, "err": err}
            _record_publish_result(args.draft_id, p, None, err)

    # Post-condition: check *all three* platforms now have at least one success=1 row
    # (we want the whole draft in a coherent "published" state, not just the ones we retried)
    step("Post-condition · 檢查三平台最終狀態")
    final_status = {}
    for p in ("fb", "threads", "ig"):
        db_name = PLATFORM_TO_DB[p]
        last = _latest_publish_log_success(args.draft_id, db_name)
        final_status[p] = last  # 0 / 1 / None
        pp = {1: "✔", 0: "✗", None: "·"}[last]
        print(f"  [{p:8s}] last publish_log success = {pp}  ({last!r})")

    all_ok = all(final_status.get(p) == 1 for p in ("fb", "threads", "ig"))
    any_retried_ok = any(results.get(p, {}).get("ok") for p in results)

    if all_ok:
        step("Mark published")
        _mark_published(args.draft_id, d["news_id"], args.og_image)
    elif any_retried_ok:
        print("\n⚠ 部分平台仍失敗，但有至少一平台這次重試成功。")
        print("  drafts / news_items 的 'published' 狀態**先不更新**，直到三平台都至少有一筆 success=1。")

    step("Summary")
    print(f"  draft_id       : {args.draft_id}")
    print(f"  news_id        : {d['news_id']}")
    print(f"  new og_image   : {args.og_image}")
    print(f"  retried        : {[p for p,_ in ordered_plan]}")
    for p in ("fb", "threads", "ig"):
        r = results.get(p)
        if r is None:
            print(f"  [{p:8s}] (skipped)")
        else:
            mark = "OK  " if r["ok"] else "FAIL"
            print(f"  [{p:8s}] {mark}")
    print(f"  final all-success-1: {all_ok}")

    return 0 if all_ok else 7


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Retry publish for an existing draft with a new image URL."
    )
    ap.add_argument("draft_id", help="draft id (16-char hex, from tools/.last_emergency_draft_id)")
    ap.add_argument("--og-image", required=True, help="new image URL Meta can fetch")
    ap.add_argument(
        "--platforms",
        default=None,
        help="comma-sep short names: fb,ig,threads (default: all three)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="re-publish even if last publish_log row is success=1 (NOT RECOMMENDED)",
    )
    return ap.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
