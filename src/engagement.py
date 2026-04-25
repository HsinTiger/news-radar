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

FB_PAGE_ID = os.getenv("FB_PAGE_ID")  # 用於 normalize 沒帶 page prefix 的舊 post_id
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")


def _normalize_fb_post_id(post_id: str) -> str:
    """把 publisher 不一致的 post_id 統一成 `{page_id}_{post_id}` 格式。

    為什麼要這個 helper（2026-04-25 發現）：
    publisher 在不同路徑下儲存的 platform_post_id 格式不一致——某些 publish
    路徑（/photos endpoint）回傳完整的 `{page_id}_{post_id}`、某些（/feed
    endpoint with link）回傳裸 `{post_id}`。Meta Graph API 對裸 post_id 開放
    的欄位很有限：連 `reactions.summary(total_count)` 都會回 `(#100) Tried
    accessing nonexisting field (reactions)`。

    所以 engagement 端 defensive 修正：看到沒有底線的 post_id 就主動拼回完整
    格式。對已經有底線的（之前 publish 路徑存對的）原樣回傳，不重複加 prefix。

    這只是補救，不是 root fix。Root fix 在 publisher.py 應該保證寫進 DB 的
    post_id 永遠帶 page prefix——但那是另一條 PR。
    """
    if "_" in post_id:
        return post_id  # 已是完整格式
    if not FB_PAGE_ID:
        return post_id  # 沒環境變數可拼 → 維持原狀（會失敗，但讓錯誤訊息誠實）
    return f"{FB_PAGE_ID}_{post_id}"

FB_GRAPH = "https://graph.facebook.com/v20.0"
TH_GRAPH = "https://graph.threads.net/v1.0"


# ---------- 單平台抓取 ----------

async def fetch_fb_insights(client: httpx.AsyncClient, post_id: str) -> Dict:
    """FB 粉專貼文 / 相片的互動指標。

    為什麼這支 fetch 改寫過兩次（2026-04-25）：

    第 1 次：Meta 在某個版本把 `/{post_id}?fields=...,shares` 的 `shares` 子物件
    砍掉了——新版 link / photo posts 回 `(#100) Tried accessing nonexisting
    field (shares)`，16/16 全 fail。改寫成「basic + insights」雙 step。

    第 2 次（同日下午）：發現裸 post_id（沒有 `{page_id}_` prefix）連
    `reactions.summary(total_count)` 都會回 `nonexisting field (reactions)`。
    publisher 端在不同路徑寫進 DB 的 post_id 格式不一致——/photos endpoint 寫
    完整 `{page_id}_{post_id}`、/feed endpoint with image_url 寫裸 `{post_id}`。
    在 engagement 端 defensive 修正：呼叫 _normalize_fb_post_id() 統一格式。

    Step 設計（任一 step 失敗都不影響另一 step 的數字）：
      Step 1: `/{normalized_id}?fields=reactions.summary,comments.summary`
              → likes + comments（如果 post_id 格式對的話）
      Step 2: `/{normalized_id}/insights?metric=...` → reach + views + 反應細項
              → reach + views_total
              → 用 post_reactions_by_type_total 當 likes 的 backup（萬一 Step 1
                的 reactions field 不可用，也至少有 insights 端的反應總計）

    return ok=True 條件：兩個 step 至少有一個拿到數字。兩個都失敗才回 ok=False。
    """
    nid = _normalize_fb_post_id(post_id)

    # ---- Step 1：基本互動（不見得每篇都支援，失敗就跳過不 abort） ---------
    params_basic = {
        "fields": "reactions.summary(total_count),comments.summary(total_count)",
        "access_token": FB_PAGE_ACCESS_TOKEN,
    }
    likes = 0
    comments = 0
    step1_ok = False
    data_basic: Dict = {}
    try:
        resp = await client.get(f"{FB_GRAPH}/{nid}", params=params_basic, timeout=30.0)
        data_basic = resp.json()
        if resp.status_code == 200:
            likes = int((data_basic.get("reactions") or {}).get("summary", {}).get("total_count") or 0)
            comments = int((data_basic.get("comments") or {}).get("summary", {}).get("total_count") or 0)
            step1_ok = True
        # 否則不 abort，留給 Step 2 試試
    except Exception as e:
        data_basic = {"exception": str(e)}

    # ---- Step 2：insights — reach / views / reactions 細項 -----------------
    # 文件：https://developers.facebook.com/docs/graph-api/reference/v20.0/insights
    insights_metrics = ",".join([
        "post_impressions_unique",          # reach (去重曝光)
        "post_impressions",                 # views (含重複)
        "post_reactions_by_type_total",     # 各反應類型總計（like/love/wow/haha/sad/angry）
    ])
    reach = 0
    views_total = 0
    reactions_from_insights = 0
    step2_ok = False
    insights_data: Dict = {}
    try:
        resp2 = await client.get(
            f"{FB_GRAPH}/{nid}/insights",
            params={"metric": insights_metrics, "access_token": FB_PAGE_ACCESS_TOKEN},
            timeout=30.0,
        )
        insights_data = resp2.json()
        if resp2.status_code == 200:
            for item in insights_data.get("data", []):
                name = item.get("name")
                values = item.get("values") or []
                if not values:
                    continue
                val = values[0].get("value")
                if name == "post_impressions_unique":
                    reach = int(val or 0)
                elif name == "post_impressions":
                    views_total = int(val or 0)
                elif name == "post_reactions_by_type_total":
                    # value 是 dict {"like": N, "love": N, ...} — 加總當 likes
                    if isinstance(val, dict):
                        reactions_from_insights = sum(int(v or 0) for v in val.values())
            step2_ok = True
    except Exception:
        pass

    # likes 的 final 值：Step 1 拿到就用 Step 1（精確 reactions.summary），
    # Step 1 失敗就用 Step 2 的 reactions_by_type 加總當 backup
    if not step1_ok and reactions_from_insights:
        likes = reactions_from_insights

    # 兩個 step 都掛 → 才回 ok=False，讓上層記錯誤
    if not step1_ok and not step2_ok:
        return {
            "ok": False,
            "error": data_basic,
            "raw": {"basic": data_basic, "insights": insights_data, "normalized_id": nid},
        }

    return {
        "ok": True,
        "likes": likes,
        "comments": comments,
        "shares": 0,        # FB API 不再單獨開放 shares metric；保留 0 不偽造
        "views": views_total,
        "reach": reach,
        "raw": {"basic": data_basic, "insights": insights_data, "normalized_id": nid},
    }


