"""讀者數據迴圈（2026-11-20）：選題與標題改用數據判斷，不靠模型的感覺。

主編問「是不是題材不夠爆」。查了研究、也拿我們 116 篇的數據對照後的結論：
- 只叫模型判斷「哪個標題比較好」，準度接近丟銅板（LOLA, Marketing Science 2025：GPT-4 只有 57–65%）；
  要拿真實讀者數據校準，再用小規模實驗修正。
- 選題原本只看節目長度、新鮮度和模型評分，沒有「台灣讀者有沒有感」這一項。

這個模組提供三樣東西，都不會因為抓不到資料就擋住寫稿（拿不到就回空的，呼叫端照舊）：
1. taiwan_buzz()：台灣這兩天在討論什麼（Google 搜尋趨勢台灣、PTT 股票／科技工作／房屋板、新聞庫裡的台灣媒體標題）。
2. calibration_examples()：我們讀者真的多打開／少打開的文章（scripts/substack_stats_report.py 每週算好）。
3. title_arm() / title_style()：標題實驗。主編選「張力要多少交給實驗數據決定」，
   所以每篇在「有張力」與「中性」兩組之間用湯普森抽樣分配，每週報表把結果寫回 title_arms.json。
"""
from __future__ import annotations

import hashlib
import html
import json
import random
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATS_DIR = REPO / "data" / "substack_stats"
BUZZ_DIR = STATS_DIR / "tw_buzz"
CALIBRATION_PATH = STATS_DIR / "reader_calibration.json"
TITLE_ARMS_PATH = STATS_DIR / "title_arms.json"

TRENDS_RSS = "https://trends.google.com/trending/rss?geo=TW"
PTT_BOARDS = {"Stock": "PTT股票板", "Tech_Job": "PTT科技工作板", "home-sale": "PTT房屋板"}
TW_MEDIA_FEEDS = ("TechNews 科技新報", "中央社 財經", "中央社 國際", "公視新聞 PTS")
_UA = {"User-Agent": "Mozilla/5.0 (compatible; reader-signals/1.0)"}

# 標題實驗的分組。「張力」以 116 篇回歸裡那組負面字為底（那組比同期多 4.5% 讀者，90% 區間 +1.5%～+7.7%），
# 再補幾個常見的張力字。不收單字「斷」：會把「判斷」也算進去。
_TENSION = re.compile(r"危險|死|失敗|虧|崩|陷阱|詛咒|焦慮|出局|錯|蒸發|跌|恐|怕|殺|騙|韭菜|毀滅|垃圾|討厭|殘酷|"
                      r"豪賭|焚|危機|卡住|卡在|卡關|欠|輸|窮|賠|裁員|代價|翻車|踩雷|斷頭|斷鏈|中斷|困|逼|搶|吃掉|消失|不敢|來不及")
# 明顯的標題黨：不管實驗結果都擋（CHI 2021：標題黨並不會可靠地讓人更好奇，還傷信任）。
_CLICKBAIT = re.compile(r"震驚|驚呆|必看|99\s*%\s*的人|絕對不知道|竟然|瘋傳|崩潰了|太狂|嚇死|保證|史上最|"
                        r"一定要看|看完.{0,4}哭|不看會後悔|秒懂|懶人包")
# 標題用字的複雜度（Shulman, Markowitz & Rogers 2024, Science Advances：華盛頓郵報與 Upworthy 三萬多組標題實驗，
# 用常見字、短句、少抽象詞的標題點擊較多；記者自己看不出這個差別）。我們 116 篇裡簡單標題比複雜標題多 4.5% 讀者，
# 但多半跟冒號標題重疊、分不開，所以當成「同組裡挑最簡單的」，不當硬門檻。
_ABSTRACT = re.compile(r"悖論|理性|範式|典範|本質|維度|賦能|生態系|敘事|結構性|機制|邏輯|解碼|重構|顯化|主權|熵|"
                       r"神聖|殉道|套利|流動性|幻象|飛輪|底牌|算盤|焚化爐|置換|合法性|不對稱|能動性|心性|解析度|"
                       r"槓鈴|中間層|後稀缺|異化|深度|框架|架構|系統性|典型|範疇|悖")
ARMS = ("tension", "neutral")
ARM_LABEL = {"tension": "有張力", "neutral": "中性"}


# ---------------------------------------------------------------------------
# 台灣這兩天在討論什麼
# ---------------------------------------------------------------------------

def parse_trends_rss(xml: str, limit: int = 20) -> list[str]:
    """Google 搜尋趨勢 RSS：每個趨勢的關鍵字，後面接第一則相關新聞標題（關鍵字本身常常只是人名）。"""
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml or "", re.S)[:limit]:
        kw = re.search(r"<title>(.*?)</title>", item, re.S)
        news = re.search(r"<ht:news_item_title>(.*?)</ht:news_item_title>", item, re.S)
        if not kw:
            continue
        text = html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", kw.group(1))).strip()
        if news:
            text += "：" + html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", news.group(1))).strip()
        out.append(text[:60])
    return out


def parse_ptt_index(page: str, limit: int = 20) -> list[str]:
    """PTT 看板列表頁的文章標題；公告、置底與回文不算。"""
    titles = re.findall(r'<div class="title">\s*<a href="[^"]+">(.*?)</a>', page or "", re.S)
    out = []
    for t in titles:
        t = html.unescape(t).strip()
        if not t or t.startswith(("[公告]", "Re:", "Fw:", "[協尋]", "[賣", "[買", "[租")) or "置底" in t:
            continue
        out.append(t[:60])
    return out[-limit:]


