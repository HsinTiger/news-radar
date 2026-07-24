"""
News Radar · Fetcher
[Module 1] Deterministic 爬蟲層 — 零 token 消耗
RSS feedparser → httpx 抓原始 HTML → 交給 cleaner 處理
"""
from __future__ import annotations
import asyncio
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin

import httpx
import feedparser
import yaml
from bs4 import BeautifulSoup

from .schema import NewsItem

_BASE = Path(__file__).resolve().parent.parent
CONFIG_PATH = _BASE / "config" / "config.yaml"


def load_config() -> Dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def make_news_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _limited_entries(entries: List[Any], configured_limit: Any) -> List[Any]:
    """Bound archive-heavy feeds without changing existing feed behavior.

    Several Taiwan official RSS endpoints expose hundreds of historical rows.
    Fetching every linked page on first enablement would bury current public-
    interest signals and create an avoidable traffic spike.  Missing limits keep
    the legacy behavior; malformed limits fail closed instead of silently
    truncating a feed.
    """
    if configured_limit is None:
        return list(entries)
    if (
        isinstance(configured_limit, bool)
        or not isinstance(configured_limit, int)
        or configured_limit < 1
        or configured_limit > 100
    ):
        raise ValueError("feed max_entries must be an integer in 1..100")
    return list(entries[:configured_limit])


def _resolve_entry_link(feed_url: str, entry_link: str) -> str:
    """Resolve relative RSS entry links against the authoritative feed URL."""
    return urljoin(feed_url, str(entry_link or "").strip())


# ---------- URL rewriters（deterministic，易測、易回放）----------

def _rewrite_url_for_extraction(url: str) -> str:
    """把已知對 trafilatura 不友善的 URL 改寫成「內容一致但好抓」的版本。

    目前規則：
      * `(www|new|m).reddit.com/r/...` → `old.reddit.com/r/...`
        new reddit 是全 JS render + 積極的 bot wall，trafilatura 幾乎抽不到東西；
        old.reddit.com 是 server-rendered HTML，正文就在第一屏，抽取成功率 >> 95%。

    新增規則時請同步補 `tests/unit/test_fetcher_helpers.py`。
    """
    if "reddit.com/r/" in url and "old.reddit.com" not in url:
        for host in ("://www.reddit.com", "://new.reddit.com", "://m.reddit.com"):
            if host in url:
                return url.replace(host, "://old.reddit.com", 1)
        # 裸 reddit.com（無子域名）也蓋掉
        if "://reddit.com/r/" in url:
            return url.replace("://reddit.com", "://old.reddit.com", 1)
    return url


def _reddit_rss_to_markdown(raw_summary: str) -> Optional[str]:
    """Reddit RSS `<summary>` 裡帶的是 post body 的 HTML。
    把它純化成可讀文字；若結果太短（多半是 link post 只有 `[link] [comments]`），
    回 None 讓下游走正常 fetch_html 流程。
    """
    if not raw_summary:
        return None
    try:
        text = BeautifulSoup(raw_summary, "html.parser").get_text("\n", strip=True)
    except Exception:
        return None
    # 清掉 reddit 常見的 boilerplate
    for noise in ("[link]", "[comments]", "submitted by"):
        text = text.replace(noise, "")
    text = "\n".join(line for line in text.splitlines() if line.strip())
    return text if len(text) > 60 else None


