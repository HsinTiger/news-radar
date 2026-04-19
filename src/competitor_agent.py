"""
News Radar · Competitor Analysis Agent (v2)

修復 Gemini 初版 (v1) 的三大致命傷：
  1) 10s 全域 asyncio.wait_for 把啟動 Chromium + 三個 KOL 一起包進去 → 必定超時被 kill，
     使用者感受是「瀏覽器視窗完全沒開」。v2 改為 per-KOL 45s timeout，launch 本身不設限。
  2) 無 user_data_dir → 每次都是全新 session，FB 每次都要重登。v2 改用
     launch_persistent_context 搭配 data/00_competitors/chrome_profile/，首次以
     `--login` 手動登入一次，之後會自動沿用 session。
  3) 無登入牆偵測 → 抓到登入 HTML 也會靜默寫入 DB。v2 檢查 URL 與頁面特徵後明確回報
     `login_wall` 狀態，並建議使用者跑 --login。

同時修正：
  - asyncio.get_event_loop().time() 改為真實 datetime.now()
  - parse_report 的 strip('123. ') 改為 regex ^\\d+\\.\\s*，避免吞字
  - 啟動前檢查 playwright 套件是否可 import，給清楚錯誤指引
  - CLI 旗標：--login / --manual / --skip-ai
"""
import os
import re
import sys
import json
import asyncio
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

# ---- Playwright import guard ----
try:
    from playwright.async_api import (
        async_playwright,
        TimeoutError as PlaywrightTimeoutError,
    )
except ImportError:
    sys.stderr.write(
        "❌ Playwright 尚未安裝。請執行：\n"
        "   pip install playwright python-dotenv google-generativeai\n"
        "   playwright install chromium\n"
    )
    sys.exit(1)

# ---- Optional AI dependency ----
try:
    import google.generativeai as genai  # type: ignore
except ImportError:
    genai = None  # 允許 --skip-ai 或環境未裝時仍可跑爬蟲

try:
    import anthropic  # type: ignore
except ImportError:
    anthropic = None  # Gemini fallback 用

# ---- Paths ----
WORKSPACE_ROOT = Path(__file__).parent.parent
REPORT_FILE = WORKSPACE_ROOT.parent / "FB粉絲專頁調研報告.md"
COMPETITOR_DIR = WORKSPACE_ROOT / "data" / "00_competitors"
COMPETITOR_DB = COMPETITOR_DIR / "competitor_db.json"
TACTICS_MD = COMPETITOR_DIR / "kol_tactics.md"
# ⚠️ 重要：profile 絕對不能放在 OneDrive / iCloud / Dropbox 同步目錄下。
# 雲端同步會搶 Chromium 的 SQLite 檔案鎖（Cookies、Preferences 等），
# 造成 `launch_persistent_context` 靜默卡死、瀏覽器視窗不會出現。
# 預設改放到 ~/.cache/news_radar/chrome_profile，可用 --profile-dir 覆寫。
DEFAULT_USER_DATA_DIR = Path.home() / ".cache" / "news_radar" / "chrome_profile"
USER_DATA_DIR: Path = DEFAULT_USER_DATA_DIR  # 由 main() 依 CLI 覆寫

KOL_MAPPING = {
    # display_name (必須完全等於 FB粉絲專頁調研報告.md 內的 ## 標題文字) → FB 粉專 handle
    "IEO國際經濟觀察 (IEObserve)": "intleconobserve",
    "Fox Hsiao (狐說八道)": "hinet",
    "游庭皓的財經皓角 (Yu Ting-hao)": "yutinghaosfinance",
}

# ---- Timing knobs (seconds) ----
PER_KOL_TIMEOUT = 360         # 每位 KOL 上限 6 分鐘（深度抓取 + 展開 + 抓圖需要時間）
PER_ARTICLE_TIMEOUT = 20      # 單篇貼文展開+擷取上限（避免某一篇吃光整個 KOL 預算）
GOTO_TIMEOUT_MS = 30_000
POST_LOAD_WAIT_MS = 6_000     # 等待 FB 前端 hydrate
SCROLL_TIMES = 14             # 深度滾動收集更多貼文
SCROLL_PIXEL = 1400
POSTS_PER_KOL = 15            # 每位 KOL 抓幾篇（對應 10–20 的目標）
LOGIN_WINDOW_SECONDS = 240    # --login 時給使用者 4 分鐘手動登入（偵測到登入成功會提前結束）
BROWSER_LAUNCH_TIMEOUT = 60   # Chromium launch 本身的上限（真的壞才會到）
DEBUG_DIR_NAME = "debug"      # empty 時把 HTML / screenshot 存這裡供你檢查


