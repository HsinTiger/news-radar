"""Slot-based topic routing for the 3×/day cadence (2026-06-27, 信哥拍板).

時段三分（見計畫選題配比）：
  - 早 08:00（盤前）+ 午 12:30（收盤）= 市場桶（科技/商業/總經）
  - 晚 21:00（到家）           = 政治桶（政治/政策/軍事/時事）

設計原則（活下去）：**soft bias，不 hard filter**。`reorder_by_slot` 只把該 slot 桶的
候選排到前面（桶內維持原本 weighted_score 順序），其餘殿後——所以即使某桶當下沒料，
也不會「組不出稿/開天窗」，只是退而求其次。整套藏在 `EDITORIAL_MODE` flag 後，預設關＝
完全沿用舊行為（reorder 變 no-op、slot 一律 None）。

slot 由 run 的 UTC 時間推出（cron：早 23:45 / 午 04:15 / 晚 12:45 UTC）。手動/排程外的
run（slot=None）不做 reorder，維持舊的 freshness/score 行為。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional, Sequence

# 早午=市場桶；晚=政治桶。other 兩邊都不屬於，恆殿後。
MARKET_CATEGORIES = frozenset({
    "ai_model", "ai_agent", "ai_application", "supply_chain",
    "earnings", "tw_stocks", "us_stocks", "tech_product_launch",
})
POLITICS_CATEGORIES = frozenset({
    "policy_geopolitics", "tw_politics", "military_defense", "current_affairs",
})

_SLOT_BUCKET = {"market": MARKET_CATEGORIES, "politics": POLITICS_CATEGORIES}


def editorial_mode() -> bool:
    """新編輯模式總開關。關（預設）＝所有 slot 路由變 no-op、完全沿用舊行為。"""
    return os.getenv("EDITORIAL_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def slot_routing_enabled() -> bool:
    """Recovery uses historical topic weights instead of legacy 3x/day buckets."""
    return editorial_mode() and os.getenv("AUTOMATION_MODE", "").strip().lower() != "recovery"


def editor_enforce() -> bool:
    """總編輯閘是否『真的殺稿』。預設關＝shadow mode（只跑五關、記 log，不擋發文），
    讓人先觀察副編在真實稿上的判斷，校準後再 EDITOR_ENFORCE=1 開啟真殺。"""
    return os.getenv("EDITOR_ENFORCE", "").strip().lower() in ("1", "true", "yes", "on")


def current_slot(now_utc: Optional[datetime] = None) -> Optional[str]:
    """依 UTC 小時推出當前 slot：'market'（早/午）/ 'politics'（晚）/ None（排程外）。

    cron：早 23:45 / 午 04:15 / 晚 12:45 UTC（+GitHub Actions 延遲 ~5-15min），故用區間容錯。
    """
    h = (now_utc or datetime.now(timezone.utc)).hour
    if h in (23, 0, 1):      # 早 08:00 台灣（盤前）
        return "market"
    if h in (4, 5):          # 午 12:30 台灣（收盤）
        return "market"
    if h in (12, 13, 14):    # 晚 21:00 台灣（到家）
        return "politics"
    return None              # 手動 / force / 排程外 → 不偏


def bucket_categories(slot: Optional[str]) -> frozenset:
    return _SLOT_BUCKET.get(slot or "", frozenset())


def _topic_of(row) -> str:
    """從 sqlite3.Row / dict / 物件取 topic_category，取不到回 ''。"""
    try:
        if hasattr(row, "keys"):           # sqlite3.Row
            return (row["topic_category"] if "topic_category" in row.keys() else "") or ""
    except Exception:
        pass
    return (getattr(row, "topic_category", "") or "") if not isinstance(row, dict) \
        else (row.get("topic_category") or "")


def reorder_by_slot(rows: Sequence, slot: Optional[str]) -> List:
    """把屬於 slot 桶的列穩定地排到前面（桶內維持原順序），其餘殿後。

    slot=None 或 editorial_mode 關 → 原樣回傳（no-op，活下去：flag 關就是舊行為）。
    """
    rows = list(rows)
    if not slot or not slot_routing_enabled():
        return rows
    bucket = bucket_categories(slot)
    if not bucket:
        return rows
    in_bucket = [r for r in rows if _topic_of(r) in bucket]
    rest = [r for r in rows if _topic_of(r) not in bucket]
    return in_bucket + rest
