#!/usr/bin/env python3
"""
News Radar · Emergency Oneshot Publisher
=========================================

編輯判斷：有一條明顯值得發的新聞，但 harvester 關鍵字白名單沒命中 / feed 清單裡沒
那個來源，導致它永遠進不了 compose queue。這支腳本是『手動繞過 harvester，其餘一切
照規矩走完』的官方入口。

Pipeline：
    fetch → extract (trafilatura) → score_news (real, ≥0.7 gate)
    → compose_multi_platform → finalize_variant → human YES
    → publish_to_{fb,threads,ig} → DB (drafts + platform_drafts + publish_log)

設計原則（與 `scripts/first_batch_publish.py` 完全對齊）：

1. **真實 scorer**：呼叫 `src.scorer.score_news`，不 bypass、不 hardcode。
   Score 低於 `AUTO_PUBLISH_THRESHOLD`（run_pipeline 的 SSOT，目前 0.7）→ abort。
   Emergency 不是護身符；編輯角度拿不到該分數就代表題材自己站不住。

2. **Voice rules 一致**：沿用 `config/news_radar_soul.md` + 三份 platform appendix；
   這支腳本只覆寫 `editorial_note`，不動 soul / appendix。

3. **人工 YES gate**：三份 draft 全文印到 terminal，輸入大寫 YES 才 publish；
   其他任何輸入（含 Enter）= abort，DB 不寫、API 不打。

4. **Idempotent**：跑同一 URL 第二次，若該 news_id 已有 publish_log 成功紀錄，
   直接 exit 0 不重發。

5. **失敗 abort 不降級**：任何一步失敗（composer 回 None、char 超限、某平台 publish
   失敗）→ exit ≠ 0，不降級成 emergency template、不重試。寫錯的 row 要靠上層
   (push_state.sh) 提醒；這一層只負責把『我以為發成功』和『三筆 publish_log 都成功』
   綁成同一件事。

6. **--editorial-note 選填**：預設用本檔 `DEFAULT_EMERGENCY_EDITORIAL_NOTE`
   （編輯判斷一條緊急新聞該用什麼角度切）；也接受 --editorial-note-file 指向一份
   手寫的 .md / .txt 覆寫預設。首發以外的一次性 emergency 發文建議每次自己寫。

Usage：
    cd ~/news_radar && source .venv/bin/activate

    # (A) 預設：給 URL，腳本自己 fetch
    python tools/emergency_oneshot.py --url "https://www.reuters.com/..."

    # (B) URL 抓不到（paywall / 反爬 / JS-rendered）— 你手邊有 PDF
    python tools/emergency_oneshot.py --url "https://..." \
           --pdf ~/Downloads/reuters_article.pdf --title "..." \
           --og-image "https://..."

    # (C) 你從網頁複製了正文到一個 .md / .txt 檔
    python tools/emergency_oneshot.py --url "https://..." \
           --content-file ./pasted.md --title "..." --og-image "https://..."

    # (D) 你想發自己的原創構思（沒有原始新聞）— 用任意 canonical URL 當 id
    python tools/emergency_oneshot.py --url "note://hsin/2026-04-23/meta-surveillance" \
           --content-file ./my_brief.md --title "我對這波員工監控新聞的看法"

    # 共同選項
    python tools/emergency_oneshot.py --url "..." --dry-run              # 只 compose 不 publish
    python tools/emergency_oneshot.py --url "..." --editorial-note-file ./note.md

Exit codes：
    0  三平台都 publish 成功 + DB 寫入完成（或已 published → idempotent 早退）
    1  score < AUTO_PUBLISH_THRESHOLD
    2  argparse / 環境前置錯
    3  fetch / extract 失敗（HTML 抓不到 / trafilatura 抽不出正文）
    4  composer 回 None（Gemini + Claude fallback 皆失效）
    5  char_count 超限，finalize_variant 壓不下來
    6  使用者輸入非 YES（主動 abort）
    7  至少一平台 publish 失敗
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

# ---- 讓 `python tools/emergency_oneshot.py` 直接跑、不用 PYTHONPATH hack ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.cleaner import extract_markdown, extract_og_image, extract_og_video  # noqa: E402
from src.composer import compose_multi_platform, finalize_variant  # noqa: E402
from src.fetcher import fetch_html  # noqa: E402
from src.publisher import publish_to_fb, publish_to_ig, publish_to_threads  # noqa: E402
from src.scorer import score_news  # noqa: E402
from src.schema import NewsItem  # noqa: E402
from src import db as dbmod  # noqa: E402

# AUTO_PUBLISH_THRESHOLD 是 run_pipeline.py 的 SSOT；import 而不是硬編
# 這樣之後調策略時，emergency 自動跟著 auto-publish cadence 走
from run_pipeline import AUTO_PUBLISH_THRESHOLD  # noqa: E402


DB_PATH = PROJECT_ROOT / "data" / "01_harvest" / "news_radar.db"
DRAFT_ID_BREADCRUMB = PROJECT_ROOT / "tools" / ".last_emergency_draft_id"

PLATFORM_TO_DB = {"fb": "facebook", "ig": "instagram", "threads": "threads"}

# Canonical order — 影響 compose 內部優先級、step_write_drafts 的 drafts row 來源、
# step_publish 的順序。Operator 用 --platforms 指定子集時，仍按此順序排列。
ALL_PLATFORMS = ("fb", "ig", "threads")


def parse_platforms_arg(arg: Optional[str]) -> List[str]:
    """把 --platforms 參數（如 "fb,threads" 或 "ig" 或 None）轉成排序好的子集。

    None / 空字串 → 預設三平台都發
    含未知平台 → fail(2) 給乾淨錯誤訊息
    保留 ALL_PLATFORMS 順序（不論 user 怎麼排）
    """
    if not arg:
        return list(ALL_PLATFORMS)
    requested = {p.strip().lower() for p in arg.split(",") if p.strip()}
    invalid = sorted(p for p in requested if p not in ALL_PLATFORMS)
    if invalid:
        fail(2, f"--platforms 含未知值 {invalid}；合法值：{list(ALL_PLATFORMS)}")
    if not requested:
        fail(2, "--platforms 解析後空集合（逗號之間沒內容？）")
    return [p for p in ALL_PLATFORMS if p in requested]

# ---- 預設編輯角度 note ---------------------------------------------------
# 這條 note 是『Reuters: Meta capturing employee mouse/keystrokes for AI training
# 2026-04-21』的專用角度。未來若跑別條新聞，請用 --editorial-note-file 覆寫，
# 或直接編輯這段。沿用 soul §Ⅵ.6 的 voice rules（敘事主體＝資深科技記者，不加
# 勵志油膩、不幫讀者下結論）。
DEFAULT_EMERGENCY_EDITORIAL_NOTE = """這是 News Radar 的 emergency 插播：harvester 關鍵字白名單沒命中，但編輯判斷這條值得搶發。
目標：以『冷靜且具同理心的資深科技商業評論家』之姿出場，維持 soul §Ⅵ.6 的敘事語調（可用第一人稱「我」，但避免『我認為』、『我的反思』這類虛詞）。
benchmark：Tim Cook 宣布接棒、John Ternus 上台時的產業新聞語感——事實先行、人名與產品名具體、情緒沉得住。