def _is_cloud_synced_path(p: Path) -> bool:
    """偵測常見雲端同步目錄，Chromium 不該放這裡。"""
    markers = (
        "CloudStorage",       # macOS OneDrive / Google Drive / Dropbox via File Provider
        "OneDrive",
        "iCloud",
        "Dropbox",
        "Google Drive",
        "GoogleDrive",
    )
    s = str(p)
    return any(m in s for m in markers)


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# =====================================================================
# 1. Zero-token Markdown parser（從 FB粉絲專頁調研報告.md 抽出三位 KOL 的基準檔）
# =====================================================================
def parse_report() -> Dict[str, Any]:
    if not REPORT_FILE.exists():
        log(f"⚠️ 找不到調研報告: {REPORT_FILE}")
        return {}
    content = REPORT_FILE.read_text(encoding="utf-8")
    db: Dict[str, Any] = {}
    # 以 '\n## ' 切段，逐段比對 KOL_MAPPING
    for sect in re.split(r"\n## ", content):
        first_line = sect.split("\n", 1)[0].strip()
        # 去掉 "1. " / "2. " 這種編號前綴
        name = re.sub(r"^\d+\.\s*", "", first_line).strip()
        if name not in KOL_MAPPING:
            continue
        kol_id = KOL_MAPPING[name]
        # 抓表格：| **維度** | 結果 |
        table_matches = re.findall(r"\|\s*\*\*(.*?)\*\*\s*\|\s*(.*?)\s*\|", sect)
        traits = {k.strip(): v.strip() for k, v in table_matches}
        db[kol_id] = {
            "display_name": name,
            "historical_traits": traits,
            "live_posts": [],
            "crawl_status": "pending",
            "crawl_note": "",
        }
    return db


# =====================================================================
# 2. Live Crawler（Playwright，launch_persistent_context + per-KOL timeout）
# =====================================================================
def looks_like_login_wall(url: str, html: str) -> bool:
    """判斷是否被 FB 導向登入牆。"""
    low_url = url.lower()
    if "login" in low_url or "checkpoint" in low_url or "/recover" in low_url:
        return True
    if 'name="email"' in html and 'name="pass"' in html:
        return True
    # FB 的未登入臨時牆通常不含 role="article"
    if "登入 Facebook" in html and 'role="article"' not in html:
        return True
    return False


# 依序嘗試的 post 容器 selector；前者命中就不試後者。
POST_SELECTORS = [
    "div[role='article']",                         # 個人頁常見
    "div[data-pagelet^='FeedUnit']",               # 新版 feed unit
    "div[data-pagelet*='TimelineFeedUnit']",       # 大型粉專 timeline
    "div[data-pagelet*='ProfileTimeline']",        # 舊版 Profile
    "div[data-ad-preview='message']",              # 貼文正文區
]


SEE_MORE_LABELS = ("查看更多", "See more", "顯示更多", "Show more", "繼續閱讀",
                   "See More", "Read more", "展开", "더 보기")


async def _expand_see_more(article, max_passes: int = 2) -> int:
    """展開貼文內的「查看更多」按鈕。單一 DOM 查詢 + 多輪 pass。

    優化：一次只抓「role=button」且文字正好是展開詞的節點，避免 broad selector
    把外層容器也撈進來。限制 2 輪避免在某篇貼文卡太久。
    """
    clicked_total = 0
    # 單一複合 selector，把 CSS filter 交給瀏覽器做，比多次 Python loop 快
    css = ", ".join(
        f"div[role='button']:has-text('{lbl}')" for lbl in SEE_MORE_LABELS
    )
    for _pass in range(max_passes):
        clicked_this_pass = 0
        try:
            buttons = await article.locator(css).all()
        except Exception:
            break
        for btn in buttons[:6]:
            try:
                if not await btn.is_visible():
                    continue
                # 文字正好是展開詞的 inline button，>20 字視為外層容器
                txt = (await btn.inner_text()).strip()
                if len(txt) > 20:
                    continue
                await btn.click(timeout=1200, force=True)
                clicked_this_pass += 1
                clicked_total += 1
                await article.page.wait_for_timeout(250)
            except Exception:
                continue
        if clicked_this_pass == 0:
            break  # 沒東西可點就退出
    return clicked_total


