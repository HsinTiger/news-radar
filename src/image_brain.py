"""Deterministic character selection for News Radar cover renderers.

The writing model owns only title, subtitle, and article body.  This module has
no text-to-image API, cover-prompt builder, or manual generation instructions;
it maps editorial context to an existing character asset and expression.  The
Substack and Meta renderers then compose those assets into final images.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


_EXPRESSION_HINTS: dict[str, dict[str, str]] = {
    "robot": {
        "gotcha": "default hard-topic expression",
        "skeptical": "structural doubt",
        "smug": "contradiction exposed",
        "curious": "new technology",
        "presenting": "company or earnings analysis",
        "alert": "urgent market move",
        "celebrating": "record or breakout",
    },
    "owl": {
        "ahha": "default reflective expression",
        "wink": "contrarian insight",
        "pondering": "open question",
        "reading": "long-form evening analysis",
        "warm": "humanities reflection",
        "cautionary": "risk warning",
        "teaching": "explainer",
    },
}
_DEFAULT_EXPRESSION = {"robot": "gotcha", "owl": "ahha"}

_ROBOT_TOPICS = {
    "us_stocks",
    "tw_stocks",
    "ai_model",
    "ai_agent",
    "ai_application",
    "tech_product_launch",
    "supply_chain",
    "earnings",
}

_OWL_TOPICS = {
    "culture",
    "contrarian",
    "society",
    "history",
    "politics",
    "health",
    "media",
    "labor",
}

# topic_category 實務上很常是 "" 或 "other"（2026-08-16 的 podcast 日誌裡一半是
# other），所以題材看不出來時要有第二層判斷，否則全部倒向同一隻角色。
_HARD_TITLE_MARKERS = (
    "AI", "GPU", "CPU", "IC", "ETF", "IPO", "SaaS", "API",
    "晶片", "半導體", "模型", "算力", "資料中心", "電網", "產能", "供應鏈",
    "財報", "營收", "毛利", "獲利", "estimates", "股價", "市值", "估值",
    "研發", "程式", "演算法", "自動化", "機器人", "雲端", "資安", "專利",
)


def _title_is_hard(title=None) -> bool:
    text = (title or "")
    upper = text.upper()
    return any(m.upper() in upper for m in _HARD_TITLE_MARKERS)


def pick_character(topic_category=None, mode=None, title=None) -> str:
    """Select an existing character asset without involving the writer model.

    以前 ``mode == "podcast"`` 直接回 owl。podcast 一天兩篇、是產出的大宗，
    於是 2026-08 的封面清一色是達達——雙 IP 等於只剩一隻。改成：只有財報專欄
    （賺錢有道）鎖定瑞瑞當專欄識別，其餘一律看題材，題材認不出來再看標題。
    """
    if mode == "company":
        return "robot"
    topic = (topic_category or "").strip()
    if topic in _ROBOT_TOPICS:
        return "robot"
    if topic in _OWL_TOPICS:
        return "owl"
    return "robot" if _title_is_hard(title) else "owl"


# 輪替池只放語氣中性的表情。alert／celebrating／smug／wink／cautionary 帶明確
# 情緒，硬輪到壞消息上放慶祝的瑞瑞會出事，所以那些只由下面的關鍵字規則觸發。
_ROTATION_POOL = {
    "robot": ("gotcha", "curious", "skeptical", "presenting"),
    "owl": ("ahha", "pondering", "teaching", "reading", "warm"),
}
_RECENT_PATH = Path(__file__).resolve().parents[1] / "data" / "substack_drafts" / ".cover_recent.json"
_RECENT_KEEP = 6


def _recent_picks() -> list:
    """最近用過的 角色_表情。壞掉或不存在都當成沒有紀錄——這只影響變化度，
    不該讓封面產不出來。"""
    try:
        data = json.loads(_RECENT_PATH.read_text(encoding="utf-8"))
        return [str(x) for x in data][-_RECENT_KEEP:] if isinstance(data, list) else []
    except Exception:
        return []


def remember_pick(character=None, expression=None) -> None:
    """記下這次的選角，讓下一篇避開。podcast 一次跑兩篇、是兩個獨立行程，
    只靠標題雜湊仍可能連續撞同一個表情，所以要留一點狀態。"""
    if not character or not expression:
        return
    try:
        picks = _recent_picks() + [f"{character}_{expression}"]
        _RECENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RECENT_PATH.write_text(
            json.dumps(picks[-_RECENT_KEEP:], ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def _rotate_expression(character, title=None) -> str:
    """標題決定起點（同一篇永遠得到同一個表情，可重現），再往後找第一個最近
    沒用過的。用 blake2s 而不是 sum(ord)：中文標題的 ord 總和分佈很擠。"""
    pool = _ROTATION_POOL.get(character) or (_DEFAULT_EXPRESSION.get(character, "ahha"),)
    digest = hashlib.blake2s((title or "").encode("utf-8"), digest_size=4).hexdigest()
    start = int(digest, 16) % len(pool)
    recent = set(_recent_picks())
    for offset in range(len(pool)):
        candidate = pool[(start + offset) % len(pool)]
        if f"{character}_{candidate}" not in recent:
            return candidate
    return pool[start]


def _prefer(character, choice, title=None) -> str:
    """語意規則挑的表情優先，但剛用過就改走輪替。ai_model 的 podcast 很密集，
    一路都是 curious 只是換一種「每張都一樣」。"""
    if f"{character}_{choice}" not in set(_recent_picks()):
        return choice
    return _rotate_expression(character, title)


def pick_expression(topic_category=None, mode=None, title=None, character=None) -> str:
    """Map category, mode, and title mood to an existing expression asset."""
    char = character if character in ("robot", "owl") else pick_character(
        topic_category, mode, title
    )
    title_text = (title or "").strip()
    topic = (topic_category or "").strip()

    if char == "robot":
        if any(word in title_text for word in ("暴跌", "急殺", "閃崩", "崩", "重挫", "突發", "警報")):
            return "alert"
        if any(word in title_text for word in ("新高", "突破", "創紀錄", "里程碑", "飆", "大漲", "狂飆")):
            return "celebrating"
        if any(word in title_text for word in ("早就", "錯了", "打臉")):
            return "smug"
        if mode == "company" or topic in ("earnings", "company"):
            return _prefer(char, "presenting", title_text)
        if topic in ("ai_model", "ai_agent", "ai_application", "tech_product_launch"):
            return _prefer(char, "curious", title_text)
        if topic == "supply_chain":
            return _prefer(char, "skeptical", title_text)
        return _rotate_expression(char, title_text)

    if any(word in title_text for word in ("風險", "泡沫", "小心", "陷阱", "警訊", "別被", "別再")):
        return "cautionary"
    if any(word in title_text for word in ("什麼是", "入門", "科普", "懶人包", "一次搞懂", "解析")):
        return _prefer(char, "teaching", title_text)
    if "為什麼" in title_text or title_text.endswith(("？", "?")):
        return _prefer(char, "pondering", title_text)
    if topic == "culture":
        return _prefer(char, "warm", title_text)
    if topic == "contrarian":
        return "wink"
    if mode == "evening":
        return _prefer(char, "reading", title_text)
    return _rotate_expression(char, title_text)


def _anchor_gaze(title=None):
    """Alternate the character side deterministically from the title.

    原本用 sum(ord) 的奇偶。中文標題的碼位分佈很擠，實測 14 個真實標題是
    11:3 —— 角色幾乎固定站同一邊，跟表情不變一起造成「每張封面長一樣」。
    改用雜湊取奇偶，分佈才是真的平均。"""
    digest = hashlib.blake2s((title or "").encode("utf-8"), digest_size=4).digest()
    if digest[0] % 2 == 0:
        return "left", "looking right"
    return "right", "looking left"
