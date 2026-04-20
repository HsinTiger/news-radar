"""
News Radar · Composer 模組（Milestone 3.1：三平台各自獨立寫稿）
功能：
- 一次 LLM call 產出 MultiPlatformDraft（fb / ig / threads 三版）。
- system_instruction = news_radar_soul.md + 三份 platform appendix 拼接。
- LLM 負責把字數控制在各平台合規範圍；Python 端做最後的安全網校驗，
  若 Threads 超過 500 字元，會先嘗試刪尾端 hashtag、再刪最後一段；
  極端情況（仍超限）會把該變體標記為 INVALID，publisher 拒絕發布，
  而非像舊版那樣 `text[:500]` 暴力截斷。

=== 大原則：API 配額／任何原因失效時，當下主導的 agent 自己充當大腦 ===
（Phase 8.11 established 2026-04-19；Phase 8.13 generalised 2026-04-19）

任何 stage（composer / scorer / reflector / …）只要偵測到所依賴的 API call
額度不夠、被 rate limit、回 500、或任何原因 fail，**不要重試同一條 API 通路**，
而是由「當下正在執行此任務串的主 agent」直接充當大腦代為完成這一步。

**主 agent 未必是 Claude**。可能是 Claude（Cowork / Claude Code 端）、
可能是 GPT（使用者在另一個客戶端發起）、可能是另一個 Gemini 會話。原則以
**角色**定義：誰是當下這一串任務的發起者與主導者，誰就是該 fallback 的大腦。
這讓本專案在任何主 agent 下都可運行，而不是把能力耦合在單一廠商。

落地介面（呼叫端責任）：
  1. 每個會呼叫外部 LLM 的 stage，失敗時必須 print 一條清楚的 fallback 訊號
     （錯誤碼 / 原始輸入 / 平台規格 / editorial_note），讓主 agent 能直接接手。
  2. 呼叫端提供一條「結構化手稿 fallback」入口（例如 --from-json），接主 agent
     手寫的 JSON（格式見 data/first_batch_manual_drafts.json），跳過 LLM 步驟、
     仍走 finalize_variant 的字數／hashtag 校驗與 publish 流程。
  3. 主 agent 手寫時必須沿用同一份 soul / appendix / editorial_note（從檔案 Read，
     不憑記憶），以確保跨主 agent 產出一致。

這條原則的用意：
- API 額度或故障都不應該擋住已經排好的發文。
- 主 agent 的品質在首發／重要題目上通常勝過 flash-lite 之類的輕量模型。
- 設計上不預設「誰才是合法 composer」，使本專案可跨主 agent 協作。
- 擴展：未來要加 --llm=claude|gpt|gemini 自動切路由時，路由層只是把「當下主
  agent」從手動角色變成自動選擇，上層介面（JSON schema + finalize_variant）
  不需要動。

=== Threads 主題標籤 (topic pill) 原則 ===
（Phase 8.12 established, 2026-04-19）

Threads 會把貼文的 hashtags[0] 自動升級為貼文頂部的『主題標籤 pill』，並在正文中
吞掉它的 `#` 字元；這是 Threads 的發現性 (discoverability) 特性，不是 bug。
因此 PlatformVariant 新增了 `primary_topic_tag` 欄位，和其餘 `hashtags` 分離，
finalize_variant 會把 primary_topic_tag 自動放到 hashtags 最前面、並去重。
好處：reflector / scorer 之後可以單獨追蹤『哪個 topic tag 帶來最多流量』，
而不會被尾端補充標籤稀釋。FB / IG 不依賴此機制，但仍建議填寫以便跨平台比對。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import List, Optional, Tuple, Dict

from dotenv import load_dotenv

from src.llm_brain import call_for_json
from src.schema import MultiPlatformDraft, PlatformVariant

# 定位 .env 與設定
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOUL_PATH = PROJECT_ROOT / "config" / "news_radar_soul.md"
PLATFORMS_DIR = PROJECT_ROOT / "config" / "platforms"

PLATFORM_FILES = {
    "fb": PLATFORMS_DIR / "fb_v2.md",
    "ig": PLATFORMS_DIR / "ig_v2.md",
    "threads": PLATFORMS_DIR / "threads_v2.md",
}

# 平台字數硬約束（字元計算，與 Meta API 對齊）
PLATFORM_LIMITS = {
    "fb": 1000,
    "ig": 2000,
    "threads": 500,
}

# 平台 hashtag 建議數量（上下限都是寬鬆指引）
PLATFORM_HASHTAG_RANGE = {
    "fb": (3, 5),
    "ig": (5, 10),
    "threads": (1, 3),
}


# ---------- Soul 讀取 ----------
# Phase 8.19 起，LLM 呼叫改由 src.llm_brain 負責，不再在 composer.py
# 管理 Gemini client。保留 get_gemini_client 空殼以防下游遺留 import，
# 但實際邏輯已下沉到 llm_brain.call_for_json。

def get_gemini_client():
    raise RuntimeError(
        "get_gemini_client() 已於 Phase 8.19 移除：LLM 呼叫請改用 src.llm_brain.call_for_json。"
    )


def _read_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_soul_bundle() -> Tuple[str, Dict[str, str]]:
    """回傳 (main_soul_text, {fb: appendix_text, ig: ..., threads: ...})。"""
    main = _read_file(SOUL_PATH) or "你是一位資深科技分析師，語氣精簡有力。"
    appendices = {k: _read_file(v) for k, v in PLATFORM_FILES.items()}
    return main, appendices


# ---------- 組裝 & 校驗 ----------

def assemble_full_text(variant: PlatformVariant, platform: str = "fb") -> str:
    """把 PlatformVariant 組裝成最終發文字串。
    - title 當作第一行 / hook。
    - body 接著，段落之間空一行。
    - hashtags 置於末尾。
    - [NEW] Threads 專屬：在 hashtags 前增加額外空行，確保呼吸感。
    """
    parts: List[str] = []
    if variant.title:
        parts.append(variant.title.strip())
    if variant.body:
        parts.append(variant.body.strip())
    
    if variant.hashtags:
        cleaned = []
        body_lower = (variant.body or "").lower()
        for h in variant.hashtags:
            h2 = (h or "").strip()
            if not h2:
                continue
            if not h2.startswith("#"):
                h2 = "#" + h2
            if h2.lower() not in body_lower:
                cleaned.append(h2)
        
        if cleaned:
            hashtag_str = " ".join(cleaned)
            # 針對 Threads，在標籤前加上一個空白字元，避免 API 吞掉第一個 '#'
            if platform == "threads":
                parts.append(" " + hashtag_str)
            else:
                parts.append(hashtag_str)

    return "\n\n".join(parts).strip().replace("\n\n\n", "\n\n")


def _squeeze_to_limit(variant: PlatformVariant, limit: int, platform: str) -> Tuple[PlatformVariant, bool]:
    """若超出字數上限，依序策略縮減。"""
    full = assemble_full_text(variant, platform)
    if len(full) <= limit:
        return variant, True

    # 1) 刪尾端 hashtag
    hashtags = list(variant.hashtags or [])
    while hashtags and len(assemble_full_text(variant.model_copy(update={"hashtags": hashtags}), platform)) > limit:
        hashtags.pop()
    if len(assemble_full_text(variant.model_copy(update={"hashtags": hashtags}), platform)) <= limit:
        new = variant.model_copy(update={"hashtags": hashtags})
        return new, True

    # 2) 刪 body 最後一段
    paragraphs = [p for p in (variant.body or "").split("\n\n") if p.strip()]
    while paragraphs:
        paragraphs.pop()
        candidate = variant.model_copy(update={
            "hashtags": hashtags,
            "body": "\n\n".join(paragraphs),
        })
        if len(assemble_full_text(candidate, platform)) <= limit:
            return candidate, True

    return variant.model_copy(update={"hashtags": hashtags, "body": variant.body or ""}), False


def _normalize_tag(raw: str) -> str:
    """把任意輸入轉成合法 hashtag 形式（以單一 `#` 開頭、無內部空白）。
    輸入為空或僅為 `#` 時回傳空字串。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # 允許使用者在 JSON 手稿中連續多個 `#`，先剝光再補一個
    s = "#" + s.lstrip("#").replace(" ", "")
    return "" if s == "#" else s


