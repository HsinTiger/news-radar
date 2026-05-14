"""
News Radar · Threads Substack CTA Pool (Phase 2, 2026-05-14)

實作 config/platforms/threads_v2.md §14.6 的反指紋導流 CTA 邏輯。

責任邊界：
- decide whether to inject CTA in this compose run (1/3 probability by default)
- pick a style category (A-E)，從候選池排除最近 EXCLUDE_LAST_N 篇用過
- 提供 LLM 用的 style description（純描述、**不含例句**，避免照抄）
- 把 chosen style 寫進 JSON history 檔，供下次決策 + reflector 5/29 對照

不負責：
- 實際生成 CTA 文字（由 composer.py 的 LLM call 處理）
- DB schema 修改（用 JSON file 持久化即可，避免動 SQLite 風險）

History 檔位置：data/01_harvest/cta_history.json
格式：{"recent": [{"style": "B", "ts": "2026-05-14T10:18:00+00:00"}, ...]}（新到舊）

ENV 覆蓋：
- NEWS_RADAR_CTA_PROBABILITY=0.0  → 永不注入（rollback 用）
- NEWS_RADAR_CTA_PROBABILITY=1.0  → 永遠注入（smoke test 用）
- 預設 0.333 = 1/3
"""
from __future__ import annotations
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal

CTAStyle = Literal["A", "B", "C", "D", "E"]
ALL_STYLES: tuple[CTAStyle, ...] = ("A", "B", "C", "D", "E")

# 預設注入機率 1/3（threads_v2.md §14.6.1）
DEFAULT_INJECTION_PROBABILITY = 1.0 / 3.0
EXCLUDE_LAST_N = 2  # 排除最近 N 篇用過的風格類
HISTORY_MAX_LEN = 30  # 保留最近 30 筆 CTA 紀錄，給 reflector 對照用

# 風格描述（**絕對不含例句** — 例句只在 threads_v2.md §14.6.2 給人類讀者用）
# 餵給 LLM 的版本：純抽象風格描述 + 反照抄要求。任何疑似「完整句子」的字串
# 都要從這裡剝除，否則 LLM 會直接照抄、反指紋設計失效。
STYLE_DESCRIPTIONS: dict[CTAStyle, str] = {
    "A": (
        "「另一頭」型：暗示「另一個地方」存在，但不催促、不命令、不直接點名 Substack。"
        "口氣含蓄、像隨手提到、不像在導流。語感應該是：暗示某種延伸版本／"
        "另一個閱讀場景，讓讀者好奇但不被推銷。"
    ),
    "B": (
        "「篇幅藉口」型：用字數限制當理由帶到完整版。"
        "可以提及 hsin73.substack.com 這個域名（但**不寫成 URL**，例如不寫 https://）。"
        "口氣自然——把「短文寫不下」當理由帶到長版、比直接導流更不像廣告。"
    ),
    "C": (
        "「自嘲過長」型：自嘲、自貶，反而打消讀者「被推銷」的警戒感。"
        "口氣輕鬆、自損——強調自己寫太多、怕讀者嫌囉嗦這類人味敘述。"
        "讀者會因為「他自承囉嗦」反而願意去看完整版。"
    ),
    "D": (
        "「邀請進入」型：完全不提地點、絕對不點名 Substack。"
        "只暗示「想看更多的人會自己找到」——靠好奇心而非導引。"
        "可暗示讀者可以自己 search 找到、但不直接給目的地名字。"
    ),
    "E": (
        "「續集承諾」型：把長版包裝成「下一個時間點要做的動作」、不是「另一個地方」。"
        "含明確的時間錨點——明天早上／本週五／下一篇／月底——讓讀者覺得是承諾而非廣告。"
    ),
}

# History 檔位置（絕對路徑、不管從哪個 cwd 執行都對得上）
_BASE = Path(__file__).resolve().parent.parent
CTA_HISTORY_PATH = _BASE / "data" / "01_harvest" / "cta_history.json"


def _get_probability() -> float:
    """讀取 ENV 覆寫機率；不合法 fallback 預設。"""
    raw = os.getenv("NEWS_RADAR_CTA_PROBABILITY")
    if raw is None:
        return DEFAULT_INJECTION_PROBABILITY
    try:
        val = float(raw)
        if 0.0 <= val <= 1.0:
            return val
    except (TypeError, ValueError):
        pass
    return DEFAULT_INJECTION_PROBABILITY


