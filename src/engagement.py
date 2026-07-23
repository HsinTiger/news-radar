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
from dataclasses import dataclass
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

FB_GRAPH = f"https://graph.facebook.com/{os.getenv('META_GRAPH_VERSION', 'v20.0')}"
TH_GRAPH = "https://graph.threads.net/v1.0"


# ---------- 低互動偵測 (Phase 9 2026-04-28) ----------
#
# Meta Insights API 對於互動不足的貼文會回傳 error response（非 200 status），
# 而非回傳空的 insights 陣列。E.g.:
#   - IG/Threads: `(#10) Insights cannot be accessed for this post` (OAuth error)
#   - FB: `(#100) Tried accessing nonexisting field (reactions)` (GraphMethod error)
#
# 這些是合法的「零互動」信號，應視為 OK_zero_engagement（已 poll、無資料）
# 而非 Fail（API 呼叫失敗、應重試）。Phase 9 engagement coverage 需要區分：
#   - OK_zero_engagement：計入統計，但互動值為 0
#   - Fail：API 失敗、token 過期、network 錯誤（需要診斷）
#   - RateLimit：暫時被限流（會在下一個 cycle 重試）
#
# Spec: PM_Radar/specs/engagement_fail3_diagnosis.md §3 H1

def _is_low_engagement_error(error_response: Dict) -> bool:
    """Check if a Meta API error response is a 'no engagement yet' signal.

    Low-engagement error codes:
      - #10 (OAuthException): "Insights cannot be accessed for this post" (IG/Threads)
      - #100 (GraphMethodException): "Tried accessing nonexisting field (...)" (FB)

    These indicate the post exists but has insufficient engagement to surface metrics.
    Should be treated as OK_zero_engagement, not Fail.
    """
    if not error_response:
        return False
    error_obj = error_response.get("error", {})
    if not isinstance(error_obj, dict):
        return False
    code = error_obj.get("code")
    message = error_obj.get("message", "").lower()

    # Error code 100 is also used for invalid/deprecated metric names. Treating
    # every #100 as zero engagement hides a broken collector as valid data.
    # Require a low-signal message, never the numeric code alone.
    if "insights cannot be accessed" in message:
        return True
    if "insufficient data" in message:
        return True
    if "nonexisting field" in message and "reactions" in message:
        return True

    return False