def _parse_rss_time(entry) -> str:
    """RSS 時間格式千奇百怪，統一成 ISO8601。抓不到就用 now。"""
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            return datetime(*tm[:6], tzinfo=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


_BROWSER_HEADERS = {
    # Phase 8.10-b：Peter Zeihan 事件揭穿了 `fetch_feed` 以前用 httpx 預設 UA
    # (`python-httpx/x.y`) 會被 Cloudflare 直接 403。統一成 Safari UA，與
    # `fetch_html` + `tools/diagnose_feeds.py` 對齊，避免「diag HEALTHY 但 harvest 403」
    # 這類會浪費除錯時間的訊號不一致。
    #
    # Phase 8.10-c：刻意只送 UA、不送 narrow `Accept` header。上一版給了
    # `Accept: application/rss+xml, ...` 結果 art19.com 對 Howard Marks 回空體
    # （diag 拿 77 entries，harvest 拿 0）—— 某些 podcast CDN 會做 content-negotiation，
    # 看到窄版 Accept 就回 fallback 內容。讓 httpx 用預設 `Accept: */*` 最穩。
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
}
_TRANSIENT_FEED_STATUS = {429, 500, 502, 503, 504}
_FEED_RETRY_DELAYS = (0.5, 1.5)


async def _get_feed_with_retry(
    client: httpx.AsyncClient,
    url: str,
) -> httpx.Response:
    """Retry only bounded transient HTTP failures; never loop on bad URLs."""
    last_response: httpx.Response | None = None
    for attempt in range(len(_FEED_RETRY_DELAYS) + 1):
        response = await client.get(
            url,
            timeout=15,
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
        )
        last_response = response
        if response.status_code not in _TRANSIENT_FEED_STATUS:
            response.raise_for_status()
            return response
        if attempt < len(_FEED_RETRY_DELAYS):
            await asyncio.sleep(_FEED_RETRY_DELAYS[attempt])
    assert last_response is not None
    last_response.raise_for_status()
    return last_response


async def fetch_feed(client: httpx.AsyncClient, feed_cfg: Dict[str, Any]) -> List[NewsItem]:
    """抓單個 RSS feed，回傳本次新發現的 NewsItem 清單（raw_html / clean_markdown 留空，
    交給 cleaner 下一階段填。）"""
    name = feed_cfg["name"]
    url = feed_cfg["url"]
    print(f"[Module 1] 抓取 Feed → {name}")

    try:
        r = await _get_feed_with_retry(client, url)
    except Exception as e:
        print(f"[Module 1]  ↳ ⚠️ 失敗：{e}")
        return []

    parsed = feedparser.parse(r.text)
    items: List[NewsItem] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    source_type = feed_cfg.get("source_type", "article")
    prefilled = 0
    skipped_no_link = 0
    skipped_no_title = 0

    try:
        entries = _limited_entries(parsed.entries, feed_cfg.get("max_entries"))
    except ValueError as exc:
        print(f"[Module 1]  ↳ ⚠️ 設定錯誤：{exc}")
        return []

    for entry in entries:
        # Phase 8.10-d：art19 的 podcast RSS 有些 entry 的 <link> 標籤缺失，
        # feedparser 解到的 entry.link 是空字串。podcast 另有 enclosure (MP3 URL)
        # 與 id (art19 GUID)，fallback 到這些 URL 仍是可穩定 hash 的 identifier；
        # id generation 用這個 URL 也不會跟其他 feed 撞 hash。
        link = entry.get("link")
        if not link:
            enclosures = entry.get("enclosures") or []
            if enclosures and isinstance(enclosures, list):
                link = enclosures[0].get("href") or enclosures[0].get("url")
        if not link:
            link = entry.get("id") or entry.get("guid")
        if link:
            link = _resolve_entry_link(url, link)
        title = entry.get("title")
        if not link:
            skipped_no_link += 1
            continue
        if not title:
            skipped_no_title += 1
            continue

        raw_sum = entry.get("summary", "")
        clean_md: Optional[str] = None

        # Phase 8.16：RSS enclosure 裡常常直接帶媒體檔 URL（podcast MP3、
        # 原生影片 feed 的 MP4）。比 HTML og:video 更穩 —— 文章頁可能被
        # Cloudflare 擋，但 RSS enclosure 已在 feed 文件裡。
        # 用保守判定：只收 MIME type 以 "video/" 或 "audio/" 開頭、或路徑看起來像
        # 直鏈媒體檔的 URL。
        enclosure_video_url: Optional[str] = None
        enclosure_is_direct: bool = False
        for enc in entry.get("enclosures", []) or []:
            enc_url = enc.get("href") or enc.get("url")
            if not enc_url:
                continue
            enc_type = (enc.get("type") or "").lower()
            is_media = enc_type.startswith("video/") or enc_type.startswith("audio/")
            if not is_media:
                # 退路：以副檔名粗判（許多 feed 的 enclosure 沒填 type）
                path = enc_url.split("?", 1)[0].lower()
                if not path.endswith((".mp4", ".m4v", ".mov", ".webm", ".mp3", ".m4a")):
                    continue
            enclosure_video_url = enc_url
            enclosure_is_direct = enc_url.split("?", 1)[0].lower().endswith(
                (".mp4", ".m4v", ".mov", ".webm")
            )
            break

        # 預先填 clean_markdown 的幾條快速道路（跳過 fetch_html）：
        # 1) YouTube：description 就是我們要的，HTML 頁全是 JS render
        # 2) source_type == social（Reddit / X / 未來任何社群）：
        #    RSS summary 通常就是 post body HTML；`_reddit_rss_to_markdown`
        #    名字雖有 reddit，實際是通用「HTML summary → 純文字」工具。
        #    回 None 代表 summary 太單薄（link-only post），下游走 fetch_html 重試。
        # 3) source_type == rss_summary（Phase 8.15b）：
        #    專給「RSS feed 活、但文章頁被 Cloudflare 擋」的源用（例：The Block）。
        #    強制從 RSS <description> 預填；即使被底層 helper 判太短回 None，
        #    仍用 title + 原始 raw_sum 文字兜底，不走 fetch_html（因為必 403）。
        if "youtube.com" in link:
            clean_md = f"YouTube Interview Description:\n{raw_sum}"
            prefilled += 1
        elif source_type == "social":
            md = _reddit_rss_to_markdown(raw_sum)
            if md:
                # 前綴只是人眼 debug 用；composer 真正依賴的是 `source_type` 欄位
                if "reddit.com" in link:
                    clean_md = f"Reddit Post:\n{md}"
                elif "twitter.com" in link or "x.com" in link:
                    clean_md = f"X Post:\n{md}"
                else:
                    clean_md = f"Social Post:\n{md}"
                prefilled += 1
        elif source_type == "rss_summary":
            # 先試標準 HTML→文字；若太短退回原始 summary 的粗暴 strip，
            # 最後兜底帶上 title 以確保至少有 title 層級的訊號。
            md = _reddit_rss_to_markdown(raw_sum)
            if not md and raw_sum:
                try:
                    md = BeautifulSoup(raw_sum, "html.parser").get_text(" ", strip=True)
                except Exception:
                    md = raw_sum
            if md:
                clean_md = f"Article Summary (RSS only):\n{title}\n\n{md}"
            else:
                # 最壞情況：RSS 連 summary 都沒有，只留 title。下游 min_word_count
                # 門檻（rss_summary: 60）多半會擋下，但仍讓它進 DB 做 audit trail。
                clean_md = f"Article Summary (RSS only):\n{title}"
            prefilled += 1

        items.append(
            NewsItem(
                id=make_news_id(link),
                feed_name=name,
                feed_tier=feed_cfg.get("tier", "secondary"),
                source_type=source_type,
                url=link,
                title=title,
                published_at=_parse_rss_time(entry),
                fetched_at=now_iso,
                language=feed_cfg.get("language"),
                clean_markdown=clean_md,
                og_video_url=enclosure_video_url,
                og_video_is_direct=enclosure_is_direct,
                tags=feed_cfg.get("tags", []),
                status="fetched",
            )
        )

    # Phase 8.10-d：同時露 raw 與 kept count。harvest 實跑若看到
    # "raw=77 kept=0 skip_no_link=77" 就是 link fallback 沒救到；
    # "raw=0" 就是 feedparser 完全解不到（content-negotiation 或 body truncation）。
    raw = len(parsed.entries)
    limited_note = (
        f" limited={len(entries)}" if len(entries) != raw else ""
    )
    skip_note = ""
    if skipped_no_link or skipped_no_title:
        skip_note = f"  [skipped: no_link={skipped_no_link}, no_title={skipped_no_title}]"
    prefilled_note = f"（其中 {prefilled} 篇已從 RSS 預填內容）" if prefilled else ""
    print(
        f"[Module 1]  ↳ RSS entry 數 raw={raw}{limited_note} "
        f"kept={len(items)}{prefilled_note}{skip_note}"
    )
    return items


async def fetch_html(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """抓單篇文章的 raw HTML。超時或失敗回 None。

    會先套用 `_rewrite_url_for_extraction`（例如 www.reddit.com → old.reddit.com）
    讓 trafilatura 那一層有機會拿到真正的正文。
    """
    target = _rewrite_url_for_extraction(url)
    if target != url:
        print(f"[Module 1]  ↳ URL 改寫：{url[:50]}... → {target[:60]}...")
    try:
        r = await client.get(target, timeout=20, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Accept": "text/html,application/xhtml+xml",
        })
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[Module 1]  ↳ ⚠️ HTML 抓取失敗 {target[:60]}... : {e}")
        return None


def is_too_old(published_at_iso: str, max_age_hours: int) -> bool:
    """判斷文章是否過舊。容錯：parse 失敗時回 False (不要因時區錯判而 Drop)"""
    try:
        dt = datetime.fromisoformat(published_at_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - dt
        return age > timedelta(hours=max_age_hours)
    except Exception:
        return False


async def harvest_all_feeds(cfg: Dict[str, Any]) -> List[NewsItem]:
    """並行抓取所有 feeds。"""
    async with httpx.AsyncClient() as client:
        tasks = [fetch_feed(client, f) for f in cfg["feeds"]]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    all_items: List[NewsItem] = []
    for batch in results:
        all_items.extend(batch)

    # 時間過濾
    max_age = cfg["filters"]["max_age_hours"]
    before = len(all_items)
    all_items = [x for x in all_items if not is_too_old(x.published_at, max_age)]
    print(f"[Module 1] 時間過濾：{before} → {len(all_items)} (> {max_age}h 的已 drop)")

    return all_items