async def fetch_ig_insights(client: httpx.AsyncClient, post_id: str) -> Dict:
    """IG Business 帳號貼文的完整互動。

    為什麼這支 fetch 改寫過（2026-04-25）：
    舊版只打 `/{post_id}?fields=like_count,comments_count`，所以 reach / saved /
    views 永遠 hardcode 為 0——dashboard 看到「IG 全 0」其實是 bug，不是真相。

    現在分兩支打：
      Step 1：`/{post_id}?fields=like_count,comments_count,...` 拿基本互動。
      Step 2：`/{post_id}/insights?metric=reach,saved,views,total_interactions`
              拿 IG Insights 真正想看的訊號。

    Metric 列表為什麼是這四個：
      - `reach`：演算法分發是否到位的最直接訊號
      - `saved`：IG 演算法權重最高的訊號之一（saves 比 likes 影響更大）
      - `views`：v22+ 取代了舊的 `impressions`（後者已 deprecated）
      - `total_interactions`：likes+comments+shares+saves 加總，當 sanity check

    Step 2 失敗（permission 不夠 / 太舊 / Reels 限定 metric 不同）不影響 Step 1
    回傳，這樣至少 likes/comments 還是會進 DB。
    """
    # ---- Step 1：基本互動 -------------------------------------------------
    params_basic = {
        "fields": "like_count,comments_count,media_type,media_product_type,timestamp",
        "access_token": IG_ACCESS_TOKEN,
    }
    try:
        resp = await client.get(f"{FB_GRAPH}/{post_id}", params=params_basic, timeout=30.0)
        data_basic = resp.json()
        if resp.status_code != 200:
            return {"ok": False, "error": data_basic, "raw": data_basic}

        likes = int(data_basic.get("like_count") or 0)
        comments = int(data_basic.get("comments_count") or 0)
    except Exception as e:
        return {"ok": False, "error": str(e), "raw": {"exception": str(e)}}

    # ---- Step 2：insights — reach / saved / views / total_interactions ---
    # 文件：https://developers.facebook.com/docs/instagram-api/reference/ig-media/insights
    # v22+ 移除 `impressions`，必須用 `views` 代替（否則 #100 deprecation error）。
    insights_metrics = "reach,saved,views,total_interactions"
    reach = 0
    saved = 0
    views_total = 0
    total_interactions = 0
    insights_data: Dict = {}
    try:
        resp2 = await client.get(
            f"{FB_GRAPH}/{post_id}/insights",
            params={"metric": insights_metrics, "access_token": IG_ACCESS_TOKEN},
            timeout=30.0,
        )
        insights_data = resp2.json()
        if resp2.status_code == 200:
            for item in insights_data.get("data", []):
                name = item.get("name")
                values = item.get("values") or []
                if not values:
                    continue
                v = int(values[0].get("value") or 0)
                if name == "reach":
                    reach = v
                elif name == "saved":
                    saved = v
                elif name == "views":
                    views_total = v
                elif name == "total_interactions":
                    total_interactions = v
    except Exception:
        pass  # 不污染基本互動回傳

    # IG「shares」在 v22+ insights 沒單獨的 metric（被併進 total_interactions）。
    # 若 total_interactions > likes+comments+saved，差額大致是 shares + sticker_taps，
    # 但 Meta 沒保證精確；保守不推算，shares 留 0。
    return {
        "ok": True,
        "likes": likes,
        "comments": comments,
        "shares": 0,
        "saves": saved,             # 新增：IG-specific high-signal metric
        "views": views_total,
        "reach": reach,
        "total_interactions": total_interactions,  # 新增：sanity check 用
        "raw": {"basic": data_basic, "insights": insights_data},
    }


