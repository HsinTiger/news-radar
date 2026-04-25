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


async def publish_to_fb(
    text: str,
    image_url: Optional[str] = None,
    local_file_path: Optional[str] = None,
    video_url: Optional[str] = None,
) -> Dict:
    """發 FB 粉專。

    優先級：
        1. video_url（公開 .mp4 URL）→ `/videos` 端點
        2. local_file_path → `/photos` with file upload (Plan B)
        3. image_url → `/photos` with url= (Plan A)
        4. 皆無 / image_url 非合法 → `/feed` 純文字

    歷史脈絡（2026-04-25 一日內反覆）：
    今天稍早把這支 fetch 改成 `attached_media` 兩步流程（先 published=false 上
    圖、再 /feed 帶 attached_media），原以為這樣才能讓貼文出現在 Posts tab。
    深入測試後發現：Posts/Photos tab 的可見性問題其實是 **Meta App 在 Dev mode**
    導致的——切到 Live mode 之後，所有歷史 /photos 上傳的貼文 retroactively
    出現在 Posts tab，跟 attached_media post 並無顯示差異。

    既然 /photos 一步流程在 Live mode 下表現相同、且：
      - API call 少 1 次（少 ~1 秒）
      - FB 對 photo post 有歷史 reach 紅利（algorithm 偏愛 native photo）
      - Photos tab + Posts tab 雙重曝光
    沒有理由維持兩步流程。Revert 回 /photos。

    Keep（仍有用）：`_is_valid_http_url` URL guard——擋住相對路徑 og_image
    （`/_astro/...`）這種 FB 會回 `(#100)` 的爛 URL，省一個必爛的 API call。
    Drop（已無用）：`_fb_upload_unpublished_photo` helper、`FB_FALLBACK_PHOTO_ID`
    env var、`json` import。

    詳細日誌見 docs/worklog/2026-04-25.md「FB endpoint 來回踩坑記」。
    """
    if _over_limit(text, FB_MAX):
        msg = f"FB 文字超限:{len(text)} > {FB_MAX},拒發"
        print(f"[Publisher: FB] {msg}")
        return {"success": False, "error": {"local_reject": msg}}

    base = "https://graph.facebook.com/v20.0"
    params = {"access_token": FB_PAGE_ACCESS_TOKEN}

    # 影片優先
    if video_url:
        print(f"[Publisher: FB] 正在由影片 URL 發布 (/videos)...")
        endpoint = f"/{FB_PAGE_ID}/videos"
        params.update({"file_url": video_url, "description": text})
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(f"{base}{endpoint}", params=params)
            data = resp.json()
            if resp.status_code == 200:
                print(f"[Success: FB] Video ID: {data.get('id')}")
                return {"success": True, "id": data.get("id"), "media_kind": "video"}
            print(f"[Error: FB video] {data.get('error', {}).get('message')}")
            return {"success": False, "error": data}

    # local file 上傳（自家下載過的圖一定能傳成功）
    if local_file_path and os.path.exists(local_file_path):
        print(f"[Publisher: FB] 正在由在地檔案發布 (Plan B)...")
        endpoint = f"/{FB_PAGE_ID}/photos"
        params["caption"] = text
        # 用 with 包著確保 exception 時也會關檔；之前寫 `open(...)` 直接塞 dict
        # 會 leak file handle 如果 httpx call 拋例外。
        with open(local_file_path, "rb") as fh:
            files = {"source": fh}
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{base}{endpoint}", params=params, files=files)
                data = resp.json()
                if resp.status_code == 200:
                    print(f"[Success: FB] ID: {data.get('id')}")
                    return {"success": True, "id": data.get("id"), "media_kind": "image"}
                print(f"[Error: FB] {data.get('error', {}).get('message')}")
                return {"success": False, "error": data}

    # image_url 上傳（FB 端抓圖）
    # URL guard：擋住 `/_astro/x.png` 這種相對路徑爛 URL，省一個必爛的 API call
    if image_url and _is_valid_http_url(image_url):
        print(f"[Publisher: FB] 正在由網址發布 (Plan A)...")
        endpoint = f"/{FB_PAGE_ID}/photos"
        params.update({"url": image_url, "caption": text})
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{base}{endpoint}", params=params)
            data = resp.json()
            if resp.status_code == 200:
                print(f"[Success: FB] ID: {data.get('id')}")
                return {"success": True, "id": data.get("id"), "media_kind": "image"}
            print(f"[Error: FB] {data.get('error', {}).get('message')}")
            return {"success": False, "error": data}
    elif image_url:
        # 有給 image_url 但不是合法 http(s) URL → 不要打 API（會回 #100）
        print(f"[Publisher: FB] image_url 非合法 http(s) URL，跳過圖片改純文字 /feed: {image_url!r}")

    # 純文字（image_url None / 無效 / 沒給）
    print(f"[Publisher: FB] 正在進行純文字發布...")
    endpoint = f"/{FB_PAGE_ID}/feed"
    params["message"] = text
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{base}{endpoint}", params=params)
        data = resp.json()
        if resp.status_code == 200:
            print(f"[Success: FB] ID: {data.get('id')}")
            return {"success": True, "id": data.get("id"), "media_kind": "text"}
        print(f"[Error: FB] {data.get('error', {}).get('message')}")
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
