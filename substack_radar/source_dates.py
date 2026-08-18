"""來源日期：抓得出日期就抓，抓不出就說抓不出，不要假裝新鮮。

為什麼需要
----------
2026-08-18 的聯發科稿引用了 `n.yam.com/Article/20251124477055`——2025 年 11 月的
報導，距今 9 個月。裡面「摩根士丹利目標價 1,288 元、高盛 1,400 元」在當時是合理的
（那時股價約 1,100–1,500），但寫進 2026 年 8 月的稿子、而股價已經 3,885，等於
暗示外資看空 65%。

來源閘門本來只驗「活著」與「相關」，沒有驗「新不新」。對財報與估值類的文章，
過期的數字比不相關的來源更危險——它看起來完全切題。
"""
from __future__ import annotations

import re
from datetime import date, datetime

# 網址裡的日期是最可靠的訊號：/20251124/、/2025/11/24/、?date=2025-11-24
_URL_DATE = (
    re.compile(r"/(20\d{2})(\d{2})(\d{2})"),
    re.compile(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})"),
)
# 內文常見的中文日期
_TEXT_DATE = re.compile(r"(20\d{2})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})")


def _mk(y: str, m: str, d: str) -> date | None:
    try:
        value = date(int(y), int(m), int(d))
    except ValueError:
        return None
    return value if date(2000, 1, 1) <= value <= date.today() else None


def published_on(url: str = "", text: str = "") -> date | None:
    """回傳來源日期；抓不到回 None（而不是猜今天）。"""
    for pattern in _URL_DATE:
        m = pattern.search(url or "")
        if m:
            got = _mk(*m.groups())
            if got:
                return got
    m = _TEXT_DATE.search((text or "")[:1500])
    if m:
        return _mk(*m.groups())
    return None


def age_days(url: str = "", text: str = "", today: date | None = None) -> int | None:
    got = published_on(url, text)
    if got is None:
        return None
    return ((today or date.today()) - got).days


def annotate(sources: list[dict], today: date | None = None) -> list[dict]:
    """就地補上 published_on / age_days，讓事實表與閘門都看得到。"""
    for src in sources or []:
        got = published_on(str(src.get("url") or ""), str(src.get("excerpt") or ""))
        src["published_on"] = got.isoformat() if got else None
        src["age_days"] = ((today or date.today()) - got).days if got else None
    return sources
