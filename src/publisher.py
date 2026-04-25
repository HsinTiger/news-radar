"""
News Radar · Publisher 模組（Milestone 3.1 + Phase 8.14 影片擴充）
功能：將已由 composer 組好的「平台專屬文字」送往 Facebook / Instagram / Threads。

設計變更（相對 Milestone 2）：
- 移除 `text[:500]` 暴力截斷。字數合規由 composer 負責，publisher 只做三件事：
    (1) 長度最終稽查（超限就拒發 + log，絕不截斷）
    (2) 呼叫 Meta Graph API
    (3) 回傳乾淨的結果 dict
- 所有 `time.sleep` 都使用 `asyncio.sleep`（保留既有非同步安全設計）。

Phase 8.14 · 短影片支援（2026-04-19）：
- 三個 publisher 都新增 `video_url` 參數，優先於 image_url。
- 餵進 Meta API 的必須是公開可存取的 .mp4 URL（廠商 CDN / S3 / R2 / GitHub raw），
  YouTube / TikTok 分享連結不是 .mp4 檔案，Meta 無法抓取。
- IG Reels / Threads VIDEO 使用「建立容器 → 輪詢 status_code 直到 FINISHED → publish」
  而非固定 sleep。輪詢 60 秒 timeout、每 3 秒打一次。
- FB `/videos` 是 sync-ish 端點，回傳即代表接收；我們不做輪詢。
- 不支援本地 upload 影片（我們的原則是『不落地』，影片只走 URL）。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Dict, Optional

import httpx
from dotenv import load_dotenv

from .image_prep import prepare_image_for_ig

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
# Optional: pre-uploaded brand fallback photo. When set, image upload failures
# fall back to this photo_id in attached_media instead of degrading to a
# text-only post (which would break visual consistency in the feed).
# To populate: manually upload a logo/brand image to your FB Page (no
# special API needed), grab the photo_id from the URL or via API, paste here.
FB_FALLBACK_PHOTO_ID = os.getenv("FB_FALLBACK_PHOTO_ID")

THREADS_USER_ID = os.getenv("THREADS_USER_ID")
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN")

IG_BUSINESS_ACCOUNT_ID = os.getenv("IG_BUSINESS_ACCOUNT_ID")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")

# 硬上限（僅用於最終稽查，composer 理應已處理）
FB_MAX = 60000          # Meta 官方技術上限 63K，取保守值
IG_MAX = 2200
THREADS_MAX = 500

# 影片容器輪詢參數
_VIDEO_POLL_INTERVAL_SEC = 3
_VIDEO_POLL_TIMEOUT_SEC = 90


def _over_limit(text: str, limit: int) -> bool:
    return len(text or "") > limit


async def _poll_ig_container_finished(client: httpx.AsyncClient, creation_id: str, access_token: str) -> Dict:
    """輪詢 IG 容器狀態直到 FINISHED / ERROR / TIMEOUT。
    回傳 {success: bool, status_code, error?}。
    """
    base = f"https://graph.facebook.com/v20.0/{creation_id}"
    elapsed = 0
    while elapsed < _VIDEO_POLL_TIMEOUT_SEC:
        resp = await client.get(base, params={"fields": "status_code,status", "access_token": access_token})
        data = resp.json()
        sc = data.get("status_code")
        print(f"   · [IG poll] elapsed={elapsed}s status_code={sc}")
        if sc == "FINISHED":
            return {"success": True, "status_code": sc}
        if sc == "ERROR":
            return {"success": False, "status_code": sc, "error": data}
        await asyncio.sleep(_VIDEO_POLL_INTERVAL_SEC)
        elapsed += _VIDEO_POLL_INTERVAL_SEC
    return {"success": False, "status_code": "TIMEOUT", "error": f"Polling timed out after {_VIDEO_POLL_TIMEOUT_SEC}s"}


async def _poll_threads_container_finished(client: httpx.AsyncClient, creation_id: str, access_token: str) -> Dict:
    """Threads 版的容器狀態輪詢。端點：graph.threads.net/v1.0/{container_id}"""
    base = f"https://graph.threads.net/v1.0/{creation_id}"
    elapsed = 0
    while elapsed < _VIDEO_POLL_TIMEOUT_SEC:
        resp = await client.get(base, params={"fields": "status,error_message", "access_token": access_token})
        data = resp.json()
        st = data.get("status")
        print(f"   · [Threads poll] elapsed={elapsed}s status={st}")
        if st == "FINISHED":
            return {"success": True, "status": st}
        if st == "ERROR" or st == "EXPIRED":
            return {"success": False, "status": st, "error": data}
        await asyncio.sleep(_VIDEO_POLL_INTERVAL_SEC)
        elapsed += _VIDEO_POLL_INTERVAL_SEC
    return {"success": False, "status": "TIMEOUT", "error": f"Polling timed out after {_VIDEO_POLL_TIMEOUT_SEC}s"}


# ---------- Facebook ----------
def _is_valid_http_url(url: Optional[str]) -> bool:
    """簡單的 http(s):// URL guard。

    擋掉幾種會讓 FB 回 `(#100) url should represent a valid URL` 的型態：
      - None / 空字串
      - 相對路徑：`/path/img.png`、`./img.png`、`../foo.png`
      - protocol-relative：`//cdn.example.com/img.png`（FB 不接受）
      - 裸 host：`cdn.example.com/img.png`
      - 含空白字元（很少見但 RSS 偶爾會塞）

    為什麼不用 urllib.parse 全套 validation：那會誤殺 query string 含特殊字元的合
    法 URL（Reuters resizer 那種 auth=...&width=... 帶 base64 的）。簡單前綴判斷
    就夠擋掉今天遇到的 case，又不容易誤殺。
    """
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not url:
        return False
    if " " in url:
        return False
    return url.startswith("http://") or url.startswith("https://")


async def _fb_upload_unpublished_photo(
    client: httpx.AsyncClient,
    image_url: Optional[str] = None,
    local_file_path: Optional[str] = None,
) -> Optional[str]:
    """上傳一張圖到粉專的『未發佈圖庫』（published=false），回傳 photo_id。

    這是 attached_media 兩步流程的 Step 1。失敗回 None（讓上層決定怎麼降級），
    成功回 FB 給的 photo_id（供 Step 2 的 attached_media 使用）。

    優先 local_file_path（自家下載過的圖，FB 一定抓得到），fallback 到 image_url
    （FB 自己抓——對某些 CDN 會 403，那時候才會回 None）。

    URL guard（2026-04-25 加）：拿到 image_url 之前先驗證是 http(s):// 開頭。
    某些 site（例如 Astro 部落格）的 og:image 是相對路徑（`/_astro/x.png`），
    cleaner 沒 urljoin 補成絕對 URL 就直接給我們，丟給 FB 會回 `(#100)`。直接
    pre-validate 跳過，讓上層 fallback 不浪費 API call。
    """
    base = "https://graph.facebook.com/v20.0"
    endpoint = f"/{FB_PAGE_ID}/photos"

    # local file 優先：自己下載過的圖一定能傳成功
    if local_file_path and os.path.exists(local_file_path):
        params = {"published": "false", "access_token": FB_PAGE_ACCESS_TOKEN}
        with open(local_file_path, "rb") as fh:
            files = {"source": fh}
            try:
                resp = await client.post(f"{base}{endpoint}", params=params, files=files, timeout=120.0)
            except Exception as e:
                print(f"[Publisher: FB] _upload_unpublished (local) 例外：{e}")
                return None
        data = resp.json()
        if resp.status_code == 200:
            return data.get("id")
        print(f"[Publisher: FB] _upload_unpublished (local) 失敗：{data.get('error', {}).get('message')}")
        return None

    if image_url:
        # Pre-validate 在打 API 前擋掉相對路徑 / 半成品 URL
        if not _is_valid_http_url(image_url):
            print(f"[Publisher: FB] _upload_unpublished (url) skipped — 非合法 http(s) URL: {image_url!r}")
            return None

        params = {
            "url": image_url,
            "published": "false",
            "access_token": FB_PAGE_ACCESS_TOKEN,
        }
        try:
            resp = await client.post(f"{base}{endpoint}", params=params, timeout=120.0)
        except Exception as e:
            print(f"[Publisher: FB] _upload_unpublished (url) 例外：{e}")
            return None
        data = resp.json()
        if resp.status_code == 200:
            return data.get("id")
        print(f"[Publisher: FB] _upload_unpublished (url) 失敗：{data.get('error', {}).get('message')}")
        return None

    return None


async def publish_to_fb(
    text: str,
    image_url: Optional[str] = None,
    local_file_path: Optional[str] = None,
    video_url: Optional[str] = None,
) -> Dict:
    """發 FB 粉專。

    歷史 vs 現況（2026-04-25 改寫）：
    舊版走 `/{page-id}/photos?url=...&caption=...`，FB 把這種貼文歸類成
    「相片更新」，**只出現在 Photos tab、不在 Posts tab**——對非 follower
    完全隱形。我們累積 23+1 篇都中槍。

    新版走 `attached_media` 模式（Meta Business Suite / Buffer / Hootsuite 用的
    同一條 path）：
        Step 1: POST /{page-id}/photos?url=...&published=false → 拿 photo_id
        Step 2: POST /{page-id}/feed?message=...&attached_media=[{media_fbid}]
                → Posts tab 上的原生圖文貼文

    優先級：
        1. video_url        → `/videos`（不變）
        2. 圖片（image_url 或 local_file_path）→ attached_media 兩步流程
           - Step 1 失敗 + 有 FB_FALLBACK_PHOTO_ID env → 用 fallback photo
           - Step 1 失敗 + 沒 fallback → 純文字 `/feed`（仍在 Posts tab）
        3. 純文字 → `/feed`（不變）

    return shape 同舊版（success / id / media_kind / error），caller 不用改。
    """
    if _over_limit(text, FB_MAX):
        msg = f"FB 文字超限:{len(text)} > {FB_MAX},拒發"
        print(f"[Publisher: FB] {msg}")
        return {"success": False, "error": {"local_reject": msg}}

    base = "https://graph.facebook.com/v20.0"

    # ---- 影片優先（不變）-------------------------------------------------
    if video_url:
        print(f"[Publisher: FB] 正在由影片 URL 發布 (/videos)...")
        endpoint = f"/{FB_PAGE_ID}/videos"
        params = {
            "file_url": video_url,
            "description": text,
            "access_token": FB_PAGE_ACCESS_TOKEN,
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(f"{base}{endpoint}", params=params)
            data = resp.json()
            if resp.status_code == 200:
                print(f"[Success: FB] Video ID: {data.get('id')}")
                return {"success": True, "id": data.get("id"), "media_kind": "video"}
            print(f"[Error: FB video] {data.get('error', {}).get('message')}")
            return {"success": False, "error": data}

    # ---- 圖片：attached_media 兩步流程 -----------------------------------
    media_fbid: Optional[str] = None
    fallback_used: bool = False

    async with httpx.AsyncClient() as client:
        if image_url or local_file_path:
            print(f"[Publisher: FB] Step 1 · 上傳未發佈圖片...")
            media_fbid = await _fb_upload_unpublished_photo(
                client,
                image_url=image_url,
                local_file_path=local_file_path,
            )
            if media_fbid:
                print(f"[Publisher: FB] Step 1 OK · photo_id={media_fbid}")
            else:
                # 圖片上傳失敗的兩種降級路徑：
                if FB_FALLBACK_PHOTO_ID:
                    print(f"[Publisher: FB] Step 1 失敗 → 使用 FB_FALLBACK_PHOTO_ID")
                    media_fbid = FB_FALLBACK_PHOTO_ID
                    fallback_used = True
                else:
                    print(f"[Publisher: FB] Step 1 失敗 → 降級純文字 /feed（仍在 Posts tab）")

        # ---- Step 2: /feed ------------------------------------------------
        endpoint = f"/{FB_PAGE_ID}/feed"
        # 注意：data= 用 form body 傳 attached_media（JSON 字串），FB 比較穩；
        # 把 attached_media 放在 query string 容易被截斷或編碼出錯。
        params = {"access_token": FB_PAGE_ACCESS_TOKEN}
        body = {"message": text}
        if media_fbid:
            body["attached_media"] = json.dumps([{"media_fbid": media_fbid}])

        print(f"[Publisher: FB] Step 2 · 發 /feed (attached={'yes' if media_fbid else 'no'})...")
        try:
            resp = await client.post(
                f"{base}{endpoint}",
                params=params,
                data=body,
                timeout=120.0,
            )
        except Exception as e:
            return {"success": False, "error": {"exception": str(e)}}

        data = resp.json()
        if resp.status_code == 200:
            post_id = data.get("id") or data.get("post_id")
            kind_label = (
                "image_fallback" if fallback_used
                else ("image" if media_fbid else "text")
            )
            print(f"[Success: FB] post_id={post_id}  media_kind={kind_label}")
            return {
                "success": True,
                "id": post_id,
                "media_kind": kind_label,
            }
        err_msg = (data.get("error") or {}).get("message", "")
        print(f"[Error: FB /feed] {err_msg}")
        return {"success": False, "error": data}


# ---------- Threads ----------
async def publish_to_threads(
    text: str,
    image_url: Optional[str] = None,
    video_url: Optional[str] = None,
) -> Dict:
    """發 Threads。500 字元硬上限，超限直接拒發（不再截斷）。
    若有 video_url 則用 media_type=VIDEO，否則用 IMAGE。
    影片容器會輪詢 status 直到 FINISHED 才 publish。"""
    if _over_limit(text, THREADS_MAX):
        msg = f"Threads 文字超限:{len(text)} > {THREADS_MAX},拒發（composer 端應先壓短）"
        print(f"[Publisher: Threads] {msg}")
        return {"success": False, "error": {"local_reject": msg}}

    base = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}"

    if video_url:
        print(f"[Publisher: Threads] 建立 VIDEO 媒體容器... ({len(text)} 字)")
        container_params = {
            "media_type": "VIDEO",
            "video_url": video_url,
            "text": text,
            "access_token": THREADS_ACCESS_TOKEN,
        }
        media_kind = "video"
    else:
        if not image_url:
            return {"success": False, "error": {"local_reject": "Threads 必須有 image_url 或 video_url"}}
        print(f"[Publisher: Threads] 建立 IMAGE 媒體容器... ({len(text)} 字)")
        container_params = {
            "media_type": "IMAGE",
            "image_url": image_url,
            "text": text,
            "access_token": THREADS_ACCESS_TOKEN,
        }
        media_kind = "image"

    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(f"{base}/threads", params=container_params)
        container_data = resp.json()
        if resp.status_code != 200:
            print(f"[Error: Threads] 容器建立失敗: {container_data}")
            return {"success": False, "error": container_data}

        creation_id = container_data.get("id")

        if media_kind == "video":
            print(f" ↳ VIDEO 容器已建立: {creation_id},開始輪詢處理狀態...")
            poll = await _poll_threads_container_finished(client, creation_id, THREADS_ACCESS_TOKEN)
            if not poll["success"]:
                print(f"[Error: Threads] 影片處理未完成: {poll}")
                return {"success": False, "error": poll}
        else:
            print(f" ↳ 容器已建立: {creation_id},等待 10 秒讓伺服器抓取圖片...")
            await asyncio.sleep(10)

        publish_params = {"creation_id": creation_id, "access_token": THREADS_ACCESS_TOKEN}
        resp = await client.post(f"{base}/threads_publish", params=publish_params)
        publish_data = resp.json()
        if resp.status_code == 200:
            print(f"[Success: Threads] ID: {publish_data.get('id')}")
            return {"success": True, "id": publish_data.get("id"), "media_kind": media_kind}
        print(f"[Error: Threads] {publish_data}")
        return {"success": False, "error": publish_data}


# ---------- Instagram ----------
async def publish_to_ig(
    text: str,
    image_url: Optional[str] = None,
    video_url: Optional[str] = None,
) -> Dict:
    """發 IG。video_url 會走 REELS 流程（含 share_to_feed=true 讓 Reel 同步上主 feed），
    image_url 走單張圖片流程。影片容器會輪詢 status_code 直到 FINISHED。"""
    if _over_limit(text, IG_MAX):
        msg = f"IG 文字超限:{len(text)} > {IG_MAX},拒發"
        print(f"[Publisher: IG] {msg}")
        return {"success": False, "error": {"local_reject": msg}}

    base = f"https://graph.facebook.com/v20.0/{IG_BUSINESS_ACCOUNT_ID}"

    if video_url:
        print(f"[Publisher: IG] 建立 REELS 媒體容器... ({len(text)} 字)")
        container_params = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": text,
            "share_to_feed": "true",
            "access_token": IG_ACCESS_TOKEN,
        }
        media_kind = "video"
    else:
        if not image_url:
            return {"success": False, "error": {"local_reject": "IG 必須有 image_url 或 video_url"}}

        # Pre-flight: IG 對 aspect ratio (0.8–1.91) 跟 filesize (≤8 MB) 嚴格把關，
        # FB / Threads 不管。在打 API 前先探頭，不合規則嘗試 CDN rewrite，救不回來
        # 就 local_reject 優雅跳過 IG。詳見 src/image_prep.py。
        prep = await prepare_image_for_ig(image_url)
        print(prep.log_line())
        if not prep.is_usable:
            return {"success": False, "error": {"local_reject": f"image_prep: {prep.reason}"}}
        image_url = prep.url

        print(f"[Publisher: IG] 建立 IMAGE 媒體容器... ({len(text)} 字)")
        container_params = {
            "image_url": image_url,
            "caption": text,
            "access_token": IG_ACCESS_TOKEN,
        }
        media_kind = "image"

    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(f"{base}/media", params=container_params)
        container_data = resp.json()
        if resp.status_code != 200:
            print(f"[Error: IG] 容器建立失敗: {container_data}")
            return {"success": False, "error": container_data}

        creation_id = container_data.get("id")

        if media_kind == "video":
            print(f" ↳ REELS 容器已建立: {creation_id},開始輪詢處理狀態...")
            poll = await _poll_ig_container_finished(client, creation_id, IG_ACCESS_TOKEN)
            if not poll["success"]:
                print(f"[Error: IG] Reels 處理未完成: {poll}")
                return {"success": False, "error": poll}
        else:
            print(f" ↳ 容器已建立: {creation_id},等待 5 秒...")
            await asyncio.sleep(5)

        publish_params = {"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN}
        resp = await client.post(f"{base}/media_publish", params=publish_params)
        publish_data = resp.json()
        if resp.status_code == 200:
            print(f"[Success: IG] ID: {publish_data.get('id')}")
            return {"success": True, "id": publish_data.get("id"), "media_kind": media_kind}
        print(f"[Error: IG] {publish_data}")
        return {"success": False, "error": publish_data}


if __name__ == "__main__":
    pass