async def _eager_images_in_article(article) -> None:
    """把 article 內所有 lazy img 改 eager，加速後續 src 抓取。
    不做 scroll_into_view_if_needed（那會讓視口反覆跳動、每篇多加 1s）。
    """
    try:
        await article.evaluate(
            "el => el.querySelectorAll('img').forEach("
            "img => { if (img.loading === 'lazy') img.loading = 'eager'; })"
        )
    except Exception:
        pass


async def _extract_images(article) -> List[str]:
    """擷取貼文內的 FB CDN 圖片 URL（略過 FB 靜態圖示）。"""
    try:
        imgs = await article.locator("img").all()
    except Exception:
        return []
    urls: List[str] = []
    for img in imgs[:20]:
        try:
            src = await img.get_attribute("src")
            if not src:
                continue
            # blob / data URL 是未登入狀態或 lazy-load 的假 URL，直接略過
            if src.startswith("blob:") or src.startswith("data:"):
                continue
            # rsrc.php 是 FB 自家靜態資源（圖示）；scontent/fbcdn 才是真正的內容圖
            if "rsrc.php" in src:
                continue
            if "scontent" not in src and "fbcdn" not in src:
                continue
            # 過濾大頭貼/頁面封面常見尺寸（太小的通常是縮圖/頭像）
            # FB img 的 width/height attribute 不可靠，先不硬性過濾尺寸
            if src in urls:
                continue
            urls.append(src)
        except Exception:
            continue
    return urls[:5]  # 每篇最多存 5 張


async def _extract_post_url(article) -> str:
    """試圖拿到貼文 permalink。"""
    selectors = [
        "a[href*='/posts/']",
        "a[href*='/permalink/']",
        "a[href*='/videos/']",
        "a[href*='/photos/']",
    ]
    for sel in selectors:
        try:
            links = await article.locator(sel).all()
        except Exception:
            continue
        for link in links[:5]:
            try:
                href = await link.get_attribute("href")
                if not href:
                    continue
                if href.startswith("/"):
                    href = "https://www.facebook.com" + href
                # 去除追蹤參數後的 & 部分
                href = href.split("?__tn__=")[0]
                return href
            except Exception:
                continue
    return ""


async def _dump_debug(page, kol_id: str, reason: str) -> Optional[Path]:
    """當抓取 status 是 empty / login_wall 時，把 HTML + 截圖存起來供事後檢查。"""
    debug_dir = COMPETITOR_DIR / DEBUG_DIR_NAME
    debug_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = debug_dir / f"{ts}_{kol_id}_{reason}"
    try:
        html = await page.content()
        (prefix.with_suffix(".html")).write_text(html, encoding="utf-8")
    except Exception:
        pass
    try:
        await page.screenshot(path=str(prefix.with_suffix(".png")), full_page=False)
    except Exception:
        pass
    return prefix