def _validate_and_fix_hashtags(variant: PlatformVariant) -> PlatformVariant:
    """修掉常見 LLM / 手稿臭蟲，並把 primary_topic_tag 併入 hashtags 最前面：
    - 首項 hashtag 漏掉 `#`
    - hashtag 內含空白；若有，拆成多個
    - primary_topic_tag 必定出現在 hashtags[0]，且 hashtags 不重複
    """
    cleaned: List[str] = []
    seen: set = set()

    def _push(tag: str) -> None:
        t = _normalize_tag(tag)
        if not t:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        cleaned.append(t)

    # 1) primary_topic_tag 永遠排第一
    primary = _normalize_tag(variant.primary_topic_tag or "")
    if primary:
        _push(primary)

    # 2) 再把 hashtags 依序補齊
    for raw in variant.hashtags or []:
        # 容忍使用者用空白把多個 hashtag 連在一起
        for piece in (raw or "").split():
            _push(piece)

    return variant.model_copy(update={
        "hashtags": cleaned,
        "primary_topic_tag": primary or None,
    })


def finalize_variant(variant: PlatformVariant, platform: str) -> Tuple[PlatformVariant, str, bool]:
    """統一出口：修 hashtag、壓字數、組 full_text、回傳 (variant, full_text, ok)。"""
    fixed = _validate_and_fix_hashtags(variant)
    limit = PLATFORM_LIMITS.get(platform, 500)
    squeezed, ok = _squeeze_to_limit(fixed, limit, platform)
    full_text = assemble_full_text(squeezed, platform)
    final = squeezed.model_copy(update={"char_count": len(full_text)})
    return final, full_text, ok