async def _fetch_insights_separately(
    client: httpx.AsyncClient,
    endpoint: str,
    metrics: List[str],
    access_token: Optional[str],
) -> tuple[Dict, Dict, List[str]]:
    """Probe metrics independently so one deprecated metric cannot poison all.

    Returns ``(values, errors, successful_metrics)``. The raw error stays
    attached to its metric, allowing the dashboard to report degraded data
    instead of silently converting an API contract failure into a zero.
    """
    values: Dict = {}
    errors: Dict = {}
    successful: List[str] = []
    for metric in metrics:
        try:
            response = await client.get(
                endpoint,
                params={"metric": metric, "access_token": access_token},
                timeout=30.0,
            )
            payload = response.json()
            if response.status_code != 200:
                errors[metric] = payload
                continue
            successful.append(metric)
            entries = payload.get("data", []) if isinstance(payload, dict) else []
            for item in entries:
                name = item.get("name") or metric
                metric_values = item.get("values") or []
                if metric_values:
                    values[name] = metric_values[0].get("value")
        except Exception as exc:
            errors[metric] = {"exception": str(exc)}
    return values, errors, successful



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
      Step 2: `/{normalized_id}/insights?metric=...` → engaged users + clicks + 反應細項
              → 不再把已失效的 impressions 指標偽裝成 reach / views
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

    # ---- Step 2：insights — engaged users / clicks / reactions 細項 -------
    # 文件：https://developers.facebook.com/docs/graph-api/reference/v20.0/insights
    insights_metrics = [
        "post_engaged_users",               # unique people who engaged
        "post_clicks",                      # total post clicks
        "post_reactions_by_type_total",     # 各反應類型總計（like/love/wow/haha/sad/angry）
    ]
    engaged_users = 0
    clicks = 0
    reactions_from_insights = 0
    step2_ok = False
    values, metric_errors, successful_metrics = await _fetch_insights_separately(
        client,
        f"{FB_GRAPH}/{nid}/insights",
        insights_metrics,
        FB_PAGE_ACCESS_TOKEN,
    )
    engaged_users = int(values.get("post_engaged_users") or 0)
    clicks = int(values.get("post_clicks") or 0)
    reaction_value = values.get("post_reactions_by_type_total")
    if isinstance(reaction_value, dict):
        reactions_from_insights = sum(int(value or 0) for value in reaction_value.values())
    step2_ok = bool(successful_metrics)
    insights_data: Dict = {
        "values": values,
        "errors": metric_errors,
        "successful_metrics": successful_metrics,
    }
    # 兩個 step 都掛 → 檢查是否為低互動錯誤
    # 如果都是低互動錯誤（特定 error code），視為 OK_zero_engagement；
    # 否則才回 ok=False 讓上層記錯誤。
    step1_low_engagement = _is_low_engagement_error(data_basic)
    step2_low_engagement = bool(metric_errors) and all(
        _is_low_engagement_error(error) for error in metric_errors.values()
    )

    if (not step1_ok or step1_low_engagement) and (not step2_ok or step2_low_engagement):
        if (step1_low_engagement or step2_low_engagement):
            # 低互動信號（API 有回，但無資料）→ OK_zero_engagement
            return {
                "ok": True,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "views": 0,
                "reach": 0,
                "engaged_users": 0,
                "clicks": 0,
                "raw": {"basic": data_basic, "insights": insights_data, "normalized_id": nid},
            }
        else:
            # 真的 API 失敗（token 過期、網路錯誤等）→ Fail
            return {
                "ok": False,
                "error": data_basic,
                "raw": {"basic": data_basic, "insights": insights_data, "normalized_id": nid},
            }

    if not step1_ok and not step2_ok:
        return {
            "ok": False,
            "error": data_basic,
            "raw": {"basic": data_basic, "insights": insights_data, "normalized_id": nid},
        }

    return {
        "ok": True,
        "likes": likes or reactions_from_insights,
        "comments": comments,
        "shares": 0,        # FB API 不再單獨開放 shares metric；保留 0 不偽造
        "views": 0,         # post_impressions 已失效；未知不能偽裝成曝光 0
        "reach": 0,         # post_impressions_unique 已失效；同上
        "engaged_users": engaged_users,
        "clicks": clicks,
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
            # 檢查是否為低互動錯誤
            if _is_low_engagement_error(data_basic):
                # 低互動信號 → OK_zero_engagement
                return {
                    "ok": True,
                    "likes": 0,
                    "comments": 0,
                    "shares": 0,
                    "saves": 0,
                    "views": 0,
                    "reach": 0,
                    "total_interactions": 0,
                    "raw": data_basic,
                }
            else:
                # 真的 API 失敗 → Fail
                return {"ok": False, "error": data_basic, "raw": data_basic}

        likes = int(data_basic.get("like_count") or 0)
        comments = int(data_basic.get("comments_count") or 0)
    except Exception as e:
        return {"ok": False, "error": str(e), "raw": {"exception": str(e)}}

    # ---- Step 2：insights — reach / saved / views / total_interactions ---
    # 文件：https://developers.facebook.com/docs/instagram-api/reference/ig-media/insights
    # v22+ 移除 `impressions`，必須用 `views` 代替（否則 #100 deprecation error）。
    insights_metrics = ["reach", "saved", "views", "total_interactions"]
    reach = 0
    saved = 0
    views_total = 0
    total_interactions = 0
    values, metric_errors, successful_metrics = await _fetch_insights_separately(
        client,
        f"{FB_GRAPH}/{post_id}/insights",
        insights_metrics,
        IG_ACCESS_TOKEN,
    )
    reach = int(values.get("reach") or 0)
    saved = int(values.get("saved") or 0)
    views_total = int(values.get("views") or 0)
    total_interactions = int(values.get("total_interactions") or 0)
    insights_data: Dict = {
        "values": values,
        "errors": metric_errors,
        "successful_metrics": successful_metrics,
    }

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
            # 檢查是否為低互動錯誤
            if _is_low_engagement_error(data):
                # 低互動信號 → OK_zero_engagement
                return {
                    "ok": True,
                    "likes": 0,
                    "replies": 0,
                    "reposts": 0,
                    "quotes": 0,
                    "comments": 0,
                    "shares": 0,
                    "saves": 0,
                    "views": 0,
                    "reach": 0,
                    "raw": data,
                }
            else:
                # 真的 API 失敗 → Fail
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


# ---------- Phase 8.23 (2026-04-25): log-scale time-series bucket dispatch ----------
#
# 為了讓 dashboard 能畫 EngagementGrowthChart（x 軸 = post age 1h/24h/168h、
# y 軸 = likes / views / reach），把 polling 從「每 4h 均勻」改成「依 post 齡 log-scale」。
# 每個 (draft, platform) 的 t0 是 publish_log.posted_at（最早 success=1 那筆），
# 在 [1, 24, 168] 三個 bucket 各 poll 一次、保留時序資料。
#
# 落地組件：
#   - 新欄位 engagement_stats.post_age_bucket（INTEGER, NULL OK；canonical 1/24/168）
#   - CHECK trigger + partial UNIQUE INDEX 防 dup-bucket（schema migration in db.py）
#   - VIEW engagement_stats_latest 給 dashboard 讀 latest snapshot
#   - sync_bucket_polls() 為新 hourly cron entry，取代舊的 sync_all_posts()
#   - rate limiter src.rate_limit.can_call 防超量
#
# 詳見 data/01_harvest/migrations/2026-04-25_log_scale_engagement.sql。

CANONICAL_BUCKETS = (1, 24, 168)  # hours since first successful publish
TOLERANCE_HOURS = 0.25            # ±15 min — wider than cron interval prevents miss


@dataclass(frozen=True)
class PollTask:
    """One (draft, platform, bucket) poll instruction emitted by select_posts_to_poll()."""
    draft_id: str
    platform: str            # facebook / instagram / threads
    platform_post_id: str
    bucket: int              # 1, 24, or 168
    posted_at: datetime      # tz-aware UTC


def _parse_iso_utc(s: str) -> datetime:
    """Parse an ISO-8601 string from publish_log.posted_at; ensure tz-aware UTC.
    Naive → ValueError (publish_log audit confirmed all 1751 rows have +00:00)."""
    if not s:
        raise ValueError("empty timestamp")
    # Python 3.11+ accepts 'Z' suffix; 3.10 doesn't. Normalize defensively.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime in publish_log.posted_at: {s}")
    return dt.astimezone(timezone.utc)


def _bucket_already_polled(conn, draft_id: str, platform: str, bucket: int) -> bool:
    """True if engagement_stats already has a row for this (draft, platform, bucket).
    Belt-and-suspenders with the partial UNIQUE INDEX (which raises on conflict)."""
    row = conn.execute(
        """
        SELECT 1 FROM engagement_stats
        WHERE draft_id=? AND platform=? AND post_age_bucket=?
        LIMIT 1
        """,
        (draft_id, platform, bucket),
    ).fetchone()
    return row is not None


def select_posts_to_poll(conn, now_utc: datetime) -> List[PollTask]:
    """Decide which (draft, platform, bucket) tuples to poll on this hourly run.

    Algorithm:
      1. SELECT distinct (draft, platform, MIN(posted_at)) FROM publish_log
         WHERE success=1 AND platform_post_id is non-empty.
      2. For each row: compute age_h = now_utc - posted_at (in hours).
         - If age_h > 168 + TOLERANCE_HOURS: skip (window expired)
         - For each bucket in CANONICAL_BUCKETS:
             - If |age_h - bucket| <= TOLERANCE_HOURS:
               - If not already polled at this bucket: emit PollTask

    now_utc must be tz-aware UTC; naive → ValueError (defensive — every
    timestamp downstream depends on tz consistency).
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be tz-aware UTC")

    rows = conn.execute(
        """
        SELECT
            pl.draft_id,
            pl.platform,
            pl.platform_post_id,
            MIN(pl.posted_at) AS first_posted_at
        FROM publish_log pl
        WHERE pl.success = 1
          AND pl.platform_post_id IS NOT NULL
          AND pl.platform_post_id != ''
        GROUP BY pl.draft_id, pl.platform
        """
    ).fetchall()

    tasks: List[PollTask] = []
    window_max_h = CANONICAL_BUCKETS[-1] + TOLERANCE_HOURS
    window_min_h = CANONICAL_BUCKETS[0] - TOLERANCE_HOURS

    for r in rows:
        try:
            posted_at = _parse_iso_utc(r["first_posted_at"])
        except ValueError as e:
            print(f"[bucket] skip — invalid posted_at for "
                  f"{r['draft_id'][:12]}/{r['platform']}: {e}")
            continue

        age_h = (now_utc - posted_at).total_seconds() / 3600.0

        if age_h > window_max_h:
            continue  # window already passed; never poll this (draft, platform) again
        if age_h < window_min_h:
            continue  # too fresh; first bucket is 1h

        for bucket in CANONICAL_BUCKETS:
            if abs(age_h - bucket) > TOLERANCE_HOURS:
                continue
            if _bucket_already_polled(conn, r["draft_id"], r["platform"], bucket):
                continue
            tasks.append(PollTask(
                draft_id=r["draft_id"],
                platform=r["platform"],
                platform_post_id=r["platform_post_id"],
                bucket=bucket,
                posted_at=posted_at,
            ))

    return tasks


async def sync_bucket_polls(conn) -> Dict:
    """Hourly cron entry: dispatch bucket polls for (draft, platform) tuples
    falling within ±15min tolerance of canonical buckets [1, 24, 168] h.

    Replaces `sync_all_posts` (uniform 4h polling). Most cron ticks will have
    0 tasks (no bucket alignment) — that's expected and cheap.
    """
    # Local import to avoid module-load circular dep when other modules
    # transitively import engagement before rate_limit.
    from src.rate_limit import can_call

    now_utc = datetime.now(timezone.utc)
    tasks = select_posts_to_poll(conn, now_utc)
    print(f"[Engagement] hourly bucket dispatch · {len(tasks)} tasks "
          f"@ {now_utc.isoformat(timespec='seconds')}")

    if not tasks:
        return {"total": 0, "ok": 0, "failed": 0, "rate_limited": 0, "failures": []}

    ok_count = 0
    failures: List[Dict] = []
    rate_limited = 0
    # Per-cron-run tracker for can_call(). At our scale (max ~30 tasks/cron)
    # cross-run rate limits are not exercised, so empty-init is correct.
    recent_calls: Dict[str, List[datetime]] = {
        "facebook": [], "instagram": [], "threads": [],
    }

    async with httpx.AsyncClient() as client:
        for task in tasks:
            platform = task.platform
            fetcher = PLATFORM_FETCHERS.get(platform)
            if not fetcher:
                print(f"  ↳ [Skip] unsupported platform: {platform}")
                continue
            token_required = {
                "facebook": FB_PAGE_ACCESS_TOKEN,
                "instagram": IG_ACCESS_TOKEN,
                "threads": THREADS_ACCESS_TOKEN,
            }[platform]
            if not token_required:
                print(f"  ↳ [Skip] {platform} no access token configured")
                continue

            now = datetime.now(timezone.utc)
            allowed, secs = can_call(platform, now, recent_calls[platform])
            if not allowed:
                rate_limited += 1
                print(f"  ↳ [RateLimit] {platform} blocked, retry in {secs}s — "
                      f"deferring {task.draft_id[:8]}@bucket={task.bucket}")
                continue

            result = await fetcher(client, task.platform_post_id)
            fetched_at = datetime.now(timezone.utc).isoformat()
            recent_calls[platform].append(now)

            if result.get("ok"):
                dbmod.insert_engagement(
                    conn,
                    draft_id=task.draft_id,
                    platform=platform,
                    platform_post_id=task.platform_post_id,
                    fetched_at=fetched_at,
                    likes=result.get("likes", 0),
                    comments=result.get("comments", 0),
                    shares=result.get("shares", 0),
                    saves=result.get("saves", 0),
                    reposts=result.get("reposts", 0),
                    quotes=result.get("quotes", 0),
                    replies=result.get("replies", 0),
                    views=result.get("views", 0),
                    reach=result.get("reach", 0),
                    engaged_users=result.get("engaged_users", 0),
                    clicks=result.get("clicks", 0),
                    raw_json=json.dumps(result.get("raw"), ensure_ascii=False),
                    post_age_bucket=task.bucket,
                )
                ok_count += 1
                print(f"  ↳ [OK] {platform:9s} bucket={task.bucket:>3d}h "
                      f"{task.platform_post_id[:14]}… "
                      f"likes={result.get('likes')} views={result.get('views')} "
                      f"reach={result.get('reach', 0)} "
                      f"engaged_users={result.get('engaged_users', 0)} "
                      f"clicks={result.get('clicks', 0)}")
            else:
                err = result.get("error")
                failures.append({
                    "platform": platform,
                    "draft_id": task.draft_id,
                    "bucket": task.bucket,
                    "error": err,
                })
                print(f"  ↳ [Fail] {platform:9s} bucket={task.bucket:>3d}h "
                      f"{task.platform_post_id[:14]}… err={str(err)[:80]}")

            await asyncio.sleep(0.2)

    conn.commit()
    print(f"[Engagement] done | OK={ok_count} Fail={len(failures)} "
          f"RateLimit={rate_limited} · committed")
    return {
        "total": len(tasks),
        "ok": ok_count,
        "failed": len(failures),
        "rate_limited": rate_limited,
        "failures": failures,
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
                    engaged_users=result.get("engaged_users", 0),
                    clicks=result.get("clicks", 0),
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
                if result.get("engaged_users"):
                    extra_bits.append(f"engaged_users={result['engaged_users']}")
                if result.get("clicks"):
                    extra_bits.append(f"clicks={result['clicks']}")
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

    # 持久化：Python 3.6+ 的 sqlite3 在 conn.close() 不再隱式 commit；
    # 必須明確 commit() 否則整批 INSERT 會被 rollback（log 看到 OK=N 但 DB 0 rows
    # 的詭異情況——2026-04-25 早上 50 個 OK 全 rollback 才發現）。
    conn.commit()
    print(f"[Engagement] 完成 | OK={ok_count} Fail={len(failures)} · committed")
    return {"total": len(rows), "ok": ok_count, "failed": len(failures), "failures": failures}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="News Radar engagement worker")
    parser.add_argument(
        "--legacy-uniform",
        action="store_true",
        help="跑舊的 uniform sync_all_posts（一次性 backfill 用；預設走新的 hourly bucket dispatch）",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=50,
        help="--legacy-uniform 模式下的取樣上限",
    )
    args = parser.parse_args()

    async def _main():
        # Runtime state may have been created by an older release. Apply the
        # idempotent schema migration before the collector writes new metrics.
        dbmod.init_db()
        conn = dbmod.get_conn()
        if args.legacy_uniform:
            print("[Engagement] mode = legacy uniform (sync_all_posts)")
            summary = await sync_all_posts(conn, max_posts=args.max_posts)
        else:
            print("[Engagement] mode = log-scale bucket dispatch (sync_bucket_polls)")
            summary = await sync_bucket_polls(conn)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        conn.close()

    asyncio.run(_main())