async def scrape_kol(page, kol_id: str) -> Dict[str, Any]:
    url = f"https://www.facebook.com/{kol_id}"
    log(f"→ 抓取 {kol_id}: {url}")
    result: Dict[str, Any] = {
        "url": url,
        "posts": [],
        "status": "unknown",
        "note": "",
        "selector_used": "",
    }

    try:
        await page.goto(url, timeout=GOTO_TIMEOUT_MS, wait_until="domcontentloaded")
    except PlaywrightTimeoutError:
        result["status"] = "goto_timeout"
        return result
    except Exception as e:
        result["status"] = "goto_error"
        result["note"] = str(e)[:200]
        return result

    await page.wait_for_timeout(POST_LOAD_WAIT_MS)

    # 登入牆偵測
    try:
        final_url = page.url
        html_snippet = await page.content()
    except Exception as e:
        result["status"] = "read_fail"
        result["note"] = str(e)[:200]
        return result

    if looks_like_login_wall(final_url, html_snippet):
        result["status"] = "login_wall"
        result["note"] = f"被重導到登入頁 (final_url={final_url})"
        await _dump_debug(page, kol_id, "login_wall")
        return result

    # 關閉可能阻擋的 Cookie 同意對話框（粉專第一次登入常見）
    try:
        for label in ["Allow all cookies", "允許全部 Cookie", "只允許必要的 Cookie", "接受必要的 Cookie"]:
            btn = page.get_by_role("button", name=label)
            if await btn.count() > 0:
                await btn.first.click(timeout=2000)
                await page.wait_for_timeout(800)
                break
    except Exception:
        pass

    # 捲動數次撈更多貼文（大型粉專頁面貼文區塊在相對下方）
    for i in range(SCROLL_TIMES):
        try:
            await page.mouse.wheel(0, SCROLL_PIXEL)
        except Exception:
            break
        await page.wait_for_timeout(1200)

    # 依序嘗試多組 selector
    import hashlib

    posts: List[Dict[str, Any]] = []
    selector_used = ""
    for sel in POST_SELECTORS:
        try:
            loc = page.locator(sel)
            count = await loc.count()
        except Exception:
            continue
        if count == 0:
            continue
        log(f"  selector {sel!r} 命中 {count} 個節點")
        articles = await loc.all()
        seen_hashes = set()

        async def _process_one(art):
            """單篇貼文展開 + 擷取，有 PER_ARTICLE_TIMEOUT 保護。回 None 代表略過。

            文字抓取策略（解決預覽層 vs 展開後全文的 DOM 分裂問題）：
              1) 先點開所有「查看更多」
              2) 優先抓 `data-ad-preview='message'` 容器下**所有** `[dir='auto']` 並串接
                 —— 展開後的完整段落會插在這個容器的兄弟節點裡
              3) fallback：若 message 容器抓不到，退回「取所有 [dir='auto'] 最長 + 第二長」
            """
            await _eager_images_in_article(art)
            await _expand_see_more(art, max_passes=3)
            # 等前端把展開的段落插回 DOM
            try:
                await art.page.wait_for_timeout(400)
            except Exception:
                pass

            text = ""
            # 策略 1：data-ad-preview='message' 容器內所有段落串接
            try:
                msg = art.locator("div[data-ad-preview='message']").first
                if await msg.count() > 0:
                    paragraphs = await msg.locator("div[dir='auto']").all_inner_texts()
                    cleaned = [p.strip() for p in paragraphs if p and p.strip()]
                    # 去重連續重複段落（FB 有時會渲染預覽+全文兩份）
                    dedup: List[str] = []
                    for p in cleaned:
                        if dedup and (p in dedup[-1] or dedup[-1] in p):
                            # 取較長的那份
                            if len(p) > len(dedup[-1]):
                                dedup[-1] = p
                            continue
                        dedup.append(p)
                    text = "\n\n".join(dedup).strip()
            except Exception:
                text = ""

            # 策略 2（fallback）：最長 + 第二長 [dir='auto']
            if len(text) < 30:
                try:
                    texts = await art.locator("div[dir='auto']").all_inner_texts()
                except Exception:
                    texts = []
                if texts:
                    t1 = max(texts, key=len)
                    rest = sorted((t for t in texts if t != t1), key=len, reverse=True)
                    text = t1
                    if rest and len(rest[0].strip()) >= 200:
                        text = text + "\n\n" + rest[0]
                else:
                    text = await art.inner_text()
            text = text.strip()
            if len(text) < 30:
                return None

            images = await _extract_images(art)
            post_url = await _extract_post_url(art)
            footer_raw = (await art.inner_text())[:600].replace("\n", " | ")
            return {
                "text": text[:8000],
                "text_length": len(text),
                "images": images,
                "post_url": post_url,
                "footer": footer_raw,
                "captured_at": datetime.now().isoformat(timespec="seconds"),
            }

        # 正式抓取（展開 + 擷取一氣呵成，逐篇有 timeout 保護）
        for art in articles[: POSTS_PER_KOL * 2]:
            try:
                post = await asyncio.wait_for(_process_one(art), timeout=PER_ARTICLE_TIMEOUT)
            except asyncio.TimeoutError:
                log(f"    ⏱️ 單篇 >{PER_ARTICLE_TIMEOUT}s，跳過")
                continue
            except Exception:
                continue
            if post is None:
                continue

            text = post["text"]
            h = hashlib.md5(text[:120].encode("utf-8")).hexdigest()
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            posts.append(post)
            if len(posts) >= POSTS_PER_KOL:
                break

        if posts:
            selector_used = sel
            break  # 命中一組就停

    result["selector_used"] = selector_used
    result["posts"] = posts
    result["status"] = "ok" if posts else "empty"

    if not posts:
        # 存 HTML + screenshot 供 DOM 結構診斷
        dump = await _dump_debug(page, kol_id, "empty")
        if dump:
            result["note"] = f"debug dump → {dump.with_suffix('.html').name}"

    return result


