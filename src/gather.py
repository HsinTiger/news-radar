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


_FACTCHECK_LIKE = ("%查核%", "%TFC%", "%MyGoPen%", "%事實查核%")
_RELATED_SOURCE_SCAN_LIMIT = 1000
_MIN_SHORTER_TITLE_COVERAGE = 0.35


def _same_story(seed: Set[str], candidate: Set[str], min_overlap: int) -> bool:
    """Reject generic same-beat matches while retaining the same event.

    Two common bigrams such as ``食安`` and ``影片`` are not enough to turn an
    unrelated fact-check into evidence for the selected story.  Coverage of
    the shorter title keeps the rule usable for concise wire headlines.
    """
    if not seed or not candidate:
        return False
    overlap = len(seed & candidate)
    shorter_coverage = overlap / min(len(seed), len(candidate))
    return overlap >= min_overlap and shorter_coverage >= _MIN_SHORTER_TITLE_COVERAGE


def source_authority(feed_name: str, tags: str, feed_tier: str) -> tuple[int, str]:
    """Map configured provenance to the Recovery source hierarchy."""
    haystack = f"{feed_name} {tags}".lower()
    if "factcheck" in haystack or any(
        marker.lower() in haystack for marker in ("查核", "MyGoPen", "TFC")
    ):
        return 30, "獨立事實查核"
    if "disclosure" in haystack or "證交所" in feed_name:
        return 45, "交易所／公司揭露"
    if "primary-record" in haystack or any(
        marker in feed_name for marker in ("行政院", "食藥署")
    ):
        return 50, "官方第一手"
    if "public-broadcast" in haystack or any(
        marker in feed_name for marker in ("中央社", "公視")
    ):
        return 40, "通訊社／公共媒體"
    if str(feed_tier or "").lower() == "primary":
        return 20, "具名主要媒體"
    return 10, "具名次要媒體"


_HIGH_RISK_CORROBORATION_MARKERS = (
    "民進黨", "國民黨", "民眾黨", "藍綠", "藍白", "總統", "市長", "立委",
    "立法院", "政府", "食安", "食品", "不合格", "回收", "有毒", "致癌",
    "超標", "苯駢芘", "醫療", "健康", "弊案", "貪污", "收賄", "圖利",
    "隱匿", "造假", "搜索", "起訴", "交保", "判決", "裁罰",
)


def requires_authoritative_corroboration(
    *,
    title: str,
    content: str,
    feed_name: str,
    tags: str,
    feed_tier: str,
) -> bool:
    """Require stronger evidence for risky claims originating in ordinary media."""
    authority, _ = source_authority(feed_name, tags, feed_tier)
    if authority >= 30:
        return False
    material = f"{title}\n{content[:1200]}"
    return any(marker in material for marker in _HIGH_RISK_CORROBORATION_MARKERS)


def has_authoritative_corroboration(
    conn: sqlite3.Connection,
    news_id: str,
    title: str,
    *,
    days: int = 3,
    min_overlap: int = 3,
) -> bool:
    """Return whether the same event appears in an authority >= fact-check tier."""
    seed = _tokens(title)
    if not seed:
        return False
    try:
        rows = conn.execute(
            """
            SELECT title,feed_name,tags,feed_tier
              FROM news_items
             WHERE id != ?
               AND datetime(published_at) > datetime('now', ?)
               AND datetime(published_at) <= datetime('now', '+6 hours')
             ORDER BY datetime(published_at) DESC
             LIMIT ?
            """,
            (news_id, f"-{days} day", _RELATED_SOURCE_SCAN_LIMIT),
        ).fetchall()
    except Exception:
        return False
    for row in rows:
        authority, _ = source_authority(
            row["feed_name"] or "",
            row["tags"] or "",
            row["feed_tier"] or "",
        )
        if authority >= 30 and _same_story(
            seed, _tokens(row["title"] or ""), min_overlap
        ):
            return True
    return False


