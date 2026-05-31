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

from pydantic import BaseModel, Field, field_validator

from src.llm_brain import call_for_json


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

class SubstackDraft(BaseModel):
    """LLM 結構化輸出 contract for Substack long-form article."""

    title: str = Field(
        ...,
        description="最終文章標題（不是原始素材標題）。從 §6 標題公式庫 9 種原型挑一個；盡量含具體錨點(公司/數字/事件)+隱喻或反直覺翻轉。",
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
            "本文正體中文 markdown。目標字數由 env SUBSTACK_WORD_CAP 控制（預設"
            " 2000–3500 字，含全形標點不含 hashtag）。長文允許多層展開、"
            "deep-research 結果可以攤開細節，但仍受反 AI 味與 Anti-Conclusion 規範。"
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

    # 2026-05-30: truncate overlong title/subtitle BEFORE the max_length check, so a
    # full ~8-min generation isn't thrown away just because the model overshot the
    # title/subtitle by a few chars (it's not retryable, so rejection = wasted draft).
    @field_validator("title", "subtitle", mode="before")
    @classmethod
    def _truncate_headline(cls, v, info):
        if isinstance(v, str):
            cap = {"title": 60, "subtitle": 80}.get(info.field_name)
            if cap and len(v) > cap:
                return v[:cap].rstrip("，、。；：「」『』（）()【】 　")
        return v


# --------------------------------------------------------------------------
# Soul loading
# --------------------------------------------------------------------------

CONFIG_DIR = Path(__file__).resolve().parent / "config"  # substack/config (2026-05-30 moved)
SOUL_PATH = CONFIG_DIR / "substack_soul.md"
VOICE_ANCHOR_PATH = CONFIG_DIR / "substack_voice_anchor.md"


def load_substack_soul() -> str:
    """Single source of truth — config/substack_soul.md。"""
    if not SOUL_PATH.exists():
        raise FileNotFoundError(
            f"substack_soul.md not found at {SOUL_PATH}. "
            "Run from news_radar repo root, or check config/ exists."
        )
    return SOUL_PATH.read_text(encoding="utf-8")


def load_voice_anchor() -> str:
    """Voice exemplars distilled from Hsin's best articles. Teaches the model the
    TARGET voice by example (not by rule) — the lever against stiff / AI-sounding
    prose. Optional: returns '' if the file is absent."""
    try:
        return VOICE_ANCHOR_PATH.read_text(encoding="utf-8") if VOICE_ANCHOR_PATH.exists() else ""
    except Exception:
        return ""


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

# 大陸用法 banned list — 2026-05-12 加入。
# 命中時為 warning（非 hard reject）。完整對應表見 config/substack_soul.md §11。
# 排序：(found_term, suggested_replacement, category)
_MAINLAND_TERMS = [
    # 人名
    ("特朗普",   "川普",            "人名"),
    ("奧巴馬",   "歐巴馬",          "人名"),
    ("默克爾",   "梅克爾",          "人名"),
    ("扎克伯格", "祖克柏",          "人名"),
    ("普京",     "普丁",            "人名"),
    ("澤連斯基", "澤倫斯基",        "人名"),
    ("內塔尼亞胡","納坦雅胡",       "人名"),
    ("默多克",   "梅鐸",            "人名"),
    ("朔爾茨",   "蕭茲",            "人名"),
    ("馬克龍",   "馬克宏",          "人名"),
    # 資訊／網路／軟體 (高 priority)
    ("互聯網",   "網際網路／網路",  "資訊"),
    ("視頻",     "影片",            "資訊"),
    ("軟件",     "軟體",            "資訊"),
    ("硬件",     "硬體",            "資訊"),
    ("屏幕",     "螢幕",            "資訊"),
    ("服務器",   "伺服器",          "資訊"),
    ("數據庫",   "資料庫",          "資訊"),
    ("文件夾",   "資料夾",          "資訊"),
    ("程序員",   "工程師",          "資訊"),
    ("算法",     "演算法",          "資訊"),
    ("內存",     "記憶體",          "資訊"),
    ("帶寬",     "頻寬",            "資訊"),
    ("接口",     "介面",            "資訊"),
    ("模塊",     "模組",            "資訊"),
    ("鏈接",     "連結",            "資訊"),
    ("點贊",     "按讚",            "資訊"),
    ("登錄",     "登入",            "資訊"),
    ("賬號",     "帳號",            "資訊"),
    ("賬戶",     "帳戶",            "資訊"),
    ("默認",     "預設",            "資訊"),
    ("缺省",     "預設",            "資訊"),
    ("設置",     "設定",            "資訊"),
    ("兼容",     "相容",            "資訊"),
    ("並發",     "並行",            "資訊"),
    ("性能",     "效能",            "資訊"),
    ("反饋",     "回饋",            "資訊"),
    ("標簽",     "標籤",            "資訊"),
    ("在線",     "線上",            "資訊"),
    ("黑客",     "駭客",            "資訊"),
    # 商業／市場
    ("創始人",   "創辦人",          "商業"),
    ("短信",     "簡訊",            "商業"),
    # 度量
    ("千米",     "公里",            "度量"),
    ("厘米",     "公分",            "度量"),
    ("千克",     "公斤",            "度量"),
]


def autofix_mainland_terms(draft: "SubstackDraft") -> List[str]:
    """Deterministically replace unambiguous mainland terms in title/subtitle/body.

    2026-05-30 (Optimization B): the full 大陸→台灣 lookup table used to live in
    substack_soul.md §11 and was shipped to the LLM on every call (~hundreds of
    tokens of pure reference). That table is now enforced here at zero token cost
    so the soul prompt can drop it.

    Split rule:
      - replacement WITHOUT "／" → unambiguous → auto-replace here.
      - replacement WITH "／" (e.g. 互聯網→網際網路／網路) → left untouched;
        audit_substack_draft still WARNS so a human/LLM picks the right one.
      - genuinely context-sensitive terms (數據/質量/智能/移動/用戶) are deliberately
        absent from _MAINLAND_TERMS, so they are never touched.

    Mutates `draft` in place and returns a list of human-readable fix messages.
    """
    import re as _re

    fixes: List[str] = []
    for found, repl, category in _MAINLAND_TERMS:
        if "／" in repl:
            continue  # ambiguous — leave for audit warning
        # When the mainland term is a substring of its own fix (算法 ⊂ 演算法),
        # a blind replace corrupts already-correct text (演算法 → 演演算法). Use a
        # negative lookbehind on the repl prefix so only standalone uses are fixed.
        pattern = None
        if found in repl:
            prefix = repl.split(found)[0]
            if prefix:
                pattern = _re.compile(f"(?<!{_re.escape(prefix)}){_re.escape(found)}")
        for field in ("title", "subtitle", "body_markdown"):
            val = getattr(draft, field)
            if pattern is not None:
                new_val, cnt = pattern.subn(repl, val)
                if cnt:
                    setattr(draft, field, new_val)
                    fixes.append(f"[自動修正:{category}] {field}『{found}』×{cnt} → 『{repl}』")
            elif found in val:
                cnt = val.count(found)
                setattr(draft, field, val.replace(found, repl))
                fixes.append(f"[自動修正:{category}] {field}『{found}』×{cnt} → 『{repl}』")
    return fixes


def autofix_dashes(draft: "SubstackDraft", keep: int = 1) -> List[str]:
    """Convert excess 破折號 (em-dashes —/―) in body PROSE to 逗號 — a deterministic
    de-AI cleanup (the model over-uses em-dashes; soul 限 ≤1). 2026-05-30.

    Discipline:
      - Each maximal run of em-dashes (「—」「——」…) counts as ONE dash unit.
      - Keep the first `keep` units (soul allows ≤1); convert the rest to 「，」.
      - **Skip blockquote lines** (start with 「>」) so §13 inline-image markers and
        the footer (which carry English gen-prompts / hyphens) are left untouched.
      - Collapse any 「，，」 the swap produces.
    Mutates draft.body_markdown; returns one fix message (or []).
    """
    budget = keep
    converted = 0

    def _repl(m):
        nonlocal budget, converted
        if budget > 0:
            budget -= 1
            return m.group(0)        # keep this dash unit as-is
        converted += 1
        return "，"

    out_lines = []
    for line in draft.body_markdown.split("\n"):
        if line.lstrip().startswith(">"):   # §13 marker / footer blockquote → leave alone
            out_lines.append(line)
            continue
        new_line = re.sub(r"[—―]+", _repl, line)
        new_line = re.sub(r"，{2,}", "，", new_line)  # tidy doubled commas
        out_lines.append(new_line)

    if converted:
        draft.body_markdown = "\n".join(out_lines)
        return [f"[自動修正:破折號] 內文破折號 ×{converted} → 逗號（保留 {keep} 個）"]
    return []


# Word cap envelope (2026-05-12 升級):
#   Hsin 把 1500 字 hard cap 撤掉，因為 deep-research 後的素材值得長文展開。
#   新預設 3500 字上限，下限按 60% 比例縮為 ~2000 字（避免短打硬填到上限）。
#   可由 env override：SUBSTACK_WORD_CAP=N。
#   下限自動 = max(1200, cap * 0.55)，讓使用者只需設一個值。
SUBSTACK_WORD_CAP = int(os.getenv("SUBSTACK_WORD_CAP", "3500"))
SUBSTACK_WORD_FLOOR = max(1200, int(SUBSTACK_WORD_CAP * 0.55))


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

    # 1. 字數 — 上下限由 SUBSTACK_WORD_CAP env 控制
    n = _count_chinese_chars(body)
    if n < SUBSTACK_WORD_FLOOR:
        warnings.append(
            f"[字數低於下限] {n} 字 < {SUBSTACK_WORD_FLOOR}。需擴寫。"
            f" (上限 {SUBSTACK_WORD_CAP}，env: SUBSTACK_WORD_CAP)"
        )
    elif n > SUBSTACK_WORD_CAP:
        warnings.append(
            f"[字數超過上限] {n} 字 > {SUBSTACK_WORD_CAP}。需精煉。"
            f" (env: SUBSTACK_WORD_CAP)"
        )

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

    # 5. 破折號 — 最多 1 次。只數「內文 (非 blockquote) 的破折號單位」，與
    #    autofix_dashes 同範圍同計法：§13 視覺標記/footer 的英文 prompt 含「—」不算，
    #    舊版 count("——")+count("—") 掃全文又重複計數，會誤報。
    _prose = "\n".join(l for l in body.split("\n") if not l.lstrip().startswith(">"))
    dash_count = len(re.findall(r"[—―]+", _prose))
    if dash_count > 1:
        warnings.append(f"[破折號濫用] 內文破折號 {dash_count} 處 (上限 1)。改成句號／逗號／重寫。")

    # 6. 「不是 X、是 Y」對仗
    if re.search(r"不是.{1,15}[、，,]\s*[而是是].{1,15}", body):
        warnings.append("[對仗濫用] 發現『不是 X、是 Y』對仗句。改寫成『而』+ 具體脈絡。")

    # 7. 同篇重複用同 domain（透過 cover_image_prompt 偵測）
    #   這條由 caller 在 history 比對更準，這裡略

    # 8. 大陸用法檢查 (2026-05-12) — soul.md §11 規範
    # title + subtitle + body 都掃。命中為 warning (false-positive 可能存在,
    # 例如「質量」在物理脈絡是合法的；作者看到警告自己判斷)。
    full_text = f"{draft.title}\n{draft.subtitle}\n{body}"
    hits = []
    for found, repl, category in _MAINLAND_TERMS:
        # Strip the legit Taiwanese replacement first so a mainland term that is
        # a substring of its own fix (e.g. 算法 ⊂ 演算法) isn't false-flagged.
        haystack = full_text.replace(repl, "")
        if found in haystack:
            count = haystack.count(found)
            hits.append((found, repl, category, count))
    if hits:
        for found, repl, category, count in hits:
            warnings.append(
                f"[大陸用法] 『{found}』×{count} → 改用『{repl}』 ({category})"
            )

    return warnings


# --------------------------------------------------------------------------
# Prompt builder
# --------------------------------------------------------------------------

def _build_system_instruction(soul: str) -> str:
    anchor = load_voice_anchor()
    anchor_block = (
        f"\n=== 聲音錨點（用『範例』學聲音，比規則更重要）===\n{anchor}\n"
        if anchor else ""
    )
    return (
        "你是 News Radar 的 Substack 長文寫手——Visionary Analyst。\n"
        f"輸出 {SUBSTACK_WORD_FLOOR}-{SUBSTACK_WORD_CAP} 字長文，採用『硬商業邏輯 × 暖哲學靈魂』。\n"
        "\n"
        "=== 唯一靈魂源（必須完整內化）===\n"
        f"{soul}\n"
        f"{anchor_block}"
        "\n"
        "=== 重申最高優先級規則 ===\n"
        "1. §0 品牌宣言：替讀者咀嚼。讀完累 → 重寫。\n"
        "2. §2.6 Anti-Conclusion：結尾禁『總而言之』，必須提問／懸念。\n"
        "3. §3 Metaphor Diversification：絕不再用熱力學／建築學／生物演化。\n"
        "   從 6 個 domain 抽 1 個（不重複近 7 篇用過的）。\n"
        "4. §5 黑名單：「不是 X、是 Y」「○○感」「穩／撐／懂」一律不准。\n"
        f"5. §6 字數區間：{SUBSTACK_WORD_FLOOR}-{SUBSTACK_WORD_CAP} 字（含全形標點）。"
        f"超過上限必砍，低於下限必擴。env: SUBSTACK_WORD_CAP。\n"
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
        # === 2026-05-30 舉一反三 reasoning step（對抗「就事論事、生硬」）===
        "=== 動筆前先做這步（舉一反三）===\n"
        "在心裡先回答（不要寫進文章）：這則素材的**核心張力**是什麼？這個張力能照到哪 "
        "**2-3 個不相干的領域**？（例如投資題照到人生決策、科技題照到歷史或心理）。\n"
        "挑其中最有共鳴的一個，當成全文的**跨域類比骨幹**——讓讀者讀完覺得「這不只在講這條新聞」。\n"
        "這是『舉一反三』的引擎；只複述素材本身 = 失敗。\n\n"
        # === 2026-05-30 token-free 改版：研究改為「離線預抓素材」、不再 agentic 上網 ===
        "=== 事實紀律：只用上面的『原始素材』，不要上網查 ===\n"
        "本任務**沒有** WebSearch / WebFetch 工具（已停用）。上面的『原始素材』已由離線\n"
        "harvester（RSS / YouTube 逐字稿 / 文章正文）預先抓好清洗，是你**唯一**的事實來源。\n"
        "  (a) 具體金額／百分比／日期／人名職稱：素材裡有 → 照用；素材裡沒有 → 寫定性描述\n"
        "      （『近期』『數家公司』『幅度可觀』），**絕不可**自己掰一個數字或日期。\n"
        "  (b) **禁止幻覺背書**：「據業內傳出」「業界專家認為」「市場普遍預期」一律不可寫。\n"
        "  (c) 不需要外部佐證的：自家論述、比喻、§3 metaphor domain 的類比、抽象推論——放手寫。\n"
        "把事實自然寫進 body_markdown（不要列『資料來源』或附 URL，這是 essay 不是學術論文）。\n\n"
        # === 2026-05-30 重新接上 §13 inline image 視覺編輯（automated draft 漏掉的部分）===
        "=== 內文視覺標記（你兼任視覺編輯，務必做）===\n"
        "在 body_markdown 中**插入 3-6 個內文視覺標記**，給 Hsin 事後找圖／生圖用。規則：\n"
        "  - 落點：挑「抽象概念 → 具體場景」的轉折處；每個 ▉ 小節 0-2 個；**不要**放在開場 hook 與結尾。\n"
        "  - 不要自己畫圖或附真實 URL，只插下面這個 markdown blockquote 標記：\n"
        "> 🖼 視覺位置 · {3-8 字標題}\n"
        "> 場景描述：{1-3 句、第三人稱、含具體 time/place/物件}\n"
        "> 🔍 Path B · Google 搜：「{真實英文搜尋字串}」｜推薦來源：{2-3 個，Wikipedia→大刊 archive→stock}\n"
        "> 🎨 Path C · 生圖 prompt：{可直接貼 ChatGPT image 的英文 prompt，含 B&W documentary / side profile / 1960s LIFE 等風格約束}\n"
        "  - 這些標記**算進 body_markdown 字串**（用真實換行），不要另開欄位。\n\n"
        "=== 輸出格式：直接回一個 JSON object，欄位如下（缺一不可）===\n"
        "{\n"
        '  "title": "...",                  // 8-60 字。用 §6 標題公式庫 9 種原型之一(多為 鉤子+：+payoff 或 開放問句)，含具體錨點，禁新聞稿陳述式/震驚體/listicle。\n'
        '  "subtitle": "...",               // 10-80 字。不可重複 title。Substack 列表頁勾子。\n'
        f'  "body_markdown": "...",          // {SUBSTACK_WORD_FLOOR}-{SUBSTACK_WORD_CAP} 中文字。\n'
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
        timeout_s=1000,  # ~17 分鐘：字數恢復 3500（≈30-35K output tokens），實測
                         # 720s 仍會撞牆；無 web research 競爭時間，拉高給純生成餘裕（retry 仍在）。
        backends=backends,
        # 2026-05-30: 關掉 agentic 上網，逼 composer 只用預抓素材（token-free 改版）。
        disallowed_tools=("WebSearch", "WebFetch"),
    )

    # Cost metering (Optimization D, 2026-05-30): record every call — success or
    # fail — so token_usage_daily reflects real spend and before/after deltas are
    # measurable. Non-fatal: never let metering break composition.
    try:
        from src.db import record_token_usage

        record_token_usage(
            provider=result.provider,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
        )
        print(
            f"[SubstackComposer] 💰 usage logged: provider={result.provider} "
            f"in={result.input_tokens} out={result.output_tokens} "
            f"cost=${result.cost_usd:.4f}"
        )
    except Exception as exc:
        print(f"[SubstackComposer] ⚠️ token metering skipped: {exc}")

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