async def _launch_persistent(p, profile_dir: Path):
    """包一層 60s timeout，避免 OneDrive 類場景無聲卡死。"""
    log(f"    [1/3] 啟動 Chromium（最多 {BROWSER_LAUNCH_TIMEOUT}s）...")
    ctx = await asyncio.wait_for(
        p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        ),
        timeout=BROWSER_LAUNCH_TIMEOUT,
    )
    log("    [2/3] Chromium 啟動完成，建立頁面...")
    return ctx


async def run_login_flow() -> None:
    """開一個 Chromium 視窗讓使用者手動登入 FB，session 寫入 USER_DATA_DIR。"""
    if _is_cloud_synced_path(USER_DATA_DIR):
        log(f"❌ Profile 路徑位於雲端同步資料夾，Chromium 會卡死：{USER_DATA_DIR}")
        log("    請改用 --profile-dir 指定一個本機路徑，例如：")
        log("      python src/competitor_agent.py --login --profile-dir ~/.cache/news_radar/chrome_profile")
        return

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    log(f"🔐 開啟 Chromium 供手動登入（倒數 {LOGIN_WINDOW_SECONDS} 秒）...")
    log(f"    Profile 目錄: {USER_DATA_DIR}")
    log("    [0/3] 啟動 Playwright driver（node.js）...")
    async with async_playwright() as p:
        try:
            ctx = await _launch_persistent(p, USER_DATA_DIR)
        except asyncio.TimeoutError:
            log(f"❌ Chromium 啟動逾時（>{BROWSER_LAUNCH_TIMEOUT}s）。常見原因：")
            log("    1) Profile 目錄在雲端同步區（OneDrive/iCloud/Dropbox）")
            log("    2) 沒跑過 playwright install chromium")
            log("    3) macOS 權限被擋住（第一次跑需授權）")
            return
        except Exception as e:
            log(f"❌ 無法啟動 Chromium：{e}")
            log("    請先執行：playwright install chromium")
            return

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        log("    [3/3] 導向 FB 登入頁...")
        try:
            await page.goto("https://www.facebook.com/login", timeout=GOTO_TIMEOUT_MS)
        except Exception as e:
            log(f"⚠️ 開啟 FB 登入頁失敗：{e}")
        log(f"請在視窗中完成登入；最長等待 {LOGIN_WINDOW_SECONDS} 秒。")
        log("    偵測到 URL 離開 /login 或 /checkpoint 會自動繼續。")

        # 每 3 秒 poll 一次 URL；偵測到離開登入頁就提前結束
        waited = 0
        poll_every = 3
        login_detected = False
        while waited < LOGIN_WINDOW_SECONDS:
            try:
                await asyncio.sleep(poll_every)
            except asyncio.CancelledError:
                break
            waited += poll_every
            try:
                current_url = page.url.lower()
            except Exception:
                # page 被使用者關掉 → 當作結束
                log("    （視窗已關閉）")
                break
            if "facebook.com" in current_url and "/login" not in current_url \
                    and "/checkpoint" not in current_url and "/recover" not in current_url:
                # 再多等 2 秒讓 cookies 寫完
                await asyncio.sleep(2)
                login_detected = True
                log(f"✅ 偵測到登入成功（URL={current_url[:80]}），提前結束登入視窗。")
                break
            if waited % 30 == 0:
                log(f"    ⏳ 已等待 {waited}s / {LOGIN_WINDOW_SECONDS}s …")

        try:
            await ctx.close()
        except Exception:
            pass
    if login_detected:
        log("✅ 登入視窗已關閉。Session 已存入 profile，下次自動沿用。")
    else:
        log("⚠️ 倒數結束但沒偵測到明確的登入成功。")
        log("    若你確實有登入，cookies 也已存入 profile，可直接往下跑。")