本則新聞可用的角度（非必須全寫，這是判斷取捨的基準）：

1. **AI 資料飢渴把魔爪伸回員工身上**：過去大模型的訓練資料是『公開網路 + 版權交易』，
   現在出現『員工作業行為』這個新檔位。這不是 CCTV、不是 DLP，是把員工每天跟公司
   系統互動的過程當成訓練 corpus。資料供給側的壓力已經推到這種程度，值得把『資料
   稀缺性』當成宏觀主線之一。

2. **公司設備再也不是私人空間**：員工在公司筆電上寫程式、回 Slack、開 Figma —— 這
   些動作本來就被各種 EDR / DLP 看著，但現在多了一層『被拿去訓練 AI』。工作裝置
   vs 員工私人時間的邊界再次被重畫。引用當事文件的原話（如果 Reuters 原文有）來
   承擔判斷性敘述，不要自己下『這是監控資本主義』這類情緒結論。

3. **資料勞動的灰色地帶**：員工『產生』的這些 keystroke / mouse-movement 是不是一種
   未計價的勞動？如果拿去訓練的模型最終被 Meta 當內部生產力工具使用，員工的操作
   行為就變成一種『以薪水換取的附贈資產』。這是制度／勞資的灰區，不是道德指控；
   保持技術評論語氣，引用 Meta 官方聲明（若 Reuters 有），讓讀者自己判斷。

寫作要求：
- 第一句務必有客觀事實（Meta / Reuters 日期 / 資料類型）。不要反問句、不要『你知道嗎』。
- 用自己咀嚼後的敘述，展示『資深科技記者 + 分析師』的混合語感。
- 絕對禁止『這說明了兩件事』『拆解兩層邏輯』這類八股條列結構。
- 第一人稱「我」可以用（soul §Ⅵ.6），但避免『我認為』『我的反思』『我覺得』。
- 讓讀者在通勤 90 秒能帶走核心事實 + 兩個方向性 insight；技術詞要轉譯，數字要有地基。
- 結尾不下結論、不總結、不『這對投資人意味著…』；把最後一筆關鍵事實放好，讓讀者推導。
"""


# ---------- 小工具 --------------------------------------------------------

def banner(text: str) -> None:
    print()
    print("=" * 72)
    print(f"  {text}")
    print("=" * 72)


def step(text: str) -> None:
    print(f"\n▶ {text}")


def fail(rc: int, msg: str) -> "None":
    print(f"\n❌ [exit {rc}] {msg}", file=sys.stderr)
    sys.exit(rc)


def already_published(news_id: str) -> Optional[str]:
    """回傳已存在的 draft_id 若此 news_id 已成功發過（至少 1 筆 publish_log success=1）。
    否則回 None。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT d.id
              FROM drafts d
              JOIN publish_log pl ON pl.draft_id = d.id
             WHERE d.news_id = ? AND pl.success = 1
             LIMIT 1
            """,
            (news_id,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------- Pipeline steps ------------------------------------------------

def _extract_pdf_text(pdf_path: Path) -> str:
    """從 PDF 抽出純文字。優先 pypdf（純 Python，零系統依賴），
    pypdf 缺席時 fallback 到 macOS 常見的 `pdftotext`（poppler），都沒有清楚 fail。

    Reuters / NYT / WSJ 的『print-to-PDF』輸出通常文字層完整；image-only 掃描稿
    要自己先 OCR。
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        pages = [p.extract_text() or "" for p in reader.pages]
        return "\n\n".join(pages).strip()
    except ImportError:
        pass

    import shutil
    import subprocess
    if shutil.which("pdftotext"):
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        raise RuntimeError(
            f"pdftotext 非零退出：rc={result.returncode}  stderr={result.stderr[:200]}"
        )

    raise RuntimeError(
        "PDF 抽文字失敗：沒有 pypdf 也沒有 pdftotext。\n"
        "  修法：pip install pypdf  (在 news_radar 的 venv 裡)\n"
        "  或    brew install poppler  (裝 pdftotext)\n"
        "  或    自己把 PDF 存成 .txt/.md 後改用 --content-file"
    )


