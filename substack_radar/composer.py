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
        description="最終文章標題（不是原始素材標題）。外層放好奇/情緒鉤、別塞硬數據；先從這份素材長出標題，§6 公式庫只當寫完後的檢查清單(不是挑一個來套)。含一個具體錨點(公司/數字/事件)+反直覺翻轉。",
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
        "none",
    ] = Field(
        ...,
        description="本篇核心比喻 domain（最多一個、點到為止）。**完全不靠比喻就填 'none'**（鼓勵——比喻過多會文謅謅）。",
    )
    hook_type: Literal[
        "contrarian_question",
        "contrarian_reframe",
        "concrete_punch",
        "narrative_hook",
        "provocative_statement",
        "insider_question",
    ] = Field(
        ...,
        description=(
            "標題 hook 型態。嚴格輪流使用不同類型，不可連續兩篇同型態。\n"
            '  "contrarian_question": 反直覺問句（有時自然產生為什麼，但不必每篇如此）\n'
            '  "contrarian_reframe": 把常識翻轉成全新框架\n'
            '  "concrete_punch": 以一個具體數字/日期/事實直接開場，不做鋪陳\n'
            '  "narrative_hook": 以一段簡潔的觀察/矛盾場景切入，不是問句\n'
            '  "provocative_statement": 一個看起來不對但很可能正確的斷言\n'
            '  "insider_question": 把讀者放在決策者位置——"你要怎麼選"類的問句\n'
            "**不要連續兩篇用同一種 hook_type。**"
        ),
    )
    cover_image_prompt: Optional[str] = Field(
        default=None,
        description=(
            "（2026-05-31 停用）封面 prompt 區塊已移除。封面改由 Python 生圖 (cover.png) "
            "自動產生，使用者再從 §13 段落圖片建議自選一張替換。此欄留空 (null) 即可。"
        ),
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
    generated_by: Optional[str] = Field(
        default=None,
        description="（非 LLM 欄位）pipeline 在生成後填入的『產文路線/模型』標記。LLM 不要填，留 null。",
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

# 大陸用法 banned list — 2026-05-12 加入；2026-06-02 抽到 src/locale_tw.py 共用
# （substack 與 meta 三平台同表）。命中時為 warning（非 hard reject）。
# 完整對應表見 config/substack_soul.md §11。排序：(found, replacement, category)
from src.locale_tw import MAINLAND_TERMS as _MAINLAND_TERMS, to_traditional


def autofix_traditional(draft: "SubstackDraft") -> List[str]:
    """簡體→台灣繁體 (OpenCC s2tw) backstop — runs first, so fallback LLMs that
    emit Simplified Chinese never ship. Mutates title/subtitle/body_markdown."""
    fixes: List[str] = []
    for field in ("title", "subtitle", "body_markdown"):
        val = getattr(draft, field, None)
        if not val:
            continue
        new = to_traditional(val)
        if new != val:
            setattr(draft, field, new)
            fixes.append(f"[自動修正:繁化] {field} 簡體→台灣繁體")
    return fixes


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


# 盤古之白：中文與半形英數之間補一個空格（借 baoyu-format-markdown 的排版慣例，
# 不裝 skill）。保護 code span / markdown 連結 / URL / blockquote 不被插空格。
_CJK = r"一-鿿㐀-䶿"
_PROTECT_SPAN = re.compile(
    r"`[^`]*`"                       # inline code
    r"|!?\[[^\]]*\]\([^)]*\)"        # markdown link / image
    r"|https?://\S+|www\.\S+"        # bare URL
)
_PANGU_A = re.compile(rf"([{_CJK}])([A-Za-z0-9])")
_PANGU_B = re.compile(rf"([A-Za-z0-9])([{_CJK}])")


def autofix_cjk_spacing(draft: "SubstackDraft") -> List[str]:
    """在中文字與半形英數之間補空格（盤古之白）。決定性、可逆性低風險的排版 polish。

    紀律（與 autofix_dashes 同精神）：
      - 逐行處理；**跳過 fenced code block（``` 圍起）與 blockquote 行（>）**，
        §13 視覺標記/footer 的英文 prompt 與 URL 不動。
      - 行內先把 code span / markdown 連結 / 裸 URL 抽成 placeholder 再補空格，
        還原後不會在網址或連結裡塞空格。
      - 全形標點不在 CJK 表意文字範圍內，故「中，A」「）GPT」不會被加空格。
    只動 body_markdown；回傳一則 fix 訊息（或 []）。
    """
    body = draft.body_markdown or ""
    if not body:
        return []

    added = 0
    in_fence = False
    out_lines: List[str] = []
    for line in body.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence or stripped.startswith(">"):
            out_lines.append(line)
            continue

        held: List[str] = []

        def _stash(m: "re.Match") -> str:
            held.append(m.group(0))
            return f"\x00{len(held) - 1}\x00"

        protected = _PROTECT_SPAN.sub(_stash, line)
        new_line, n1 = _PANGU_A.subn(r"\1 \2", protected)
        new_line, n2 = _PANGU_B.subn(r"\1 \2", new_line)
        if held:
            new_line = re.sub(r"\x00(\d+)\x00", lambda m: held[int(m.group(1))], new_line)
        added += n1 + n2
        out_lines.append(new_line)

    if added:
        draft.body_markdown = "\n".join(out_lines)
        return [f"[自動修正:盤古之白] 中英數間補空格 ×{added}"]
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
        "3. §3 比喻節制：**預設不用比喻**（metaphor_domain_used=none）；真要用一篇最多一個、點到為止；多用具體事實／數據。絕不用熱力學／建築／演化。\n"
        "   從 6 個 domain 抽 1 個（不重複近 7 篇用過的）。\n"
        "4. §5 黑名單：「不是 X、是 Y」「○○感」「穩／撐／懂」一律不准。\n"
        f"5. §6 字數區間：{SUBSTACK_WORD_FLOOR}-{SUBSTACK_WORD_CAP} 字（含全形標點）。"
        f"超過上限必砍，低於下限必擴。env: SUBSTACK_WORD_CAP。\n"
        "6. §8 五秒拍片測試：每段抽象論述後必須有具體場景錨點。\n"
        "7. §9 完美 = AI：句子長短刻意不平均、留一個沒講完的暗示。\n"
    )


def _material_for_prompt(raw_content: str, mode: str) -> str:
    """How much source material to feed the writer, by mode.

    morning/evening are short articles/news → first 6000 chars is plenty.

    podcast transcripts are 1–3 hr interviews (often 100k–300k chars). We feed the
    **whole** transcript: Gemini 3.1 Pro has a ~1M-token context window (a 3-hr
    episode ≈ 75k tokens, <10% of it), and the sharpest Q&A insight can sit anywhere
    in the conversation — head/tail slicing would drop the meaty middle. The only
    real ceiling is the OS argv limit: gemini CLI passes the prompt via `-p <arg>`,
    and macOS ARG_MAX is ~1MB, so cap at 500k chars (~500KB, ~125k tokens) which
    covers essentially every real podcast while staying well clear of E2BIG. The
    rare outlier above that keeps the whole front + tail, eliding the least possible.
    """
    text = raw_content or ""
    if mode != "podcast":
        return text[:6000]
    PODCAST_CAP = 500_000
    if len(text) <= PODCAST_CAP:
        return text
    return f"{text[:440000]}\n\n（……逐字稿過長，僅中段省略一小部分……）\n\n{text[-60000:]}"


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
        "podcast": (
            "【Mode: podcast / Type C 長訪談萃取】"
            "下面的素材是一集 YouTube 長訪談 podcast 的**完整逐字稿**（自動字幕、無講者標記，全長皆在，"
            "需自行從上下文判斷誰在說話）。這類內容的價值在主持人與來賓**一來一回的問答**："
            "一個好問題逼出一個反直覺的回答，追問再把它推深。\n"
            "你的任務**不是**摘要整集訪談，而是：①從這場對話裡挑出**一個**最反直覺、最有洞察的觀點或"
            "交鋒（某個被追問出來的真話、某個與主流相反的判斷）；②以它為文章骨幹，把來賓的論證"
            "重新組織成你自己的深度推論（可改寫、濃縮對話，但不可捏造他沒說的數字或主張）；"
            "③點出這個觀點對讀者的決策或對某個更大模式的意義。寧可深挖一點，也不要面面俱到的流水帳。"
        ),
    }.get(mode, "")

    return (
        f"{mode_hint}\n\n"
        f"=== 編輯指令 ===\n{editorial_note or '按既有靈魂風格自由發揮。'}\n\n"
        f"=== 原始素材 ===\n標題：{raw_title}\n本文：{_material_for_prompt(raw_content, mode)}\n\n"
        f"=== 主題分類 ===\n{topic_category}\n\n"
        f"=== 多樣性提醒 ===\n{avoid}\n\n"
        # === 2026-06-02 反模板（Hsin：範例是參照不是照抄；先跑舉一反三推理鏈，別在固定句型裡輪轉）===
        "=== 第零步：標題多樣性——禁止每篇都用『為什麼 X：為什麼 Y』模板 ===\n"
        "最近的文章因為標題結構太固定（全是『為什麼 X：為什麼 Y』）被 Hsin 明確批評。"
        "從這篇開始：**刻意輪換 hook_type**——可以用反直覺斷言開頭、用具體數字衝擊、"
        "用場景觀察切入、或用決策者提問。hook_type 有 6 種可選，不要連續兩篇踩同一種。"
        "標題不是『為什麼』公式填空。\n\n"
        "=== 第一步：先跑一條『舉一反三推理鏈』，再動筆（這步決定這篇是不是模板貨）===\n"
        "**最重要的元規則：本提示裡所有的範例字串（標題範例、thesis 句型、小標範例、§6 標題公式庫、"
        "metaphor domain 清單、各種「像『…』」舉例）全部只是『示意原理』用的，嚴禁照抄、嚴禁套殼填空。** "
        "它們的用途是讓你『寫完後回頭檢查方向對不對』，不是讓你挑一個套上去。**若你的標題、開場句、小標或"
        "收尾跟範例幾乎一樣，就是失敗**——代表你在輪轉固定句型，而不是針對這份素材思考。\n"
        "動筆前，先針對**這一份具體素材**在心裡推一條鏈（不要寫進文章）：\n"
        "  (1) 這份素材最反直覺、最違反多數人預期的**那一個**點是什麼？（要具體到這份素材本身，不能是通用大道理）\n"
        "  (2) 往下推一層：它影響誰、改變哪個賽局、推到極端會怎樣？背後真實的機制／數字是什麼？\n"
        "  (3) 它照到讀者的哪個決策、或哪個更大的模式？\n"
        "  (4) 用 (1)–(3) 長出**這篇獨有**的角度、骨架與句法，再回頭用下面的原則檢查方向——\n"
        "      **不是**反過來先挑一個公式／句型再把素材填進去。\n"
        "每篇的角度、結構、開場與收尾句法都應該因素材而不同；明顯在固定幾種句型裡輪轉 = 失敗。\n"
        "**風格硬規範（違反任一條都算失敗；以下舉的例子都是『不要這樣』或『示意』，同樣禁照抄）：**\n"
        "  1. **禁止用比喻／類比／擬人當骨幹。** 不要『像一把還能開火的舊武器』『街頭的規矩』『一場微小的反叛』"
        "這種跨域比喻或文學意象。全篇最多 0–1 個比喻，能不用就不用；metaphor_domain_used 預設填 \"none\"。\n"
        "  2. **禁止場景灌水與帶入式開場。** 不要『凌晨一點半你盯著螢幕』『你正站在展場前』這種虛構第二人稱情境"
        "來鋪陳或充字數。開門見山講事實與判斷。\n"
        "  3. **禁止後設碎念。** 不要寫『我寫到這裡也卡住了』『說來慚愧』這種自我對話填充。\n"
        "  4. **要數據與具名事實。** 每個論點儘量綁一個素材裡的具體數字／型號／日期／金額；用事實推進，"
        "少用形容詞堆疊（『令人咋舌』『眼花撩亂』『天價』這類盡量不用）。\n"
        "  5. **要人類分析師的明確判斷。** 該下結論就下，講清楚『所以對讀者意味著什麼、該怎麼做』，可以有立場；"
        "結尾給判斷或一個真實的取捨，不要用空泛反問句收尾充數。\n"
        "只複述素材 = 失敗；靠比喻與情境鋪陳假裝深刻 = 文謅謅，也是失敗。目標：像一個懂行、冷靜、有觀點的人"
        "在跟你分析，而不是抒情散文。\n\n"
        # === 2026-05-30 token-free 改版：研究改為「離線預抓素材」、不再 agentic 上網 ===
        "=== 事實紀律：只用上面的『原始素材』，不要上網查 ===\n"
        "本任務**沒有** WebSearch / WebFetch 工具（已停用）。上面的『原始素材』已由離線\n"
        "harvester（RSS / YouTube 逐字稿 / 文章正文）預先抓好清洗，是你**唯一**的事實來源。\n"
        "  (a) 具體金額／百分比／日期／人名職稱：素材裡有 → 照用；素材裡沒有 → 寫定性描述\n"
        "      （『近期』『數家公司』『幅度可觀』），**絕不可**自己掰一個數字或日期。\n"
        "  (b) **禁止幻覺背書**：「據業內傳出」「業界專家認為」「市場普遍預期」一律不可寫。\n"
        "  (c) 不需要外部佐證的：自家論述、比喻、§3 metaphor domain 的類比、抽象推論——放手寫。\n"
        "把事實自然寫進 body_markdown（不要列『資料來源』或附 URL，這是 essay 不是學術論文）。\n\n"
        # === 2026-06-02 §13 視覺標記 = 重新抓注意力的節點（取自 MrBeast 留存框架）===
        "=== 內文視覺標記（你兼任視覺編輯）===\n"
        "在 body_markdown 插入 **2-3 個**內文視覺標記（不要更多，過多會稀釋）。把它們當『重新抓住注意力的節點』來放：\n"
        "  - 落點：① 開場兌現後第一個「抽象概念→具體場景」的轉折；② **文章中段、全篇最強數據／最反直覺對比的旁邊**（re-engagement spike）；③（可選）某個主要小節開頭。**不要**放在開場 hook 之前與結尾。\n"
        "  - 不要自己畫圖或附真實 URL，只插下面這個 markdown blockquote 標記：\n"
        "> 🖼 視覺位置 · {3-8 字標題}\n"
        "> 場景描述：{1-3 句、第三人稱、含具體 time/place/物件}\n"
        "> 🔍 Path B · Google 搜：「{真實英文搜尋字串}」｜推薦來源：{2-3 個，Wikipedia→大刊 archive→stock}\n"
        "> 🎨 Path C · 生圖 prompt：{可直接貼 ChatGPT image 的英文 prompt，含 B&W documentary / side profile / 1960s LIFE 等風格約束}\n"
        "  - 這些標記**算進 body_markdown 字串**（用真實換行），不要另開欄位。\n\n"
        # === 2026-06-02 互動工程 scaffold（對抗 CTR/讚率下滑；取自 MrBeast 注意力框架）===
        "=== 結構與留存（CTR 靠標題/副標，讚率靠下面這套，務必照做）===\n"
        "  1. **開場 2 句內兌現標題的承諾**——不要鋪陳虛構場景、不要暖場。第一段就把標題答應的衝突／數字直接端出來。\n"
        "  2. **前置一句反直覺 thesis**（你的判斷）放在開場前三段內，是一句強斷言。"
        "（句型示意、禁照抄：「X 根本不是 A，而是偽裝成 A 的 B」只是示範『強度』，請自己長一句。）\n"
        "  3. **每個 ▉ 小節用『具名資訊型小標』**：▉ 後接一句能單獨讀懂、帶鉤子的短句，不要用「第二層含義」這種抽象標籤。"
        "每個小節 = 一個讓讀者覺得『有在前進』的微目標。（示意、禁照抄：別每篇都長成「平台不想要下一個 X」那種句型。）\n"
        "  4. **中段安排一個 spike**：把全篇最猛的數據或最反直覺的對比放在文章中段引爆（搭配上面 §13 的中段視覺）。\n"
        "  5. **結尾用『這告訴我們什麼』收**：把個案推成**可套用到其他產業／讀者決策的通用框架**，並給 1-2 個具體的跨領域例子，讓讀者『帶走一個框架』。\n"
        "  6. 結尾最後加一句**邀請讀者回應的鉤子**（針對文章拋一個具體問題請讀者思考），不要只有訂閱鈕。\n\n"
        "=== 輸出格式：直接回一個 JSON object，欄位如下（缺一不可）===\n"
        "{\n"
        '  "title": "...",                  // 8-60 字。**鉤子要多樣性**：不要連續兩篇用同一種 hook_type。'
        '**尤其禁止每篇都用「為什麼」開頭**——那會讓你的 feed 看起來像同一個模板填不同素材。輪流用：'
        'concrete_punch(數字衝擊)、narrative_hook(觀察切入)、provocative_statement(斷言)、'
        'insider_question(決策者視角)、contrarian_question(反問)、contrarian_reframe(框架翻轉)。'
        '外層放好奇/情緒鉤，不要把硬數據/術語塞進標題（數字與型號留給副標與內文）。'
        '含一個具體錨點，禁新聞稿陳述式/震驚體/listicle。\n'
        '  "subtitle": "...",               // 10-80 字。不可重複 title。這裡才放最有力的「具體數字+反差」當 Substack 列表頁勾子。\n'
        f'  "body_markdown": "...",          // {SUBSTACK_WORD_FLOOR}-{SUBSTACK_WORD_CAP} 中文字。\n'
        "                                   //   開場2句兌現承諾+前置 thesis；▉ 具名小標；中段 spike。\n"
        "                                   //   結尾禁『總而言之』：先給可帶走的通用框架+具體例子，再以提問/懸念收，末句邀請讀者回應。\n"
        '  "metaphor_domain_used": "...",   // ENUM：6 domain 之一，或 "none"（不靠比喻，鼓勵）：\n'
        '                                   //     "signal_processing" | "music_theory" |\n'
        '                                   //     "contrarian_markets" | "cinematic_pacing" |\n'
        '                                   //     "street_culture" | "architecture_space" | "none"\n'
        "                                   //   **預設 none**；真要用最多一個、點到為止；絕不用熱力學/化學/生物演化。\n"
        '  "hook_type": "...",              // ENUM 以下其一，**不要連續兩篇同一種**：\n'
        '                                   //     "contrarian_question" | "contrarian_reframe" |\n'
        '                                   //     "concrete_punch" | "narrative_hook" |\n'
        '                                   //     "provocative_statement" | "insider_question"\n'
        '  "cover_image_prompt": null,      // 已停用，固定填 null（封面改用 §13 段落圖片建議自選）。\n'
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
#
# 2026-06-20 (Hsin)：Gemini CLI 個人版被 Google 在 6/18 收掉，AI Pro 額度改由 Antigravity CLI
# (agy) 取用 → 主寫換成 antigravity_cli(本機 agy -p，token-free gemini-3.1-pro)，倒了才退
# gemini API(2.5-flash 免費)→opencode(長文兜底)→cerebras→groq。雲端沒裝 agy 會自動略過。
# 注意：.lower() 會把模型名小寫化但 backend 名稱本就全小寫，agy 的 --model 在 _try_agy 內另給。
SUBSTACK_BACKEND = os.getenv(
    "SUBSTACK_COMPOSER_BACKEND", "antigravity_cli,gemini,opencode,cerebras,groq"
).lower()


_KNOWN_BACKENDS = {"antigravity_cli", "claude_cli", "gemini_cli", "gemini", "opencode", "groq", "cerebras"}


def _resolve_backends() -> Optional[tuple]:
    """Map env-var string → call_for_json `backends` tuple (按序嘗試).

    2026-06-01 (Hsin directive): Substack 寫文**拿掉 claude_cli**，改 gemini CLI
    (AI Pro 帳號，由 GEMINI_CLI_CONFIG_DIRS 多帳號輪替) → 免費 Gemini API key。
    支援逗號清單，例：SUBSTACK_COMPOSER_BACKEND="gemini_cli,gemini"。
    """
    # 逗號清單 → tuple（最彈性、最直白）
    if "," in SUBSTACK_BACKEND:
        chain = tuple(b for b in (x.strip() for x in SUBSTACK_BACKEND.split(",")) if b in _KNOWN_BACKENDS)
        if chain:
            return chain
    if SUBSTACK_BACKEND in ("default", "auto", "fallback"):
        # 預設寫文鏈：gemini CLI(Pro，多帳號) → 免費 API key。**不含 claude_cli。**
        return ("gemini_cli", "gemini")
    if SUBSTACK_BACKEND == "claude_cli":
        # 顯式要 claude 才用（手動 override）；仍掛 gemini 備援。
        return ("claude_cli", "gemini_cli", "gemini")
    if SUBSTACK_BACKEND in _KNOWN_BACKENDS:  # 強制單一後端
        return (SUBSTACK_BACKEND,)
    print(
        f"[SubstackComposer] ⚠️ Unknown SUBSTACK_COMPOSER_BACKEND={SUBSTACK_BACKEND!r}; "
        f"defaulting to gemini_cli→gemini (no claude)."
    )
    return ("gemini_cli", "gemini")


def describe_route(provider: str, model: str) -> str:
    """Human-readable 產文路線：which model/platform actually generated the draft.
    Known from the run (envelope modelUsage + ANTHROPIC_BASE_URL) — no LLM query.

    - claude_cli + ANTHROPIC_BASE_URL set → CCR/proxy 路由（host + 實際模型名）
    - claude_cli + 原生（無 base_url）+ claude-* 模型 → 原生 Claude 方案 (Pro/Max)
    - gemini / groq / cerebras → 該 API key 平台 + 模型
    """
    m = model or "?"
    base_url = (os.getenv("ANTHROPIC_BASE_URL") or "").strip()
    if provider == "claude_cli":
        if base_url:
            try:
                from urllib.parse import urlparse
                host = urlparse(base_url).netloc or base_url
            except Exception:
                host = base_url
            return f"CCR/代理路由 @ {host} · 模型 {m}"
        if m.startswith("claude-"):
            return f"原生 Claude 方案 (Claude CLI / Pro·Max) · 模型 {m}"
        return f"Claude CLI · 模型 {m}"
    if provider == "gemini_cli":
        return f"Gemini CLI (Google AI Pro) · 模型 {m}"
    if provider in ("gemini", "groq", "cerebras"):
        return f"{provider} API key 平台 · 模型 {m}"
    if provider == "none":
        return "（無）所有 LLM 路徑皆失敗"
    return f"{provider} · 模型 {m}"


async def compose_substack_article(
    *,
    title: str,
    content: str,
    mode: Literal["morning", "evening", "podcast"] = "morning",
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
        timeout_s=1300,  # ~22 分鐘：字數上限 3500（≈30-35K output tokens），實測 1000s
                         # 仍會撞牆；無 web research 競爭時間，拉高給純長文生成餘裕（retry 仍在）。
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

    # Provenance: stamp WHICH model / route actually wrote this draft (known from
    # the run itself — no need to ask the LLM). Goes to the top of the draft.
    provenance = describe_route(result.provider, result.model)
    result.data.generated_by = provenance
    print(f"[SubstackComposer] ℹ️ 產文路線：{provenance}")

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
        print(f"COVER PROMPT: {(d.cover_image_prompt or '(disabled)')[:120]}")
        warnings = audit_substack_draft(d)
        if warnings:
            print("\n⚠️ AUDIT WARNINGS:")
            for w in warnings:
                print(f"  - {w}")
        else:
            print("\n✅ Audit clean")

    asyncio.run(_smoke())
