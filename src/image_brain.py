"""Deterministic character selection for News Radar cover renderers.

The writing model owns only title, subtitle, and article body.  This module has
no text-to-image API, cover-prompt builder, or manual generation instructions;
it maps editorial context to an existing character asset and expression.  The
Substack and Meta renderers then compose those assets into final images.
"""

from __future__ import annotations


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


def pick_character(topic_category=None, mode=None) -> str:
    """Select an existing character asset without involving the writer model."""
    if mode == "company":
        return "robot"
    if mode == "podcast":
        return "owl"
    if (topic_category or "") in _ROBOT_TOPICS:
        return "robot"
    return "owl"


def pick_expression(topic_category=None, mode=None, title=None, character=None) -> str:
    """Map category, mode, and title mood to an existing expression asset."""
    char = character if character in ("robot", "owl") else pick_character(
        topic_category, mode
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
            return "presenting"
        if topic in ("ai_model", "ai_agent", "ai_application", "tech_product_launch"):
            return "curious"
        if topic == "supply_chain":
            return "skeptical"
        return "gotcha"

    if any(word in title_text for word in ("風險", "泡沫", "小心", "陷阱", "警訊", "別被", "別再")):
        return "cautionary"
    if any(word in title_text for word in ("什麼是", "入門", "科普", "懶人包", "一次搞懂", "解析")):
        return "teaching"
    if "為什麼" in title_text or title_text.endswith(("？", "?")):
        return "pondering"
    if topic == "culture":
        return "warm"
    if topic == "contrarian":
        return "wink"
    if mode == "evening":
        return "reading"
    return "ahha"


def _anchor_gaze(title=None):
    """Alternate the character side deterministically from the title."""
    if (sum(map(ord, title or "")) % 2) == 0:
        return "left", "looking right"
    return "right", "looking left"