def _guess_title_from_content(content: str, url: str) -> str:
    """沒有 HTML 也沒有 --title 時的退路：第一行非空白且長度 2–120。
    markdown H1 的 `#` 會被剝掉。都不符就退回 URL 尾段。"""
    for line in content.splitlines():
        s = line.strip().lstrip("#").strip()
        if 2 <= len(s) <= 120:
            return s
    return url.rsplit("/", 1)[-1] or url


async def step_fetch_and_extract(
    url: str,
    title_override: Optional[str],
    content_file: Optional[Path] = None,
    pdf_path: Optional[Path] = None,
    og_image_override: Optional[str] = None,
) -> dict:
    """Step 1：載入素材 → build NewsItem + upsert。

    來源優先序（由高到低，只走第一條成功的）：
      1. --pdf：pypdf / pdftotext 抽文字
      2. --content-file：讀 .md / .txt
      3. 預設：fetch URL → trafilatura

    title 優先序：--title > HTML 的 og:title / <title> > 內容第一行 > URL 尾段。
    og_image 優先序：--og-image > HTML 的 og:image（只有 fetch 路徑有）> None。

    回傳 dict(id, title, content, og_image, og_video, og_video_is_direct)。
    """
    og_image: Optional[str] = og_image_override
    og_video_url: Optional[str] = None
    og_video_is_direct: bool = False
    html: Optional[str] = None

    if pdf_path:
        step(f"Step 1 · 從 PDF 載入素材：{pdf_path}")
        if not pdf_path.exists():
            fail(3, f"PDF 不存在：{pdf_path}")
        try:
            markdown = _extract_pdf_text(pdf_path)
        except RuntimeError as e:
            # 把 RuntimeError 翻成乾淨的 exit 3，讓 .sh 的錯誤分類對上
            # （原本會 uncaught 變 exit 1，被 .sh 誤報成『score < threshold』）
            fail(3, str(e))
        if not markdown or len(markdown) < 80:
            fail(3, f"PDF 抽出的文字過短（{len(markdown)} chars）— 可能是掃描稿 / image-only PDF")
        wc = max(1, len("".join(markdown.split())) // 3)
        print(f"   ↳ word_count={wc}  文字頭 200 字：{markdown[:200].replace(chr(10), ' ')}...")

    elif content_file:
        step(f"Step 1 · 從檔案載入素材：{content_file}")
        if not content_file.exists():
            fail(3, f"content file 不存在：{content_file}")
        markdown = content_file.read_text(encoding="utf-8").strip()
        if not markdown or len(markdown) < 40:
            fail(3, f"content file 太短（{len(markdown)} chars）")
        wc = max(1, len("".join(markdown.split())) // 3)
        print(f"   ↳ word_count={wc}  文字頭 200 字：{markdown[:200].replace(chr(10), ' ')}...")

    else:
        step(f"Step 1 · fetch HTML：{url}")
        async with httpx.AsyncClient() as client:
            html = await fetch_html(client, url)
        if not html:
            fail(3,
                 f"fetch_html 回 None（網路 / 4xx / timeout / 反爬）→ {url}\n"
                 f"   救援：加 --pdf <path> 或 --content-file <path>，見 --help。")
        print(f"   ↳ HTML 長度：{len(html)} chars")

        step("Step 2 · trafilatura 抽正文")
        markdown, wc = extract_markdown(html)
        if not markdown:
            fail(3, "trafilatura 抽不到正文（JS-rendered / 403 / paywall）。"
                    "改用 --pdf 或 --content-file 繞過。")
        print(f"   ↳ word_count={wc}  markdown 頭 200 字：{markdown[:200].replace(chr(10), ' ')}...")

        if og_image is None:
            og_image = extract_og_image(html)
        og_video_url, og_video_is_direct = extract_og_video(html)

    print(f"   ↳ og_image={og_image}")
    if og_video_url:
        print(f"   ↳ og_video={og_video_url} (direct={og_video_is_direct})")

    # 決定 title：CLI 覆寫 > og:title > <title> > 內容第一行 > URL 尾段
    title = title_override
    if not title and html:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        og_t = soup.find("meta", property="og:title")
        if og_t and og_t.get("content"):
            title = og_t["content"].strip()
        else:
            t = soup.find("title")
            if t and t.text:
                title = t.text.strip()
    if not title:
        title = _guess_title_from_content(markdown, url)
    print(f"   ↳ title={title!r}")

    # 組 NewsItem + upsert（idempotent：若已存在 upsert_news 會回 False，不覆蓋）
    now_iso = datetime.now(timezone.utc).isoformat()
    news_id = hashlib.sha1(url.encode("utf-8")).hexdigest()
    item = NewsItem(
        id=news_id,
        feed_name="emergency_manual",
        feed_tier="primary",
        source_type="article",
        url=url,
        title=title,
        published_at=now_iso,
        fetched_at=now_iso,
        language="en",  # Reuters 多半是英文；scorer / composer 內部會翻繁中
        raw_html=None,  # 預設不落地 raw_html
        clean_markdown=markdown,
        word_count=wc,
        og_image_url=og_image,
        og_video_url=og_video_url,
        og_video_is_direct=og_video_is_direct,
        status="fetched",
    )
    conn = dbmod.get_conn()
    inserted = dbmod.upsert_news(conn, item)
    conn.close()
    print(f"   ↳ upsert_news: news_id={news_id} {'(新 insert)' if inserted else '(已存在，不覆蓋)'}")

    return {
        "id": news_id,
        "title": title,
        "content": markdown,
        "og_image": og_image,
        "og_video": og_video_url,
        "og_video_is_direct": og_video_is_direct,
    }


async def step_score(title: str, content: str) -> float:
    """Step 3：呼叫 real scorer，回傳 confidence_score。低於 threshold → exit 1。"""
    step("Step 3 · 真實 scorer（不 bypass、不 hardcode）")
    result = await score_news(title, content)
    if result is None:
        fail(1, "scorer 回 None（Gemini + Claude fallback 皆失效）→ 視同無法判斷，abort")
    breakdown = result.score_breakdown
    print(f"   ↳ confidence_score={result.confidence_score:.3f}  (threshold={AUTO_PUBLISH_THRESHOLD})")
    print(f"   ↳ data_density={breakdown.data_density:.2f}  "
          f"strategic_signal={breakdown.strategic_signal:.2f}  "
          f"news_novelty={breakdown.news_novelty:.2f}  "
          f"persona_fit={breakdown.persona_fit:.2f}")
    print(f"   ↳ editorial_note（scorer 自寫）：{result.editorial_note[:160]}...")

    if result.confidence_score < AUTO_PUBLISH_THRESHOLD:
        fail(
            1,
            f"confidence_score={result.confidence_score:.3f} < AUTO_PUBLISH_THRESHOLD={AUTO_PUBLISH_THRESHOLD}。"
            f"Emergency 不 bypass 分數——題材本身站不住就不發。"
        )
    return result.confidence_score


async def step_compose(
    title: str,
    content: str,
    og_image: Optional[str],
    editorial_note: str,
    platforms: List[str],
) -> dict:
    """Step 4：composer → finalize_variant 指定平台。任一平台 char_count 超限 → exit 5。

    platforms 由 --platforms flag 決定，預設三平台都發。指定子集時：
      - 只 finalize 子集（非選中的平台 LLM 還是會生（compose_multi_platform 一次 call
        生三份），但我們不 finalize、不寫 DB、不 publish）
      - banner 只印選中的 draft 全文
    """
    sel_label = ",".join(platforms)
    step(f"Step 4 · compose_multi_platform（平台={sel_label}）")
    t0 = time.time()
    draft = await compose_multi_platform(
        title=title,
        content=content,
        og_image=og_image,
        editorial_note=editorial_note,
        platforms=platforms,
    )
    print(f"   ↳ composer 耗時 {time.time() - t0:.1f}s")
    if not draft:
        fail(4, "compose_multi_platform 回 None（Gemini + Claude fallback 皆失效）")

    finalized = {}
    for p in platforms:
        v = getattr(draft, p, None)
        if not v:
            fail(5, f"composer 沒有產出 {p} 變體（指定發送平台必須齊全）")
        v2, full_text, ok = finalize_variant(v, p)
        finalized[p] = (v2, full_text, ok)

    banner(f"Step 5 · 已 finalize 的 draft 全文（{len(finalized)} 平台 · 請仔細看）")
    for p, (v2, full_text, ok) in finalized.items():
        print()
        print(f"------- {p.upper()} (char_count={v2.char_count}, ok={ok}) -------")
        print(full_text)
        print()

    bad = [p for p, (_, _, ok) in finalized.items() if not ok]
    if bad:
        fail(5, f"以下平台壓不下字數：{bad}")

    return finalized


DRAFTS_JSON_PATH = PROJECT_ROOT / "data" / "emergency_last_drafts.json"


def _write_drafts_json(item: dict, finalized: dict) -> Path:
    """把三份 finalized draft 寫成 JSON，讓 chat 端的 orchestrator 不用
    scrape stdout 也能讀到三個平台的 full_text / hashtags / char_count。
    被 step_yes_gate() 在 block 之前呼叫。"""
    DRAFTS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "item": {
            "id": item["id"],
            "title": item["title"],
            "og_image": item.get("og_image"),
        },
        "drafts": {
            p: {
                "title": v2.title,
                "body": v2.body,
                "hashtags": v2.hashtags,
                "full_text": full_text,
                "char_count": v2.char_count,
                "ok": ok,
            }
            for p, (v2, full_text, ok) in finalized.items()
        },
        "composed_at": datetime.now(timezone.utc).isoformat(),
    }
    DRAFTS_JSON_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return DRAFTS_JSON_PATH


def step_yes_gate(
    item: dict,
    finalized: dict,
    approve_file: Optional[Path] = None,
    approve_timeout: int = 1800,
) -> None:
    """Step 6：人工確認。

    兩種模式：
      (a) Terminal 模式（預設）：stdin 等 YES，block 到使用者打字為止。
          設計原則：你親手看過才發。

      (b) --approve-file 模式：chat-based 同意。Python 先把三份 draft 寫進
          data/emergency_last_drafts.json，然後 poll `approve_file`。
          外部 orchestrator（e.g. Cowork 裡的我）讀 JSON → 貼 draft 給使用者看
          → 使用者在 chat 說 go → orchestrator 把 'GO' 寫進 approve_file →
          Python 偵測到繼續 publish。

          批准檔合法內容只有 'GO'（大小寫不敏感）。其他內容 = abort。
          超過 approve_timeout 秒沒等到 → abort。

    兩種模式 abort 的 exit code 都是 6。
    """
    # 先把 drafts 寫檔，不論哪種模式都寫（--approve-file 用來讀、terminal 用來事後 debug）
    drafts_path = _write_drafts_json(item, finalized)
    print(f"\n[drafts-json] {drafts_path.relative_to(PROJECT_ROOT)}")

    banner("Step 6 · 確認發布")

    if approve_file is None:
        print("輸入大寫 YES 送出三個平台；其他任何輸入（含 Enter）= abort（DB 不寫、API 不打）")
        try:
            ans = input("> ").strip()
        except EOFError:
            ans = ""
        if ans != "YES":
            print("中止。沒有任何貼文被送出。")
            sys.exit(6)
        return

    # --approve-file 模式
    print(f"等候批准檔：{approve_file}")
    print(f"寫入 'GO' 繼續：echo GO > {approve_file}")
    print(f"其他內容（含空字串）= abort。超過 {approve_timeout}s 未等到 = abort。")

    # 避免 stale 檔誤發：若一開始就存在，拒絕（可能是上次 run 殘留的）
    if approve_file.exists():
        print(f"❌ 批准檔 {approve_file} 在 Python 抵達 YES gate 之前就已存在。")
        print("   這很可能是上次 run 殘留的 stale 檔；拒絕讀以免誤發。")
        print(f"   清掉再跑：rm {approve_file}")
        sys.exit(6)

    start = time.time()
    poll = 2.0
    while True:
        if approve_file.exists():
            try:
                content = approve_file.read_text(encoding="utf-8").strip().upper()
            except Exception as e:
                print(f"   ↳ 讀批准檔失敗（{e}），下一輪重試")
                time.sleep(poll)
                continue
            if content == "GO":
                print("✔ 收到 GO → 繼續 publish")
                return
            print(f"❌ 批准檔內容 = {content[:60]!r}（非 'GO'）→ abort")
            sys.exit(6)
        if time.time() - start > approve_timeout:
            print(f"❌ 等候批准超時（{approve_timeout}s 無人寫檔）→ abort")
            sys.exit(6)
        time.sleep(poll)


def step_write_drafts(item: dict, finalized: dict) -> str:
    """Step 7a：INSERT drafts + platform_drafts。回傳 draft_id。

    drafts 表只存一筆 canonical row（title / hashtags / full_text 等概要欄位），
    依 ALL_PLATFORMS 順序選 finalized 裡第一個有的平台當資料來源。例如：
      - 三平台齊發 → fb 為 canonical
      - 只發 ig + threads → ig 為 canonical
      - 只發 threads → threads 為 canonical
    platform_drafts 則只 INSERT finalized 含的子集。
    """
    step(f"Step 7a · 寫入 drafts + platform_drafts ({len(finalized)} platforms)")
    persona_version = "news_radar_soul_v1"
    appendix_version = "v2"
    draft_id = hashlib.sha1(
        (item["id"] + persona_version + datetime.now(timezone.utc).isoformat()).encode()
    ).hexdigest()[:16]

    # canonical platform = ALL_PLATFORMS 順序裡第一個有 finalize 的
    canonical_p = next((p for p in ALL_PLATFORMS if p in finalized), None)
    if canonical_p is None:
        fail(5, "step_write_drafts 收到空 finalized；不應該發生")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    v2_fb, full_text_fb, _ = finalized[canonical_p]
    cur.execute(
        """
        INSERT INTO drafts(
            id, news_id, persona_version, title, hashtags,
            image_url, full_text, generated_at, status,
            llm_provider, llm_model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            item["id"],
            persona_version,
            v2_fb.title,
            json.dumps(v2_fb.hashtags or [], ensure_ascii=False),
            item["og_image"],
            full_text_fb,
            datetime.now(timezone.utc).isoformat(),
            "approved",
            "gemini",
            "gemini-2.0-flash-lite",
        ),
    )

    for p, (v2, full_text, _) in finalized.items():
        cur.execute(
            """
            INSERT INTO platform_drafts(
                draft_id, platform, title, body, hashtags, full_text,
                char_count, appendix_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                PLATFORM_TO_DB[p],
                v2.title,
                v2.body,
                json.dumps(v2.hashtags or [], ensure_ascii=False),
                full_text,
                v2.char_count,
                appendix_version,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    conn.commit()
    conn.close()
    print(f"   ↳ draft_id = {draft_id}")
    return draft_id


def step_rehost_image(item: dict) -> dict:
    """Step 6.5 · rehost og_image 到 GitHub raw (--auto-rehost 啟用時)。

    為什麼要做：許多新聞 CDN（Reuters resizer、Bloomberg、WSJ、FT…）對
    Referer / auth token 設防，Meta Graph API 抓不到就會 IG/Threads 全 FAIL
    （FB code 324、IG/Threads code 2207052）。本 step 預先下載 og_image、
    commit + push 到 repo 的 assets/，換成 raw.githubusercontent.com 的
    穩定 URL 再往 publish 走。

    失敗即 abort：任何下載 / commit / push 失敗 → fail(3)。不降級用原始
    URL（那樣下游 publish 一定會失敗，錯誤訊息還更難 debug）。

    Idempotent：assets/{news_id[:16]}.* 已存在 → 跳過下載 + 跳過 push，
    回舊的 raw URL。

    Side effects：
      1. item['og_image'] 就地改寫成新 URL（publish 階段會用）
      2. news_items.og_image_url UPDATE 成新 URL（audit trail）
    """
    step("Step 6.5 · auto-rehost og_image → GitHub raw")
    if not item.get("og_image"):
        fail(3, "--auto-rehost 開啟但沒有 og_image 可 rehost。"
                "請用 --og-image 手動指定，或確認 fetch 路徑拿得到 og:image。")

    # tools/ 已經在 sys.path（PROJECT_ROOT 在最上面 insert），import 即可
    try:
        from tools.image_rehost import rehost_to_github_raw
    except ImportError as e:
        fail(3, f"找不到 tools/image_rehost.py：{e}")

    try:
        new_url = rehost_to_github_raw(
            image_url=item["og_image"],
            news_id=item["id"],
            title_hint=item.get("title") or "",
        )
    except RuntimeError as e:
        fail(3, f"auto-rehost 失敗：{e}")

    print(f"   ↳ 舊 og_image：{item['og_image'][:80]}...")
    print(f"   ↳ 新 og_image：{new_url}")

    # Sync to news_items so Archive / dashboard show the URL we actually used
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE news_items SET og_image_url=? WHERE id=?",
        (new_url, item["id"]),
    )
    conn.commit()
    conn.close()
    print(f"   ↳ news_items.og_image_url 已同步更新")

    item["og_image"] = new_url
    return item


async def step_publish(draft_id: str, item: dict, finalized: dict) -> dict:
    """Step 7b：依 ALL_PLATFORMS 順序 publish 已 finalize 的子集。
    每一筆都 record_publish_result。回傳 {platform: {ok, resp, err}}。

    若 finalized 不含某平台 → 跳過（不 publish_log、不錯誤）。
    """
    selected = [p for p in ALL_PLATFORMS if p in finalized]
    sel_label = " → ".join(p.upper() for p in selected)
    step(f"Step 7b · publish（{sel_label}）")
    # Emergency 目前統一走圖片路徑；影片 URL 若有，也只當備註（Meta 上傳影片需要額外
    # 驗證 mp4 可下載，emergency 不做這件事以免 polling 卡住）
    publish_image_url = item["og_image"]
    results = {}
    for p in selected:
        v2, full_text, _ = finalized[p]
        print(f"\n>>> [{p.upper()}] publishing ({v2.char_count} 字) ...")
        t0 = time.time()
        try:
            if p == "fb":
                r = await publish_to_fb(full_text, image_url=publish_image_url, video_url=None)
            elif p == "threads":
                r = await publish_to_threads(full_text, image_url=publish_image_url, video_url=None)
            else:
                r = await publish_to_ig(full_text, image_url=publish_image_url, video_url=None)
            ok_flag = bool(r.get("success")) if isinstance(r, dict) else False
            if ok_flag:
                print(f"    ✔ 成功，耗時 {time.time() - t0:.1f}s")
                print(f"    回傳：{json.dumps(r, ensure_ascii=False)[:400]}")
                results[p] = {"ok": True, "resp": r, "err": None}
                record_publish_result(draft_id, p, r, None)
            else:
                err = json.dumps(r, ensure_ascii=False)[:500] if isinstance(r, dict) else str(r)
                print(f"    ✗ 失敗(publisher 回報)：{err}")
                results[p] = {"ok": False, "resp": r, "err": err}
                record_publish_result(draft_id, p, r, err)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"    ✗ 失敗(例外)：{err}")
            results[p] = {"ok": False, "resp": None, "err": err}
            record_publish_result(draft_id, p, None, err)
    return results


def record_publish_result(draft_id: str, platform: str, resp: Optional[dict], err: Optional[str]) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    platform_post_id = None
    if resp and isinstance(resp, dict):
        platform_post_id = str(resp.get("id") or resp.get("post_id") or "")
    cur.execute(
        """
        INSERT INTO publish_log(
            draft_id, platform, platform_post_id,
            posted_at, success, error_message
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            PLATFORM_TO_DB[platform],
            platform_post_id,
            datetime.now(timezone.utc).isoformat(),
            1 if err is None else 0,
            err,
        ),
    )
    conn.commit()
    conn.close()


def step_mark_published(draft_id: str, news_id: str, any_success: bool) -> None:
    """Step 7c：至少一個平台成功 → drafts.status='published' + news_items.status='published'。"""
    if not any_success:
        return
    step("Step 7c · 標 drafts + news_items 為 published")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE drafts SET status='published' WHERE id=?", (draft_id,))
    cur.execute("UPDATE news_items SET status='published' WHERE id=?", (news_id,))
    conn.commit()
    conn.close()
    print("   ↳ drafts.status = 'published', news_items.status = 'published'")


# ---------- main ----------------------------------------------------------

async def main_async(args) -> int:
    # 環境檢查：DB、soul 檔存在
    if not DB_PATH.exists():
        fail(2, f"DB 不存在：{DB_PATH}")
    soul = PROJECT_ROOT / "config" / "news_radar_soul.md"
    if not soul.exists():
        fail(2, f"soul 檔不存在：{soul}")

    banner(f"Emergency Oneshot Publisher  ·  URL = {args.url}")
    print(f"AUTO_PUBLISH_THRESHOLD = {AUTO_PUBLISH_THRESHOLD}  (import from run_pipeline.py)")

    # Idempotency：若此 URL 對應的 news_id 已經有 publish_log success → 早退
    news_id = hashlib.sha1(args.url.encode("utf-8")).hexdigest()
    existing = already_published(news_id)
    if existing and not args.force:
        print(f"\n✅ 已發過：news_id={news_id} 對應 draft_id={existing} 有 publish_log success 紀錄。")
        print(f"   不重發（加 --force 強制重走 pipeline；注意：會產生新的 draft_id 與多一筆 publish_log）。")
        # 寫 breadcrumb 讓 .sh 的 push_state --expect-draft 也能 work
        DRAFT_ID_BREADCRUMB.write_text(existing, encoding="utf-8")
        return 0

    # 組 editorial_note
    if args.editorial_note_file:
        note_path = Path(args.editorial_note_file)
        if not note_path.exists():
            fail(2, f"--editorial-note-file 檔不存在：{note_path}")
        editorial_note = note_path.read_text(encoding="utf-8")
        print(f"   ↳ editorial_note 讀自 {note_path}")
    else:
        editorial_note = DEFAULT_EMERGENCY_EDITORIAL_NOTE
        print("   ↳ editorial_note：使用本檔 DEFAULT_EMERGENCY_EDITORIAL_NOTE")

    # Pipeline
    item = await step_fetch_and_extract(
        url=args.url,
        title_override=args.title,
        content_file=Path(args.content_file) if args.content_file else None,
        pdf_path=Path(args.pdf) if args.pdf else None,
        og_image_override=args.og_image,
    )
    score = await step_score(item["title"], item["content"])

    # 解析 --platforms（預設三平台，否則 user 指定子集）
    selected_platforms = parse_platforms_arg(args.platforms)
    print(f"   ↳ 選定平台：{selected_platforms}")

    finalized = await step_compose(
        item["title"], item["content"], item["og_image"], editorial_note,
        platforms=selected_platforms,
    )

    if args.dry_run:
        # Dry-run 也寫 drafts-json，讓 chat orchestrator 可以拿來預覽
        _write_drafts_json(item, finalized)
        banner(f"Dry-run 結束（三份 draft 已印 + 寫到 {DRAFTS_JSON_PATH.relative_to(PROJECT_ROOT)}；"
               f"DB 不寫、API 不打）")
        return 0

    step_yes_gate(
        item=item,
        finalized=finalized,
        approve_file=Path(args.approve_file) if args.approve_file else None,
        approve_timeout=args.approve_timeout,
    )

    # Step 6.5 (optional) · auto-rehost og_image 到 GitHub raw
    # 順序重要：YES gate 之後才 rehost（user 先看 drafts 再決定要不要動用 git push）；
    # step_write_drafts 之前 rehost（drafts.image_url 直接存對的 URL）。
    if args.auto_rehost:
        step_rehost_image(item)

    draft_id = step_write_drafts(item, finalized)
    # 寫 breadcrumb 給 .sh 用（push_state.sh --expect-draft）
    DRAFT_ID_BREADCRUMB.write_text(draft_id, encoding="utf-8")

    results = await step_publish(draft_id, item, finalized)
    any_ok = any(r["ok"] for r in results.values())
    all_ok = all(r["ok"] for r in results.values())
    step_mark_published(draft_id, item["id"], any_ok)

    banner("完成")
    print(f"draft_id = {draft_id}   （寫到 {DRAFT_ID_BREADCRUMB.relative_to(PROJECT_ROOT)} 給 .sh 驗證用）")
    print(f"score    = {score:.3f}")
    for p, r in results.items():
        tag = "OK  " if r["ok"] else "FAIL"
        pid = (r["resp"] or {}).get("id") if r["resp"] else None
        print(f"  [{p.upper():<7}] {tag}  platform_post_id={pid}  err={r.get('err') or '-'}")

    # 印一個機讀行給 .sh grep（避免 scripts 解析上面人類格式）
    print(f"\nDRAFT_ID={draft_id}")

    if all_ok:
        return 0
    return 7  # 至少一平台失敗


def main() -> None:
    ap = argparse.ArgumentParser(
        description="News Radar · Emergency oneshot publisher（繞過 harvester 但不繞過 scorer）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--url", required=True,
                    help="要搶發的 URL (https://...) — news_id = sha1(url) 做 idempotency。"
                         "若用 --pdf/--content-file 當原始素材，URL 仍需填（當作 canonical id 與"
                         "後續 archive 的 link）；原創構思可用 note://... 或 brief://... 之類假 URL。")
    ap.add_argument("--title", default=None,
                    help="手動覆寫標題（預設從 og:title / <title> / 內容第一行 / URL 尾段抓）")
    # 手動素材入口 — 三條路互斥，都沒給時預設 fetch URL
    ap.add_argument("--content-file", default=None,
                    help="不 fetch URL，改讀這份 .md / .txt 當正文。"
                         "用於 URL 抓不到（paywall / 反爬）時你自己貼的文字，"
                         "或原創構思的 brief。")
    ap.add_argument("--pdf", default=None,
                    help="不 fetch URL，改從 PDF 抽文字當正文（pypdf 優先，pdftotext fallback）。"
                         "給『把網頁另存成 PDF』的場景（Reuters / WSJ 反爬時常用）。")
    ap.add_argument("--og-image", default=None,
                    help="圖片 URL 覆寫。fetch 路徑會預設用 HTML 的 og:image；"
                         "--pdf / --content-file 路徑沒有自動來源，這個旗標是你唯一的選項。"
                         "若 IG 要能發，這個務必給（Threads/IG 沒圖 local-reject）。")
    ap.add_argument("--editorial-note-file", default=None,
                    help="覆寫預設 editorial note（讀 .md / .txt 純文字）")
    ap.add_argument("--dry-run", action="store_true",
                    help="跑到 compose 為止，三份 draft 印到 terminal 並寫 JSON，"
                         "不 YES gate 不 publish")
    ap.add_argument("--force", action="store_true",
                    help="即便此 URL 已發過，仍強制重走 pipeline（會多一筆 publish_log）")
    # Mobile-friendly chat gate
    ap.add_argument("--approve-file", default=None,
                    help="手機流：不用 terminal 打 YES，改在此路徑寫 'GO' 讓 Python 偵測。"
                         "flow：你 Dispatch 丟素材 → 我（chat 端）跑 --approve-file → 我把三份 "
                         "draft 從 data/emergency_last_drafts.json 讀出來貼給你看 → 你回 'go' → "
                         "我 echo GO > <approve-file> → Python 繼續 publish。")
    ap.add_argument("--approve-timeout", type=int, default=1800,
                    help="--approve-file 模式的等候上限（秒）；預設 30 分。"
                         "超時自動 abort（exit 6），避免 Python 永遠卡著。")
    ap.add_argument("--auto-rehost", action="store_true",
                    help="YES 之後 publish 之前，自動下載 og_image、commit + push 到 "
                         "assets/、換成 raw.githubusercontent.com 的穩定 URL 再發。"
                         "解決 Reuters / WSJ / Bloomberg 等 CDN 擋 Meta fetcher 的問題"
                         "（FB 324 / IG/Threads 2207052）。失敗即 abort（exit 3）。"
                         "idempotent：assets/{news_id[:16]}.* 已存在就直接用。")
    ap.add_argument("--platforms", default=None,
                    help="逗號分隔指定要發哪幾個平台：fb, ig, threads。"
                         "未指定 → 三平台都發。範例：--platforms threads（只發 Threads）；"
                         "--platforms fb,threads（不發 IG，例如沒圖場景）。"
                         "Note：選 ig 但沒有合法 og_image 會 publish-time fail（IG 強制要圖）。")
    args = ap.parse_args()

    # 互斥檢查：--pdf / --content-file 不能同時給
    if args.pdf and args.content_file:
        print("❌ --pdf 與 --content-file 互斥，只能給一個", file=sys.stderr)
        sys.exit(2)

    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