# ---------- 主 LLM call ----------

def _build_system_instruction(soul: str, appendices: dict) -> str:
    parts = [
        "你是 News Radar 的多平台寫手，必須同時扮演 FB / IG / Threads 三個平台的「當地寫手」。",
        "三篇內容必須以同一則新聞為源，但語氣、字數、hashtag 全部按各自平台規格調整。",
        "絕對不要產生中途被截斷的句子或孤立的「#」符號。",
        "",
        "=== 目標受眾 ===",
        "對『科技、時事、最前沿國際動態與商業資訊』有興趣的讀者；年齡層不限。",
        "撰寫時的前設：讀者對科技與商業有基本熟悉度（不需要把 AI / API / 雲端這類詞從零開始解釋），",
        "但不假設讀者是產業從業者。術語要轉譯、數據要有地基、結論要能被『通勤路上 90 秒內讀完』帶走。",
        "",
        "=== 核心靈魂（三平台共用）===",
        soul,
        "",
        "=== FB Appendix ===",
        appendices.get("fb", ""),
        "",
        "=== IG Appendix ===",
        appendices.get("ig", ""),
        "",
        "=== Threads Appendix ===",
        appendices.get("threads", ""),
        "",
        "=== 輸出硬性要求 ===",
        "🔥🔥🔥 [最高優先級] 所有產出內容（包含正文、標題與標籤）必須全數翻譯並轉化為流暢的『繁體中文 (zh-TW)』，絕對禁止原文照貼或中英夾雜。 🔥🔥🔥",
        f"- Threads 變體：總字元 ≤ {PLATFORM_LIMITS['threads']}，目標 420–480。hashtags 1–3 個。",
        f"- FB 變體：總字元 ≤ {PLATFORM_LIMITS['fb']}，目標 700–900。hashtags 3–5 個。",
        f"- IG 變體：總字元 ≤ {PLATFORM_LIMITS['ig']}，目標 600–900。hashtags 5–10 個。",
        "- 撰寫規範：",
        "  1. 標題與第一句務必包含客觀事實或核心數據。",
        "  2. 寫作流暢度必須達到『資深科技記者播報』的連貫水準，資訊必須自行咀嚼吸收後陳述。",
        "  3. 絕對禁止使用『這說明了兩件事』、『拆解兩層邏輯』等網路碎文的八股條列結構。",
        "  4. 必須在文體中保持冷靜且具同理心的第三人稱敘事，提供客觀深度的商業洞察，絕對避免使用『我的反思』或『我認為』這類主觀且制式化的寫法。",
        "- 每個 hashtag 都必須以 `#` 開頭，不得有空格。",
        "- 三個變體的 char_count 欄位必須回填實際字元數（含空白、標點、hashtag）。",
        "- 每個變體都必須填 `primary_topic_tag`：選一個『最能代表本貼文被發現／分類』的 hashtag",
        "  （例：新產品發表選產品代號；政策議題選政策縮寫；地緣主題選國家／區域詞）。",
        "  這個欄位對 Threads 尤其重要——Threads 會自動把 hashtags[0] 升級成貼文頂部的",
        "  『主題標籤 (topic pill)』，並在正文中吞掉它的 `#`。所以 primary_topic_tag 必須是",
        "  你最希望『點進這個 pill 會串起的分類頁』指向的那個詞。",
        "  primary_topic_tag 也會被放進 hashtags 的第一個位置；若 hashtags 裡已有同名項，系統會自動去重。",
    ]
    return "\n".join(parts)


