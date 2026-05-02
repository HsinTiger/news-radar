"""
image_prep.py — IG 發布前的圖片 pre-flight 檢查與自動修正

Why this module exists
----------------------
IG Graph API 比 FB 嚴格——它拒發 aspect ratio 在 [0.8, 1.91] 之外的圖，或
檔案大於 8 MB 的圖。FB 跟 Threads 沒這些限制。

過去做法：composer 把 og:image 的 URL 原封傳給三個平台，IG 偶爾被退（例如
Decrypt 的 1024×512 標準 banner = 2.0，剛超過 1.91）。當時的人工救法是手改
JSON 加 `image_url` 欄位走 CDN 的 on-the-fly resize。

本模組把這個「手改」自動化：在呼叫 IG publish 前先探頭，判斷 URL 是否合規；
不合規則嘗試 rewrite；真的沒招 → 回報失敗讓 caller 優雅跳過 IG。

Design constraints
------------------
- IG Graph API 只接受「公開可達 URL」，不接受 bytes 上傳
  → 本版（v1）做 URL rewrite；PIL download + pad + 上傳 gh-pages 留到 v2
- 探頭用 HTTP Range request 讀 PNG/JPEG headers，不下載整張圖
- Rewriter 失敗時以「優雅降級」為原則：寧可讓 IG 自行報錯，也不要自作聰明
  阻擋原本會成功的請求

Public API
----------
    result = await prepare_image_for_ig(url)
    if result.is_usable:
        # use result.url with IG Graph API (可能是原 URL，也可能是 rewrite 過的)
        ...
    else:
        # IG 應該跳過；result.reason 解釋為什麼
        ...

Usage in publisher.py
---------------------
    from .image_prep import prepare_image_for_ig

    async def publish_to_ig(text, image_url=None, video_url=None):
        if image_url and not video_url:
            prep = await prepare_image_for_ig(image_url)
            print(prep.log_line())
            if not prep.is_usable:
                return {"success": False, "error": {"local_reject": prep.reason}}
            image_url = prep.url
        # ... rest of publish logic

See docs/System_Architecture.md §7 追加 case study when ready.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Literal, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

# ─── Meta IG 官方規格（2026-04 driver） ────────────────────────────────────────
# Graph API docs: aspect ratio 0.8 – 1.91；filesize ≤ 8 MB；format JPEG/PNG
#
# 2026-05-02 fix: lowered MIN from 0.81 → 0.80 to match the spec exactly.
# The earlier 1% inward buffer was over-paranoid and ended up rejecting
# our OWN spec-compliant covers (1080×1350 = 0.800 exactly), causing 25h
# of silent IG publish failures. If Meta API actually rejects an edge
# value, that surfaces as a Meta-side error (clear, actionable) rather
# than a silent local_reject from us. MAX kept at 1.91 (spec value).
IG_RATIO_MIN = 0.80
IG_RATIO_MAX = 1.91
IG_FILESIZE_MAX = 8 * 1024 * 1024  # 8 MB

# Rewrite 時 target 用的 ratio；1.6 = 8:5，落在安全區正中，兩邊都有 breathing room
REWRITE_TARGET_RATIO = 1.6

Action = Literal[
    "ok",                   # 原 URL 合規，無需動作
    "rewrote",              # 透過 CDN rewrite 改成合規 URL
    "skipped_probe_failed", # 無法探頭（網路/403/etc.），讓 caller 用原 URL 自己試
    "failed_no_rewriter",   # 不合規且找不到認得的 CDN；caller 應跳過 IG
]


@dataclass
class PrepResult:
    url: Optional[str]
    action: Action
    ratio_before: Optional[float] = None
    ratio_after: Optional[float] = None
    filesize_before: Optional[int] = None
    reason: Optional[str] = None

    @property
    def is_usable(self) -> bool:
        """Caller 應該拿 self.url 去發 IG 嗎？"""
        # 探頭失敗 → 照樣拿原 URL 試（保留 pre-module 時代的行為）
        return self.action in ("ok", "rewrote", "skipped_probe_failed")

    def log_line(self) -> str:
        parts = [f"[image_prep] action={self.action}"]
        if self.ratio_before is not None:
            parts.append(f"ratio={self.ratio_before:.3f}")
        if self.ratio_after is not None and self.ratio_after != self.ratio_before:
            parts.append(f"→{self.ratio_after:.3f}")
        if self.filesize_before:
            parts.append(f"size={self.filesize_before / 1024:.0f}KB")
        if self.reason:
            parts.append(f"reason={self.reason}")
        return " ".join(parts)


# ─── Header 解析（不下載整張圖） ───────────────────────────────────────────────

def _read_png_dimensions(head: bytes) -> Optional[Tuple[int, int]]:
    """PNG: 8-byte signature + 4-byte chunk len + 'IHDR' + width(4) + height(4)."""
    if len(head) < 24:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if head[12:16] != b"IHDR":
        return None
    width = struct.unpack(">I", head[16:20])[0]
    height = struct.unpack(">I", head[20:24])[0]
    return (width, height)


def _read_jpeg_dimensions(head: bytes) -> Optional[Tuple[int, int]]:
    """JPEG: 掃 SOF0/SOF2 marker（0xFFC0 / 0xFFC2 等），從中取 width/height。"""
    if not head.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i < len(head) - 9:
        if head[i] != 0xFF:
            i += 1
            continue
        marker = head[i + 1]
        # SOI/EOI/restart markers: no length field
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        # Safety: need 2 bytes of segment length
        if i + 4 > len(head):
            break
        segment_len = struct.unpack(">H", head[i + 2:i + 4])[0]
        # SOF markers: 0xC0–0xCF, 但 0xC4 (DHT) / 0xC8 (JPG) / 0xCC (DAC) 不是 SOF
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 > len(head):
                return None
            # 略過 precision(1)；接 height(2) width(2)
            height = struct.unpack(">H", head[i + 5:i + 7])[0]
            width = struct.unpack(">H", head[i + 7:i + 9])[0]
            return (width, height)
        i += 2 + segment_len
    return None


def parse_dimensions(head: bytes) -> Optional[Tuple[int, int]]:
    """純函式：給 header bytes 回傳 (width, height)。PNG 優先，JPEG 次之。"""
    return _read_png_dimensions(head) or _read_jpeg_dimensions(head)


# ─── 網路探頭 ─────────────────────────────────────────────────────────────────

async def probe_image(
    url: str,
    timeout: float = 5.0,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[Tuple[int, int, int]]:
    """
    回傳 (width, height, filesize_bytes) 或 None（任何失敗都是 None，caller 自己處理）。

    方法：HTTP Range GET 前 64 KB，足以覆蓋幾乎所有 PNG/JPEG 的 header。
    filesize 從 Content-Range 的 total 取；沒給則 fallback 到 Content-Length。
    """
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        r = await client.get(url, headers={"Range": "bytes=0-65535"})
        if r.status_code not in (200, 206):
            return None
        head = r.content
        dims = parse_dimensions(head)
        if dims is None:
            return None
        cr = r.headers.get("content-range", "")  # e.g. "bytes 0-65535/1234567"
        if "/" in cr:
            try:
                filesize = int(cr.split("/")[-1])
            except ValueError:
                filesize = len(head)
        else:
            try:
                filesize = int(r.headers.get("content-length", "0")) or len(head)
            except ValueError:
                filesize = len(head)
        return (*dims, filesize)
    except (httpx.HTTPError, httpx.InvalidURL):
        return None
    finally:
        if owns_client:
            await client.aclose()


# ─── CDN rewriters ────────────────────────────────────────────────────────────
# 每個 rewriter：收 (url, target_w, target_h) → 回傳新 URL 或 None（表示此 host
# 不適用）。順序由 _REWRITERS 決定，host-specific 優先於 generic。

def _rewrite_decrypt(url: str, target_w: int, target_h: int) -> Optional[str]:
    """Decrypt CDN: /resize/{w}/height/{h}/wp-content/..."""
    p = urlparse(url)
    if "cdn.decrypt.co" not in p.netloc:
        return None
    parts = p.path.split("/")
    # 預期: ["", "resize", "{w}", "height", "{h}", "wp-content", ...]
    if len(parts) < 6 or parts[1] != "resize" or parts[3] != "height":
        return None
    parts[2] = str(target_w)
    parts[4] = str(target_h)
    return urlunparse(p._replace(path="/".join(parts)))


def _rewrite_wp_content(url: str, target_w: int, target_h: int) -> Optional[str]:
    """
    WordPress（/wp-content/uploads/...）上常以 Jetpack 或 W3 Total Cache 提供
    on-the-fly resize：加 ?w=&h=&crop=1 query string。不是每個站都認，但命中
    率在科技新聞類很高。
    """
    p = urlparse(url)
    if "/wp-content/" not in p.path:
        return None
    q = parse_qs(p.query)
    q["w"] = [str(target_w)]
    q["h"] = [str(target_h)]
    q["crop"] = ["1"]
    return urlunparse(p._replace(query=urlencode(q, doseq=True)))


# 順序：最精準的 host-specific 放前面；generic fallback 放後面
_REWRITERS = [_rewrite_decrypt, _rewrite_wp_content]


# ─── Target 尺寸計算 ──────────────────────────────────────────────────────────

def _choose_target_size(width: int, height: int) -> Tuple[int, int]:
    """
    基於原尺寸選一個落在安全區正中（ratio 1.6）的 target 尺寸。
    策略：保留 width；height = width / 1.6（調成較矮或較高都走這條）。
    超過 1600 寬則等比降採（避免觸 IG 8 MB 上限）。
    """
    width_cap = 1600
    if width > width_cap:
        width = width_cap
    new_height = int(round(width / REWRITE_TARGET_RATIO))
    return (width, new_height)


# ─── Main entry ───────────────────────────────────────────────────────────────

async def prepare_image_for_ig(
    url: str,
    client: Optional[httpx.AsyncClient] = None,
) -> PrepResult:
    """
    IG publish 前的 pre-flight + auto-rewrite。

    流程：
        1. 探頭原 URL → 讀 (w, h, filesize)
        2. 探頭失敗 → 回 skipped_probe_failed（保留原 URL，照樣可用）
        3. 合規 → 回 ok
        4. 不合規 → 走 rewriter chain，第一個回傳有效 URL 且探頭合規者勝
        5. 全部 rewriter 都沒用 → failed_no_rewriter（caller 應跳過 IG）

    注意：探頭網路錯誤不應擋下 IG 發文——這個模組只做「能救就救」，不做「把關」。
    真正的把關在 Meta API 本身。
    """
    probe = await probe_image(url, client=client)
    if probe is None:
        return PrepResult(
            url=url, action="skipped_probe_failed",
            reason="cannot read image dimensions (network/404/non-image)",
        )

    width, height, filesize = probe
    ratio = width / height if height else 0.0
    ratio_ok = IG_RATIO_MIN <= ratio <= IG_RATIO_MAX
    size_ok = filesize <= IG_FILESIZE_MAX

    if ratio_ok and size_ok:
        return PrepResult(
            url=url, action="ok",
            ratio_before=ratio, filesize_before=filesize,
        )

    target_w, target_h = _choose_target_size(width, height)

    for rewriter in _REWRITERS:
        new_url = rewriter(url, target_w, target_h)
        if new_url is None or new_url == url:
            continue
        new_probe = await probe_image(new_url, client=client)
        if new_probe is None:
            continue
        nw, nh, nsize = new_probe
        nratio = nw / nh if nh else 0.0
        if IG_RATIO_MIN <= nratio <= IG_RATIO_MAX and nsize <= IG_FILESIZE_MAX:
            return PrepResult(
                url=new_url, action="rewrote",
                ratio_before=ratio, ratio_after=nratio,
                filesize_before=filesize,
            )

    reasons = []
    if not ratio_ok:
        reasons.append(f"ratio={ratio:.3f} outside [{IG_RATIO_MIN},{IG_RATIO_MAX}]")
    if not size_ok:
        reasons.append(f"size={filesize} bytes > {IG_FILESIZE_MAX}")
    return PrepResult(
        url=None, action="failed_no_rewriter",
        ratio_before=ratio, filesize_before=filesize,
        reason="; ".join(reasons) + "; no CDN rewriter matched",
    )
