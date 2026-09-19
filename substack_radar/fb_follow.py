"""FB 粉專「主力爸爸我錯了」跟發 Substack 草稿。

2026-09-19 信哥拍板：IG／Threads 帳號沒了不救，專心讓 FB 自動經營回來。粉專
從 8/3 起就沒有新貼文——雲端 full_pipeline 只剩 instagram 一個目標，FB 早就
沒人發。這條線改成跟著 Substack 三個專欄的草稿走：有草稿，就改寫成 FB 原生
貼文（擬人化口吻）配封面圖發出去。

主路徑（2026-09-20 起，信哥：省 token）：Substack 寫手在同一次輸出裡順便寫 FB 版，
compose 建好草稿後呼叫 post_with_draft() 立即發文，不另外跑 agy。

下面這條是舊的獨立流程，留作手動補發（python -m substack_radar.fb_follow --folder …），
它會另外呼叫 agy 寫稿與稽核：
  1. 掃本機草稿資料夾，挑出「夠久、還沒發過、事實稽核有過」的最舊一篇。
  2. 問 Substack 這篇草稿還在不在：被刪掉 → 不發；已公開 → 貼文附文章連結；
     還是草稿 → 貼文自己講完重點，連結導到訂閱首頁。
  3. agy 寫手（最新一代 Gemini）產出貼文＋兩張圖卡的字 → 確定性閘門（含實際
     排版：放不下就退稿）→ agy 稽核（換家族）→ 有問題就帶著工單重寫，最多三輪。
     沒過就不發，記下原因。
  4. 兩張圖卡（substack_radar/fb_cards.py）＋貼文發到 FB，記帳本。

為什麼等 FB_MIN_AGE_H 小時才發：草稿產出後信哥還沒看過。留一段時間讓他把不
要的草稿刪掉，刪掉的就不會出現在 FB。

安全閥：沒設 FB_FOLLOW_LIVE=1 就只產稿不發（等同 --dry-run）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DRAFTS_DIR = REPO / "data" / "substack_drafts"
LEDGER_PATH = DRAFTS_DIR / ".fb_posted.json"
NEWS_DB = REPO / "data" / "01_harvest" / "news_radar.db"

PUB_HOME = "https://hsin73.substack.com"
PAGE_NAME = "主力爸爸我錯了"

# 上線那天之前的草稿不回補：一次倒 25 篇舊稿到 FB 只會洗版。
SINCE = os.getenv("FB_FOLLOW_SINCE", "2026-09-19")
MIN_AGE_H = float(os.getenv("FB_MIN_AGE_H", "6"))
MAX_AGE_D = float(os.getenv("FB_MAX_AGE_D", "7"))
MAX_ATTEMPTS = int(os.getenv("FB_MAX_ATTEMPTS", "2"))
MAX_ROUNDS = int(os.getenv("FB_AUDIT_ROUNDS", "3"))
AGY_TIMEOUT_S = int(os.getenv("FB_AGY_TIMEOUT_S", "420"))

COLUMN_DESC = {
    "賺錢有道": "每週日一篇公司拆解，看一家公司怎麼賺錢",
    "吹牛免稅": "每天一篇，從一集長篇 podcast 延伸出來的思考",
    "主編精選": "主編親自挑的題目",
}

# 閘門「沒過」的標記。其他 audit_warnings（標題過長、段落過長…）是文風，
# 不影響事實，照發；這個代表證據／數字違規沒修掉，不該再擴散到 FB。
FACT_FAIL_MARK = "[品質迴圈未通過]"


# ---------------------------------------------------------------------------
# 候選草稿
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    folder: Path
    meta: dict
    created_at: datetime
    column: str
    headline: str
    key: str
    draft_id: str | None = None
    skip_reason: str | None = None

    @property
    def rel(self) -> str:
        return str(self.folder.relative_to(DRAFTS_DIR))


def _load_ledger() -> dict:
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_ledger(ledger: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LEDGER_PATH)


def split_column(title: str) -> tuple[str, str]:
    """「越拚命，為何越失控｜吹牛免稅」→（吹牛免稅, 越拚命，為何越失控）。"""
    text = (title or "").strip()
    for label in COLUMN_DESC:
        if text.endswith(f"｜{label}"):
            return label, text[: -len(label) - 1].strip()
        if text.startswith(f"{label}｜"):
            return label, text[len(label) + 1:].strip()
    return "", text


def _dedupe_key(meta: dict, headline: str) -> str:
    """同一個題材重跑會留下新舊兩版草稿，FB 只能發一版。"""
    source = meta.get("source") or {}
    if source.get("ticker"):
        return f"company:{source['ticker']}"
    if source.get("id"):
        return f"source:{source['id']}"
    return f"title:{headline}"


def _draft_id_from_db(meta: dict) -> str | None:
    source_id = (meta.get("source") or {}).get("id")
    if not source_id or not NEWS_DB.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT substack_draft_id FROM news_items WHERE id=?", (source_id,)
            ).fetchone()
        finally:
            conn.close()
        return str(row[0]) if row and row[0] else None
    except Exception:
        return None


def scan(now: datetime | None = None, ledger: dict | None = None) -> list[Candidate]:
    """回傳可發的候選（最舊的在前）。被排除的不回傳，原因印在 log。"""
    now = now or datetime.now()
    ledger = _load_ledger() if ledger is None else ledger
    posted_keys = {v.get("key") for v in ledger.values() if v.get("status") == "posted"}

    found: list[Candidate] = []
    for day_dir in sorted(DRAFTS_DIR.glob("20??-??-??")):
        if day_dir.name < SINCE:
            continue
        for folder in sorted(p for p in day_dir.iterdir() if p.is_dir()):
            meta_path = folder / "metadata.json"
            if not meta_path.exists() or not (folder / "Article_Full.md").exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(meta.get("created_at", ""))
            except Exception:
                continue
            column, headline = split_column(meta.get("title", ""))
            found.append(Candidate(
                folder=folder, meta=meta, created_at=created, column=column,
                headline=headline, key=_dedupe_key(meta, headline),
                draft_id=str(meta.get("substack_draft_id") or "") or None,
            ))

    newest_by_key: dict[str, Candidate] = {}
    for cand in found:
        if cand.key not in newest_by_key or cand.created_at > newest_by_key[cand.key].created_at:
            newest_by_key[cand.key] = cand

    eligible = []
    for cand in found:
        entry = ledger.get(cand.rel) or {}
        age = now - cand.created_at
        if entry.get("status") == "posted":
            continue
        if entry.get("attempts", 0) >= MAX_ATTEMPTS:
            reason = "重試次數用完"
        elif not cand.column:
            reason = "不是三個專欄之一"
        elif newest_by_key[cand.key] is not cand:
            reason = "同題材有較新的版本"
        elif cand.key in posted_keys:
            reason = "同題材已經發過"
        elif age < timedelta(hours=MIN_AGE_H):
            reason = f"草稿才 {age.total_seconds() / 3600:.1f} 小時，留時間給主編刪稿"
        elif age > timedelta(days=MAX_AGE_D):
            reason = "超過 7 天，不回補"
        elif any(str(w).startswith(FACT_FAIL_MARK) for w in cand.meta.get("audit_warnings") or []):
            reason = "Substack 端的事實稽核沒過"
        else:
            reason = None
        if reason:
            print(f"[FBFollow] ⏭️ {cand.rel}：{reason}")
            continue
        cand.draft_id = cand.draft_id or _draft_id_from_db(cand.meta)
        eligible.append(cand)
    eligible.sort(key=lambda c: c.created_at)
    return eligible


# ---------------------------------------------------------------------------
# Substack 端狀態
# ---------------------------------------------------------------------------

def remote_status(cand: Candidate, api=None) -> tuple[str, str | None]:
    """('deleted'|'draft'|'published'|'unknown', 公開網址)。

    查不到對應的草稿編號就回 unknown——寧可不發，也不要把主編刪掉的稿子發出去。"""
    if not cand.draft_id:
        return "unknown", None
    if api is None:
        from substack import Api  # type: ignore
        api = Api(
            cookies_string=os.environ["SUBSTACK_COOKIES_STRING"],
            publication_url=os.environ["SUBSTACK_PUBLICATION_URL"],
        )
    try:
        draft = api.get_draft(int(cand.draft_id))
    except Exception as exc:
        if "404" in str(exc):
            return "deleted", None
        raise
    if draft.get("is_published") and draft.get("slug"):
        return "published", f"{PUB_HOME}/p/{draft['slug']}"
    return "draft", None


# ---------------------------------------------------------------------------
# 原始來源（信哥 2026-09-19：大方說出處，具體的原始節目反而讓讀者更相信）
# ---------------------------------------------------------------------------

@dataclass
class SourceInfo:
    kind: str = ""            # youtube / web / company / ""
    show: str = ""            # 節目（頻道）名稱；網頁則是網域
    url: str = ""
    episode: str = ""
    duration_s: int = 0
    references: list[str] = field(default_factory=list)

    @property
    def duration_text(self) -> str:
        if self.duration_s <= 0:
            return ""
        h, m = divmod(round(self.duration_s / 60), 60)
        return f"{h} 小時 {m} 分" if h else f"{m} 分鐘"

    def fact_strings(self) -> tuple[str, ...]:
        """來源本身的事實（節目長度、集數標題），貼文可以引用。"""
        return tuple(x for x in (self.duration_text, self.episode) if x)

    def card_options(self) -> tuple[str, ...]:
        if self.kind != "youtube" or not self.show:
            return ()
        full = f"原始節目｜{self.show}・{self.duration_text}" if self.duration_text else ""
        return tuple(x for x in (full, f"原始節目｜{self.show}") if x)

    def prompt_block(self, cand: "Candidate") -> str:
        lines = []
        if self.kind == "youtube":
            lines.append(f"原始節目：{self.show}（YouTube）")
            if self.episode:
                lines.append(f"這集標題：{self.episode}")
            if self.duration_text:
                lines.append(f"節目長度：{self.duration_text}")
        elif self.kind == "web":
            lines.append(f"原始文章：{self.episode or self.url}（{self.show}）")
        elif self.kind == "company":
            src = cand.meta.get("source") or {}
            lines.append(f"這是我們自己的公司拆解：{src.get('title', '')}（{src.get('ticker', '')}）")
        if self.references:
            lines.append("文章引用的資料出處：" + "、".join(self.references))
        lines.append("（連結由系統附在文末，貼文裡不要寫網址）")
        return "\n".join(lines)


def _youtube_meta(url: str) -> tuple[str, int, str]:
    import subprocess

    ytdlp = REPO / ".venv" / "bin" / "yt-dlp"
    try:
        out = subprocess.run(
            [str(ytdlp), "--no-warnings", "--skip-download",
             "--print", "%(channel)s\t%(duration)s\t%(title)s", url],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip().splitlines()
        channel, duration, title = (out[-1].split("\t") + ["", "", ""])[:3]
        return channel.strip(), int(float(duration)) if duration.replace(".", "").isdigit() else 0, title.strip()
    except Exception:
        return "", 0, ""


def source_info(cand: Candidate) -> SourceInfo:
    return source_info_for(cand.meta.get("source") or {})


def source_info_for(src: dict) -> SourceInfo:
    refs = []
    for r in src.get("research_sources") or []:
        name = (r.get("publisher") or "").strip()
        if name and name not in refs:
            refs.append(name)
    info = SourceInfo(references=refs[:4])
    if src.get("ticker"):
        info.kind = "company"
        return info
    url = ""
    if src.get("id") and NEWS_DB.exists():
        try:
            conn = sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True)
            row = conn.execute("SELECT url, title FROM news_items WHERE id=?", (src["id"],)).fetchone()
            conn.close()
            if row:
                url, info.episode = row[0] or "", row[1] or ""
        except Exception:
            pass
    if re.match(r"https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", url):
        info.kind, info.url = "youtube", url
        channel, duration, title = _youtube_meta(url)
        info.show, info.duration_s = channel, duration
        info.episode = title or info.episode
    elif url.startswith("http"):
        info.kind, info.url = "web", url
        info.show = re.sub(r"^www\.", "", url.split("/")[2])
    return info


# ---------------------------------------------------------------------------
# 寫手與稽核
# ---------------------------------------------------------------------------

FB_RULES = """【粉專定位】
幫台灣讀者過濾國外的雜訊：把國外的長篇深度對談、商業分析，整理成幾分鐘看得完的重點。
我們不假裝原創。大方說出原始節目、來賓、出處，清楚表明這是我們整理、翻譯、再加上評論的二手資訊——具體的原始出處，反而讓讀者更相信。