async def run_scrape(db: Dict[str, Any]) -> Dict[str, Any]:
    if _is_cloud_synced_path(USER_DATA_DIR):
        log(f"❌ Profile 路徑位於雲端同步資料夾，Chromium 會卡死：{USER_DATA_DIR}")
        log("    請改用 --profile-dir 指定本機路徑。")
        return db
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    profile_empty = not any(USER_DATA_DIR.iterdir())
    if profile_empty:
        log("ℹ️ 尚未登入 FB（profile 為空）。建議先跑 `python src/competitor_agent.py --login`。")
        log("    本次會嘗試匿名抓取，大概率會撞到登入牆。")

    log("    [0/3] 啟動 Playwright driver（node.js）...")
    async with async_playwright() as p:
        try:
            ctx = await _launch_persistent(p, USER_DATA_DIR)
        except asyncio.TimeoutError:
            log(f"❌ Chromium 啟動逾時（>{BROWSER_LAUNCH_TIMEOUT}s）。")
            return db
        except Exception as e:
            log(f"❌ 無法啟動 Chromium：{e}")
            log("    檢查項目：")
            log("      1) pip install playwright")
            log("      2) playwright install chromium")
            log("      3) Profile 不要放 OneDrive/iCloud 同步區")
            return db

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        for kol_id in db.keys():
            try:
                result = await asyncio.wait_for(
                    scrape_kol(page, kol_id), timeout=PER_KOL_TIMEOUT
                )
            except asyncio.TimeoutError:
                result = {
                    "url": f"https://facebook.com/{kol_id}",
                    "posts": [],
                    "status": "per_kol_timeout",
                    "note": f">{PER_KOL_TIMEOUT}s",
                }
                log(f"  ⚠️ {kol_id} 超過 {PER_KOL_TIMEOUT}s，跳過此 KOL。")

            db[kol_id]["live_posts"] = result["posts"]
            db[kol_id]["crawl_status"] = result["status"]
            db[kol_id]["crawl_note"] = result["note"]
            log(f"  ← {kol_id}: {result['status']} ({len(result['posts'])} 篇)")

        try:
            await ctx.close()
        except Exception:
            pass
    return db


