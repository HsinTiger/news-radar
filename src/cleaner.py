"""
News Radar · Cleaner
[Module 2] 清洗層 — 零 token 消耗
HTML → Markdown via trafilatura；抓 og:image；關鍵字 / 字數過濾
"""
from __future__ import annotations
from typing import Optional, Tuple, List, Dict, Any

from bs4 import BeautifulSoup
import trafilatura

from .schema import NewsItem


def extract_markdown(html: str) -> Tuple[Optional[str], int]:
    """
    把 HTML 純化成 markdown，回傳 (markdown, word_count)。
    word_count 是「非空白字元除以 5」的粗估（中英混合通用指標）。
    """
    md = trafilatura.extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_tables=False,
        include_links=False,
        no_fallback=False,
    )
    if not md:
        return None, 0

    # 粗估字數（中英混合）
    stripped = "".join(md.split())
    wc = max(1, len(stripped) // 3)  # 中文字元較多，除 3 比除 5 接近實際閱讀量
    return md, wc


def extract_og_image(html: str) -> Optional[str]:
    try:
        soup = BeautifulSoup(html, "html.parser")
        # 優先順序：og:image → twitter:image → 第一張 <img>
        for prop in ("og:image", "twitter:image"):
            tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                return tag["content"]
        first_img = soup.find("img")
        if first_img and first_img.get("src"):
            return first_img["src"]
    except Exception:
        pass
    return None


# Phase 8.16：Meta Graph API 上傳影片的硬性要求 = 可直 GET 的 .mp4/.mov。
# 只要 URL path 落在這組副檔名裡，我們就當做 "direct" 可以丟進 publisher 嘗試上傳；
# 落在 embed/iframe（YouTube embed、Twitter player 等）的，當成「視覺記號」存起來，
# 之後再交由 video_manager 之類的層去解成真正的 .mp4（或退回到純圖文流程）。
_DIRECT_VIDEO_EXTS = (".mp4", ".m4v", ".mov", ".webm")
# .m3u8 是 HLS playlist，Meta 不收；但我們還是算「direct」以便 publisher 端顯式拒掉
# 並 log 出來 (留 audit trail)，否則 silent fall through 很難 debug
_STREAMING_VIDEO_EXTS = (".m3u8",)


def _classify_video_url(url: Optional[str]) -> tuple[Optional[str], bool]:
    """回傳 (正規化後 URL, is_direct)。
    is_direct=True 表示 URL 指向 publisher 可嘗試上傳的媒體檔；
    False 表示是 embed / player / iframe / 未知格式，需下游再解。
    """
    if not url:
        return None, False
    url = url.strip()
    if not url:
        return None, False
    # 拆掉 query string 再判副檔名（很多 CDN 會帶 ?token=...）
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    for ext in _DIRECT_VIDEO_EXTS:
        if path.endswith(ext):
            return url, True
    for ext in _STREAMING_VIDEO_EXTS:
        if path.endswith(ext):
            # 故意標 direct=True — 讓 publisher 明確 reject HLS 而不是 silently skip
            return url, True
    return url, False


def extract_og_video(html: str) -> tuple[Optional[str], bool]:
    """從 HTML 中抽出最具價值的影片 URL，回傳 (url, is_direct)。

    優先序（嚴格由上到下）：
      1. `<meta property="og:video:secure_url">`  — HTTPS 直鏈，最優先
      2. `<meta property="og:video:url">`         — og:video 正規命名
      3. `<meta property="og:video">`             — 舊版 / 簡寫
      4. `<meta name="twitter:player:stream">`    — Twitter Cards 直接串流
      5. `<video src=...>` / `<video><source src=...></video>`

    回傳的 is_direct 由 `_classify_video_url` 判定；embed/iframe URL 會回 False，
    publisher 端才能決定要不要嘗試上傳還是退回圖文。
    """
    if not html:
        return None, False
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None, False

    # 1~3：og:video 系列（property=） + OGP 規範允許 name= 寫法（有些 CMS 會用）
    for prop in ("og:video:secure_url", "og:video:url", "og:video"):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            url, is_direct = _classify_video_url(tag["content"])
            if url:
                return url, is_direct

    # 4：twitter:player:stream（純 stream URL，不是 player HTML 頁）
    twit = soup.find("meta", attrs={"name": "twitter:player:stream"}) \
        or soup.find("meta", property="twitter:player:stream")
    if twit and twit.get("content"):
        url, is_direct = _classify_video_url(twit["content"])
        if url:
            return url, is_direct

    # 5：<video> / <source>
    video_tag = soup.find("video")
    if video_tag:
        # src 屬性直接掛在 <video> 上
        if video_tag.get("src"):
            url, is_direct = _classify_video_url(video_tag["src"])
            if url:
                return url, is_direct
        # 或塞在第一個 <source> 子節點
        source_tag = video_tag.find("source")
        if source_tag and source_tag.get("src"):
            url, is_direct = _classify_video_url(source_tag["src"])
            if url:
                return url, is_direct

    return None, False


def keyword_filter(
    item: NewsItem, must_any: List[str], must_exclude: List[str]
) -> Tuple[bool, Optional[str]]:
    """
    關鍵字白名單 / 黑名單過濾。
    回傳 (是否通過, drop_reason or None)。
    """
    corpus = f"{item.title}\n{item.clean_markdown or ''}".lower()

    # 黑名單：命中任一個就 Drop
    for kw in must_exclude:
        if kw.lower() in corpus:
            return False, f"blacklist:{kw}"

    # 白名單：必須命中至少一個
    if must_any:
        for kw in must_any:
            if kw.lower() in corpus:
                return True, None
        return False, "no_keyword_match"

    return True, None


def resolve_min_word_count(cfg_value: Any, source_type: str) -> int:
    """把 `filters.min_word_count` 解析成單一整數門檻。

    支援兩種 config 寫法：
      1) 整數（舊版相容）    → 所有 source_type 共用同一門檻
      2) dict（Phase 8.9）  → 依 source_type 取值；查不到再 fallback 到 `default`

    回傳值保證 ≥ 0 的 int。非預期型別一律退化成 100。
    """
    if isinstance(cfg_value, bool):
        # bool 是 int 的子型別，要先排除掉避免誤判
        return 100
    if isinstance(cfg_value, int):
        return max(0, cfg_value)
    if isinstance(cfg_value, dict):
        for key in (source_type, "default"):
            val = cfg_value.get(key)
            if isinstance(val, int) and not isinstance(val, bool):
                return max(0, val)
    return 100


def min_length_filter(
    item: NewsItem, cfg_value: Any
) -> Tuple[bool, Optional[str]]:
    """字數門檻過濾。`cfg_value` 接受 int 或 dict（見 `resolve_min_word_count`）。

    drop_reason 會把 source_type 與門檻都寫進去，方便 diagnose_harvest 分層看。
    """
    min_wc = resolve_min_word_count(cfg_value, item.source_type)
    if item.word_count < min_wc:
        return False, f"too_short[{item.source_type}]:{item.word_count}<{min_wc}"
    return True, None


async def clean_and_filter(
    item: NewsItem,
    html: str,
    cfg: Dict[str, Any],
) -> Tuple[NewsItem, bool, Optional[str]]:
    """
    完整清洗 + 過濾 pipeline。
    回傳 (更新後的 item, 是否通過, drop_reason)
    """
    # 1. 純化成 markdown (若已從 fetcher 解析則跳過，例如 Youtube)
    if not item.clean_markdown:
        md, wc = extract_markdown(html)
        item.raw_html = None  # 預設不存 raw HTML 以節省空間；debug 時可改存
        item.clean_markdown = md
        item.word_count = wc
    else:
        # 已有字串，粗估字數
        stripped = "".join(item.clean_markdown.split())
        item.word_count = max(1, len(stripped) // 3)

    # 2. 抓 og:image 與 og:video（有 html 則抓；無 html 代表 fetcher 已預填 markdown，跳過）
    if html:
        item.og_image_url = extract_og_image(html)
        # og_video_url 若 fetcher 已從 RSS enclosure 預填（podcast / 原生影片 feed），
        # 不要被 HTML 抽取的結果覆蓋（enclosure URL 通常比頁面 og:video 更穩）
        if not item.og_video_url:
            video_url, is_direct = extract_og_video(html)
            if video_url:
                item.og_video_url = video_url
                item.og_video_is_direct = is_direct

    if not item.clean_markdown:
        return item, False, "extract_failed"

    # 3. 字數過濾（cfg 端可以是 int 舊式或 dict 新式，resolve_min_word_count 處理）
    passed, reason = min_length_filter(item, cfg["filters"]["min_word_count"])
    if not passed:
        return item, False, reason

    # 4. 關鍵字過濾
    kw_cfg = cfg["keywords"]
    passed, reason = keyword_filter(
        item,
        kw_cfg.get("must_include_any", []),
        kw_cfg.get("must_exclude_any", []),
    )
    if not passed:
        return item, False, reason

    return item, True, None