【口吻：國外深度資訊的谷阿莫】
- 像谷阿莫講電影：節奏快、口語、台灣用語、短句，帶點吐槽和幽默，把好幾個小時的內容壓成幾分鐘的重點。
- 但重點一定要講對、講清楚：好笑是包裝，洞察才是內容。
- 分清楚「節目裡／原始資料怎麼說」和「我們怎麼看」，不要把自己的評論說成是來賓講的。
- 自稱「我」或「小編」都可以。不油、不說教、不喊單、不給任何買賣建議。

【鐵則】
1. 只能用文章裡有的事實、數字、人名、公司。不得新增任何文章裡沒有的數字或事件。
2. 可以有感受和看法，但不能捏造具體的個人經歷：不能說自己買了、賣了、持有、賺了、賠了什麼，也不能說見過誰、去過哪。
3. 【原始來源】有給節目或出處名稱的話，貼文裡一定要點名（例如「My First Million 這集 1 小時 26 分的訪談」）。
4. 數字一律用阿拉伯數字（寫「38%」「1,200 億」，不寫「三成八」「一千兩百億」）。
5. 純文字：不用 markdown（不要 **、#、項目符號），不寫任何網址，不加 hashtag——系統會自己補。
6. emoji 最多 2 個。

【結構】
- 第一行是鉤子，25 字以內，讓人想按「查看更多」。有節目長度時，可以用「X 分鐘看完 Y 的 Z 小時訪談」這類谷阿莫式開場，但不要每篇都一樣。
- 接著 3 到 5 個短段落，講清楚文章裡「一個」最有意思的洞察，不要把整篇摘要一遍。
- 最後一句丟一個具體的問題，邀請大家留言。
- 全文 250 到 500 字。

