"""
News Radar · Substack Editorial Composer
=========================================

為什麼獨立於 src/composer.py（三平台社群文）：

    1. 規格根本不同——三平台是短貼文，Substack 是依題型分流的長文。
       Daily、Podcast 與公司分析有不同的論證骨架與篇幅。
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
from typing import Any, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field, field_validator, model_validator
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
        # 2026-08-10：原為 24，但「≤15 字」在 prompt 裡宣告四次，
        # 等於規則講了沒人執行——22 字的標題會安靜通過。以宣告值為準。
        max_length=15,
    )
    subtitle: str = Field(
        ...,
        description="副標。作為 Substack 列表頁／email 預覽的閱讀勾子。不可重複 title。",
        min_length=10,
        max_length=80,
    )
    # 2026-08-12：Substack 草稿本來就有 search_engine_title／search_engine_description
    # 兩個欄位，我們一直留空（實測既有草稿全是 None），於是搜尋引擎拿到的是那個
    # 15 字的鉤子標題——鉤子對人有效，對搜尋沒有任何線索。這兩欄是「給機器讀的
    # 那一版」，跟 title/subtitle 分工，不是同一句話換句話說。
    seo_title: str = Field(
        ...,
        description=(
            "搜尋引擎標題，≤60 字元。直述這篇在講什麼，要含公司／產品／主題的全名，"
            "不是鉤子。可以跟 title 完全不同。"
        ),
        max_length=60,   # 同上：不設下限，補值路徑必須永遠能成立
    )
    seo_description: str = Field(
        ...,
        description=(
            "搜尋結果摘要，60–160 字元，一到兩句。平鋪直敘講出本文的實際結論或發現，"
            "不堆關鍵字、不留懸念。查不到的事不要編。"
        ),
        # 字數下限只寫在 description 裡要求模型，不設 min_length：下限一旦成為
        # 驗證硬牆，短副標退化出來的補值就會反過來把整篇稿子擋掉。SEO 文案是
        # 加分項，沒有任何理由讓它有權否決一篇已經寫完的文章。
        max_length=160,
    )
    # 2026-08-16：Substack 的 tag 是站內導覽與 SEO/AEO 的入口，我們一直沒填。
    # 這一欄由模型產生、由程式收斂——publication 裡已經有 263 個 tag，其中
    # 「AI Agent」與「aiagent」這類同義分裂就是放任自由輸入的結果。所以模型只
    # 負責想主題，正規化與併入既有 tag 交給 normalise_tags()。
    # 跟 SEO 兩欄同一個處理方式：schema 上必填，prompt 才會確實要求模型產出；
    # 但漏填由 validator 補成空陣列，絕不讓一個加分項否決已經寫完的稿子。
    # 下限不設（模型少給幾個仍可用），上限留著（偶爾會吐一長串）。
    tags: list[str] = Field(
        ...,
        description=(
            "3–5 個主題標籤，給讀者在站內找相關文章用。用最通行的說法（公司名、"
            "技術名、主題名），每個 2–12 字，不要加井號或標點，不要重複標題整句。"
        ),
        max_length=5,
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
    # SEO 兩欄在 schema 上是必填（prompt 才會確實要求模型產出），但漏填絕不能
    # 讓整篇作廢——一次生成約 8 分鐘且不可重試，丟掉的代價遠高於一句次等的
    # SEO 文案。所以驗證前先補值：模型有寫就用模型的，沒寫就從 subtitle 退化。
    # subtitle 本來就是「說明性」的那一句，比 15 字的鉤子適合餵搜尋引擎。
    @model_validator(mode="before")
    @classmethod
    def _backfill_seo_fields(cls, data):
        if not isinstance(data, dict):
            return data
        subtitle = (data.get("subtitle") or "").strip()
        title = (data.get("title") or "").strip()
        # tags 同理：schema 必填是為了讓 prompt 要求模型產出，不是為了有權退稿。
        raw_tags = data.get("tags")
        if not isinstance(raw_tags, list):
            # 模型偶爾把陣列寫成「AI、比特幣」這種一整串，切開比丟掉划算。
            data["tags"] = (
                [p for p in re.split(r"[、,;／/]+", str(raw_tags)) if p.strip()]
                if raw_tags
                else []
            )
        if not str(data.get("seo_title") or "").strip() and subtitle:
            data["seo_title"] = subtitle[:60]
        if not str(data.get("seo_description") or "").strip():
            fallback = subtitle
            if len(fallback) < 30:
                # subtitle 太短就往正文借：取開頭的散文句，跳過標題行與空行。
                body = str(data.get("body_markdown") or "")
                prose = [
                    ln.strip()
                    for ln in body.splitlines()
                    if ln.strip() and not ln.lstrip().startswith(("#", ">", "-", "*", "|"))
                ]
                tail = " ".join(prose[:2])
                if fallback and not fallback.endswith(("。", "！", "？", ".", "!", "?")):
                    fallback += "。"
                fallback = f"{fallback}{tail}".strip()
            data["seo_description"] = (fallback or title)[:160]
        return data

    @field_validator("title", "subtitle", "seo_title", "seo_description", mode="before")
    @classmethod
    def _truncate_headline(cls, v, info):
        if isinstance(v, str):
            cap = {
                "title": 15,
                "subtitle": 80,
                "seo_title": 60,
                "seo_description": 160,
            }.get(info.field_name)
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


class EditorialResearchBrief(BaseModel):
    """First LLM pass: source comprehension and research direction, not prose."""

    article_form: Literal["investigation", "argument", "self_growth"] = Field(
        ...,
        description="最適合這批材料的文型：調查、論證或自我成長。",
    )
    source_digest: str = Field(
        ...,
        min_length=80,
        max_length=1200,
        description="已消化的主來源摘要，保留反差、衝突與必要背景。",
    )
    compelling_exchange: str = Field(
        ...,
        min_length=30,
        max_length=1200,
        description="Podcast 保留主持人追問與來賓主張；公司文保留數字與敘事的張力。",
    )
    source_claims: list[str] = Field(
        ...,
        min_length=2,
        max_length=10,
        description="只列主來源直接支持的主張、數字或原話，不得補寫。",
    )
    tensions: list[str] = Field(
        ...,
        min_length=1,
        max_length=6,
        description="材料中尚未解決的矛盾、缺口或不同解釋。",
    )
    core_question: str = Field(..., min_length=15, max_length=180)
    author_hypothesis: str = Field(
        ...,
        min_length=20,
        max_length=300,
        description="明確標示為作者待驗證的初步判斷，不是事實。",
    )
    strongest_countercase: str = Field(..., min_length=20, max_length=360)
    research_queries: list[str] = Field(
        ...,
        min_length=3,
        max_length=5,
        description="從證據缺口導出的 3–5 個不同研究角度查詢，不得只是同義改寫。",
    )
    terms_to_explain: list[str] = Field(default_factory=list, max_length=8)
    generated_by: SkipJsonSchema[Optional[str]] = Field(default=None)


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
        "每天兩篇對談延伸",
        "每天兩篇思想延伸",
        "每天一篇思想延伸",   # 2026-08-17 起的對外節奏；模型有時會自己補上
        "覺得我哪個判斷站不住，直接回信",
        "有想法？留言區聊聊",
        "✉️ 你可以直接回信，告訴我哪個判斷值得再追",
        "點此訂閱 → 不錯過下一篇拆解",
        "免費訂閱 → 明天中午就收得到下一篇",
        "你會固定收到什麼",
        "大部分財經內容寫完不必負責",
        "如果你也受夠了讀完什麼都決定不了的文章",
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
AUDIENCE_EDITORIAL_PATH = CONFIG_DIR / "editorial_audience.md"
DAILY_EDITORIAL_PATH = CONFIG_DIR / "editorial_daily.md"
WEEKLY_EDITORIAL_PATH = CONFIG_DIR / "editorial_weekly.md"
PODCAST_EDITORIAL_PATH = CONFIG_DIR / "editorial_podcast.md"
COMPANY_EDITORIAL_PATH = CONFIG_DIR / "editorial_company.md"


@dataclass(frozen=True)
class EditorialProfile:
    name: Literal["daily", "weekly"]
    word_floor: int
    word_cap: int
    reading_minutes: str
    brief_path: Path
    article_kind: Literal["daily", "weekly", "podcast", "company"]


DAILY_PROFILE = EditorialProfile(
    "daily", 1800, 2800, "7–10", DAILY_EDITORIAL_PATH, "daily"
)
WEEKLY_PROFILE = EditorialProfile(
    "weekly", 3400, 5500, "14–20", WEEKLY_EDITORIAL_PATH, "weekly"
)
PODCAST_PROFILE = EditorialProfile(
    "weekly", 4200, 6500, "17–25", PODCAST_EDITORIAL_PATH, "podcast"
)
COMPANY_PROFILE = EditorialProfile(
    "weekly", 3800, 6000, "15–23", COMPANY_EDITORIAL_PATH, "company"
)


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
        if override == "daily":
            return DAILY_PROFILE
    if mode == "podcast":
        return PODCAST_PROFILE
    if mode == "company":
        return COMPANY_PROFILE
    if has_deep_bundle or override == "weekly":
        return WEEKLY_PROFILE
    return DAILY_PROFILE


# 曼報 KB 蒸餾的框架（manny-li-pro-kb skills/ 的唯讀副本，由上游
# sync-skills.sh 產生）。de-ai-prose 對所有文型都適用，一律載入；
# 其餘按 profile 挑，避免無關框架灌爆 context。
MANNY_SKILLS_DIR = CONFIG_DIR / "manny_skills"
# 文風三件套對所有文型都適用，一律載入。順序即執行順序：
# 先重建思考路徑（加法）→ 再清手癖（減法）→ 最後下標題。
MANNY_ALWAYS = ("counter-case-construction.md",
                "human-editorial-layer.md", "sentence-clarity.md",
                "de-ai-prose.md", "title-engine.md", "chief-editor.md")
# 用 brief_path 檔名而非 profile.name 當 key：COMPANY_PROFILE.name 是 "weekly"
# （與 WEEKLY_PROFILE 同名），只有 brief_path 分得出公司文與一般週報。
# counter-case-construction 原本列在全部四個 key，等於無條件載入，
# per-brief 分派對它毫無作用 → 移進 MANNY_ALWAYS。
MANNY_BY_BRIEF = {
    "editorial_company.md": ("company-first-principles.md", "company-teardown.md", "capital-allocation-engine.md",
                             "cycle-and-capital-flow.md", "priced-in-or-not.md"),
}


def load_manny_frameworks(brief_name: str) -> str:
    """讀取適用於此 profile 的框架。缺檔一律略過——框架是加分項，
    不該讓寫稿因為副本沒同步就整個停擺。請勿編輯副本，改上游後重跑 sync。"""
    # 分析框架先（決定寫什麼），文風層後（決定怎麼寫）。
    # 原本順序相反，導致 chief-editor 宣稱「最後一次通看」之後
    # 又接上 company-teardown 這種第一步的分析框架。
    names = MANNY_BY_BRIEF.get(brief_name, ()) + MANNY_ALWAYS
    blocks = []
    for name in names:
        try:
            text = (MANNY_SKILLS_DIR / name).read_text(encoding="utf-8")
            # 剝掉同步 header。它是給維護者的護欄（「請勿在此編輯」＋來源 commit），
            # 對模型是純雜訊，五個檔加起來 752 字元。留在檔案裡、不進 prompt。
            text = re.sub(r"^<!--.*?-->\s*", "", text, flags=re.S).strip()
        except Exception:
            continue
        if text:
            blocks.append(text)
    if not blocks:
        return ""
    return (
        "===== 分析與文風框架（曼報 Pro 知識庫蒸餾）=====\n"
        "框架是透鏡，不是內容。裡面的舉例只示範拆法，"
        "絕不可當成本期標的的事實寫進文章。\n\n"
        "**怎麼用這些框架（先讀這段）**\n"
        "以下規則分兩種，份量不同：\n\n"
        "**硬規則（只有三條，違反就是錯）**\n"
        "1. 不虛構——沒有的證據就寫「查不到」，不要補推測填洞。\n"
        "2. 每個判斷都要能被推翻——寫出在什麼條件下它算錯。\n"
        "3. 框架的舉例不是本期事實。\n\n"
        "**其餘全部是手藝建議，不是檢查表。**\n"
        "它們描述「通常這樣寫比較好」，不是「不准那樣寫」。"
        "你如果有理由違反其中一條而且文章因此更好，就違反它——"
        "刻意的破格是寫作的一部分，逐條滿足規則寫出來的文章會很悶。\n\n"
        "衝突時的優先順序：**文章好看 > 規則整齊**。"
        "當你發現為了滿足某條規則而讓句子變得彆扭，那條規則在這裡就不適用。\n\n"
        "（名詞化、比較基準、子標題冒號這幾項已有機器檢查會事後提醒，"
        "你不必在寫作時分心盯它們——先把文章寫好。）\n\n"
        + "\n\n---\n\n".join(blocks)
    )


def load_editorial_brief(profile: EditorialProfile) -> str:
    """Load the shared reader, voice, cadence contracts, and KB frameworks."""
    paths = (AUDIENCE_EDITORIAL_PATH, COMMON_EDITORIAL_PATH, profile.brief_path)
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError("missing editorial brief: " + ", ".join(str(path) for path in missing))
    parts = [path.read_text(encoding="utf-8").strip() for path in paths]
    frameworks = load_manny_frameworks(profile.brief_path.name)
    if frameworks:
        parts.append(frameworks)
    return "\n\n".join(parts)


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


# 空動詞 + 雙字動作名詞 → 還原成動詞。只收「拿掉空動詞後語意不變」的組合，
# 不做泛用規則：中文有大量「進行X」是合法的（進行曲、進行式），也有「造成」
# 後面接的是結果而非動作（造成傷亡）。所以用白名單，寧可漏抓不可改錯。
_NOMINALISATION_FIXES = {
    # 只留「拿掉空動詞後單獨成句仍然自然」的組合。
    # 實測剔除的反例：「產生影響」→「影響」在句尾會變成「這影響」（語意不完整），
    # 「給予支持」→「支持」同理。那些需要受詞才通順，天真替換會讓中文更差。
    "做出判斷": "判斷", "做出決定": "決定", "做出回應": "回應", "做出選擇": "選擇",
    "加以說明": "說明", "加以檢討": "檢討",
    "實現獲利": "獲利", "實現成長": "成長",
}
# 「對X進行評估」→「評估X」。這個要換語序，不是刪字，所以單獨處理——
# 只做刪字會得到「對市場評估」這種彆扭句。
_NOMINALISATION_REORDER = re.compile(
    # 右邊界不可省：原本抓固定兩個字，遇到「對持有的資產進行未實現損益調整」
    # 會把「未實現損益」從中間切斷，產出「未實持有的資產現損益調整」，
    # 句子壞掉且已送進草稿。加上標點／行尾邊界後，只有詞確定結束才動它。
    r"對([一-鿿]{2,8})(進行|做出|加以)([一-鿿]{2,4})(?=[，。；：、！？\s]|$)"
)


def autofix_nominalisation(draft: "SubstackDraft") -> List[str]:
    """把「進行評估」這類空動詞＋名詞化還原成動詞。

    與 autofix_dashes 同層級的確定性清理。之所以要自動改而不是繼續加指令：
    sentence-clarity 已明文禁止、audit 也在報，2026-08-10 的 MSTR 稿仍出現
    12 處。指令對這個習慣無效，跟子標題冒號是同一個劇本。

    保守設計：白名單比對，不用正則泛化。中文裡「進行」後面接的不一定是
    動作名詞（進行曲），「造成」後面常是結果而非動作（造成傷亡），
    泛用規則會改錯句子。漏抓只是留下一則警告，改錯是實質損害。
    引用區塊（>）不動，那是既定的 footer 與封面指示。
    """
    lines, hits = [], []
    for line in draft.body_markdown.split("\n"):
        if line.lstrip().startswith(">"):
            lines.append(line)
            continue
        def _reorder(m):
            hits.append(f"對{m.group(1)}{m.group(2)}{m.group(3)}")
            return f"{m.group(3)}{m.group(1)}"
        line = _NOMINALISATION_REORDER.sub(_reorder, line)
        for bad, good in _NOMINALISATION_FIXES.items():
            if bad in line:
                hits.append(bad)
                line = line.replace(bad, good)
        lines.append(line)
    if not hits:
        return []
    draft.body_markdown = "\n".join(lines)
    uniq = "」「".join(dict.fromkeys(hits))
    return [f"[自動修正:名詞化] 「{uniq}」共 {len(hits)} 處還原成動詞"]


def autofix_heading_colons(draft: "SubstackDraft") -> List[str]:
    """子標題的冒號：擇一保留，不是兩邊都要。

    「主題：註解」對模型是安全牌——不必決定要突出哪一半。但讀者會一路讀到
    「報告小節」的節奏。de-ai-prose 明文禁止、audit 也在報，Flash 仍照犯
    （2026-08-10 從 4/5 降到 1/6，沒有歸零），所以改成自動處理。

    規則：保留資訊量較高的那一半。中文子標題的資訊通常在冒號右邊
    （「最強反方：證券化槓桿帶來的期權溢價」→ 右半才是內容），
    但右半太短時保留左半。人物訪談的冒號是掛人名，不動。
    """
    _PERSON_HINT = ("專訪", "訪談", "對談", "問答")
    lines, fixed = [], []
    for line in draft.body_markdown.split("\n"):
        stripped = line.lstrip()
        if not stripped.startswith("#") or not any(m in line for m in ("：", ":")):
            lines.append(line)
            continue
        if any(h in line for h in _PERSON_HINT):
            lines.append(line)
            continue
        hashes = stripped[: len(stripped) - len(stripped.lstrip("#"))]
        text = stripped.lstrip("#").strip()
        sep = "：" if "：" in text else ":"
        left, _, right = text.partition(sep)
        left, right = left.strip(), right.strip()
        if not left or not right:
            lines.append(line)
            continue
        keep = right if len(right) >= 6 else left
        fixed.append(text)
        lines.append(f"{hashes} {keep}")
    if not fixed:
        return []
    draft.body_markdown = "\n".join(lines)
    return [f"[自動修正:子標題冒號] {len(fixed)} 個擇一保留（例：{fixed[0][:28]}）"]


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

    # 子標題的冒號。原本只檢查主標題，於是「主題：註解」式的子標題完全沒被擋。
    # 2026-08-09 實測有明顯模型差異：同一天同一條管線，
    # Gemini 3.6 Flash 寫的 Salesforce 文 4/5 個子標題帶冒號
    # （「錢從哪裡來：深植企業骨血的軟體帝國」），
    # Opus 4.6 寫的亞當·斯密文 0/5。
    # 冒號式子標題對模型是安全牌——不必決定要突出哪一半，兩邊都塞就好；
    # 但讀者會一路讀到「報告小節」的節奏，而不是有人帶著他想。
    # 指令攔不住（de-ai-prose 已寫明仍照犯），所以在這裡用機器擋。
    _headings = [
        line.lstrip("#").strip()
        for line in body.splitlines()
        if line.lstrip().startswith("#")
    ]
    # sentence-clarity ② 名詞化：動作被藏進抽象名詞，再補一個空動詞去帶它。
    # 「進行評估」→「評估」。這類有明確字面特徵，不必靠模型自覺。
    # 只報真正的名詞化，不報正常搭配。原本用「動詞 + 任意 2-4 漢字」的粗略正則，
    # 把「產生現金流」「造成的帳面虧損」「給予估值溢價」這類完全正確的中文全算進去——
    # 2026-08-10 的 MSTR 稿被報 13 處，逐一檢視後幾乎全是誤報。
    # 誤報比漏報有害：它會逼寫手去改本來就對的句子，也讓真正的問題被雜訊蓋掉。
    # 改成與 autofix 共用同一份白名單，兩者判準一致。
    _nominal = [k for k in _NOMINALISATION_FIXES if k in body]
    _nominal += [m.group(0) for m in _NOMINALISATION_REORDER.finditer(body)]
    if len(_nominal) >= 2:
        warnings.append(
            f"[名詞化] 「{'」「'.join(dict.fromkeys(_nominal))}」等 {len(_nominal)} 處；"
            "動作藏在抽象名詞裡，還原成動詞（進行評估→評估）。"
        )

    # sentence-clarity ⑧ 比較級沒有基準。分析型文章裡，沒有「跟什麼比」的
    # 比較級等於沒有資訊。用「同句是否出現數字」當近似判準。
    _bare_compare = []
    for _sent in re.split(r"[。！？\n]", body):
        if re.search(r"(更高|更快|更有效|更便宜|更成功|大幅|明顯|領先)", _sent) \
           and not re.search(r"\d", _sent):
            _bare_compare.append(_sent.strip()[:22])
    if _bare_compare:
        warnings.append(
            f"[比較無基準] {len(_bare_compare)} 句用了比較級卻沒給數字"
            f"（例：{_bare_compare[0]}）；補上跟什麼比、差多少。"
        )

    _colon_headings = [h for h in _headings if any(m in h for m in ("：", ":"))]
    if _colon_headings:
        warnings.append(
            f"[子標題雙焦點] {len(_colon_headings)}/{len(_headings)} 個子標題含冒號"
            f"（例：{_colon_headings[0][:24]}）；子標題只講一件事，冒號前後擇一。"
        )

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
        if compact and not compact.startswith((">", "- ", "* ", "#")) and len(compact) > 260:
            warnings.append("[段落過長] 有單段超過 260 字，請拆成一段一件事。")
            break

    if "我" not in body:
        warnings.append("[缺少作者聲音] 全文沒有第一人稱判斷；請讓讀者知道作者如何理解證據。")

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
        f"本次採 {profile.name.upper()}/{profile.article_kind} profile：{word_floor}–{word_cap} 個中文字，"
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


def _build_research_brief_prompt(
    *,
    raw_title: str,
    raw_content: str,
    mode: str,
    topic_category: str,
    profile: EditorialProfile,
) -> str:
    """First pass: understand the primary source before asking the web anything."""
    if mode not in {"podcast", "company"}:
        raise ValueError("research brief is only required for podcast/company")
    if mode == "podcast":
        source_contract = (
            "先完成 Podcast 理解，再提出網路調研方向。用 300–800 字寫出"
            "引人入勝的摘要：讀者要能看見主持人的追問、來賓的主張、"
            "兩者真正的分歧與為何值得繼續追。不要按時間軸摘要整集。"
        )
    else:
        source_contract = (
            "先完成財報理解：分開公司敘事、財報事實、數字間的張力與尚未揭露之處。"
            "若選 self_growth，必須從財報證據導出可檢驗的決策方法，不可寫成心靈鳥湯。"
        )
    return (
        "=== 階段一：主來源消化 ===\n"
        f"題型：{mode}\n分類：{topic_category}\n預定深度：{profile.word_floor}–{profile.word_cap} 字\n"
        f"{source_contract}\n\n"
        "此階段不得使用網路資料，也不寫文章。將主來源直接支持的資訊、"
        "作者初步假說與目前未知分開。從未知與反方導出 3–5 個不同研究角度的"
        "延伸調研查詢；至少涵蓋官方或第一手資料、量化或實證證據、獨立分析與"
        "最強反方，查詢不可只是同義改寫。"
        "查詢只是待調查清單，不得把延伸資料當成已經完成。\n\n"
        f"=== 主來源 ===\n標題：{raw_title}\n"
        f"{_material_for_prompt(raw_content, mode, profile)}\n\n"
        "直接回傳符合 EditorialResearchBrief schema 的 JSON。"
    )


_ARTICLE_FORM_CONTRACTS = {
    "investigation": (
        "調查型：從對談或數字中的異常開場 → 定義要查的問題 → "
        "逐步建立證據鏈 → 展示衝突證據與資料缺口 → 給出有邊界的判斷。"
    ),
    "argument": (
        "論證型：先還原主來源最強的主張 → 提出我的論點與理由 → "
        "以多源證據檢驗 → 用最有力的版本處理反方 → 修正後的結論與後續訊號。"
    ),
    "self_growth": (
        "自我成長型：從對談帶來的認知衝突開場 → 拆出背後機制 → "
        "用研究區分適用與不適用情境 → 提出可實驗的行動 → 說明什麼結果會讓我改變想法。"
    ),
}


def _build_deep_writer_prompt(
    *,
    raw_title: str,
    mode: str,
    topic_category: str,
    editorial_note: str,
    profile: EditorialProfile,
    research_brief: EditorialResearchBrief,
    research_sources: Sequence[Any],
    social_reach: Any = None,
) -> str:
    from substack_radar.editorial_research import (
        prompt_block,
        social_prompt_block,
        validate_research_sources,
    )

    sources = validate_research_sources(research_sources)
    form_contract = _ARTICLE_FORM_CONTRACTS[research_brief.article_form]
    word_floor, word_cap = word_range_for(profile)
    terms = "、".join(research_brief.terms_to_explain) or "（無預設；寫作時自行判斷）"
    claims = "\n".join(f"- {claim}" for claim in research_brief.source_claims)
    tensions = "\n".join(f"- {item}" for item in research_brief.tensions)
    return (
        "=== 階段二：整合成讀者文章 ===\n"
        f"Profile：{profile.name}/{profile.article_kind}（{word_floor}–{word_cap} 字）\n"
        f"素材類型：{mode}\n主題分類：{topic_category}\n原始標題：{raw_title}\n"
        f"文型：{research_brief.article_form}\n{form_contract}\n\n"
        f"=== 編輯指令 ===\n{editorial_note or '沒有額外指令；依證據做最佳編輯判斷。'}\n\n"
        "=== 主來源萃取（已在階段一消化）===\n"
        f"摘要：{research_brief.source_digest}\n"
        f"關鍵交鋒：{research_brief.compelling_exchange}\n"
        f"主來源可支持的說法：\n{claims}\n"
        f"尚未解決：\n{tensions}\n\n"
        "=== 延伸證據（5–10 個去重、可點擊來源）===\n"
        f"{prompt_block(sources)}\n\n"
        f"{social_prompt_block(social_reach)}\n\n"
        "=== 作者判斷邊界 ===\n"
        f"核心問題：{research_brief.core_question}\n"
        f"作者假說：{research_brief.author_hypothesis}\n"
        f"最強反方：{research_brief.strongest_countercase}\n"
        "把主來源的主張、延伸證據、作者推論與未知分開；"
        "外部事實必須能對回上方來源，衝突時不擅自裁決。\n\n"
        "=== 寫前主張—證據圖（只在內部完成，不輸出）===\n"
        "先把每個複合說法拆成單一可查證斷言，再將每個斷言對到它在文章中的角色"
        "（背景、機制、數據、反方或限制）與證據。只能使用上方編號來源；找不到"
        "證據的斷言要刪除，或清楚降為作者假說與未知。單一來源的說法必須具名歸屬"
        "並說明限制；遇到衝突證據就呈現分歧，不用來源數量假裝確定。正文按子問題"
        "組織，不按來源逐篇介紹，也不為了顯得研究充分而重複塞引用。"
        "\n\n=== 具名歸屬鐵則 ===\n寫「根據 X」「X 指出」「X 統計」時，X **只能是上方編號來源裡真的出現過的機構或媒體**。\n不可以寫「MIC 等研究機構」「供應鏈訪查」「業者公開預估」「分析機構如某某投資總監」這種聽起來有出處、實際上查不到的歸屬——2026-08-18 的瑞昱與聯發科兩篇都犯了同一類錯，每一篇都要另一個模型回頭擦。\n來源自己引述了第三方（例如「理財周刊引述 Dell 統計」），要把兩層都寫出來，不可以只寫 Dell。\n找不到出處的數字：改寫成不帶數字的判斷，或整句刪掉。寧可少一個數字，不要多一個假出處。\n\n"
        "=== 作者聲音 ===\n"
        "以第一人稱「我」書寫自己已消化後的理解、推理與判斷，"
        "像一個真正經營個人部落格的作者。不得虛構親身經驗、採訪、見聞或情緒；"
        "不確定就說不確定。\n\n"
        "=== 降低認知負擔 ===\n"
        "5–10 個來源是作者的研究投入，不是要全數塞進正文。"
        "只保留會改變讀者理解或作者判斷的證據；其餘由 pipeline 放在來源區。"
        "先過資訊價值閘門：一段至少要帶來新證據、必要的因果步驟、最強反方、"
        "必要定義或讀者後果之一，否則不寫。支持同一件事的來源合併呈現；"
        "若刪掉一段仍不影響論證或讀者判斷，就刪掉。"
        "一節只推進一個子問題，先給結論句再解釋，每段只放一個重點。"
        "先說人話，專有名詞第一次出現時緊接短註；當至少三個名詞無法避免，"
        "才加一個簡短的「專有名詞註解」段落。"
        f"優先注意的詞：{terms}。\n\n"
        "=== 呈現 ===\n"
        "Podcast 文先讓讀者看見那段值得追的對談與觀點，再進入延伸調研；"
        "不寫成逐字稿摘要。公司文先交代商業問題，再讓數字檢驗敘事。"
        "使用 5–7 個內容型小標。標題 ≤15 字，副標補具體反差。"
        "最後留一個本文特有的具體回信問題。"
        "不輸出製程、生圖 prompt、來源清單、footer 或訂閱 CTA。\n\n"
        "=== 輸出格式：直接回一個 JSON object ===\n"
        '{"title":"...","subtitle":"...","body_markdown":"..."}\n'
        "不要回 markdown fence、註解或 JSON 以外文字。"
    )


def _build_user_prompt(
    *,
    raw_title: str,
    raw_content: str,
    mode: str,
    topic_category: str,
    editorial_note: str,
    profile: EditorialProfile,
    research_brief: Optional[EditorialResearchBrief] = None,
    research_sources: Sequence[Any] = (),
    social_reach: Any = None,
) -> str:
    if research_brief is not None:
        return _build_deep_writer_prompt(
            raw_title=raw_title,
            mode=mode,
            topic_category=topic_category,
            editorial_note=editorial_note,
            profile=profile,
            research_brief=research_brief,
            research_sources=research_sources,
            social_reach=social_reach,
        )
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
        "不要寫『據業內傳出』『市場普遍認為』這類無來源背書。"
        "\n\n=== 具名歸屬鐵則 ===\n寫「根據 X」「X 指出」「X 統計」時，X **只能是上方編號來源裡真的出現過的機構或媒體**。\n不可以寫「MIC 等研究機構」「供應鏈訪查」「業者公開預估」「分析機構如某某投資總監」這種聽起來有出處、實際上查不到的歸屬——2026-08-18 的瑞昱與聯發科兩篇都犯了同一類錯，每一篇都要另一個模型回頭擦。\n來源自己引述了第三方（例如「理財周刊引述 Dell 統計」），要把兩層都寫出來，不可以只寫 Dell。\n找不到出處的數字：改寫成不帶數字的判斷，或整句刪掉。寧可少一個數字，不要多一個假出處。\n\n"
        "=== 呈現 ===\n"
        "標題 ≤15 字，只承諾一件事；副標補上最重要的具體反差。開頭兩段交代背景與本文問題。"
        "正文使用 5–7 個能單獨讀懂的內容型小標，短段落、一段一件事。"
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


def _record_usage(result: Any, *, stage: str) -> None:
    """Meter both comprehension and writing calls without making drafts depend on it."""
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
            f"[SubstackComposer] 💰 {stage} usage logged: provider={result.provider} "
            f"in={result.input_tokens} out={result.output_tokens} "
            f"cost=${result.cost_usd:.4f}"
        )
    except Exception as exc:
        print(f"[SubstackComposer] ⚠️ {stage} token metering skipped: {exc}")


async def plan_editorial_research(
    *,
    title: str,
    content: str,
    mode: Literal["podcast", "company"],
    topic_category: str = "other",
    editorial_profile: Literal["auto", "daily", "weekly"] = "auto",
    has_deep_bundle: bool = False,
    temperature: float = 0.2,
) -> Optional[EditorialResearchBrief]:
    """Digest the primary source and identify evidence gaps before web research."""
    profile = resolve_editorial_profile(
        mode,
        override=editorial_profile,
        has_deep_bundle=has_deep_bundle,
    )
    prompt = _build_research_brief_prompt(
        raw_title=title,
        raw_content=content,
        mode=mode,
        topic_category=topic_category,
        profile=profile,
    )
    result = await call_for_json(
        system=(
            "你是 HsinTiger 的研究編輯。此階段只負責消化主來源、"
            "找出值得追問的衝突與證據缺口；不寫文章、不使用外部知識補洞。"
        ),
        prompt=prompt,
        response_model=EditorialResearchBrief,
        temperature=temperature,
        timeout_s=1100,
        backends=_resolve_backends(),
        disallowed_tools=("WebSearch", "WebFetch"),
    )
    _record_usage(result, stage="research-brief")
    if result.data is None:
        print(
            "[SubstackComposer] ❌ 主來源消化失敗，不進入延伸調研："
            f"{result.raw_error}"
        )
        return None
    result.data.generated_by = describe_route(result.provider, result.model)
    return result.data


async def compose_substack_article(
    *,
    title: str,
    content: str,
    mode: Literal["morning", "evening", "podcast", "company"] = "morning",
    topic_category: str = "other",
    editorial_note: str = "",
    editorial_profile: Literal["auto", "daily", "weekly"] = "auto",
    has_deep_bundle: bool = False,
    research_brief: Optional[EditorialResearchBrief] = None,
    research_sources: Sequence[Any] = (),
    social_reach: Any = None,
    temperature: float = 0.4,
) -> Optional[SubstackDraft]:
    """產出單篇 Substack 長文草稿。

    Architecture:
      - 預設依 SUBSTACK_COMPOSER_BACKEND 的 writer chain 依序嘗試。
      - Podcast/company 必須先有 EditorialResearchBrief 與 5–10 源 evidence pack。
      - Writer 的 WebSearch / WebFetch 仍停用；它只能用已驗證研究包。
      - 題型 profile 決定論證骨架、字數與認知負擔規則。

    Returns:
        SubstackDraft on success.
        None on LLM failure (caller 必須 skip 並 notify user).
    """
    profile = resolve_editorial_profile(
        mode,
        override=editorial_profile,
        has_deep_bundle=has_deep_bundle,
    )
    if mode in {"podcast", "company"} and research_brief is None:
        print("[SubstackComposer] ❌ 深度題型缺 EditorialResearchBrief，拒絕假裝完成調研。")
        return None
    system = _build_system_instruction(profile)
    try:
        prompt = _build_user_prompt(
            raw_title=title,
            raw_content=content,
            mode=mode,
            topic_category=topic_category,
            editorial_note=editorial_note,
            profile=profile,
            research_brief=research_brief,
            research_sources=research_sources,
            social_reach=social_reach,
        )
    except Exception as exc:
        print(f"[SubstackComposer] ❌ 研究包未通過，拒絕產稿：{exc}")
        return None

    backends = _resolve_backends()
    result = await call_for_json(
        system=system,
        prompt=prompt,
        response_model=SubstackDraft,
        temperature=temperature,
        timeout_s=1800,  # Podcast 最長 6500 字；保留本機 CLI 排隊與 retry 餘裕。
        backends=backends,
        # 2026-05-30: 關掉 agentic 上網，逼 composer 只用預抓素材（token-free 改版）。
        disallowed_tools=("WebSearch", "WebFetch"),
    )

    _record_usage(result, stage="final-writer")

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