def factcheck_note(conn: sqlite3.Connection, title: str, *, min_overlap: int = 3, days: int = 45) -> str:
    """Phase 3 查證：若 TFC/MyGoPen 有同主題查核 → 回『事實查核提醒』；無則空字串。
    台灣政治/時事題尤其重要——避免轉述已被闢謠的說法、並可附可二次查核連結。"""
    seed = _tokens(title)
    if not seed:
        return ""
    try:
        like_sql = " OR ".join("feed_name LIKE ?" for _ in _FACTCHECK_LIKE)
        rows = conn.execute(
            f"""SELECT title, feed_name, url FROM news_items
                WHERE ({like_sql})
                  AND datetime(published_at) > datetime('now', ?)
                  AND datetime(published_at) <= datetime('now', '+6 hours')
                ORDER BY datetime(published_at) DESC LIMIT 60""",
            (*_FACTCHECK_LIKE, f"-{days} day"),
        ).fetchall()
    except Exception:
        return ""
    hits = [
        r
        for r in rows
        if _same_story(seed, _tokens(r["title"] or ""), min_overlap)
    ]
    if not hits:
        return ""
    lines = ["⚠️【事實查核提醒（TFC/MyGoPen 有相關查核，務必比對、勿轉述已被闢謠的說法）】"]
    for r in hits[:2]:
        lines.append(f"· [{r['feed_name']}] {r['title']}（{r['url'] or ''}）")
    return "\n".join(lines)


def gather_brief(
    conn: sqlite3.Connection,
    news_id: str,
    title: str,
    *,
    topic_category: Optional[str] = None,
    max_related: int = 3,
    days: int = 3,
    min_overlap: int = 3,
) -> str:
    """回傳『多源脈絡』block（同主題其他報導），找不到 → 空字串。

    以標題 token 重疊判定「同一件事」（≥min_overlap 個共同 token）。不靠 topic_category
    硬篩（跨類同題也抓得到），但若給了 topic_category 會優先同類。
    """
    try:
        rows = conn.execute(
            """
            SELECT id, title, feed_name, clean_markdown, topic_category,
                   tags, feed_tier, source_type, published_at, url
            FROM news_items
            WHERE id != ?
              AND datetime(published_at) > datetime('now', ?)
              AND datetime(published_at) <= datetime('now', '+6 hours')
              AND clean_markdown IS NOT NULL AND LENGTH(clean_markdown) > 120
            ORDER BY datetime(published_at) DESC
            LIMIT ?
            """,
            (news_id, f"-{days} day", _RELATED_SOURCE_SCAN_LIMIT),
        ).fetchall()
    except Exception:
        return ""

    seed = _tokens(title)
    if not seed:
        return ""
    scored = []
    for r in rows:
        candidate_tokens = _tokens(r["title"] or "")
        ov = len(seed & candidate_tokens)
        if _same_story(seed, candidate_tokens, min_overlap):
            # 同 topic_category 加一點分，讓同類同題優先
            same_topic = topic_category and (r["topic_category"] == topic_category)
            authority, authority_label = source_authority(
                r["feed_name"] or "",
                r["tags"] or "",
                r["feed_tier"] or "",
            )
            scored.append((authority, ov + (1 if same_topic else 0), authority_label, r))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    picked = [(authority_label, r) for _, _, authority_label, r in scored[:max_related]]

    lines = []
    if picked:
        lines.append(
            "【多源脈絡（只供主標題所指同一事件交叉查證；不得切換成另一事件）】"
        )
        for authority_label, r in picked:
            snippet = re.sub(r"\s+", " ", (r["clean_markdown"] or "")[:280]).strip()
            lines.append(
                f"· [{authority_label}｜{r['feed_name']}] {r['title']}"
                f"（{r['published_at']}）：{snippet}（{r['url'] or ''}）"
            )
        lines.append(
            f"（共 {len(picked)} 篇同事件來源；主標題仍是唯一寫作主題，"
            "只可補充可交叉核對的事實）"
        )
    # Phase 3 查證：附 TFC/MyGoPen 同主題查核提醒（即使沒有多源脈絡也要出現）。
    fc = factcheck_note(conn, title)
    if fc:
        if lines:
            lines.append("")
        lines.append(fc)
    return "\n".join(lines)


__all__ = [
    "factcheck_note",
    "gather_brief",
    "has_authoritative_corroboration",
    "requires_authoritative_corroboration",
    "source_authority",
]