這篇是我們自己「{column}」專欄的文章（{column_desc}）。要提專欄就用「我們的{column}」這種說法，不要寫成你去讀了別人的專欄；不提也可以。

【兩張圖卡】貼文會配兩張橫式圖卡，讀者滑過動態牆時一眼就要看懂，所以卡上只放極少的字：
- HOOK：第一張的大字鉤子。16 字以內，分成 1 到 2 行，用「／」標出換行位置，每行 8 字以內；換行要落在語意斷點（例如「台積電／還能不能買？」），行首不能是標點。
- POINT：第二張的一句重點。30 字以內，分成 1 到 3 行，用「／」換行，每行 16 字以內；是文章最關鍵的一個洞察或反差，不要跟 HOOK 重複。
- FIGURE：如果重點有一個關鍵數字，放在這裡當大字（例如「58%」「1,200 億」），10 字以內，POINT 裡就不要再寫一次這個數字；沒有就留空。必須是文章裡原本就有的數字。"""

WRITER_PROMPT = """你是 Facebook 粉絲專頁「{page}」的小編，要把一篇電子報文章改寫成一則 FB 貼文。

{rules}

【原始來源】
{source_block}

{feedback}
【文章標題】{title}
【副標】{subtitle}
【文章全文】
{article}

只照這個格式輸出，不要其他文字：
<<<HOOK>>>
（鉤子）
<<<POINT>>>
（重點句）
<<<FIGURE>>>
（數字，沒有就空白）
<<<POST>>>
（貼文本文）
<<<END>>>"""

AUDIT_PROMPT = """你是 FB 貼文的事實稽核員。下面是一篇已經過事實查核的電子報文章，以及小編改寫成的 FB 貼文。只檢查三件事：

