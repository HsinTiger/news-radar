"""
News Radar · Substack Editorial Composer
=========================================

為什麼獨立於 src/composer.py（三平台社群文）：

    1. 規格根本不同——三平台是短貼文，Substack 分為 Daily 1400–2200 字
       與 Weekly 2800–4200 字。共用 system_instruction 會互相妥協。
    2. 語氣根本不同——Substack 是有人味的分析信，社群是 90 秒短打。
    3. 校驗根本不同——Substack 要檢查 profile 字數、段落、證據邊界與回信問題。

落地介面：
    draft = await compose_substack_article(
        title="...",         # 原始素材標題（不是最終文章標題）
        content="...",       # 原始素材內容
        mode="morning",      # "morning"(type a 深度新聞) or "evening"(type b 獨立選題)
        topic_category="ai_model",  # 對應 topic_taxonomy.py 的 category_id
        editorial_note="",
        editorial_profile="auto",   # morning/evening=daily; podcast/company=weekly
    )
    if draft is None:
        # LLM 兩條路都失敗，呼叫端 skip 並通知 user
        return
    # 用 draft.title / draft.body_markdown / draft.cover_prompt ...

後置 audit 只做可機械判斷的檢查並輸出 warning；文章判斷仍由 owner review。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from src.llm_brain import call_for_json


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

class SubstackDraft(BaseModel):
    """LLM 結構化輸出 contract for Substack long-form article."""

    title: str = Field(
        ...,
        description="文章標題，≤15 字，只承諾一件事；禁用冒號拼兩個焦點（人物訪談除外）。",
        min_length=4,
        max_length=24,
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
            "台灣繁體中文 markdown。字數與深度由 Daily/Weekly profile 決定；"
            "使用內容型小標與短段落，清楚區分證據、推論、未知，最後提出具體回信問題。"
        ),
    )
    generated_by: SkipJsonSchema[Optional[str]] = Field(
        default=None,
        description=(
            "（非 LLM 欄位）pipeline 在生成後記錄的實際 provider/model；"
            "由 file writer 以 reader-facing provenance 寫入，LLM 不得自行填寫。"
        ),
    )

    # 2026-05-30: truncate overlong title/subtitle BEFORE the max_length check, so a
    # full ~8-min generation isn't thrown away just because the model overshot the
    # title/subtitle by a few chars (it's not retryable, so rejection = wasted draft).
    @field_validator("title", "subtitle", mode="before")
    @classmethod
    def _truncate_headline(cls, v, info):
        if isinstance(v, str):
            cap = {"title": 24, "subtitle": 80}.get(info.field_name)
            if cap and len(v) > cap:
                # 在字數上限內收在「最後一個標點邊界」＝留一個語意完整的標題，而非從字
                # 中間硬切（信哥 2026-06-28：要合理的標題、不要語意一半就斷）。找不到夠
                # 靠後的邊界才退回硬切上限（保底）。
                window = v[:cap]
                cut = max((window.rfind(ch) for ch in "。！？!?，,、；;：:"), default=-1)
                if cut >= cap // 2:        # 邊界要夠靠後，免得砍到只剩半句
                    window = window[:cut + 1]
                return window.rstrip("，、。；：:;,！？!?「」『』（）()【】 　")
        return v


# --------------------------------------------------------------------------
# Reader-ready boundary
# --------------------------------------------------------------------------

_PRODUCTION_MARKERS = (
    "🖼 視覺位置",
    "🔍 Path B",
    "🎨 Path C",
    "生圖 prompt",
    "生圖 Prompt",
    "封面圖 Prompt",
    "cover_image_prompt",
    "chart_prompt",
    "發布前刪",
    "發文前請刪",
    "substack-editor",
)


def strip_production_instructions(markdown: str) -> str:
    """Remove authoring instructions that must never reach a reader.

    The prompt forbids these blocks, but old models, queued drafts, and pasted
    text can still contain them. Apply this cleanup when files are written and
    again at the remote API boundary.
    """
    text = re.sub(
        r"<!--\s*substack-editor:.*?-->",
        "",
        markdown or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    paragraphs = re.split(r"\n\s*\n", text)
    kept: List[str] = []
    skipping_inline_visual = False

    for paragraph in paragraphs:
        compact = paragraph.strip()
        if not compact:
            continue

        # The cover prompt is an appended authoring tail, never article copy.
        if "封面圖 Prompt" in compact:
            break

        if "🖼 視覺位置" in compact:
            skipping_inline_visual = True
            continue

        if skipping_inline_visual:
            if compact.startswith(("場景描述：", "場景描述:", "🔍 Path B", "🎨 Path C")):
                continue
            skipping_inline_visual = False

        if "產文路線" in compact and "發布前刪" in compact:
            continue

        if any(
            marker in compact
            for marker in (
                "🔍 Path B",
                "🎨 Path C",
                "chart_prompt",
                "發布前刪",
                "發文前請刪",
            )
        ):
            continue

        kept.append(compact)

    return "\n\n".join(kept).strip()


def strip_generated_footer(markdown: str) -> str:
    """Remove known pipeline-owned footers before one canonical footer is added."""
    footer_markers = (
        "我專門拆解：那些你已經被市場說服",
        "📅 每天 3 分鐘",
        "🔄 365 天複利",
        "把複雜世界寫成人話，保留真正值得你判斷的部分",
        "📅 每天兩篇對談延伸",
        "✉️ 你可以直接回信，告訴我哪個判斷值得再追",
        "點此訂閱 → 不錯過下一篇拆解",
    )
    paragraphs = re.split(r"\n\s*\n", markdown or "")
    kept = [
        paragraph.strip()
        for paragraph in paragraphs
        if paragraph.strip()
        and not any(marker in paragraph for marker in footer_markers)
    ]
    return "\n\n".join(kept).strip()


def assert_reader_ready_markdown(markdown: str) -> None:
    """Fail closed if authoring metadata survives deterministic cleanup."""
    if not (markdown or "").strip():
        raise ValueError("reader-ready gate rejected empty content")
    found = [marker for marker in _PRODUCTION_MARKERS if marker in (markdown or "")]
    if found:
        raise ValueError(
            "reader-ready gate rejected production instructions: "
            + ", ".join(sorted(set(found)))
        )


# --------------------------------------------------------------------------
# Editorial profiles
# --------------------------------------------------------------------------

CONFIG_DIR = Path(__file__).resolve().parent / "config"
COMMON_EDITORIAL_PATH = CONFIG_DIR / "editorial_voice.md"
DAILY_EDITORIAL_PATH = CONFIG_DIR / "editorial_daily.md"
WEEKLY_EDITORIAL_PATH = CONFIG_DIR / "editorial_weekly.md"


@dataclass(frozen=True)
class EditorialProfile:
    name: Literal["daily", "weekly"]
    word_floor: int
    word_cap: int
    reading_minutes: str
    brief_path: Path


DAILY_PROFILE = EditorialProfile("daily", 1400, 2200, "6–8", DAILY_EDITORIAL_PATH)
WEEKLY_PROFILE = EditorialProfile("weekly", 2800, 4200, "12–16", WEEKLY_EDITORIAL_PATH)


def resolve_editorial_profile(
    mode: str,
    *,
    override: Optional[str] = None,
    has_deep_bundle: bool = False,
) -> EditorialProfile:
    """Resolve writing depth without changing the source-selection mode."""
    if override and override != "auto":
        if override not in {"daily", "weekly"}:
            raise ValueError(f"unknown editorial profile: {override}")
        return DAILY_PROFILE if override == "daily" else WEEKLY_PROFILE
    if has_deep_bundle or mode in {"podcast", "company"}:
        return WEEKLY_PROFILE
    return DAILY_PROFILE


def load_editorial_brief(profile: EditorialProfile) -> str:
    """Load only the common voice plus the selected cadence brief."""
    missing = [path for path in (COMMON_EDITORIAL_PATH, profile.brief_path) if not path.exists()]
    if missing:
        raise FileNotFoundError("missing editorial brief: " + ", ".join(str(path) for path in missing))
    common = COMMON_EDITORIAL_PATH.read_text(encoding="utf-8").strip()
    cadence = profile.brief_path.read_text(encoding="utf-8").strip()
    return f"{common}\n\n{cadence}"


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
# Human-readable mapping lives in config/substack_reference.md.
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
    The lookup table is enforced here at zero token cost rather than being sent
    to the writer on every call.

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
    cleanup for a common model habit.

    Discipline:
      - Each maximal run of em-dashes (「—」「——」…) counts as ONE dash unit.
      - Keep the first `keep` units; convert the rest to 「，」.
      - Skip blockquote lines so deterministic footer/cover instructions stay untouched.
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
        if line.lstrip().startswith(">"):   # deterministic footer / cover blockquote
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
        deterministic footer/cover blockquote 的英文 prompt 與 URL 不動。
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


# Backward-compatible exports for old callers. New composition and audit code
# always use the resolved EditorialProfile instead of one global envelope.
SUBSTACK_WORD_FLOOR = DAILY_PROFILE.word_floor
SUBSTACK_WORD_CAP = DAILY_PROFILE.word_cap


def word_range_for(profile: EditorialProfile) -> tuple[int, int]:
    """Return the cadence range, with an explicit emergency cap override.

    SUBSTACK_DAILY_WORD_CAP / SUBSTACK_WEEKLY_WORD_CAP are preferred. The old
    SUBSTACK_WORD_CAP remains a compatibility fallback for installed workers.
    """
    raw_cap = os.getenv(f"SUBSTACK_{profile.name.upper()}_WORD_CAP") or os.getenv(
        "SUBSTACK_WORD_CAP"
    )
    if not raw_cap:
        return profile.word_floor, profile.word_cap
    cap = max(1200, int(raw_cap))
    floor = min(profile.word_floor, max(1200, int(cap * 0.65)))
    return floor, cap


def _count_chinese_chars(text: str) -> int:
    """數中文字＋全形標點。半形字符／英文不計。"""
    return sum(
        1
        for ch in text
        if "一" <= ch <= "鿿"
        or ch in "，。！？；：「」『』（）、—…"
    )


def audit_substack_draft(
    draft: SubstackDraft,
    *,
    profile: EditorialProfile = DAILY_PROFILE,
) -> List[str]:
    """Return list of soft-fail warnings (empty = clean).

    呼叫端決定要 hard-reject 還是只 log warning。CLI 預設只 log。
    """
    warnings: List[str] = []
    body = draft.body_markdown

    word_floor, word_cap = word_range_for(profile)

    # 0. 列表頁承諾：短標題只承諾一件事，副標不能只是重複一次。
    if len(draft.title) > 15:
        warnings.append(f"[標題過長] {len(draft.title)} 字 > 15；請只留一個閱讀承諾。")
    if any(mark in draft.title for mark in ("：", ":")):
        warnings.append("[標題雙焦點] 標題含冒號；除人物訪談外，通常代表塞了兩件事。")
    if draft.title.strip("？?。！! ") in draft.subtitle:
        warnings.append("[副標重複] 副標應補具體反差或 payoff，不要重述主標。")

    # 1. 字數 — Daily / Weekly 各自有獨立 envelope。
    n = _count_chinese_chars(body)
    if n < word_floor:
        warnings.append(
            f"[字數低於下限] {n} 字 < {word_floor}（{profile.name}）。需補證據或刪題。"
        )
    elif n > word_cap:
        warnings.append(
            f"[字數超過上限] {n} 字 > {word_cap}（{profile.name}）。需精煉。"
        )

    # 1b. 2026-08-05 已移除內文生圖。舊 marker 留在稿內只會把內部製程
    #     洩漏給讀者，也不會再被替換成圖片。
    if any(marker in body for marker in ("🖼 視覺位置", "Path B", "Path C", "chart_prompt")):
        warnings.append("[舊內文視覺標記] writer 仍輸出已移除的生圖／搜尋指令，必須刪除。")

    # 1c. Substack 是 email 關係，不用籠統 CTA 收尾。問題必須留在最後一屏。
    tail = body.strip()[-400:]
    if "？" not in tail and "?" not in tail:
        warnings.append("[缺少具體回信問題] 最後一屏沒有可讓讀者真正回覆的問題。")
    if any(generic in tail for generic in ("歡迎留言", "你怎麼看", "大家怎麼看")):
        warnings.append("[空泛互動問題] 請改問本文特有的取捨、經驗或觀測訊號。")

    # 1d. 手機閱讀：一段只傳達一件事。Markdown blockquote / list 不在此限。
    for paragraph in re.split(r"\n\s*\n", body):
        compact = paragraph.strip()
        if compact and not compact.startswith((">", "- ", "* ", "#")) and len(compact) > 320:
            warnings.append("[段落過長] 有單段超過 320 字，請拆成一段一件事。")
            break

    opening = re.sub(r"[#>*_`\s]", "", body[:160])
    if any(
        generic in opening
        for generic in ("在這個快速變動的時代", "在這個充滿變化的時代", "隨著科技快速發展")
    ):
        warnings.append("[空泛開場] 前兩段應交代具體背景與本文問題，不要用時代感暖場。")

    # 2. Generic closing blacklist
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
    #    autofix_dashes 同範圍同計法：footer/cover blockquote 的英文 prompt 含「—」不算，
    #    舊版 count("——")+count("—") 掃全文又重複計數，會誤報。
    _prose = "\n".join(l for l in body.split("\n") if not l.lstrip().startswith(">"))
    dash_count = len(re.findall(r"[—―]+", _prose))
    if dash_count > 1:
        warnings.append(f"[破折號濫用] 內文破折號 {dash_count} 處 (上限 1)。改成句號／逗號／重寫。")

    # 6. 「不是 X、是 Y」對仗
    if re.search(r"不是.{1,15}[、，,]\s*[而是是].{1,15}", body):
        warnings.append("[對仗濫用] 發現『不是 X、是 Y』對仗句。改寫成『而』+ 具體脈絡。")

    # 7. 大陸用法檢查（human-readable mapping: substack_reference.md）
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

def _build_system_instruction(profile: EditorialProfile) -> str:
    word_floor, word_cap = word_range_for(profile)
    brief = load_editorial_brief(profile)
    return (
        "你是 HsinTiger Substack 的資深中文編輯與分析寫手。你的工作不是展示 AI 能力，"
        "而是替一位忙碌、聰明的讀者把複雜問題想清楚。\n\n"
        f"本次採 {profile.name.upper()} profile：{word_floor}–{word_cap} 個中文字，"
        f"約 {profile.reading_minutes} 分鐘。\n\n"
        "=== 寫作契約 ===\n"
        f"{brief}\n\n"
        "若規則互相衝突，依序採用：事實正確與證據邊界 > 讀者理解 > 編輯指令 > 風格。"
    )


def _material_for_prompt(
    raw_content: str,
    mode: str,
    profile: EditorialProfile,
) -> str:
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
    if profile.name == "daily" and mode != "podcast":
        return text[:12_000]
    if mode != "podcast":
        return text[:120_000]
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
    profile: EditorialProfile,
) -> str:
    word_floor, word_cap = word_range_for(profile)
    mode_hint = {
        "morning": (
            "這是近期新聞素材。找出真正改變了什麼、機制是什麼，以及讀者接下來該觀察什麼；"
            "不要重寫新聞摘要。"
        ),
        "evening": (
            "這是較耐久的獨立選題。用一個具體問題串起材料，不要為了顯得深刻而拉高到空泛哲學。"
        ),
        "podcast": (
            "這是長訪談或逐字稿。Podcast 是起點，不是文章主題：挑一個最值得追問的交鋒，"
            "先還原主持人的問題與來賓的主張，再把它推成一個離開節目也成立的延伸問題。"
            "清楚區分來賓的主張、素材中的旁證與作者的推論；不要摘要整集，也不要替來賓補話。"
        ),
        "company": (
            "這是每週公司分析。依序回答：怎麼賺錢、優勢能否維持、數字是否支持、最強反方是什麼、"
            "接下來看哪兩三個領先訊號。所有財務數字只准引用素材中的『財報事實』；缺值就寫『資料未揭露』。"
        ),
    }.get(mode, "")

    return (
        f"=== 本次任務 ===\nProfile：{profile.name}（{word_floor}–{word_cap} 字）\n"
        f"素材類型：{mode}\n主題分類：{topic_category}\n{mode_hint}\n\n"
        f"=== 編輯指令 ===\n{editorial_note or '沒有額外指令；依本次素材做最佳編輯判斷。'}\n\n"
        f"=== 原始素材 ===\n標題：{raw_title}\n本文：{_material_for_prompt(raw_content, mode, profile)}\n\n"
        "=== 動筆前（只在心裡完成，不要輸出提綱）===\n"
        "1. 用一句話寫出本文要回答的問題，以及讀者為何現在要在意。\n"
        "2. 分開列出：素材直接支持的證據、你的推論、目前未知；不要把三者混寫。\n"
        "3. 找出最強反方。若素材無法裁決，就誠實保留；不要用語氣掩蓋證據缺口。\n"
        "4. 刪掉不能推進理解的背景、術語、比喻與形容詞。先說人話，術語只在能增加解析度時補在後面。\n\n"
        "=== 事實紀律 ===\n"
        "只使用原始素材中的外部事實。素材沒有的數字、日期、人名、職稱與引述不可補寫。"
        "可以做分析，但要用『我傾向』『目前看來』『仍待觀察』等自然語句讓推論與未知可辨。"
        "不要寫『據業內傳出』『市場普遍認為』這類無來源背書。\n\n"
        "=== 呈現 ===\n"
        "標題 ≤15 字，只承諾一件事；副標補上最重要的具體反差。開頭兩段交代背景與本文問題。"
        "正文使用 3–5 個能單獨讀懂的內容型小標，短段落、一段一件事。"
        "不要輸出內部製程、圖片位置、搜尋指令、圖表 prompt、資料來源清單、footer 或訂閱 CTA。"
        "最後用一個本文特有、讀者能以經驗或判斷回答的**具體回信問題**收尾；禁用『你怎麼看？』。\n\n"
        "=== 輸出格式：直接回一個 JSON object ===\n"
        "{\n"
        '  "title": "...",\n'
        '  "subtitle": "...",\n'
        '  "body_markdown": "..."\n'
        "}\n"
        "不要回 markdown fence、不要加註解、不要加任何 JSON 以外的文字。"
    )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

# Backend selection. The default starts with the local Antigravity CLI and then
# falls through the configured API/CLI writers. A comma-separated env override
# controls order; the successful provider/model is recorded on the draft.
# WebSearch/WebFetch remain disabled regardless of route.
SUBSTACK_BACKEND = os.getenv(
    "SUBSTACK_COMPOSER_BACKEND", "codex_cli,claude_cli"
).lower()


_KNOWN_BACKENDS = {
    "codex_cli",
    "antigravity_cli",
    "claude_cli",
    "gemini_cli",
    "gemini",
    "opencode",
    "groq",
    "cerebras",
}


def _resolve_backends() -> Optional[tuple]:
    """Map env-var string → call_for_json `backends` tuple (按序嘗試).

    Supports a comma-separated chain, for example
    ``SUBSTACK_COMPOSER_BACKEND=codex_cli,claude_cli``.
    """
    # 逗號清單 → tuple（最彈性、最直白）
    if "," in SUBSTACK_BACKEND:
        chain = tuple(b for b in (x.strip() for x in SUBSTACK_BACKEND.split(",")) if b in _KNOWN_BACKENDS)
        if chain:
            return chain
    if SUBSTACK_BACKEND in ("default", "auto", "fallback"):
        # Compatibility alias retained for older operator environments.
        return ("codex_cli", "claude_cli")
    if SUBSTACK_BACKEND == "claude_cli":
        # A single-backend override stays single-backend. Gemini is deliberately
        # absent from the Windows editorial writer contract.
        return ("claude_cli",)
    if SUBSTACK_BACKEND in _KNOWN_BACKENDS:  # 強制單一後端
        return (SUBSTACK_BACKEND,)
    print(
        f"[SubstackComposer] ⚠️ Unknown SUBSTACK_COMPOSER_BACKEND={SUBSTACK_BACKEND!r}; "
        f"defaulting to codex_cli→claude_cli."
    )
    return ("codex_cli", "claude_cli")


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
    if provider == "codex_cli":
        return f"Codex CLI · 模型 {m}"
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
    mode: Literal["morning", "evening", "podcast", "company"] = "morning",
    topic_category: str = "other",
    editorial_note: str = "",
    editorial_profile: Literal["auto", "daily", "weekly"] = "auto",
    has_deep_bundle: bool = False,
    temperature: float = 0.4,
) -> Optional[SubstackDraft]:
    """產出單篇 Substack 長文草稿。

    Architecture:
      - 預設依 SUBSTACK_COMPOSER_BACKEND 的 writer chain 依序嘗試。
      - WebSearch / WebFetch 明確停用；外部事實只來自預抓素材。
      - Daily / Weekly profile 決定深度與字數，不改變 source-selection mode。

    Returns:
        SubstackDraft on success.
        None on LLM failure (caller 必須 skip 並 notify user).
    """
    profile = resolve_editorial_profile(
        mode,
        override=editorial_profile,
        has_deep_bundle=has_deep_bundle,
    )
    system = _build_system_instruction(profile)
    prompt = _build_user_prompt(
        raw_title=title,
        raw_content=content,
        mode=mode,
        topic_category=topic_category,
        editorial_note=editorial_note,
        profile=profile,
    )

    backends = _resolve_backends()
    result = await call_for_json(
        system=system,
        prompt=prompt,
        response_model=SubstackDraft,
        temperature=temperature,
        timeout_s=1300,  # Weekly 最長 4200 字；保留本機 CLI 排隊與一次 retry 的餘裕。
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
        print(f"BODY LEN: {_count_chinese_chars(d.body_markdown)} 字")
        warnings = audit_substack_draft(d)
        if warnings:
            print("\n⚠️ AUDIT WARNINGS:")
            for w in warnings:
                print(f"  - {w}")
        else:
            print("\n✅ Audit clean")

    asyncio.run(_smoke())