DEFAULT_COMPOSER_MODEL = os.getenv("NEWS_RADAR_COMPOSER_MODEL", "gemini-2.0-flash-lite")


async def compose_multi_platform(
    title: str,
    content: str,
    og_image: Optional[str] = None,
    editorial_note: str = "",
    platforms: List[str] = ["fb", "ig", "threads"],
    model: Optional[str] = None,
) -> Optional[MultiPlatformDraft]:
    """產出多個平台的版本。

    Phase 8.19：改由 src.llm_brain.call_for_json 統一處理 LLM 路徑。
        1. Gemini primary（失敗時自動 fallback Claude CLI）
        2. Claude CLI fallback
        3. 兩條路都失敗 → 回 None；呼叫端必須 skip（不再塞 emergency template）

    Args:
        model: 覆寫預設 Gemini 模型名稱；None 時用 DEFAULT_COMPOSER_MODEL。
    """
    main_soul, appendices = load_soul_bundle()

    # 只保留被選中的平台指令
    filtered_appendices = {k: v for k, v in appendices.items() if k in platforms}
    system_instruction = _build_system_instruction(main_soul, filtered_appendices)

    # 組裝 Prompt
    prompt = f"""
以下是今日要處理的新聞，以及初審編輯 (Reviewer Agent) 指定的戰略方向。

=== 初審編輯指令 (Editorial Mandate) ===
{editorial_note or '按既有靈魂風格自由發揮，但需維持強數據與深度分析。'}

=== 新聞詳情 ===
標題: {title}
本文/摘要: {content[:4000]}
原始圖片網址: {og_image or '無'}

請直接輸出 MultiPlatformDraft 的 JSON，包含 {', '.join(platforms)} 的變體。
(欄位結構：{{
  "fb": {{...PlatformVariant}} or null,
  "ig": {{...PlatformVariant}} or null,
  "threads": {{...PlatformVariant}} or null,
  "image_url": "..."
}}
每個 PlatformVariant 必要欄位：title, body, hashtags (list of str with #), primary_topic_tag, char_count)
"""

    chosen_model = model or DEFAULT_COMPOSER_MODEL

    result = await call_for_json(
        system=system_instruction,
        prompt=prompt,
        response_model=MultiPlatformDraft,
        gemini_model=chosen_model,
        temperature=0.3,  # 寫作需要一點變化
        timeout_s=240,    # Claude CLI 寫長文需要較長 timeout
    )

    if result.data is None:
        print(f"[Composer] ❌ 所有 LLM 路徑皆失敗 → 呼叫端請 skip。raw_error={result.raw_error}")
        return None

    if result.provider != "gemini":
        print(f"[Composer] ℹ️ 撰寫來自 fallback 提供者：{result.provider}")

    draft = result.data
    # 兜底：若 LLM 沒填 image_url 但我們有網址，沿用
    if not draft.image_url:
        draft.image_url = og_image

    return draft


# ---------- 保留舊 API 兼容性（供尚未升級的呼叫端用）----------
# 舊 run_pipeline.py 可能會呼叫 compose_draft；我們保留一個簡單 wrapper，
# 直接取 FB 變體作為主版本。若新 pipeline 上線，這個函式可以移除。

async def compose_draft(title: str, content: str, original_og_image: Optional[str] = None):
    bundle = await compose_multi_platform(title, content, original_og_image)
    if not bundle:
        return None
    return bundle  # 讓 caller 自己 .fb / .ig / .threads 取用


if __name__ == "__main__":
    # 簡單測試
    async def _run():
        title = "Anthropic 發表 Claude Opus 4.7，SWE-bench 創新高"
        body = "Anthropic 今日官方公告推出 Claude Opus 4.7 …（略）"
        draft = await compose_multi_platform(title, body)
        if not draft:
            print("generate fail")
            return
        for p in ("fb", "ig", "threads"):
            v = getattr(draft, p)
            v2, full, ok = finalize_variant(v, p)
            print(f"--- {p.upper()} (ok={ok}) {v2.char_count} 字 ---")
            print(full)
            print()

    asyncio.run(_run())