async def fetch_threads_insights(client: httpx.AsyncClient, post_id: str) -> Dict:
    """Threads 貼文 insights：views / likes / replies / reposts / quotes。

    官方 endpoint: GET /{media-id}/insights?metric=views,likes,replies,reposts,quotes

    為什麼這支 fetch 改寫過（2026-04-25）：
    舊版本把 Threads 的 `replies` alias 成 `comments`、把 `reposts + quotes` 合
    併成 `shares`，目的是讓三平台 dashboard column 形狀一致。代價是丟資訊：

      - replies 跟 comments 是不同概念（Threads 沒有 comments、IG 沒有 replies）
      - reposts 跟 quotes 是不同行為（轉貼 vs 引用評論）
      - dashboard 想拆開顯示就抓不到 native 數值

    現在改寫 native 三欄（schema 早就有 `replies` / `reposts` / `quotes` 欄位，
    只是從沒被填過——dashboard 看到的永遠是 schema default 0），把 Threads 拿
    到的數值直接寫進對應 native 欄。`comments` / `shares` 對 Threads 是 0（這
    兩個概念對 Threads 不存在；不偽造）。
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
            "likes":    pulled.get("likes", 0),
            "replies":  pulled.get("replies", 0),    # native：DB replies 欄位（Threads 留言）
            "reposts":  pulled.get("reposts", 0),    # native：DB reposts 欄位（純轉貼）
            "quotes":   pulled.get("quotes", 0),     # native：DB quotes 欄位（引用評論）
            "comments": 0,                            # Threads 無此 metric；保留 0 不偽造
            "shares":   0,                            # 同上（拆成 reposts+quotes 後不再合併）
            "saves":    0,                            # Threads 無此 metric
            "views":    pulled.get("views", 0),
            "reach":    0,                            # Threads 無此 metric
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
                    saves=result.get("saves", 0),       # IG (post-2026-04-25)
                    reposts=result.get("reposts", 0),   # Threads native (post-2026-04-25)
                    quotes=result.get("quotes", 0),     # Threads native (post-2026-04-25)
                    replies=result.get("replies", 0),   # Threads native (post-2026-04-25)
                    views=result.get("views", 0),
                    reach=result.get("reach", 0),
                    raw_json=json.dumps(result.get("raw"), ensure_ascii=False),
                )
                ok_count += 1
                # Print a compact one-liner. Show platform-specific extras only
                # when non-zero to keep terminal noise low for cold platforms.
                extra_bits = []
                if result.get("reach"):
                    extra_bits.append(f"reach={result['reach']}")
                if result.get("saves"):
                    extra_bits.append(f"saves={result['saves']}")
                if result.get("replies"):
                    extra_bits.append(f"replies={result['replies']}")
                if result.get("reposts"):
                    extra_bits.append(f"reposts={result['reposts']}")
                if result.get("quotes"):
                    extra_bits.append(f"quotes={result['quotes']}")
                extra = (" " + " ".join(extra_bits)) if extra_bits else ""
                print(
                    f"  ↳ [OK] {platform} {post_id[:14]}… "
                    f"likes={result.get('likes')} comments={result.get('comments')} "
                    f"shares={result.get('shares')} views={result.get('views')}{extra}"
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