# =====================================================================
# 3. Manual fallback（當 profile 空、或全部 KOL 抓取失敗）
# =====================================================================
def manual_input_mode(db: Dict[str, Any]) -> Dict[str, Any]:
    log("🔧 手動輸入模式（Ctrl+C 可中途離開）")
    for kol_id, info in db.items():
        print(f"\n[{info['display_name']}]")
        try:
            text = input("貼上該 KOL 近期貼文文字（直接 Enter 跳過）: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if text:
            db[kol_id]["live_posts"] = [
                {
                    "text_preview": text[:500],
                    "footer_snippet": "",
                    "captured_at": datetime.now().isoformat(timespec="seconds"),
                }
            ]
            db[kol_id]["crawl_status"] = "manual"
    return db


# =====================================================================
# 4. AI Tactic Generator（Gemini 優先 → Claude 備援 → 零 API 結構化備援）
# =====================================================================
TACTIC_PROMPT_TEMPLATE = """任務：作為『競品戰略分析官』，請分析以下 KOL 的風格基準與最新動態。

資料來源 (KOL Database，已展開所有「查看更多」的完整全文)：
{db_json}

請針對我們的三個平台寫手產出具體的「心法指導」：
1. FB 產業觀察家：如何像 IEObserve 維持質感與深度？（節奏、選題、圖表邏輯）
2. IG 科技風尚師：如何學習 Fox Hsiao 的邏輯美學？（拆解架構、破題、收束）
3. Threads 辛辣分析嘴：如何掌握 游庭皓的數據爆發力？（金句濃度、數據切入點）

對每位 KOL 都要：
- 具體引用 live_posts 中 1–2 則貼文作為論據（標註「見 captured_at …」）
- 拆出 3 條可執行的戰術指令（非抽象口號）
- 指出我們的寫手目前可能的盲點

要求：全繁體中文、語氣冷靜專業、避免條列式八股、段落之間要有呼吸感。
"""


def _try_gemini(db: Dict[str, Any]) -> Optional[str]:
    """嘗試用 Gemini 產生戰術。失敗回 None。"""
    if genai is None:
        log("ℹ️ 未安裝 google-generativeai，跳過 Gemini。")
        return None
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        log("⚠️ 找不到 GEMINI_API_KEY，跳過 Gemini。")
        return None
    try:
        genai.configure(api_key=key)
        model = genai.GenerativeModel("gemini-flash-latest")
        prompt = TACTIC_PROMPT_TEMPLATE.format(
            db_json=json.dumps(db, indent=2, ensure_ascii=False)[:80000]
        )
        log("🧠 Gemini flash-latest → 提煉競品戰術...")
        resp = model.generate_content(prompt)
        text = getattr(resp, "text", None)
        if text and len(text.strip()) > 200:
            return text
        log("⚠️ Gemini 回覆過短或為空。")
    except Exception as e:
        log(f"⚠️ Gemini 失敗：{str(e)[:200]}")
    return None


def _try_anthropic(db: Dict[str, Any]) -> Optional[str]:
    """Gemini 失敗時用 Claude 接手當大腦。"""
    if anthropic is None:
        log("ℹ️ 未安裝 anthropic SDK，跳過 Claude 備援。")
        return None
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        log("⚠️ 找不到 ANTHROPIC_API_KEY，跳過 Claude 備援。")
        return None
    try:
        client = anthropic.Anthropic(api_key=key)
        prompt = TACTIC_PROMPT_TEMPLATE.format(
            db_json=json.dumps(db, indent=2, ensure_ascii=False)[:120000]
        )
        log("🧠 Claude Sonnet 4.6 → 接手競品戰術分析...")
        # 先試 Sonnet（品質/成本甜蜜點），失敗才上 Opus
        for model_id in ("claude-sonnet-4-6", "claude-opus-4-6"):
            try:
                msg = client.messages.create(
                    model=model_id,
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}],
                )
                parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
                text = "\n".join(parts).strip()
                if len(text) > 200:
                    log(f"✅ Claude {model_id} 產出成功。")
                    return text
            except Exception as e:
                log(f"⚠️ Claude {model_id} 失敗：{str(e)[:200]}")
                continue
    except Exception as e:
        log(f"⚠️ Claude 初始化失敗：{str(e)[:200]}")
    return None


def _deterministic_fallback(db: Dict[str, Any]) -> str:
    """兩個 LLM 都掛時，用手工結構化摘要備援，保證永遠有產出。"""
    lines = [
        "> ⚠️ 本次 LLM 不可用（Gemini + Claude 都失敗），以下為結構化快照，請手動提煉戰術。",
        "",
    ]
    for kol_id, info in db.items():
        lines.append(f"## {info.get('display_name', kol_id)}")
        lines.append(f"- 歷史特徵：{info.get('historical_traits', {})}")
        posts = info.get("live_posts", [])
        lines.append(f"- 近期貼文：{len(posts)} 則；狀態：{info.get('crawl_status')}")
        for i, p in enumerate(posts[:5], 1):
            text = p.get("text") or p.get("text_preview", "")
            lines.append(f"  {i}. [{p.get('captured_at','')}] {text[:180]}…")
        lines.append("")
    return "\n".join(lines)


def generate_tactics(db: Dict[str, Any]) -> None:
    """優先順序：Gemini → Claude (Anthropic) → 結構化備援。保證一定會寫檔。"""
    text = _try_gemini(db) or _try_anthropic(db) or _deterministic_fallback(db)

    TACTICS_MD.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 🎯 競品同步：本週寫手戰術指導\n\n"
        f"> 最後更新: {datetime.now().isoformat(timespec='seconds')}\n"
        f"> KOL 數：{len(db)}；貼文總數：{sum(len(v.get('live_posts',[])) for v in db.values())}\n\n"
    )
    TACTICS_MD.write_text(header + text, encoding="utf-8")
    log(f"✅ 戰術指導已產出: {TACTICS_MD}")