1. 貼文裡的每一個事實、數字、人名、公司、事件，是否都能在文章裡找到根據？有沒有誇大、扭曲原意、或把推測寫成事實？
2. 貼文有沒有捏造小編的個人經歷（自己買賣或持有什麼、賺賠多少、見過誰、去過哪）？可以有感受和看法，但不能有編造的經歷。
3. 貼文有沒有給買賣建議或保證報酬？

下面【原始來源】是系統從節目平台直接查到的事實（節目名稱、集數標題、長度），貼文引用這些不算捏造。

文風、長度、好不好看不歸你管，不要提。

只輸出一個 JSON，不要任何其他文字：
{{"pass": true 或 false, "issues": ["每一項問題：引用貼文原句，說明錯在哪、文章實際怎麼說"]}}

【原始來源】
{source_block}

【文章】
{article}

【FB 貼文（含兩張圖卡上的字）】
圖卡一：{hook}
圖卡二：{figure} {point}

{post}"""

COMPOSE_BRIEF = """【FB 粉專版】（同一次輸出，填進 fb_hook／fb_point／fb_figure／fb_post 四個欄位）
這篇寫完後會同步發到 Facebook 粉絲專頁「{page}」。請用上面這篇文章的內容，另外寫一則 FB 貼文和兩張圖卡上的字。
欄位對應：fb_hook＝下面說的 HOOK，fb_point＝POINT，fb_figure＝FIGURE（沒有就空字串），fb_post＝貼文本文。

{rules}

