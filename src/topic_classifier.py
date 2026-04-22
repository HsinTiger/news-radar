"""
News Radar · Topic Classifier（Phase 8.20 Step 2 + Topic-3 redo 2026-04-22）
=========================================================================
三層策略（由高信心 → 低信心 → LLM 托底）：
  1. Disambiguation（高信心 AND-of-OR 規則）
       針對 8 個『公司名 + 事件/產品線』這種多詞共現才能判準的 case，給出
       confidence 0.78–0.82 的高信心結論。命中就直接回，不再跑後面的 LLM。
       例：『台積電』+『法說/Q3/毛利』→ earnings 而非 supply_chain。
  2. Simple keyword fast-path（免 LLM 費用）
       讀 config/topic_keywords.yaml，第一個命中的類別（taxonomy 順序為準）
       勝出。confidence=0.60，保守值——讓呼叫端若想要高信心可再追 LLM。
       命中後會先過一層 exclusion veto（6 條）：例如『台積電 + 慈善捐款』就
       veto supply_chain，落到 LLM 那條路，避免 FP 污染 back-prop。
  3. LLM fallback（前兩層都沒有結論才跑）
       用 call_for_json + pydantic schema，強制輸出 category_id + confidence +
       rationale。LLM 雙路（Claude CLI / Gemini）都失敗則回 other(0.0)。

設計原則：
  - **單一事實來源**：類別 id / 顯示名 / 描述寫在 src.topic_taxonomy；
    這邊只處理『分類邏輯』，不 hard-code 權重或顯示名。
  - **層級透明**：每層都有獨立 public API（`match_disambiguation` /
    `classify_topic_keyword` / `is_vetoed_by_exclusion`），測試可單獨驗證。
  - **雙路可回 None**：LLM 雙路都失敗時 classifier 不假裝，回
    TopicClassification(category_id='other', confidence=0.0,
    rationale='classifier_unavailable')——讓呼叫端看得出 signal 是否可信。
  - **pure-ish**：前兩層完全 pure；只有 LLM 層是 async。
  - **冷啟動友善**：沒有 LLM 的 sandbox（無網路、無 pydantic）仍能跑前兩層，
    方便 backfill 腳本重建歷史分類。

—— 2026-04-21 overnight, 2026-04-22 Topic-3 redo, Cowork Claude
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from src.topic_taxonomy import (
    TOPIC_CATEGORIES,
    category_ids,
    classifier_prompt_block,
    taxonomy_as_dict,
)

# 只在 LLM path 才 import pydantic；keyword path 無依賴，便於 backfill
# 腳本在 pip 不完整的環境下跑。
try:
    from pydantic import BaseModel, Field, ValidationError
    _HAS_PYDANTIC = True
except ImportError:  # pragma: no cover - sandbox fallback only
    BaseModel = object  # type: ignore
    Field = lambda *a, **kw: None  # type: ignore
    ValidationError = Exception  # type: ignore
    _HAS_PYDANTIC = False


# ---------- output schema ----------

@dataclass(frozen=True)
class TopicClassification:
    """Classifier 的產出。category_id 保證在 taxonomy 內。"""
    category_id: str
    confidence: float  # 0.0 ~ 1.0
    rationale: str     # 一句話，寫進 news_items.topic_rationale


# LLM 版本的 schema（for call_for_json）。只在 pydantic 可用時會被實際使用。
if _HAS_PYDANTIC:
    class _LLMTopicOut(BaseModel):  # type: ignore[misc]
        category_id: str = Field(description="必須是 taxonomy 裡的 snake_case id")
        confidence: float = Field(description="0.0~1.0，對分類的信心")
        rationale: str = Field(description="一句話解釋為何歸入此類")


# ---------- Layer 1: Disambiguation（高信心 AND-of-OR 規則）----------

@dataclass(frozen=True)
class DisambiguationRule:
    """多詞共現才判準的情況。
    when_all_groups：外層 tuple 是 AND，inner tuple 是 OR。
      例：((台積電, TSMC), (法說, Q3, earnings)) = 必須有 TSMC 某個說法 AND 有 earnings 某個說法。
    unless_any：任一出現就不觸發（用來讓『Apple Intelligence』不被 apple_hardware 搶走）。
    """
    name: str
    when_all_groups: tuple
    unless_any: tuple
    category_id: str
    confidence: float
    rationale_hint: str


# 所有 needle 在比對時會先 .lower()，所以 tuple 裡寫原始中英文大小寫混合都可以。
DISAMBIGUATION_RULES: tuple = (
    DisambiguationRule(
        name="tsmc_earnings",
        when_all_groups=(
            ("台積電", "TSMC"),
            ("法說", "Q1 營收", "Q2 營收", "Q3 營收", "Q4 營收", "毛利率", "EPS", "guidance", "earnings"),
        ),
        unless_any=(),
        category_id="earnings",
        confidence=0.80,
        rationale_hint="disambig:tsmc+earnings",
    ),
    DisambiguationRule(
        name="nvidia_earnings",
        when_all_groups=(
            ("Nvidia", "輝達", "NVDA"),
            ("法說", "earnings", "guidance", "Q1", "Q2", "Q3", "Q4", "毛利率", "beat estimates", "missed estimates"),
        ),
        unless_any=(),
        category_id="earnings",
        confidence=0.80,
        rationale_hint="disambig:nvidia+earnings",
    ),
    DisambiguationRule(
        name="apple_hardware",
        when_all_groups=(
            ("Apple", "蘋果"),
            ("iPhone", "iPad", "MacBook", "Apple Watch", "Vision Pro", "AirPods"),
        ),
        unless_any=("Apple Intelligence",),
        category_id="tech_product_launch",
        confidence=0.78,
        rationale_hint="disambig:apple+hardware",
    ),
    DisambiguationRule(
        name="apple_ai_application",
        when_all_groups=(
            ("Apple Intelligence",),
        ),
        unless_any=(),
        category_id="ai_application",
        confidence=0.80,
        rationale_hint="disambig:apple_intelligence",
    ),
    DisambiguationRule(
        name="google_gemini_model",
        when_all_groups=(
            ("Gemini",),
            ("Google", "DeepMind", "Pro", "Ultra", "3", "2.5", "Flash"),
        ),
        unless_any=("雙子座", "星座", "占星"),
        category_id="ai_model",
        confidence=0.82,
        rationale_hint="disambig:google+gemini_model",
    ),
    DisambiguationRule(
        name="copilot_as_agent",
        when_all_groups=(
            ("Copilot",),
            ("agent", "multi-step", "autonomous", "Agent Builder", "agentic"),
        ),
        unless_any=(),
        category_id="ai_agent",
        confidence=0.78,
        rationale_hint="disambig:copilot_as_agent",
    ),
    DisambiguationRule(
        name="meta_device",
        when_all_groups=(
            ("Meta",),
            ("Quest", "Ray-Ban", "Orion", "頭戴", "VR 頭盔"),
        ),
        unless_any=("Llama",),
        category_id="tech_product_launch",
        confidence=0.78,
        rationale_hint="disambig:meta+device",
    ),
    DisambiguationRule(
        name="openai_reasoning_model",
        when_all_groups=(
            ("OpenAI", "ChatGPT"),
            ("o3", "o4", "reasoning model", "inference-time compute", "推理模型"),
        ),
        unless_any=(),
        category_id="ai_model",
        confidence=0.80,
        rationale_hint="disambig:openai_reasoning",
    ),
)


# ---------- Layer 2.5: Exclusion Veto（simple keyword FP 擋板）----------

@dataclass(frozen=True)
class ExclusionPattern:
    """當 simple keyword 把一篇明顯不是 X 類的文章判成 X 類時，veto 掉讓它走 LLM。
    trigger_any：這類 keyword 一定會出現（例：台積電、Grok），否則 veto 根本碰不到。
    veto_any：但若 haystack 同時含這些負面 context，就判定 keyword 是 FP。
    """
    name: str
    category_id: str
    trigger_any: tuple
    veto_any: tuple


EXCLUSION_PATTERNS: tuple = (
    ExclusionPattern(
        name="tsmc_philanthropy",
        category_id="supply_chain",
        trigger_any=("台積電", "TSMC"),
        veto_any=("慈善", "捐款", "贊助", "公益", "基金會", "義賣"),
    ),
    ExclusionPattern(
        name="grok_literature",
        category_id="ai_model",
        trigger_any=("Grok",),
        veto_any=("Heinlein", "海萊恩", "異鄉異客", "科幻小說", "俚語"),
    ),
    ExclusionPattern(
        name="mistral_weather",
        category_id="ai_model",
        trigger_any=("Mistral",),
        veto_any=("地中海", "強風", "氣象", "風速", "風暴"),
    ),
    ExclusionPattern(
        name="copilot_aviation",
        category_id="ai_application",
        trigger_any=("Copilot",),
        veto_any=("副駕駛", "機師", "航班", "航空公司", "駕駛艙", "波音", "空中巴士"),
    ),
    ExclusionPattern(
        name="chatgpt_meme",
        category_id="ai_application",
        trigger_any=("ChatGPT Plus",),
        veto_any=("迷因", "梗圖", "惡搞", "諷刺漫畫", "網路笑話"),
    ),
    ExclusionPattern(
        name="deepseek_mining",
        category_id="ai_model",
        trigger_any=("DeepSeek",),
        veto_any=("挖礦", "礦業", "地底探勘", "鑽孔", "深海鑽探"),
    ),
)


# ---------- Layer 1/2.5 helpers ----------

def _build_haystack(title: str, content: str) -> str:
    """統一 lowercase 後的比對文本。和 classify_topic_keyword 對齊。"""
    return f"{title or ''}\n{(content or '')[:1500]}".lower()


def _any_in(needles: tuple, haystack: str) -> bool:
    """需要時做 case-insensitive 比對。needles 可以有大小寫混合。"""
    return any((n or "").lower() in haystack for n in needles if n)


def _rule_fires(rule: DisambiguationRule, haystack: str) -> bool:
    """AND-of-OR：每個 group 至少命中一個、且 unless_any 全不中。"""
    for group in rule.when_all_groups:
        if not _any_in(group, haystack):
            return False
    if rule.unless_any and _any_in(rule.unless_any, haystack):
        return False
    return True


def match_disambiguation(title: str, content: str) -> Optional[TopicClassification]:
    """Layer 1：跑 DISAMBIGUATION_RULES；第一條命中即回（tuple 順序即優先級）。"""
    haystack = _build_haystack(title, content)
    for rule in DISAMBIGUATION_RULES:
        if _rule_fires(rule, haystack):
            return TopicClassification(
                category_id=rule.category_id,
                confidence=rule.confidence,
                rationale=rule.rationale_hint,
            )
    return None


def is_vetoed_by_exclusion(title: str, content: str, tentative_cat_id: str) -> bool:
    """Layer 2.5：tentative 分類若是某類且 haystack 含負面 context，判 FP。"""
    haystack = _build_haystack(title, content)
    for rule in EXCLUSION_PATTERNS:
        if rule.category_id != tentative_cat_id:
            continue
        if _any_in(rule.trigger_any, haystack) and _any_in(rule.veto_any, haystack):
            return True
    return False


# ---------- keyword fast-path ----------

_KEYWORDS_CACHE: Optional[Dict[str, List[str]]] = None
_KEYWORDS_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "topic_keywords.yaml"
)


def _load_keywords() -> Dict[str, List[str]]:
    """讀一次 keyword yaml；錯誤時回空 dict（讓 LLM 路接手）。"""
    global _KEYWORDS_CACHE
    if _KEYWORDS_CACHE is not None:
        return _KEYWORDS_CACHE

    if not _KEYWORDS_PATH.exists():
        print(f"[topic_classifier] ⚠️ {_KEYWORDS_PATH} 不存在 → keyword path disabled")
        _KEYWORDS_CACHE = {}
        return _KEYWORDS_CACHE

    try:
        import yaml  # type: ignore
        with _KEYWORDS_PATH.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        cleaned: Dict[str, List[str]] = {}
        for cat_id, words in raw.items():
            if not isinstance(words, list):
                continue
            cleaned[cat_id] = [str(w).strip() for w in words if str(w).strip()]
        _KEYWORDS_CACHE = cleaned
    except Exception as e:
        print(f"[topic_classifier] ⚠️ yaml 解析失敗 → keyword path disabled: {e}")
        _KEYWORDS_CACHE = {}
    return _KEYWORDS_CACHE


def classify_topic_keyword(
    title: str, content: str
) -> Optional[TopicClassification]:
    """第一層：關鍵字匹配。miss 回 None，呼叫端再打 LLM。

    匹配順序 = taxonomy 裡 category 的順序（ai_model > ai_agent > ... > other），
    因此越具體的類別越優先命中。
    """
    kw = _load_keywords()
    if not kw:
        return None

    haystack = f"{title or ''}\n{(content or '')[:1500]}".lower()

    # 按 taxonomy 順序檢查（不依 dict 插入順序）
    for cat in TOPIC_CATEGORIES:
        if cat.id == "other":
            continue  # other 不靠 keyword 命中
        words = kw.get(cat.id, [])
        for w in words:
            if not w:
                continue
            if w.lower() in haystack:
                return TopicClassification(
                    category_id=cat.id,
                    confidence=0.60,
                    rationale=f"keyword_hit:'{w}'",
                )
    return None


# ---------- LLM fallback ----------

async def classify_topic_llm(
    title: str, content: str
) -> Optional[TopicClassification]:
    """第二層：打 LLM（走 llm_brain 的 call_for_json）。
    兩條 LLM 路都失敗時回 None，呼叫端自行 fallback 到 other/confidence=0。
    """
    if not _HAS_PYDANTIC:
        print("[topic_classifier] ℹ️ pydantic 不可用 → LLM path 停用")
        return None

    # Lazy import：keyword path 呼叫者不需要付 import cost
    from src.llm_brain import call_for_json  # type: ignore

    system = (
        "你是主題分類器，任務是把科技/商業新聞歸到 News Radar 的 10 個穩定類別。\n"
        "嚴格規則：\n"
        " 1. 回傳的 category_id 必須是下列其中一個精確字串（snake_case），不可自創。\n"
        " 2. 若同時沾多類，選『最具體』的（例如『台積電法說』選 earnings 而非 tw_stocks）。\n"
        " 3. confidence 若 < 0.5 請如實回報；不要刻意拉高。\n"
        " 4. rationale 用一句話寫原因（15–40 字），之後會寫進 DB 給人工檢查。\n\n"
        + classifier_prompt_block()
    )
    prompt = (
        f"請分類以下新聞：\n"
        f"標題：{title or '(無)'}\n"
        f"內文前 1500 字：\n{(content or '')[:1500]}"
    )

    result = await call_for_json(
        system=system,
        prompt=prompt,
        response_model=_LLMTopicOut,
        gemini_model="gemini-flash-latest",
        temperature=0.1,  # 分類要穩定，降低溫度
    )
    if result.data is None:
        print(f"[topic_classifier] ❌ LLM 雙路皆失敗：{result.raw_error}")
        return None

    data = result.data
    valid_ids = set(category_ids())
    cid = data.category_id if data.category_id in valid_ids else "other"
    conf = max(0.0, min(1.0, float(data.confidence)))
    if cid != data.category_id:
        # LLM 掰了一個不存在的 id，降信心
        conf = min(conf, 0.3)

    return TopicClassification(
        category_id=cid,
        confidence=conf,
        rationale=(data.rationale or "")[:200],
    )


# ---------- orchestrator ----------

async def classify_topic(title: str, content: str) -> TopicClassification:
    """公開 API：disambig → keyword(+exclusion veto) → LLM → other(0.0)。
    保證回一個有效的 TopicClassification；taxonomy 之外的 id 不會出現。
    """
    # Layer 1：高信心 AND-of-OR 規則
    disambig_hit = match_disambiguation(title, content)
    if disambig_hit is not None:
        return disambig_hit

    # Layer 2：簡單 keyword 命中（若沒被 exclusion veto）
    kw_hit = classify_topic_keyword(title, content)
    if kw_hit is not None:
        if not is_vetoed_by_exclusion(title, content, kw_hit.category_id):
            return kw_hit
        # 有 veto → 當作沒 match，往下打 LLM

    # Layer 3：LLM fallback
    llm_hit = await classify_topic_llm(title, content)
    if llm_hit is not None:
        return llm_hit

    # 三路皆無結果 → 保險落到 other
    return TopicClassification(
        category_id="other",
        confidence=0.0,
        rationale="classifier_unavailable_or_all_missed",
    )


def compute_weighted_score(
    base_confidence: float, topic_weight: float
) -> float:
    """把 scorer 的 confidence (0..1) 乘上 topic_weight，clip 到 [0.0, 2.0]。
    抽到獨立函式是為了 scorer / backfill / 測試 用同一條路徑。
    """
    raw = float(base_confidence) * float(topic_weight)
    if raw < 0.0:
        return 0.0
    if raw > 2.0:
        return 2.0
    return raw


__all__ = [
    "TopicClassification",
    "DisambiguationRule",
    "ExclusionPattern",
    "DISAMBIGUATION_RULES",
    "EXCLUSION_PATTERNS",
    "classify_topic",
    "classify_topic_keyword",
    "classify_topic_llm",
    "match_disambiguation",
    "is_vetoed_by_exclusion",
    "compute_weighted_score",
]