def _load_history_raw() -> list:
    """讀歷史檔；不存在或損壞 → 空 list。回傳的 list item 可能是 str（舊格式）或 dict（新格式）。"""
    if not CTA_HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(CTA_HISTORY_PATH.read_text(encoding="utf-8"))
        recent = data.get("recent", [])
        return recent if isinstance(recent, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _extract_styles(raw_history: list) -> list[CTAStyle]:
    """從 raw history（混合 str/dict 都支援）抽出風格類序列。新到舊。"""
    out: list[CTAStyle] = []
    for item in raw_history:
        if isinstance(item, str) and item in ALL_STYLES:
            out.append(item)  # type: ignore[arg-type]
        elif isinstance(item, dict):
            s = item.get("style")
            if s in ALL_STYLES:
                out.append(s)
    return out


def _append_history(style: CTAStyle, news_id: Optional[str] = None) -> None:
    """把新的 CTA 紀錄寫入歷史檔（新到舊），truncate 到 HISTORY_MAX_LEN。"""
    raw = _load_history_raw()
    entry = {
        "style": style,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if news_id:
        entry["news_id"] = news_id
    new_list = [entry] + raw
    new_list = new_list[:HISTORY_MAX_LEN]
    CTA_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    CTA_HISTORY_PATH.write_text(
        json.dumps({"recent": new_list}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def decide_cta(
    probability: Optional[float] = None,
    news_id: Optional[str] = None,
) -> Optional[CTAStyle]:
    """擲骰決定本次 compose 要不要注入 CTA；要的話選哪個風格類。

    決策流程（threads_v2.md §14.6.1）：
    1. random.random() < probability → 中籤
    2. 從歷史檔讀最近 EXCLUDE_LAST_N 篇用過的風格類
    3. 從候選池排除這些 → 從剩下隨機選 1
    4. 寫回歷史檔 + 寫入 ts（給 reflector 對照用）

    Args:
        probability: 注入機率；None 時讀 ENV 或 fallback 預設 1/3
        news_id: 可選，記到歷史檔給 reflector 對照 engagement

    Returns:
        None: 沒中籤（不注入）
        CTAStyle ('A'-'E'): 中籤，並回傳所選風格類
    """
    p = probability if probability is not None else _get_probability()
    if random.random() >= p:
        return None

    raw_history = _load_history_raw()
    recent_styles = _extract_styles(raw_history)
    exclude = set(recent_styles[:EXCLUDE_LAST_N])
    candidates = [s for s in ALL_STYLES if s not in exclude]

    # Edge case 防呆：若候選空（不應發生，最多排除 2 / 5）→ 從全部抽
    if not candidates:
        candidates = list(ALL_STYLES)

    chosen: CTAStyle = random.choice(candidates)
    _append_history(chosen, news_id=news_id)
    return chosen


def get_cta_prompt_fragment(style: CTAStyle) -> str:
    """組裝注入 composer prompt 的指令片段。

    重點：餵給 LLM 的 prompt **不含例句**——只描述風格類別 + 硬性限制。
    例句在 threads_v2.md §14.6.2 是給人類讀者理解風格用，**絕不放進 prompt**，
    否則 LLM 會直接照抄、反指紋設計失效。
    """
    desc = STYLE_DESCRIPTIONS[style]
    return (
        "\n\n"
        "=== 額外要求：Threads 變體加 Substack 文字 CTA ===\n"
        f"本次 Threads 變體要在 body 結尾（最後一段、hashtag 之前）加一句 Substack 導流 CTA。\n"
        f"風格類 = {style}。風格描述：{desc}\n"
        "\n"
        "硬性限制：\n"
        "- CTA 句長度 ≤ 25 字\n"
        "- 絕對不出現可點擊 URL（不寫 https://、不寫 www.）\n"
        "- 禁用詞：「歡迎訂閱」「必看」「錯過後悔」「神文」「請按讚分享」\n"
        "- 不用 AI 塑膠味詞：「總結來說」「不容忽視」「值得我們深思」\n"
        "- 自然融入 body 結尾的節奏、不像突兀的廣告\n"
        "- 可加一個空行做視覺呼吸、但不要另起新段\n"
        "- **只加在 Threads 變體**，FB 和 IG 變體照舊、絕對不要加 CTA\n"
        "- **自己寫一個原創的句子**——不要照抄我提供的口氣描述，那只是給你抓 register 用的\n"
    )


def peek_recent_history(n: int = 5) -> list[CTAStyle]:
    """外部 debug / reflector 用：偷看最近 N 筆已注入的風格序列（新到舊）。"""
    return _extract_styles(_load_history_raw())[:n]


def get_history_path() -> Path:
    """exposed for tests + reflector"""
    return CTA_HISTORY_PATH