【原始來源】
{source_block}"""


def compose_brief(source: dict, mode: str) -> str:
    """給 Substack 寫手的 FB 區塊。任何錯誤都回空字串——FB 不能擋 Substack。"""
    try:
        column = {"company": "賺錢有道", "podcast": "吹牛免稅",
                  "morning": "主編精選", "evening": "主編精選"}.get(mode, "")
        if not column:
            return ""
        info = source_info_for(source or {})
        cand = Candidate(folder=DRAFTS_DIR, meta={"source": source or {}}, created_at=datetime.now(),
                         column=column, headline="", key="")
        return COMPOSE_BRIEF.format(
            page=PAGE_NAME,
            rules=FB_RULES.format(column=column, column_desc=COLUMN_DESC[column]),
            source_block=info.prompt_block(cand),
        )
    except Exception as exc:
        print(f"[FBFollow] ⚠️ FB 區塊產生失敗，這篇不附 FB 版：{exc}")
        return ""


_MARKDOWN = re.compile(r"\*\*|^#+\s|^\s*[-*]\s|\]\(|`", re.M)
_URL = re.compile(r"https?://|www\.", re.I)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# 人設不能編造交易經歷。這些是「我＋交易動作」的組合，看到就擋。
_FAKE_TRADE = re.compile(
    r"我(?:之前|以前|當年|當初|去年|前年|今年|最近|前幾天|上週|昨天|早就|自己|曾經|也曾|也|都|還)*"
    r"(?:買了|買進|賣了|賣掉|持有|抱著|加碼|停損|進場|出場|虧了|賠了|賺了|梭哈|all\s*in)", re.I)
# 逐字比對（不抓整串）：「Sam Altman OpenAI」連在一起在文章裡找不到，但三個字各自都在。
_LATIN_NAME = re.compile(r"(?<![A-Za-z])[A-Z][A-Za-z0-9&'\-]{2,}")
# 自家平台與通用名稱：貼文本來就會提（「完整版在 Substack」），不是文章裡的出處。
_OWN_NAMES = ("Substack", "Facebook", "YouTube", "Podcast", PAGE_NAME)
_HYPE = ("必漲", "穩賺", "保證獲利", "無腦買", "閉眼買", "買爆", "梭哈")


def _norm_number(token: str) -> str:
    return token.replace(",", "").rstrip(".")


_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_CN_RUN = re.compile(r"[零一二兩三四五六七八九十百千]+")


def _cn_to_int(text: str) -> int | None:
    """十四→14、二十六→26、三百零五→305。認不得就回 None。"""
    total, digit = 0, None
    for ch in text:
        if ch in _CN_DIGIT:
            digit = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            total += (1 if digit is None else digit) * _CN_UNIT[ch]
            digit = None
        else:
            return None
    return total + (digit or 0)


def allowed_numbers(article: str, extra: tuple[str, ...] = ()) -> set[str]:
    """文章裡出現過的數字。文章寫「十四小時」、貼文寫「14 小時」是同一個數字——
    2026-09-19 試跑時 14 就是這樣被誤判成捏造的。"""
    allowed = {_norm_number(n) for n in _NUMBER.findall(article)}
    for run in _CN_RUN.findall(article):
        if len(run) >= 2 or run in _CN_UNIT:
            value = _cn_to_int(run)
            if value is not None:
                allowed.add(str(value))
    for item in extra:
        allowed.update(_norm_number(n) for n in _NUMBER.findall(item))
    return allowed


def card_issues(piece: Piece, article: str, cand: "Candidate", out_dir: Path,
                source: SourceInfo | None = None) -> list[str]:
    """圖卡上的字：數字要在文章裡、不能跟貼文一樣違規，而且要真的排得進版面。"""
    from substack_radar.fb_cards import LINE_SEP, LayoutError, render_pair

    issues = []
    article_numbers = allowed_numbers(article, source.fact_strings() if source else ())
    missing = sorted({n for n in _NUMBER.findall(piece.card_text)
                      if _norm_number(n) not in article_numbers and len(_norm_number(n)) > 1})
    if missing:
        issues.append(f"圖卡上的數字在文章裡找不到：{'、'.join(missing)}。")
    from substack_radar.quality_loop import chinese_numerals

    cn = chinese_numerals(piece.card_text)
    if cn:
        issues.append(f"圖卡用了中文數字：{'、'.join(cn[:5])}。改成阿拉伯數字。")
    hype = [w for w in _HYPE if w in piece.card_text]
    if hype:
        issues.append(f"圖卡出現喊單字眼：{'、'.join(hype)}。刪掉。")
    if piece.figure and not _NUMBER.search(piece.figure):
        issues.append("FIGURE 要放數字；沒有關鍵數字就留空。")
    if piece.figure and _norm_number(piece.figure) in _norm_number(piece.point.replace(LINE_SEP, "")):
        issues.append("POINT 又寫了一次 FIGURE 的數字，刪掉 POINT 裡的那個。")
    try:
        render_pair(hook=piece.hook, point=piece.point, figure=piece.figure,
                    column=cand.column, mode=cand.meta.get("mode", ""),
                    topic_category=(cand.meta.get("source") or {}).get("topic_category", ""),
                    title=cand.headline, out_dir=out_dir,
                    source_options=source.card_options() if source else ())
    except LayoutError as exc:
        issues.append(f"圖卡排版：{exc}")
    return issues


def deterministic_issues(post: str, article: str, source: SourceInfo | None = None) -> list[str]:
    """程式能判斷的先判斷。每一項都寫成給寫手的工單。"""
    from substack_radar.quality_loop import chinese_numerals

    issues = []
    if source and source.kind == "youtube" and source.show and source.show.lower() not in post.lower():
        issues.append(f"貼文沒有點名原始節目「{source.show}」。要大方說出處。")
    # 英文人名／機構名要在文章或原始來源裡找得到。FB 版跟 Substack 同一次寫出來，
    # 之後 Substack 會被稽核迴圈改掉假出處，FB 版不會——這一關把那種落差擋下來。
    known = (article + " " + " ".join(source.fact_strings() + (source.show,) if source else ())
             + " " + " ".join(_OWN_NAMES)).lower()
    unknown = sorted({w for w in _LATIN_NAME.findall(post) if w.lower() not in known})
    if unknown:
        issues.append(f"這些名字在文章裡找不到：{'、'.join(unknown)}。只能提文章裡有的人名、機構。")
    body_len = len(re.sub(r"\s", "", post))
    if body_len < 150:
        issues.append(f"太短（{body_len} 字）。至少寫到 250 字，把洞察講清楚。")
    if body_len > 650:
        issues.append(f"太長（{body_len} 字）。刪到 500 字以內，只留一個洞察。")
    if _MARKDOWN.search(post):
        issues.append("用了 markdown 語法（**、#、項目符號或連結）。FB 不會渲染，改成純文字。")
    if _URL.search(post):
        issues.append("貼文裡有網址。刪掉，系統會自己補連結。")
    if post.count("#") > 0:
        issues.append("貼文裡有 hashtag。刪掉，系統會自己補。")

    article_numbers = allowed_numbers(article, source.fact_strings() if source else ())
    missing = sorted({n for n in _NUMBER.findall(post)
                      if _norm_number(n) not in article_numbers and len(_norm_number(n)) > 1})
    if missing:
        issues.append(f"這些數字在文章裡找不到：{'、'.join(missing)}。只能用文章裡有的數字，找不到就刪。")
    cn = chinese_numerals(post)
    if cn:
        issues.append(f"用了中文數字：{'、'.join(cn[:5])}。改成阿拉伯數字。")
    trade = _FAKE_TRADE.findall(post)
    if trade:
        issues.append(f"人設編造了交易經歷：「{'」「'.join(trade)}」。可以有看法，不能說自己買賣或賺賠。")
    hype = [w for w in _HYPE if w in post]
    if hype:
        issues.append(f"出現喊單字眼：{'、'.join(hype)}。刪掉。")
    return issues


@dataclass
class Piece:
    hook: str
    point: str
    figure: str
    post: str

    @property
    def card_text(self) -> str:
        return " ".join(x for x in (self.hook, self.figure, self.point) if x)


def _extract(raw: str) -> Piece | None:
    m = re.search(
        r"<<<HOOK>>>\s*(.*?)\s*<<<POINT>>>\s*(.*?)\s*<<<FIGURE>>>\s*(.*?)\s*<<<POST>>>\s*(.+?)\s*<<<END>>>",
        raw or "", re.S,
    )
    if not m:
        return None
    hook, point, figure, post = (g.strip() for g in m.groups())
    figure = "" if figure in ("", "（空白）", "無", "none", "None") else figure
    return Piece(hook=hook, point=point, figure=figure, post=post) if hook and point and post else None


def _parse_audit(raw: str) -> tuple[bool, list[str]]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError(f"稽核沒有回 JSON：{(raw or '')[:120]}")
    data = json.loads(m.group(0))
    issues = [str(i) for i in data.get("issues") or []]
    return bool(data.get("pass")) and not issues, issues


def _write(prompt: str) -> tuple[Piece, str]:
    """寫手鏈：agy 上最新一代 Gemini 排第一（src/agy_models.py）。回（稿件, 模型）。"""
    from src.llm_brain import _agy_model_chain
    from substack_radar.quality_loop import _run_agy_once

    last = None
    for model in _agy_model_chain():
        try:
            piece = _extract(_run_agy_once(prompt, model, AGY_TIMEOUT_S))
            if piece:
                return piece, model
            last = RuntimeError(f"{model} 沒有照格式輸出 HOOK／POINT／FIGURE／POST")
        except Exception as exc:
            last = exc
        print(f"[FBFollow] ⚠️ 寫手 {model} 不可用：{str(last)[:100]}")
    raise RuntimeError(f"寫手鏈全部失敗：{last}")


def _audit(prompt: str, writer: str) -> tuple[str, str]:
    """稽核鏈：先換家族（寫手是 Gemini 就先 Claude），真的都不行才退回同家族，
    並且照實記下是誰稽核的——2026-09-19 試跑時 Opus 429，實際由 Gemini 稽核
    Gemini，紀錄卻寫 Opus。"""
    from substack_radar.quality_loop import AUDIT_MODEL_CHAIN, _run_agy_once, _same_family, auditor_for

    pool = [auditor_for(writer), "Claude Sonnet 4.6 (Thinking)", *AUDIT_MODEL_CHAIN]
    order = []
    for model in pool:
        if model not in order:
            order.append(model)
    order.sort(key=lambda m: _same_family(m, writer))  # 穩定排序：不同家族在前
    last = None
    for model in order:
        try:
            raw = _run_agy_once(prompt, model, AGY_TIMEOUT_S)
            if _same_family(model, writer):
                print(f"[FBFollow] ⚠️ 不同家族的稽核全部不可用，改由同家族 {model} 稽核")
            return raw, model
        except Exception as exc:
            last = exc
            print(f"[FBFollow] ⚠️ 稽核 {model} 不可用：{str(exc)[:100]}")
    raise RuntimeError(f"稽核鏈全部不可用：{last}")


@dataclass
class Draft:
    piece: Piece | None = None
    source: SourceInfo | None = None
    writer: str = ""
    auditor: str = ""
    rounds: int = 0
    passed: bool = False
    issues: list[str] = field(default_factory=list)


def compose_post(cand: Candidate) -> Draft:
    """寫 → 確定性閘門 → agy 稽核 → 帶工單重寫，直到通過或輪數用完。"""
    article = (cand.folder / "Article_Full.md").read_text(encoding="utf-8")
    source = source_info(cand)
    print(f"[FBFollow] 原始來源：{source.kind or '無'} {source.show} {source.duration_text}")
    article_for_prompt = article[:12000]
    result = Draft(source=source)
    feedback = ""
    for round_no in range(1, MAX_ROUNDS + 1):
        result.rounds = round_no
        prompt = WRITER_PROMPT.format(
            page=PAGE_NAME,
            rules=FB_RULES.format(column=cand.column, column_desc=COLUMN_DESC[cand.column]),
            title=cand.headline, subtitle=cand.meta.get("subtitle", ""),
            article=article_for_prompt, feedback=feedback,
            source_block=source.prompt_block(cand),
        )
        piece, result.writer = _write(prompt)
        result.piece = piece

        issues = (deterministic_issues(piece.post, article, source)
                  + card_issues(piece, article, cand, cand.folder, source))
        if not issues:
            try:
                raw, result.auditor = _audit(
                    AUDIT_PROMPT.format(article=article_for_prompt, post=piece.post,
                                        hook=piece.hook.replace("／", ""),
                                        point=piece.point.replace("／", ""), figure=piece.figure,
                                        source_block=source.prompt_block(cand)),
                    result.writer,
                )
                passed, issues = _parse_audit(raw)
            except Exception as exc:
                # 稽核掛掉不能當成通過。
                passed, issues = False, [f"稽核無法執行：{str(exc)[:120]}"]
            if passed:
                result.passed, result.issues = True, []
                print(f"[FBFollow] ✅ 第 {round_no} 輪通過（寫手 {result.writer}／稽核 {result.auditor}）")
                return result
        result.issues = issues
        print(f"[FBFollow] ↻ 第 {round_no} 輪 {len(issues)} 項問題：" + "；".join(i[:60] for i in issues))
        feedback = (
            "【上一版被退回，這些問題必須全部修掉】\n"
            + "\n".join(f"- {i}" for i in issues)
            + f"\n\n【上一版】\nHOOK：{piece.hook}\nPOINT：{piece.point}\nFIGURE：{piece.figure}\n{piece.post}\n"
        )
    return result


def finalize(post: str, column: str, status: str, url: str | None,
             source: SourceInfo | None = None) -> str:
    links = []
    if source and source.kind == "youtube" and source.url:
        links.append(f"🎧 原始節目：{source.url}")
    elif source and source.kind == "web" and source.url:
        links.append(f"📄 原文：{source.url}")
    if status == "published" and url:
        links.append(f"👉 我們的完整整理：{url}")
    else:
        links.append(f"👉 完整版在 Substack，免費訂閱：{PUB_HOME}")
    return f"{post.strip()}\n\n" + "\n".join(links) + f"\n\n#{column} #{PAGE_NAME}"


# ---------------------------------------------------------------------------
# 同步發文：Substack 寫手同一次輸出的 FB 版（2026-09-20 起的主路徑，零額外 agy）
# ---------------------------------------------------------------------------

def post_with_draft(out_dir: Path, draft) -> str:
    """compose 建好 Substack 草稿後呼叫。回傳結果代碼（posted/held/skipped/preview）。

    不再呼叫 agy：FB 版由 Substack 寫手在同一次輸出裡產出，這裡只跑確定性檢查——
    而且是對「稽核迴圈改完之後」的最終文章檢查，所以稽核刪掉的數字或假出處，
    FB 版若還留著會在這裡被擋下。"""
    out_dir = Path(out_dir).resolve()
    meta = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
    column, headline = split_column(meta.get("title", ""))
    cand = Candidate(folder=out_dir, meta=meta,
                     created_at=datetime.fromisoformat(meta.get("created_at") or datetime.now().isoformat()),
                     column=column, headline=headline, key=_dedupe_key(meta, headline),
                     draft_id=str(meta.get("substack_draft_id") or "") or None)
    figure = (getattr(draft, "fb_figure", "") or "").strip()
    piece = Piece(hook=(getattr(draft, "fb_hook", "") or "").strip(),
                  point=(getattr(draft, "fb_point", "") or "").strip(),
                  figure="" if figure in ("無", "none", "None", "（空白）") else figure,
                  post=(getattr(draft, "fb_post", "") or "").strip())
    ledger = _load_ledger()

    def record(status: str, **extra) -> str:
        ledger[cand.rel] = {"status": status, "key": cand.key, "draft_id": cand.draft_id,
                            "at": datetime.now().isoformat(timespec="seconds"), **extra}
        _save_ledger(ledger)
        return status

    if not column:
        print("[FBFollow] ⏭️ 不是三個專欄之一，不發 FB。")
        return "skipped"
    if not (piece.hook and piece.point and piece.post):
        print("[FBFollow] ⏭️ 寫手這次沒產出 FB 版，不發。")
        return record("skipped", reason="寫手沒產出 FB 版")
    if any(str(w).startswith(FACT_FAIL_MARK) for w in meta.get("audit_warnings") or []):
        print("[FBFollow] ⏭️ Substack 端事實稽核沒過，FB 不跟發。")
        return record("skipped", reason="Substack 事實稽核沒過")
    if any(v.get("status") == "posted" and v.get("key") == cand.key for v in ledger.values()):
        print(f"[FBFollow] ⏭️ 同題材（{cand.key}）已經發過 FB。")
        return "skipped"

    article = (out_dir / "Article_Full.md").read_text(encoding="utf-8")
    source = source_info(cand)
    issues = deterministic_issues(piece.post, article, source) + card_issues(piece, article, cand, out_dir, source)
    text = finalize(piece.post, column, "draft", None, source)
    (out_dir / "fb_post.txt").write_text(text + "\n", encoding="utf-8")
    if issues:
        print("[FBFollow] 🛑 FB 版沒過檢查，不發：" + "；".join(issues))
        return record("held", issues=issues)
    if os.getenv("FB_FOLLOW_LIVE") != "1":
        print("[FBFollow] （FB_FOLLOW_LIVE 未開，只產出不發）\n" + text)
        return "preview"

    from src.publisher import publish_fb_carousel

    cards = [str(out_dir / "fb_card1.png"), str(out_dir / "fb_card2.png")]
    res = asyncio.run(publish_fb_carousel(cards, text, expected_count=2))
    if not res.get("success"):
        print(f"[FBFollow] ❌ FB 發文失敗：{str(res.get('error'))[:200]}")
        return record("failed", error=str(res.get("error"))[:300])
    print(f"[FBFollow] ✅ 已同步發 FB：{res.get('id')}　{headline}")
    return record("posted", fb_id=res.get("id"), route="compose-sync")


# ---------------------------------------------------------------------------
# 主流程（手動補發用：會另外呼叫 agy，平常不走這條）
# ---------------------------------------------------------------------------

def run(*, dry_run: bool, only: str | None = None, ignore_age: bool = False) -> int:
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
    live = os.getenv("FB_FOLLOW_LIVE") == "1" and not dry_run
    ledger = _load_ledger()

    if only:
        folder = Path(only).resolve()
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        column, headline = split_column(meta.get("title", ""))
        cands = [Candidate(folder=folder, meta=meta,
                           created_at=datetime.fromisoformat(meta["created_at"]),
                           column=column, headline=headline, key=_dedupe_key(meta, headline),
                           draft_id=str(meta.get("substack_draft_id") or "") or _draft_id_from_db(meta))]
    else:
        global MIN_AGE_H
        if ignore_age:
            MIN_AGE_H = 0
        cands = scan(ledger=ledger)
    if not cands:
        print("[FBFollow] 沒有可發的草稿。")
        return 0

    for cand in cands:
        status, url = remote_status(cand)
        if status in ("deleted", "unknown"):
            reason = "主編已刪除這篇草稿" if status == "deleted" else "對不到 Substack 草稿編號"
            print(f"[FBFollow] ⏭️ {cand.rel}：{reason}，不發。")
            if not dry_run:
                ledger[cand.rel] = {"status": "skipped", "reason": reason, "key": cand.key,
                                    "at": datetime.now().isoformat(timespec="seconds")}
                _save_ledger(ledger)
            continue

        print(f"[FBFollow] ✍️ {cand.rel}（Substack：{status}）")
        try:
            draft = compose_post(cand)
        except RuntimeError as exc:
            # 寫手鏈全掛（2026-09-19 深夜 agy 所有模型都 429）是基礎設施問題，
            # 不是這篇稿子的問題：不扣重試次數，下個時段再試。
            print(f"[FBFollow] ⏸️ agy 不可用，這個時段不發，下個時段再試：{str(exc)[:160]}")
            return 2
        text = finalize(draft.piece.post if draft.piece else "", cand.column, status, url, draft.source)
        (cand.folder / "fb_post.txt").write_text(text + "\n", encoding="utf-8")
        entry = ledger.get(cand.rel) or {}

        if not draft.passed:
            print(f"[FBFollow] 🛑 {MAX_ROUNDS} 輪後仍有問題，不發：" + "；".join(draft.issues))
            if not dry_run:
                ledger[cand.rel] = {**entry, "status": "held", "key": cand.key,
                                    "attempts": entry.get("attempts", 0) + 1,
                                    "issues": draft.issues,
                                    "at": datetime.now().isoformat(timespec="seconds")}
                _save_ledger(ledger)
            continue

        if not live:
            print("[FBFollow] （試跑，不發）\n" + text)
            return 0

        from src.publisher import publish_fb_carousel

        cards = [str(cand.folder / "fb_card1.png"), str(cand.folder / "fb_card2.png")]
        res = asyncio.run(publish_fb_carousel(cards, text, expected_count=2))
        if not res.get("success"):
            print(f"[FBFollow] ❌ FB 發文失敗：{str(res.get('error'))[:200]}")
            ledger[cand.rel] = {**entry, "status": "failed", "key": cand.key,
                                "attempts": entry.get("attempts", 0) + 1,
                                "error": str(res.get("error"))[:300],
                                "at": datetime.now().isoformat(timespec="seconds")}
            _save_ledger(ledger)
            return 1
        ledger[cand.rel] = {"status": "posted", "key": cand.key, "fb_id": res.get("id"),
                            "draft_id": cand.draft_id, "substack_status": status,
                            "writer": draft.writer, "auditor": draft.auditor,
                            "rounds": draft.rounds,
                            "at": datetime.now().isoformat(timespec="seconds")}
        _save_ledger(ledger)
        print(f"[FBFollow] ✅ 已發 FB：{res.get('id')}　{cand.headline}")
        return 0  # 一次排程只發一篇

    print("[FBFollow] 這一輪沒有發出任何貼文。")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FB 粉專跟發 Substack 草稿")
    ap.add_argument("--dry-run", action="store_true", help="只產稿不發")
    ap.add_argument("--folder", help="指定一個草稿資料夾（試跑用）")
    ap.add_argument("--ignore-age", action="store_true", help="不等冷卻時間（試跑用）")
    args = ap.parse_args(argv)
    return run(dry_run=args.dry_run, only=args.folder, ignore_age=args.ignore_age)


if __name__ == "__main__":
    sys.exit(main())