def _fetch(url: str, *, cookies: dict | None = None) -> str:
    import httpx

    r = httpx.get(url, headers=_UA, cookies=cookies or {}, timeout=15, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _tw_media_titles(hours: int = 48, limit: int = 25) -> list[str]:
    try:
        from substack_radar.compose import NEWS_DB_PATH

        conn = sqlite3.connect(str(NEWS_DB_PATH))
        try:
            marks = ",".join("?" * len(TW_MEDIA_FEEDS))
            rows = conn.execute(
                f"SELECT title, feed_name FROM news_items WHERE feed_name IN ({marks}) "
                f"AND published_at >= datetime('now','-{int(hours)} hours') ORDER BY published_at DESC LIMIT ?",
                (*TW_MEDIA_FEEDS, limit)).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[ReaderSignals] ⚠️ 讀不到新聞庫：{str(exc)[:80]}")
        return []
    return [f"{feed.split()[0]}｜{title[:50]}" for title, feed in rows if title]


def taiwan_buzz(*, today: date | None = None, fetch=None, refresh: bool = False) -> list[str]:
    """台灣這兩天的熱門話題，一天抓一次、存檔重用。抓不到的來源就略過，全失敗回空清單。"""
    today = today or date.today()
    cache = BUZZ_DIR / f"{today.isoformat()}.json"
    if cache.exists() and not refresh:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))["items"]
        except Exception:
            pass
    fetch = fetch or _fetch
    items: list[str] = []
    try:
        items += [f"Google搜尋趨勢｜{t}" for t in parse_trends_rss(fetch(TRENDS_RSS))]
    except Exception as exc:
        print(f"[ReaderSignals] ⚠️ Google 搜尋趨勢抓不到：{str(exc)[:80]}")
    for board, label in PTT_BOARDS.items():
        try:
            page = fetch(f"https://www.ptt.cc/bbs/{board}/index.html", cookies={"over18": "1"})
            items += [f"{label}｜{t}" for t in parse_ptt_index(page, limit=15)]
        except Exception as exc:
            print(f"[ReaderSignals] ⚠️ {label}抓不到：{str(exc)[:80]}")
    items += _tw_media_titles()
    if items:
        try:
            BUZZ_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"fetched_at": datetime.now().isoformat(timespec="seconds"),
                                         "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
    return items


# ---------------------------------------------------------------------------
# 我們讀者的口味（每週報表算好的去趨勢成績）
# ---------------------------------------------------------------------------

def calibration_examples(n: int = 6, path: Path | None = None) -> tuple[list, list]:
    """回 (多打開的, 少打開的)，每筆 (標題, 比同期多幾成)。檔案不在就回兩個空清單。"""
    try:
        data = json.loads((path or CALIBRATION_PATH).read_text(encoding="utf-8"))
    except Exception:
        return [], []
    pick = lambda xs: [(str(x["title"]), float(x["res"])) for x in xs[:n] if x.get("title")]
    return pick(data.get("top") or []), pick(data.get("bottom") or [])


def calibration_block(n: int = 6) -> str:
    top, bottom = calibration_examples(n)
    if not top or not bottom:
        return ""
    fmt = lambda xs: "\n".join(f"- {t}（{r:+.0%}）" for t, r in xs)
    return ("【我們讀者的真實口味】下面是過去文章「比同期多／少幾成讀者打開」的實際成績（已扣掉訂閱人數變化）。\n"
            f"多打開的：\n{fmt(top)}\n少打開的：\n{fmt(bottom)}\n"
            "評分時參考這些讀者實際的選擇，不是一般人的口味。")


# ---------------------------------------------------------------------------
# 標題實驗
# ---------------------------------------------------------------------------

def title_style(title: str) -> str:
    return "tension" if _TENSION.search(title or "") else "neutral"


def title_complexity(title: str) -> float:
    """越大越難讀：抽象詞、英文字（AI 除外）、引號、超過 13 個字。"""
    t = title or ""
    english = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9.+-]*", t) if w != "AI"]
    return (len(_ABSTRACT.findall(t)) + 0.5 * len(english) + (0.5 if re.search(r"[「」『』]", t) else 0)
            + (0.5 if len(re.sub(r"\s", "", t)) > 13 else 0))


def is_clickbait(title: str) -> bool:
    return bool(_CLICKBAIT.search(title or ""))


def title_arm(seed: str = "", *, path: Path | None = None, prior_sd: float = 0.08, noise_sd: float = 0.12) -> str:
    """湯普森抽樣：每組的「比同期多幾成讀者」當常態分佈，抽一次取大的。
    還沒資料時兩組機會一樣；同一篇（同一個 seed）重跑會抽到同一組。"""
    try:
        stats = json.loads((path or TITLE_ARMS_PATH).read_text(encoding="utf-8")).get("arms") or {}
    except Exception:
        stats = {}
    rng = random.Random(int(hashlib.sha256((seed or date.today().isoformat()).encode()).hexdigest()[:12], 16))
    draws = {}
    for arm in ARMS:
        s = stats.get(arm) or {}
        n, mean = int(s.get("n") or 0), float(s.get("mean") or 0.0)
        precision = 1 / prior_sd ** 2 + n / noise_sd ** 2
        post_mean = (n / noise_sd ** 2) * mean / precision
        draws[arm] = rng.gauss(post_mean, precision ** -0.5)
    return max(draws, key=draws.get)