def save_db(db: Dict[str, Any]) -> None:
    COMPETITOR_DB.parent.mkdir(parents=True, exist_ok=True)
    COMPETITOR_DB.write_text(
        json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log(f"💾 DB 已儲存: {COMPETITOR_DB}")


# =====================================================================
# 5. Entrypoint
# =====================================================================
async def run_diagnose() -> None:
    """跑最小化測試：不開 profile，只開一個純淨 Chromium，確認 Playwright 能用。"""
    log("🔬 診斷模式：嘗試啟動純淨 Chromium（不使用 profile）")
    log("    [0/2] 啟動 Playwright driver...")
    try:
        async with async_playwright() as p:
            log(f"    [1/2] 啟動 Chromium（最多 {BROWSER_LAUNCH_TIMEOUT}s）...")
            browser = await asyncio.wait_for(
                p.chromium.launch(headless=False),
                timeout=BROWSER_LAUNCH_TIMEOUT,
            )
            log("    [2/2] Chromium 視窗應該已出現。5 秒後自動關閉。")
            page = await browser.new_page()
            await page.goto("https://example.com", timeout=GOTO_TIMEOUT_MS)
            await asyncio.sleep(5)
            await browser.close()
        log("✅ 診斷通過：Playwright + Chromium 可正常啟動。")
        log("   → 代表卡在 --login 的原因是 profile 目錄（八成是 OneDrive 路徑）")
    except asyncio.TimeoutError:
        log(f"❌ Chromium 啟動逾時（>{BROWSER_LAUNCH_TIMEOUT}s）")
        log("   → Playwright 本身就壞了。請重跑：playwright install chromium")
    except Exception as e:
        log(f"❌ 診斷失敗：{e}")


async def main() -> None:
    global USER_DATA_DIR
    ap = argparse.ArgumentParser(description="News Radar Competitor Analysis Agent v2")
    ap.add_argument(
        "--login",
        action="store_true",
        help="只開瀏覽器供手動登入 FB（首次使用請先跑這個）",
    )
    ap.add_argument(
        "--manual",
        action="store_true",
        help="跳過自動抓取，直接進手動輸入模式",
    )
    ap.add_argument(
        "--skip-ai",
        action="store_true",
        help="跳過 Gemini 戰術產出（純爬蟲）",
    )
    ap.add_argument(
        "--diagnose",
        action="store_true",
        help="最小化測試：只開純淨 Chromium，確認 Playwright 是否正常",
    )
    ap.add_argument(
        "--profile-dir",
        type=str,
        default=str(DEFAULT_USER_DATA_DIR),
        help=f"Chromium user data dir（預設：{DEFAULT_USER_DATA_DIR}）。絕不要指向 OneDrive/iCloud！",
    )
    args = ap.parse_args()

    # 覆寫全域 USER_DATA_DIR
    USER_DATA_DIR = Path(args.profile_dir).expanduser().resolve()

    print("=== 🚀 News Radar Competitor Analysis Agent (v2) ===")
    log(f"Profile dir: {USER_DATA_DIR}")

    if args.diagnose:
        await run_diagnose()
        return

    if args.login:
        await run_login_flow()
        return

    db = parse_report()
    if not db:
        log("❌ 報告解析失敗。請確認 FB粉絲專頁調研報告.md 存在且包含三位 KOL 標題。")
        return
    log(f"✅ 解析到 {len(db)} 位 KOL：{list(db.keys())}")

    if args.manual:
        db = manual_input_mode(db)
    else:
        db = await run_scrape(db)
        # 若所有 KOL 都沒抓到實質內容 → 降級到手動
        bad_states = {
            "login_wall",
            "per_kol_timeout",
            "goto_timeout",
            "goto_error",
            "read_fail",
            "locator_fail",
            "empty",
            "pending",
        }
        if all(info.get("crawl_status") in bad_states for info in db.values()):
            log("⚠️ 所有 KOL 自動抓取皆未成功。")
            if any(info.get("crawl_status") == "login_wall" for info in db.values()):
                log("    提示：偵測到登入牆。請跑 `python src/competitor_agent.py --login`。")
            log("    進入手動補輸入模式（Enter 可略過）。")
            db = manual_input_mode(db)

    save_db(db)
    if not args.skip_ai:
        generate_tactics(db)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 使用者中止。")
