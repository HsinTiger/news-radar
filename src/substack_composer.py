"""
News Radar · Substack Composer (Phase 1 — long-form Visionary Analyst)
======================================================================

為什麼獨立於 src/composer.py（三平台社群文）：

    1. 規格根本不同——三平台合計 ~2000 字（FB 700 + IG 700 + TH 480），
       Substack 單篇 1500 字。共用 system_instruction 會互相妥協。
    2. 語氣根本不同——Substack 是「深夜導師」的長文 essay，社群是「白天
       通勤路上 90 秒」的短打報導。
    3. 校驗根本不同——Substack 要嚴格 1400–1600 字檢查 + Anti-Conclusion
       結尾形式檢查 + Metaphor domain 多樣性檢查，這些校驗社群版用不到。

落地介面：
    draft = await compose_substack_article(
        title="...",         # 原始素材標題（不是最終文章標題）
        content="...",       # 原始素材內容
        mode="morning",      # "morning"(type a 深度新聞) or "evening"(type b 獨立選題)
        topic_category="ai_model",  # 對應 topic_taxonomy.py 的 category_id
        editorial_note="",
        recent_metaphor_domains=[],  # 最近 7 篇用過的 metaphor domains（避免重複）
    )
    if draft is None:
        # LLM 兩條路都失敗，呼叫端 skip 並通知 user
        return
    # 用 draft.title / draft.body_markdown / draft.cover_prompt ...

Anti-Conclusion enforcement：本檔額外做後置 regex 檢查，命中黑名單 →
log warning（不自動 reject，但 CLI 端會把這個 warning 顯示給 user）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

from src.llm_brain import call_for_json


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

class SubstackDraft(BaseModel):
    """LLM 結構化輸出 contract for Substack long-form article."""

    title: str = Field(
        ...,
        description="最終文章標題（不是原始素材標題）。需採用 §6 三種 hook 之一。",
        min_length=8,
        max_length=60,
    )
    subtitle: str = Field(
        ...,
        description="副標。作為 Substack 列表頁／email 預覽的閱讀勾子。不可重複 title。",
        min_length=10,
        max_length=80,
    )
    body_markdown: str = Field(
        ...,
        description=(
            "本文正體中文 markdown。1400–1600 字（含全形標點，不含 hashtag）。"
            "使用 §4 Mode A 結構（▉ 為小節錨點）或 Mode B 敘事結構。"
            "Anti-Conclusion：結尾必須是提問／懸念／更深觀察，禁止『總而言之』收尾。"
        ),
    )
    metaphor_domain_used: Literal[
        "signal_processing",
        "music_theory",
        "contrarian_markets",
        "cinematic_pacing",
        "street_culture",
        "architecture_space",
    ] = Field(
        ...,
        description="本篇選用的核心類比 domain。同篇不可重複；最近 7 篇盡量輪換。",
    )
    hook_type: Literal["contrarian_question", "contrarian_reframe", "concrete_punch"] = Field(
        ...,
        description="標題採用的 hook 型態。(a) 反直覺問句 / (b) Contrarian reframe / (c) 具體衝擊收束。",
    )
    cover_image_prompt: str = Field(
        ...,
        description=(
            "封面圖的視覺架構師提示詞（visual_soul.md 風格）。"
            "莫蘭迪／低飽和／手繪 Moleskine 筆記本。"
            "把文章的『動態敘事透鏡』視覺化，不是裝飾。"
        ),
        min_length=30,
    )
    chart_prompt: Optional[str] = Field(
        default=None,
        description=(
            "（可選）機制解構示意圖／數據圖的提示詞，位於文章中段轉折處。"
            "如果文章主題天生沒有可視化的數據／機制，留空。"
        ),
    )
    reading_time_minutes: int = Field(
        ...,
        ge=4,
        le=7,
        description="估算閱讀時長（分鐘）。目標 5 分鐘。",
    )
    open_ending_form: Literal["question", "paradox", "deeper_observation", "silent_hint"] = Field(
        ...,
        description="結尾的開放形式。對應 §7 四種允許句式。",
    )


# --------------------------------------------------------------------------
# Soul loading
# --------------------------------------------------------------------------

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SOUL_PATH = CONFIG_DIR / "substack_soul.md"


def load_substack_soul() -> str:
    """Single source of truth — config/substack_soul.md。"""
    if not SOUL_PATH.exists():
        raise FileNotFoundError(
            f"substack_soul.md not found at {SOUL_PATH}. "
            "Run from news_radar repo root, or check config/ exists."
        )
    return SOUL_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Post-validation (independent of LLM — 球員兼裁判 avoidance)
# --------------------------------------------------------------------------

_BAD_CLOSING_PATTERNS = [
    "總而言之",
    "由此可見",
    "值得我們深思",
    "綜上所述",
    "這對投資人意味著",
    "我們可以期待",
]

_BAD_RHETORICAL_PATTERNS = [
    "本質上是一種",
    "在資本的顯微鏡下",
]

_AI_FILLER_WORDS = ["其實", "很清楚", "很簡單"]


def _count_chinese_chars(text: str) -> int:
    """數中文字＋全形標點。半形字符／英文不計。"""
    return sum(
        1
        for ch in text
        if "一" <= ch <= "鿿"
        or ch in "，。！？；：「」『』（）、—…"
    )


def audit_substack_draft(draft: SubstackDraft) -> List[str]:
    """Return list of soft-fail warnings (empty = clean).

    呼叫端決定要 hard-reject 還是只 log warning。CLI 預設只 log。
    """
    warnings: List[str] = []
    body = draft.body_markdown

    # 1. 字數
    n = _count_chinese_chars(body)
    if n < 1400:
        warnings.append(f"[字數低於下限] {n} 字 < 1400。需擴寫。")
    elif n > 1600:
        warnings.append(f"[字數超過上限] {n} 字 > 1600。需精煉。")

    # 2. Anti-Conclusion 收尾
    last_para = body.strip().split("\n")[-1] if body.strip() else ""
    for pat in _BAD_CLOSING_PATTERNS:
        if pat in last_para[-200:]:
            warnings.append(
                f"[收尾 AI 味] 末段命中黑名單『{pat}』。改寫為提問／懸念／更深觀察。"
            )

    # 3. 全文 rhetorical 黑名單
    for pat in _BAD_RHETORICAL_PATTERNS:
        if pat in body:
            warnings.append(f"[修辭黑名單] 命中『{pat}』。重寫該段。")

    # 4. 填充詞濫用（單篇 ≥ 2 次）
    for filler in _AI_FILLER_WORDS:
        c = body.count(filler)
        if c >= 2:
            warnings.append(f"[填充詞濫用] 『{filler}』出現 {c} 次 (≥ 2)。重寫")

    # 5. 破折號 — 最多 1 次
    dash_count = body.count("——") + body.count("—")
    if dash_count > 2:
        warnings.append(f"[破折號濫用] '—' 出現 {dash_count} 次 (上限 1)。改成句號／逗號／重寫。")

    # 6. 「不是 X、是 Y」對仗
    if re.search(r"不是.{1,15}[、，,]\s*[而是是].{1,15}", body):
        warnings.append("[對仗濫用] 發現『不是 X、是 Y』對仗句。改寫成『而』+ 具體脈絡。")

    # 7. 同篇重複用同 domain（透過 cover_image_prompt 偵測）
    #   這條由 caller 在 history 比對更準，這裡略

    return warnings


# --------------------------------------------------------------------------
# Prompt builder
# --------------------------------------------------------------------------

def _build_system_instruction(soul: str) -> str:
    return (
        "你是 News Radar 的 Substack 長文寫手——Visionary Analyst。\n"
        "輸出單篇 1500 字精煉長文，採用『硬商業邏輯 × 暖哲學靈魂』。\n"
        "\n"
        "=== 唯一靈魂源（必須完整內化）===\n"
        f"{soul}\n"
        "\n"
        "=== 重申最高優先級規則 ===\n"
        "1. §0 品牌宣言：替讀者咀嚼。讀完累 → 重寫。\n"
        "2. §2.6 Anti-Conclusion：結尾禁『總而言之』，必須提問／懸念。\n"
        "3. §3 Metaphor Diversification：絕不再用熱力學／建築學／生物演化。\n"
        "   從 6 個 domain 抽 1 個（不重複近 7 篇用過的）。\n"
        "4. §5 黑名單：「不是 X、是 Y」「○○感」「穩／撐／懂」一律不准。\n"
        "5. §6 字數硬上限：1400–1600 字（含全形標點）。超過必砍。\n"
        "6. §8 五秒拍片測試：每段抽象論述後必須有具體場景錨點。\n"
        "7. §9 完美 = AI：句子長短刻意不平均、留一個沒講完的暗示。\n"
    )


def _build_user_prompt(
    *,
    raw_title: str,
    raw_content: str,
    mode: str,
    topic_category: str,
    editorial_note: str,
    recent_metaphor_domains: List[str],
) -> str:
    avoid = (
        f"最近 7 篇用過的 metaphor domain（請避開）：{recent_metaphor_domains}"
        if recent_metaphor_domains
        else "目前無歷史 domain，自由選擇。"
    )
    mode_hint = {
        "morning": (
            "【Mode: morning / Type A 深度新聞】"
            "這是從 News Radar 24 小時候選池抽出的高分新聞。"
            "你的任務是把這則『短打用過』的新聞，**轉譯**成有商業洞察的長文："
            "不是再寫一遍社群版，而是把深層的商業／人性意涵挖出來。"
        ),
        "evening": (
            "【Mode: evening / Type B 獨立選題】"
            "這是一個獨立的選題（書、Podcast、概念、Hsin 的私房題目）。"
            "不必受限於『最新新聞』的時效，可以拉到更遠的時間軸與哲學層次。"
        ),
    }.get(mode, "")

    return (
        f"{mode_hint}\n\n"
        f"=== 編輯指令 ===\n{editorial_note or '按既有靈魂風格自由發揮。'}\n\n"
        f"=== 原始素材 ===\n標題：{raw_title}\n本文：{raw_content[:6000]}\n\n"
        f"=== 主題分類 ===\n{topic_category}\n\n"
        f"=== 多樣性提醒 ===\n{avoid}\n\n"
        # === 2026-05-12 research permission（Claude CLI 限定）===
        "=== 你可以用網路工具做事實查核（強烈建議）===\n"
        "本任務在 Claude CLI 環境執行，你**有 `WebSearch` 跟 `WebFetch` 工具**。\n"
        "下列三類資訊**務必查證後再寫**（隨手 1-3 個工具 call 即可，不要過度研究）：\n"
        "  (a) 具體金額／百分比／市佔率／融資輪／市場規模——例：『OpenAI 估值 5000 億美元』、\n"
        "      『台積電 2 奈米毛利率』。寫之前去 Reuters / Bloomberg / 公司 IR 站確認。\n"
        "  (b) 具體日期／時間線——例：『2026 Q3 法說會』、『5 月初發布』。對不到具體日期 → 寫『近期』別瞎掰。\n"
        "  (c) 人名 + 職稱——例：『CFO Sarah Friar』、『創辦人 Dario Amodei』。\n"
        "**不需要查的**：自家論述、比喻、§3 metaphor domain 的類比、抽象推論。\n"
        "**禁止幻覺背書**：「據業內傳出」「業界專家認為」一律不可寫——沒查到具體來源就不要寫。\n"
        "查證完直接把該事實寫進 body_markdown（**不要**在 body 裡列出『資料來源』或附 URL，\n"
        "這是 essay 不是 footnote heavy 學術文章）。\n\n"
        "=== 輸出格式：直接回一個 JSON object，欄位如下（缺一不可）===\n"
        "{\n"
        '  "title": "...",                  // 8-60 字。用 §6 三種 hook 之一，禁新聞稿陳述式。\n'
        '  "subtitle": "...",               // 10-80 字。不可重複 title。Substack 列表頁勾子。\n'
        '  "body_markdown": "...",          // 1400-1600 中文字（含全形標點，不含 hashtag）。\n'
        "                                   //   Mode A：▉ 小節錨點；Mode B：敘事弧。\n"
        "                                   //   結尾禁『總而言之』；必為提問/懸念/更深觀察。\n"
        '  "metaphor_domain_used": "...",   // ENUM，必為以下其一：\n'
        '                                   //     "signal_processing" | "music_theory" |\n'
        '                                   //     "contrarian_markets" | "cinematic_pacing" |\n'
        '                                   //     "street_culture" | "architecture_space"\n'
        "                                   //   絕不可用熱力學/化學/生物演化。\n"
        '  "hook_type": "...",              // ENUM，必為以下其一：\n'
        '                                   //     "contrarian_question" | "contrarian_reframe" |\n'
        '                                   //     "concrete_punch"\n'
        '  "cover_image_prompt": "...",     // ≥ 30 字。莫蘭迪/低飽和/手繪 Moleskine。\n'
        '  "chart_prompt": null,            // 可選；若無數據可視化，填 null。\n'
        '  "reading_time_minutes": 5,       // 整數 4-7。目標 5。\n'
        '  "open_ending_form": "..."        // ENUM，必為以下其一：\n'
        '                                   //     "question" | "paradox" |\n'
        '                                   //     "deeper_observation" | "silent_hint"\n'
        "}\n"
        "不要回 markdown fence、不要加註解、不要加任何 JSON 以外的文字。"
    )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

# Backend selection (2026-05-12 architecture shift):
#   "claude_cli"  → ONLY Claude CLI; no Gemini fallback. **預設、推薦。**
#                   理由：Gemini 2.5-flash-lite 實測寫長文時不守 1500 字 hard cap
#                   且爆破折號 / 「不是 X 是 Y」對仗；Claude CLI 對 prompt 約束
#                   遵守度高很多 + 帶 WebSearch / WebFetch 工具可以查網路數據。
#   "default"     → 維持 llm_brain 既有兩段式行為（Claude 主 → Gemini 備）。
#                   緊急逃生口；不建議長期使用。
#
#   Override via env: SUBSTACK_COMPOSER_BACKEND=claude_cli|default
SUBSTACK_BACKEND = os.getenv("SUBSTACK_COMPOSER_BACKEND", "claude_cli").lower()


def _resolve_backends() -> Optional[tuple]:
    """Map env-var string → call_for_json `backends` tuple."""
    if SUBSTACK_BACKEND == "claude_cli":
        return ("claude_cli",)
    if SUBSTACK_BACKEND in ("default", "auto", "fallback"):
        return None  # let call_for_json use its default chain
    # Unknown value → loud warning, fall back to claude_cli
    print(
        f"[SubstackComposer] ⚠️ Unknown SUBSTACK_COMPOSER_BACKEND={SUBSTACK_BACKEND!r}; "
        f"defaulting to claude_cli."
    )
    return ("claude_cli",)


async def compose_substack_article(
    *,
    title: str,
    content: str,
    mode: Literal["morning", "evening"] = "morning",
    topic_category: str = "other",
    editorial_note: str = "",
    recent_metaphor_domains: Optional[List[str]] = None,
    temperature: float = 0.4,
) -> Optional[SubstackDraft]:
    """產出單篇 Substack 長文草稿。

    Architecture (2026-05-12):
      - 預設只走 Claude CLI（SUBSTACK_COMPOSER_BACKEND=claude_cli）。Claude CLI
        在 -p mode 預設啟用 WebSearch / WebFetch / Read 等工具；prompt 內鼓勵
        composer 在寫關鍵數據前先做網路查證。
      - Gemini 不再是 fallback（除非顯式設 SUBSTACK_COMPOSER_BACKEND=default）。
        理由見模組頂部註解。

    Returns:
        SubstackDraft on success.
        None on LLM failure (caller 必須 skip 並 notify user).
    """
    soul = load_substack_soul()
    system = _build_system_instruction(soul)
    prompt = _build_user_prompt(
        raw_title=title,
        raw_content=content,
        mode=mode,
        topic_category=topic_category,
        editorial_note=editorial_note,
        recent_metaphor_domains=recent_metaphor_domains or [],
    )

    backends = _resolve_backends()
    result = await call_for_json(
        system=system,
        prompt=prompt,
        response_model=SubstackDraft,
        temperature=temperature,
        timeout_s=480,  # 長文寫作 + 可能的 web research timeout 拉到 8 分鐘
        backends=backends,
    )

    if result.data is None:
        print(
            f"[SubstackComposer] ❌ LLM 路徑失敗 (backends={backends}) → caller 請 skip。"
            f" raw_error={result.raw_error}"
        )
        return None

    print(f"[SubstackComposer] ℹ️ provider={result.provider}")

    return result.data


if __name__ == "__main__":
    import asyncio

    async def _smoke():
        d = await compose_substack_article(
            title="Anthropic Claude Mythos 限制 40 家夥伴接入",
            content=(
                "Anthropic 在 2026 年 5 月初宣布旗下最新模型 Claude Mythos "
                "將僅限 40 家企業夥伴接入，公開 API 暫不開放。內部備忘錄"
                "指出原因是『模型行為尚未在開放環境中充分對齊』，但業內"
                "推測這也與 Anthropic 近期商業策略轉向 enterprise 有關。"
            ),
            mode="morning",
            topic_category="ai_model",
            editorial_note="挑戰『限制 = 安全』的官方敘事，挖商業護城河的真相。",
        )
        if not d:
            print("FAIL")
            return
        print(f"TITLE: {d.title}")
        print(f"SUBTITLE: {d.subtitle}")
        print(f"METAPHOR: {d.metaphor_domain_used}")
        print(f"HOOK: {d.hook_type}")
        print(f"OPEN-ENDING: {d.open_ending_form}")
        print(f"BODY LEN: {_count_chinese_chars(d.body_markdown)} 字")
        print(f"COVER PROMPT: {d.cover_image_prompt[:120]}...")
        warnings = audit_substack_draft(d)
        if warnings:
            print("\n⚠️ AUDIT WARNINGS:")
            for w in warnings:
                print(f"  - {w}")
        else:
            print("\n✅ Audit clean")

    asyncio.run(_smoke())
