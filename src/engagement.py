"""
News Radar · Engagement 模組（Milestone 3）
功能：輪詢 FB / IG / Threads 的 Graph API，抓已發佈貼文的互動數據（讚、留言、轉發、瀏覽），
寫入 engagement_stats 表。Reflector 會讀這張表找「高互動 vs 低互動」的風格差異。

設計原則：
- 非同步、逐平台失敗不互相影響（try/except 包每一支 API 呼叫）。
- 回傳 dict 盡量扁平化；缺哪個欄位就給 0，不丟例外。
- 若該貼文已被刪除或權杖失效，記錯誤訊息但不中斷整批。
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from dotenv import load_dotenv

from src import db as dbmod

# 定位 .env
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")

FB_GRAPH = "https://graph.facebook.com/v20.0"
TH_GRAPH = "https://graph.threads.net/v1.0"


# ---------- 單平台抓取 ----------

async def fetch_fb_insights(client: httpx.AsyncClient, post_id: str) -> Dict:
    """FB 粉專貼文/相片的基礎互動。使用 summary 模式一次抓讚、留言、分享。"""
    # 粉專照片貼文回傳的 id 通常形如 {page_id}_{post_id}；/photos 上傳會回 {id}, {post_id}
    params = {
        "fields": "reactions.summary(total_count),comments.summary(total_count),shares",
        "access_token": FB_PAGE_ACCESS_TOKEN,
    }
    try:
        resp = await client.get(f"{FB_GRAPH}/{post_id}", params=params, timeout=30.0)
        data = resp.json()
        if resp.status_code != 200:
            return {"ok": False, "error": data, "raw": data}

        likes = int((data.get("reactions") or {}).get("summary", {}).get("total_count") or 0)
        comments = int((data.get("comments") or {}).get("summary", {}).get("total_count") or 0)
        shares = int((data.get("shares") or {}).get("count") or 0)
        return {
            "ok": True,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "views": 0,
            "reach": 0,
            "raw": data,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "raw": {"exception": str(e)}}


async def fetch_ig_insights(client: httpx.AsyncClient, post_id: str) -> Dict:
    """IG 業務帳號貼文基本互動（like_count、comments_count）。"""
    params = {
        "fields": "like_count,comments_count,media_type,media_product_type,timestamp",
        "access_token": IG_ACCESS_TOKEN,
    }
    try:
        resp = await client.get(f"{FB_GRAPH}/{post_id}", params=params, timeout=30.0)
        data = resp.json()
        if resp.status_code != 200:
            return {"ok": False, "error": data, "raw": data}
        return {
            "ok": True,
            "likes": int(data.get("like_count") or 0),
            "comments": int(data.get("comments_count") or 0),
            "shares": 0,
            "views": 0,
            "reach": 0,
            "raw": data,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "raw": {"exception": str(e)}}


async def fetch_threads_insights(client: httpx.AsyncClient, post_id: str) -> Dict:
    """Threads 貼文 insights：views / likes / replies / reposts / quotes。
    官方 endpoint: GET /{media-id}/insights?metric=views,likes,replies,reposts,quotes
    """
    metrics = "views,likes,replies,reposts,quotes"
    params = {"metric": metrics, "access_token": THREADS_ACCESS_TOKEN}
    try:
        resp = await client.get(f"{TH_GRAPH}/{post_id}/insights", params=params, timeout=30.0)
        data = resp.json()
        if resp.status_code != 200:
            return {"ok": False, "error": data, "raw": data}

        # insights 回傳 data: [{name, values: [{value: int}]}]
        pulled = {m: 0 for m in metrics.split(",")}
        for item in data.get("data", []):
            name = item.get("name")
            values = item.get("values") or []
            if name and values:
                pulled[name] = int(values[0].get("value") or 0)

        return {
            "ok": True,
            "likes": pulled.get("likes", 0),
            "comments": pulled.get("replies", 0),
            "shares": pulled.get("reposts", 0) + pulled.get("quotes", 0),
            "views": pulled.get("views", 0),
            "reach": 0,
            "raw": data,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "raw": {"exception": str(e)}}


# ---------- 統一呼叫入口 ----------

PLATFORM_FETCHERS = {
    "facebook": fetch_fb_insights,
    "instagram": fetch_ig_insights,
    "threads": fetch_threads_insights,
}


async def sync_all_posts(conn, max_posts: int = 50) -> Dict:
    """從 publish_log 撈所有成功發佈的貼文，依序抓互動、寫入 engagement_stats。"""
    rows = dbmod.list_successful_posts(conn, limit=max_posts)
    print(f"[Engagement] 掃描 {len(rows)} 筆已成功發布的貼文")

    if not rows:
        return {"total": 0, "ok": 0, "failed": 0, "failures": []}

    ok_count = 0
    failures: List[Dict] = []

    async with httpx.AsyncClient() as client:
        for row in rows:
            platform = row["platform"]
            post_id = row["platform_post_id"]
            draft_id = row["draft_id"]
            fetcher = PLATFORM_FETCHERS.get(platform)
            if not fetcher:
                print(f"  ↳ [Skip] 不支援的平台: {platform}")
                continue
            # 權杖檢查：沒配 token 就跳過，避免噴一排 400
            token_required = {
                "facebook": FB_PAGE_ACCESS_TOKEN,
                "instagram": IG_ACCESS_TOKEN,
                "threads": THREADS_ACCESS_TOKEN,
            }[platform]
            if not token_required:
                print(f"  ↳ [Skip] {platform} 未設定 access token，跳過")
                continue

            result = await fetcher(client, post_id)
            fetched_at = datetime.now(timezone.utc).isoformat()

            if result.get("ok"):
                dbmod.insert_engagement(
                    conn,
                    draft_id=draft_id,
                    platform=platform,
                    platform_post_id=post_id,
                    fetched_at=fetched_at,
                    likes=result.get("likes", 0),
                    comments=result.get("comments", 0),
                    shares=result.get("shares", 0),
                    views=result.get("views", 0),
                    reach=result.get("reach", 0),
                    raw_json=json.dumps(result.get("raw"), ensure_ascii=False),
                )
                ok_count += 1
                print(
                    f"  ↳ [OK] {platform} {post_id[:14]}… "
                    f"likes={result.get('likes')} comments={result.get('comments')} "
                    f"shares={result.get('shares')} views={result.get('views')}"
                )
            else:
                err = result.get("error")
                failures.append({"platform": platform, "post_id": post_id, "error": err})
                print(f"  ↳ [Fail] {platform} {post_id[:14]}… err={str(err)[:100]}")

            # 小小節流，對 Meta 的 rate limit 友善一點
            await asyncio.sleep(0.2)

    print(f"[Engagement] 完成 | OK={ok_count} Fail={len(failures)}")
    return {"total": len(rows), "ok": ok_count, "failed": len(failures), "failures": failures}


if __name__ == "__main__":
    async def _main():
        conn = dbmod.get_conn()
        summary = await sync_all_posts(conn)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        conn.close()

    asyncio.run(_main())
