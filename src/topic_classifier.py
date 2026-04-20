"""
News Radar · Topic Classifier（Phase 8.20 Step 2）
===================================================
兩層策略：
  1. Keyword fast-path（免 LLM 費用）
       讀 config/topic_keywords.yaml，第一個命中的類別（order 優先）勝出。
       回傳 confidence=0.6（保守：讓呼叫端知道這不是 LLM 判讀、若要高信心
       可以再追一 LLM call）。
  2. LLM fallback
       keyword 全 miss 時才打 call_for_json，用 TopicClassification pydantic schema
       強制輸出 category_id + confidence + rationale。

設計原則：
  - **單一事實來源**：類別 id / 顯示名 / 描述寫在 src.topic_taxonomy；
    這邊只處理『分類邏輯』，不 hard-code 權重或顯示名。
  - **雙路可回 None**：LLM 雙路（Claude CLI + Gemini）都失敗時，classifier
    不假裝，回 TopicClassification(category_id='other', confidence=0.0,
    rationale='classifier_unavailable')——讓呼叫端看得出 signal 是否可信。
  - **pure-ish**：keyword 層完全 pure，LLM 層為 async，呼叫端 await 即可。
  - **冷啟動友善**：classifier 可以在沒有 LLM 的 sandbox（無網路、無 pydantic
    裝好的環境）跑純 keyword 路徑，方便 backfill 腳本。

—— 2026-04-21 overnight, Cowork Claude
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
    """公開 API：keyword fast-path → LLM fallback → other(0.0)。
    保證回一個有效的 TopicClassification；taxonomy 之外的 id 不會出現。
    """
    kw_hit = classify_topic_keyword(title, content)
    if kw_hit is not None:
        return kw_hit

    llm_hit = await classify_topic_llm(title, content)
    if llm_hit is not None:
        return llm_hit

    # 雙路皆無結果 → 保險落到 other
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
    "classify_topic",
    "classify_topic_keyword",
    "classify_topic_llm",
    "compute_weighted_score",
]
