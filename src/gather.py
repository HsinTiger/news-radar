"""搜集（Phase 2）：同主題多源叢集 → 多源脈絡 brief，餵 composer 增厚度。

信哥點名「FB/IG/Thread 缺乏厚度」的根因：一篇貼文＝一條新聞＝單一來源。本模組在
compose 前，從 harvest DB 找出「同一件事的其他報導」（標題 token 重疊），組成一段
『多源脈絡』附在素材後面，讓 composer 有交叉、有補充、不再單篇轉述。

設計：純讀 DB、零 LLM、fail-safe（找不到就回空字串，不影響原流程）。藏 EDITORIAL_MODE。
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional, Set


def _tokens(s: str) -> Set[str]:
    """標題比對用 token：英數詞（≥3 字）＋ 中文 bigram。中英混排都能比。"""
    s = (s or "").lower()
    words = set(re.findall(r"[a-z0-9]{3,}", s))
    cjk = re.findall(r"[一-鿿]", s)
    bigrams = {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return words | bigrams


def gather_brief(
    conn: sqlite3.Connection,
    news_id: str,
    title: str,
    *,
    topic_category: Optional[str] = None,
    max_related: int = 3,
    days: int = 3,
    min_overlap: int = 2,
) -> str:
    """回傳『多源脈絡』block（同主題其他報導），找不到 → 空字串。

    以標題 token 重疊判定「同一件事」（≥min_overlap 個共同 token）。不靠 topic_category
    硬篩（跨類同題也抓得到），但若給了 topic_category 會優先同類。
    """
    try:
        rows = conn.execute(
            """
            SELECT id, title, feed_name, clean_markdown, topic_category
            FROM news_items
            WHERE id != ?
              AND fetched_at > datetime('now', ?)
              AND clean_markdown IS NOT NULL AND LENGTH(clean_markdown) > 120
            ORDER BY fetched_at DESC
            LIMIT 80
            """,
            (news_id, f"-{days} day"),
        ).fetchall()
    except Exception:
        return ""

    seed = _tokens(title)
    if not seed:
        return ""
    scored = []
    for r in rows:
        ov = len(seed & _tokens(r["title"] or ""))
        if ov >= min_overlap:
            # 同 topic_category 加一點分，讓同類同題優先
            same_topic = topic_category and (r["topic_category"] == topic_category)
            scored.append((ov + (1 if same_topic else 0), r))
    if not scored:
        return ""
    scored.sort(key=lambda x: -x[0])
    picked = [r for _, r in scored[:max_related]]

    lines = ["【多源脈絡（同一主題的其他報導，供交叉查證與補充，不要照抄）】"]
    for r in picked:
        snippet = re.sub(r"\s+", " ", (r["clean_markdown"] or "")[:280]).strip()
        lines.append(f"· [{r['feed_name']}] {r['title']}：{snippet}")
    lines.append(f"（共 {len(picked)} 篇同主題來源；請用來補充事實、交叉比對，而非單篇轉述）")
    return "\n".join(lines)
