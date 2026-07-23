"""
News Radar · Content Strategy Analyzer (Phase 9 Item 5+)
========================================================================
Cross-dimensional engagement analysis agent. Reads from engagement_stats
and related tables to extract actionable content-strategy signals,
generate editorial notes for the composer, and self-iterate by tracking
which suggestions were adopted and their measured effect.

Five analysis dimensions (per spec analysis-dimensions §):

  1. Hook type vs engagement rate       -- classify opening structure
  2. Word count vs engagement            -- length-effect per platform
  3. Hashtag effectiveness analysis      -- count & content patterns
  4. Publishing time vs engagement       -- hour-of-day / day-of-week
  5. Topic weight recommendations       -- which topics outperform

Design invariants:
  - `analyze()` returns structured dict, not prose
  - `suggest()` returns a short editorial_note string for the composer
  - Insufficient data -> hypotheses, never false conclusions
  - Self-iteration via local tracking file

Spec  : PM_Radar/specs/strategy_analyzer.md
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants -- Hsin-pinned thresholds
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 30
MIN_SAMPLES_FOR_FINDING = 5      # minimum samples for a confident finding
MIN_SAMPLES_FOR_HYPOTHESIS = 3   # fewer than this -> insufficient signal
HOOK_HIGH_CONF_COUNT = 3         # pattern appears >= N times -> HIGH
TOP_QUARTILE_CUT = 0.75          # top 25% engagement -> "high"
BOT_QUARTILE_CUT = 0.25          # bottom 25% -> "low"

# Path for self-iteration tracking (relative to project root)
_TRACKER_FILENAME = "strategy_tracker.json"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TRACKER_PATH = _PROJECT_ROOT / "data" / "05_reflect" / _TRACKER_FILENAME
_REPORTS_DIR = _PROJECT_ROOT / "reports"

# Minimal str for editorial_note when no analysis is possible
_FALLBACK_NOTE = "按既有靈魂風格自由發揮，但需維持強數據與深度分析。"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EngagementSample:
    """One published draft with its latest engagement snapshot per platform."""
    draft_id: str
    news_id: str
    title: str
    topic_category: Optional[str]
    weighted_score: Optional[float]
    published_at: str
    confidence_score: Optional[float]
    # platform-specific engagement columns -- all Optional (None = not polled)
    fb_likes: Optional[int]
    fb_comments: Optional[int]
    fb_shares: Optional[int]
    fb_reach: Optional[int]
    ig_likes: Optional[int]
    ig_comments: Optional[int]
    ig_shares: Optional[int]
    ig_saves: Optional[int]
    ig_reach: Optional[int]
    th_likes: Optional[int]
    th_replies: Optional[int]
    th_reposts: Optional[int]
    th_quotes: Optional[int]
    th_views: Optional[int]
    fb_clicks: Optional[int] = None
    # Draft content (from platform_drafts or drafts table)
    hook: str = ""
    char_count: int = 0
    hashtags: List[str] = field(default_factory=list)
    # Publishing time metadata
    posted_hour: Optional[int] = None
    posted_weekday: Optional[int] = None  # 0=Mon, 6=Sun


@dataclass
class Finding:
    """A single analytical finding or hypothesis.

    ``is_hypothesis`` distinguishes between a confident finding
    (supported by sufficient samples) and a hypothesis that needs
    more data before it can be treated as a recommendation.
    """
    dimension: str
    observation: str
    confidence: str                    # HIGH / MED / LOW
    evidence: Dict[str, Any] = field(default_factory=dict)
    is_hypothesis: bool = False
    sample_count: int = 0


@dataclass
class Recommendation:
    """A concrete, executable content-strategy recommendation.

    ``target_field`` maps to the editorial dimension (hook / length /
    hashtag / timing / topic) so ``suggest()`` can compose the
    editorial_note from the highest-confidence recommendations.
    """
    dimension: str
    suggestion: str
    expected_impact: str               # e.g. "提高 Hook 點擊率"
    confidence: str                    # HIGH / MED / LOW
    target_field: str = ""             # hook / length / hashtag / timing / topic
    platform: str = "all"              # facebook / instagram / threads / all


@dataclass
class HypothesisItem:
    """A question that can't yet be answered but is worth tracking."""
    question: str
    expected_signal: str
    requires_data: str                  # e.g. "more FB samples with engagement"
    timestamp: str = ""


@dataclass
class StrategyResult:
    """Result of one ``analyze()`` call."""
    ran_at: str
    lookback_days: int
    total_samples: int
    findings: List[Finding]
    recommendations: List[Recommendation]
    hypothesis_queue: List[HypothesisItem]
    editorial_note: str = ""
    tracked_suggestion_ids: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Engagement-weight helper (mirrors src/reflector/_engagement)
# ---------------------------------------------------------------------------

def _g(row: Any, key: str) -> float:
    """Coerce row[key] to float, treating None / missing as 0.0."""
    try:
        v = row[key] if isinstance(row, dict) else getattr(row, key, None)
    except (KeyError, AttributeError):
        v = None
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def engagement_weight(row: Any, platform: str) -> float:
    """Hsin-pinned engagement-weight scalar (from _engagement.py)."""
    p = platform.lower()
    if p == "facebook" or p == "fb":
        return (_g(row, "fb_likes") + 2.0 * _g(row, "fb_comments")
                + 3.0 * _g(row, "fb_shares") + 0.25 * _g(row, "fb_clicks"))
    if p == "instagram" or p == "ig":
        return (_g(row, "ig_likes") + 2.0 * _g(row, "ig_comments")
                + 3.0 * _g(row, "ig_shares") + 1.5 * _g(row, "ig_saves")
                + 0.01 * _g(row, "ig_reach"))
    if p == "threads" or p == "th":
        return (_g(row, "th_likes") + 2.0 * _g(row, "th_replies")
                + 3.0 * _g(row, "th_reposts") + 1.5 * _g(row, "th_quotes")
                + 0.005 * _g(row, "th_views"))
    return 0.0


def has_platform_engagement(row: Any, platform: str) -> bool:
    """True if at least one engagement column for this platform is non-NULL."""
    if platform in ("facebook", "fb"):
        keys = ("fb_likes", "fb_comments", "fb_shares", "fb_clicks")
    elif platform in ("instagram", "ig"):
        keys = ("ig_likes", "ig_comments", "ig_shares", "ig_saves", "ig_reach")
    elif platform in ("threads", "th"):
        keys = ("th_likes", "th_replies", "th_reposts", "th_quotes", "th_views")
    else:
        return False
    for k in keys:
        try:
            v = row[k] if isinstance(row, dict) else getattr(row, k, None)
        except (KeyError, AttributeError):
            v = None
        if v is not None:
            return True
    return False


# ---------------------------------------------------------------------------
# Hook-type classifier
# ---------------------------------------------------------------------------

def _classify_hook(hook_text: str) -> str:
    """Classify a hook string into a structural type.

    Types:
      question_hook      -- starts with Why/How/?"ending
      number_hook        -- starts with a digit
      quote_hook         -- starts with  or Japanese quotation marks
      contrast_hook      -- contains vs/VS/對比
      imperative_hook    -- starts with a verb-like command
      fact_hook          -- starts with breaking news markers
      statement_hook     -- everything else
    """
    if not hook_text:
        return "empty_hook"
    t = hook_text.strip()

    if not t:
        return "empty_hook"

    # Question hook
    question_starts = ("為什麼", "為何", "如何", "怎樣", "怎麼辦",
                       "有沒", "是否", "難道", "要不要", "能不能",
                       "What", "Why", "How", "Who", "When", "Where")
    if any(t.startswith(q) for q in question_starts):
        return "question_hook"
    if t.endswith("?") or t.endswith("？"):
        return "question_hook"

    # Quote hook
    if t.startswith(("「", "『", '"', "'", "《")):
        return "quote_hook"

    # Number hook
    if t[0].isdigit():
        return "number_hook"
    number_starts = ("一", "二", "三", "四", "五", "六", "七", "八", "九",
                     "十", "兩")
    if any(t.startswith(n) for n in number_starts):
        return "number_hook"

    # Contrast hook
    contrast_markers = (" vs ", " Vs ", " VS ", " vs.", " 對比 ", " 比較 ",
                        " X ", "&")
    if any(m in t for m in contrast_markers):
        return "contrast_hook"

    # Fact/recency hook
    if t.startswith(("最新", "快訊", "剛剛")):
        return "fact_hook"

    # Imperative hook
    imperative_starts = ("別", "不要", "快", "請", "記得", "小心", "注意")
    if any(t.startswith(s) for s in imperative_starts):
        return "imperative_hook"

    return "statement_hook"


# ---------------------------------------------------------------------------
# DB IO
# ---------------------------------------------------------------------------

def _fetch_engagement_samples(
    conn: sqlite3.Connection,
    lookback_days: int = LOOKBACK_DAYS,
) -> List[EngagementSample]:
    """Fetch all published drafts from the last N days with engagement data.

    Uses v_post_engagement_aggregated as the canonical source of per-platform
    engagement columns, then joins platform_drafts for hook/char_count and
    publish_log for timing. Falls back to base tables if the view is missing.
    """
    # Step 1: get the aggregated engagement view
    view_sql = """
        SELECT draft_id, news_id, title, topic_category, weighted_score,
               published_at, confidence_score,
               fb_likes, fb_comments, fb_shares, fb_reach, fb_clicks,
               ig_likes, ig_comments, ig_shares, ig_saves, ig_reach,
               th_likes, th_replies, th_reposts, th_quotes, th_views
          FROM v_post_engagement_aggregated
         WHERE published_at >= datetime('now', ?)
    """
    window = "-" + str(int(lookback_days)) + " days"
    try:
        raw_rows = conn.execute(view_sql, (window,)).fetchall()
    except sqlite3.OperationalError:
        return _fetch_from_base_tables(conn, lookback_days)

    if not raw_rows:
        return []

    # Step 2: batch-fetch platform_drafts content and publish_log timing
    draft_ids = [r["draft_id"] for r in raw_rows]
    content_map = _batch_fetch_draft_content(conn, draft_ids)
    pub_timing_map = _batch_fetch_publish_timing(conn, draft_ids)

    samples: List[EngagementSample] = []
    for r in raw_rows:
        did = r["draft_id"]
        content = content_map.get(did, {})
        timing = pub_timing_map.get(did, {})

        # Parse hashtags from content
        raw_tags = content.get("hashtags", "[]")
        if isinstance(raw_tags, str):
            try:
                hashtags = json.loads(raw_tags)
            except (json.JSONDecodeError, TypeError):
                hashtags = []
        elif isinstance(raw_tags, (list, tuple)):
            hashtags = list(raw_tags)
        else:
            hashtags = []

        sample = EngagementSample(
            draft_id=did,
            news_id=r["news_id"],
            title=r["title"],
            topic_category=r["topic_category"],
            weighted_score=r["weighted_score"],
            published_at=r["published_at"],
            confidence_score=r["confidence_score"],
            fb_likes=r["fb_likes"],
            fb_comments=r["fb_comments"],
            fb_shares=r["fb_shares"],
            fb_reach=r["fb_reach"],
            fb_clicks=r["fb_clicks"],
            ig_likes=r["ig_likes"],
            ig_comments=r["ig_comments"],
            ig_shares=r["ig_shares"],
            ig_saves=r["ig_saves"],
            ig_reach=r["ig_reach"],
            th_likes=r["th_likes"],
            th_replies=r["th_replies"],
            th_reposts=r["th_reposts"],
            th_quotes=r["th_quotes"],
            th_views=r["th_views"],
            hook=content.get("hook", ""),
            char_count=int(content.get("char_count", 0) or 0),
            hashtags=hashtags,
            posted_hour=timing.get("hour"),
            posted_weekday=timing.get("weekday"),
        )
        samples.append(sample)

    return samples


def _fetch_from_base_tables(
    conn: sqlite3.Connection,
    lookback_days: int,
) -> List[EngagementSample]:
    """Fallback path when v_post_engagement_aggregated doesn't exist yet."""
    rows = conn.execute(
        """
        SELECT DISTINCT e.draft_id
          FROM engagement_stats e
          JOIN drafts d ON d.id = e.draft_id
          JOIN news_items ni ON ni.id = d.news_id
         WHERE e.fetched_at >= datetime('now', ?)
         ORDER BY e.fetched_at DESC
         LIMIT 100
        """,
        ("-" + str(int(lookback_days)) + " days",),
    ).fetchall()
    if not rows:
        return []

    draft_ids = [r["draft_id"] for r in rows]
    placeholder = ",".join("?" for _ in draft_ids)

    # Fetch latest engagement row per (draft_id, platform)
    eng_rows = conn.execute(
        """
        SELECT e.*, d.title, d.hashtags
          FROM engagement_stats e
          JOIN drafts d ON d.id = e.draft_id
         WHERE e.draft_id IN ("""
        + placeholder +
        """)
           AND e.id IN (
               SELECT MAX(id) FROM engagement_stats
                WHERE draft_id IN ("""
        + placeholder +
                """)
                GROUP BY draft_id, platform
           )
         ORDER BY e.fetched_at DESC
        """,
        draft_ids + draft_ids,
    ).fetchall()

    grouped: Dict[str, Dict] = defaultdict(dict)
    for r in eng_rows:
        did = r["draft_id"]
        pl = r["platform"]
        grouped[did]["draft_id"] = did
        grouped[did]["title"] = r["title"]
        grouped[did][pl + "_likes"] = r.get("likes", 0)
        grouped[did][pl + "_comments"] = r.get("comments", 0)
        grouped[did][pl + "_shares"] = r.get("shares", 0)
        if pl == "instagram":
            grouped[did][pl + "_saves"] = r.get("saves", 0)
        if pl == "threads":
            grouped[did][pl + "_replies"] = r.get("replies", 0)
            grouped[did][pl + "_reposts"] = r.get("reposts", 0)
            grouped[did][pl + "_quotes"] = r.get("quotes", 0)
        grouped[did][pl + "_views"] = r.get("views", 0)
        grouped[did][pl + "_reach"] = r.get("reach", 0)
        if pl == "facebook":
            grouped[did][pl + "_clicks"] = r.get("clicks", 0)
        grouped[did]["raw_hashtags"] = r["hashtags"]
        grouped[did]["published_at"] = r.get("fetched_at", "")

    # Get publish timing
    pub_rows = conn.execute(
        """
        SELECT draft_id, posted_at
          FROM publish_log
         WHERE draft_id IN ("""
        + placeholder +
        """) AND success = 1
        """,
        draft_ids,
    ).fetchall()
    pub_timing: Dict[str, Dict] = {}
    for r in pub_rows:
        if r["draft_id"] not in pub_timing:
            pub_timing[r["draft_id"]] = {}
        ts = r["posted_at"]
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            pub_timing[r["draft_id"]]["hour"] = dt.hour
            pub_timing[r["draft_id"]]["weekday"] = dt.weekday()
        except (ValueError, AttributeError):
            pass

    samples: List[EngagementSample] = []
    for did, data in grouped.items():
        raw_tags = data.get("raw_hashtags", "[]")
        if isinstance(raw_tags, str):
            try:
                hashtags = json.loads(raw_tags)
            except (json.JSONDecodeError, TypeError):
                hashtags = []
        elif isinstance(raw_tags, (list, tuple)):
            hashtags = list(raw_tags)
        else:
            hashtags = []
        timing = pub_timing.get(did, {})
        samples.append(EngagementSample(
            draft_id=did,
            news_id="",
            title=data.get("title", ""),
            topic_category=None,
            weighted_score=None,
            published_at=data.get("published_at", ""),
            confidence_score=None,
            fb_likes=data.get("fb_likes"),
            fb_comments=data.get("fb_comments"),
            fb_shares=data.get("fb_shares"),
            fb_reach=data.get("fb_reach"),
            fb_clicks=data.get("fb_clicks"),
            ig_likes=data.get("ig_likes"),
            ig_comments=data.get("ig_comments"),
            ig_shares=data.get("ig_shares"),
            ig_saves=data.get("ig_saves"),
            ig_reach=data.get("ig_reach"),
            th_likes=data.get("th_likes"),
            th_replies=data.get("th_replies"),
            th_reposts=data.get("th_reposts"),
            th_quotes=data.get("th_quotes"),
            th_views=data.get("th_views"),
            hook="",
            char_count=0,
            hashtags=hashtags,
            posted_hour=timing.get("hour"),
            posted_weekday=timing.get("weekday"),
        ))

    return samples


def _batch_fetch_draft_content(
    conn: sqlite3.Connection,
    draft_ids: List[str],
) -> Dict[str, Dict]:
    """Fetch hook + char_count + hashtags from platform_drafts (preferred)
    or drafts table, keyed by draft_id."""
    if not draft_ids:
        return {}
    placeholder = ",".join("?" for _ in draft_ids)

    # Try platform_drafts first (has per-platform content)
    try:
        rows = conn.execute(
            """
            SELECT draft_id, platform, full_text, char_count, hashtags
              FROM platform_drafts
             WHERE draft_id IN ("""
            + placeholder +
            """)
             ORDER BY draft_id, platform
            """,
            draft_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    result: Dict[str, Dict] = {}
    if rows:
        seen = set()
        for r in rows:
            did = r["draft_id"]
            if did in seen:
                continue
            seen.add(did)
            full_text = r["full_text"] or ""
            hook = _extract_hook(full_text, r["platform"])
            result[did] = {
                "hook": hook,
                "char_count": int(r["char_count"] or 0) or (len(full_text) if full_text else 0),
                "hashtags": r["hashtags"] or "[]",
            }
    else:
        # Fallback to drafts table
        rows2 = conn.execute(
            """
            SELECT id, full_text, hashtags, title
              FROM drafts
             WHERE id IN ("""
            + placeholder +
            """)
            """,
            draft_ids,
        ).fetchall()
        for r in rows2:
            did = r["id"]
            full_text = r["full_text"] or ""
            result[did] = {
                "hook": full_text[:100],
                "char_count": len(full_text) if full_text else 0,
                "hashtags": r["hashtags"] or "[]",
            }
    return result


def _extract_hook(full_text: str, platform: str) -> str:
    """Extract hook per platform convention (mirrors v_draft_hook_by_platform)."""
    if not full_text:
        return ""
    if platform == "facebook":
        return full_text[:100]
    elif platform == "instagram":
        nl = full_text.find("\n")
        if nl > 0:
            return full_text[:nl]
        return full_text[:100]
    elif platform == "threads":
        return full_text[:30]
    return full_text[:100]


def _batch_fetch_publish_timing(
    conn: sqlite3.Connection,
    draft_ids: List[str],
) -> Dict[str, Dict]:
    """Fetch first successful publish time per draft."""
    if not draft_ids:
        return {}
    placeholder = ",".join("?" for _ in draft_ids)

    rows = conn.execute(
        """
        SELECT draft_id, posted_at, platform
          FROM publish_log
         WHERE draft_id IN ("""
        + placeholder +
        """)
           AND success = 1
         ORDER BY posted_at ASC
        """,
        draft_ids,
    ).fetchall()

    result: Dict[str, Dict] = {}
    for r in rows:
        did = r["draft_id"]
        if did in result:
            continue
        ts = r["posted_at"]
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            result[did] = {
                "hour": dt.hour,
                "weekday": dt.weekday(),
                "posted_at": ts,
            }
        except (ValueError, AttributeError):
            pass
    return result


def _fetch_topic_weights(conn: sqlite3.Connection) -> Dict[str, float]:
    """Fetch current topic weights from topic_weights table."""
    try:
        rows = conn.execute(
            "SELECT category_id, weight FROM topic_weights"
        ).fetchall()
        return {r["category_id"]: float(r["weight"]) for r in rows}
    except sqlite3.OperationalError:
        return {}


def _fetch_topic_engagement_summary(
    conn: sqlite3.Connection,
    lookback_days: int,
) -> Dict[str, Dict]:
    """Per-topic engagement averages over the lookback window.

    Uses v_topic_engagement_x_platform if available, otherwise computes
    from the base view.
    """
    try:
        rows = conn.execute(
            "SELECT * FROM v_topic_engagement_x_platform"
        ).fetchall()
    except sqlite3.OperationalError:
        return _fetch_topic_from_base_view(conn, lookback_days)

    out: Dict[str, Dict] = {}
    for r in rows:
        cat = r["topic_category"]
        if not cat:
            continue
        out[cat] = {
            "sample_count": int(r["sample_count"] or 0),
            "fb_avg_likes": r["fb_avg_likes_30d"],
            "ig_avg_likes": r["ig_avg_likes_30d"],
            "th_avg_likes": r["th_avg_likes_30d"],
        }
    return out


def _fetch_topic_from_base_view(
    conn: sqlite3.Connection,
    lookback_days: int,
) -> Dict[str, Dict]:
    """Fallback: compute topic engagement averages from base view."""
    try:
        rows = conn.execute(
            """
            SELECT topic_category,
                   AVG(fb_likes) AS fb_avg_likes_30d,
                   AVG(ig_likes) AS ig_avg_likes_30d,
                   AVG(th_likes) AS th_avg_likes_30d,
                   COUNT(*) AS sample_count
              FROM v_post_engagement_aggregated
             WHERE published_at >= datetime('now', ?)
               AND topic_category IS NOT NULL
             GROUP BY topic_category
            """,
            ("-" + str(int(lookback_days)) + " days",),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    out: Dict[str, Dict] = {}
    for r in rows:
        cat = r["topic_category"]
        if not cat:
            continue
        out[cat] = {
            "sample_count": int(r["sample_count"] or 0),
            "fb_avg_likes": r["fb_avg_likes_30d"],
            "ig_avg_likes": r["ig_avg_likes_30d"],
            "th_avg_likes": r["th_avg_likes_30d"],
        }
    return out


# ---------------------------------------------------------------------------
# Self-iteration tracker
# ---------------------------------------------------------------------------

def _load_tracker() -> Dict[str, Any]:
    """Load the strategy suggestion tracker from disk."""
    if not _TRACKER_PATH.exists():
        return {"suggestions": [], "runs": []}
    try:
        return json.loads(_TRACKER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return {"suggestions": [], "runs": []}


def _save_tracker(data: Dict[str, Any]) -> None:
    """Save the strategy suggestion tracker to disk."""
    _TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TRACKER_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(_TRACKER_PATH)


def _measure_current_for_field(
    samples: List[EngagementSample],
    field: str,
) -> Dict[str, Any]:
    """Measure current engagement average for a given strategy field."""
    if not samples:
        return {}
    if field == "hook":
        eng_values = [
            engagement_weight(s, "fb")
            for s in samples
            if has_platform_engagement(s, "fb") and s.hook
        ]
        eng_values += [
            engagement_weight(s, "ig")
            for s in samples
            if has_platform_engagement(s, "ig") and s.hook
        ]
        eng_values += [
            engagement_weight(s, "th")
            for s in samples
            if has_platform_engagement(s, "th") and s.hook
        ]
    elif field == "length":
        eng_values = [
            engagement_weight(s, "fb")
            for s in samples
            if has_platform_engagement(s, "fb") and s.char_count > 0
        ]
        eng_values += [
            engagement_weight(s, "ig")
            for s in samples
            if has_platform_engagement(s, "ig") and s.char_count > 0
        ]
        eng_values += [
            engagement_weight(s, "th")
            for s in samples
            if has_platform_engagement(s, "th") and s.char_count > 0
        ]
    elif field == "hashtag":
        eng_values = [
            engagement_weight(s, "ig") + engagement_weight(s, "fb")
            for s in samples
            if s.hashtags
        ]
    else:
        # topic / timing -- use all engagement available
        eng_values = [
            max(
                engagement_weight(s, "fb"),
                engagement_weight(s, "ig"),
                engagement_weight(s, "th"),
            )
            for s in samples
            if (has_platform_engagement(s, "fb")
                or has_platform_engagement(s, "ig")
                or has_platform_engagement(s, "th"))
        ]

    if not eng_values:
        return {}
    return {
        "avg_engagement": float(statistics.mean(eng_values)),
        "sample_count": len(eng_values),
    }


def _evaluate_previous_suggestions(
    samples: List[EngagementSample],
    tracker: Dict[str, Any],
) -> List[Finding]:
    """Compare engagement before/after past suggestions were adopted.

    Returns self-iteration findings.
    """
    findings: List[Finding] = []
    past_suggestions = tracker.get("suggestions", [])
    if not past_suggestions:
        return findings

    adopted = [s for s in past_suggestions if s.get("adopted_at")]
    if not adopted:
        return findings

    for s in adopted:
        target_field = s.get("target_field", "hook")
        expected = s.get("expected_impact", "")
        adopted_at = s.get("adopted_at", "")

        baseline = s.get("baseline_metrics", {})
        current = _measure_current_for_field(samples, target_field)

        if not baseline or not current:
            continue

        delta = current.get("avg_engagement", 0) - baseline.get("avg_engagement", 0)
        if delta > 0:
            direction = "up"
        elif delta < 0:
            direction = "down"
        else:
            direction = "flat"

        # Truncate suggestion text for the observation
        sug_text = s.get("suggestion", "")[:50]

        finding = Finding(
            dimension="self_iteration",
            observation=(
                "建議「" + sug_text
                + "」採用後互動" + direction
                + "，變化量=" + str(round(delta, 2))
            ),
            confidence="MED",
            evidence={
                "suggestion_id": s.get("id", ""),
                "target_field": target_field,
                "baseline": baseline,
                "current": current,
                "delta": round(delta, 4),
                "adopted_at": adopted_at,
            },
            is_hypothesis=False,
        )
        findings.append(finding)

    return findings


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def _stat_quartile(values: List[float], cut: float) -> float:
    """Return the value at the given quantile cut (0.0-1.0)."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * cut)
    idx = max(0, min(idx, len(sorted_v) - 1))
    return sorted_v[idx]


def _correlation_trend(
    x_series: List[float],
    y_series: List[float],
) -> Optional[float]:
    """Simple linear correlation (Pearson r). Returns None if not enough
    points or zero variance."""
    if len(x_series) < MIN_SAMPLES_FOR_FINDING:
        return None
    n = len(x_series)
    mean_x = statistics.mean(x_series)
    mean_y = statistics.mean(y_series)
    num = 0.0
    den_x = 0.0
    den_y = 0.0
    for xi, yi in zip(x_series, y_series):
        dx = xi - mean_x
        dy = yi - mean_y
        num += dx * dy
        den_x += dx * dx
        den_y += dy * dy
    den = (den_x * den_y) ** 0.5
    if abs(den) < 1e-10:
        return None
    r = num / den
    # Clip to [-1, 1] for floating-point noise
    return max(-1.0, min(1.0, r))


# ---------------------------------------------------------------------------
# Content Strategy Analyzer
# ---------------------------------------------------------------------------

class ContentStrategy:
    """Cross-dimensional content-strategy analysis engine.

    Usage::

        conn = dbmod.get_conn()
        cs = ContentStrategy(conn)
        result = cs.analyze()
        editorial_note = cs.suggest()
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        lookback_days: int = LOOKBACK_DAYS,
    ):
        self.conn = conn
        self.lookback_days = lookback_days
        self._result: Optional[StrategyResult] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self) -> Dict[str, Any]:
        """Run full content-strategy analysis.

        Returns:
            Dict with keys:
              - findings:       List[Finding]
              - recommendations: List[Recommendation]
              - hypothesis_queue: List[HypothesisItem]
        """
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        samples = _fetch_engagement_samples(self.conn, self.lookback_days)
        total_samples = len(samples)

        if not samples:
            fallback = StrategyResult(
                ran_at=started_at,
                lookback_days=self.lookback_days,
                total_samples=0,
                findings=[],
                recommendations=[],
                hypothesis_queue=[
                    HypothesisItem(
                        question="尚無互動數據可用，無法進行內容策略分析。"
                                 "需要至少一篇已發布且有互動統計的貼文。",
                        expected_signal="engagement_stats 表中有任何一筆數據",
                        requires_data="至少 1 筆已發布稿件的互動數據",
                        timestamp=started_at,
                    )
                ],
                editorial_note=_FALLBACK_NOTE,
            )
            self._result = fallback
            return self._to_analysis_dict(fallback)

        # Per-dimension analysis
        all_findings: List[Finding] = []
        all_recommendations: List[Recommendation] = []
        all_hypotheses: List[HypothesisItem] = []

        # 1. Hook type vs engagement
        hook_findings, hook_recos, hook_hyp = self._analyze_hooks(samples)
        all_findings.extend(hook_findings)
        all_recommendations.extend(hook_recos)
        all_hypotheses.extend(hook_hyp)

        # 2. Word count vs engagement
        wc_findings, wc_recos, wc_hyp = self._analyze_word_count(samples)
        all_findings.extend(wc_findings)
        all_recommendations.extend(wc_recos)
        all_hypotheses.extend(wc_hyp)

        # 3. Hashtag effectiveness
        ht_findings, ht_recos, ht_hyp = self._analyze_hashtags(samples)
        all_findings.extend(ht_findings)
        all_recommendations.extend(ht_recos)
        all_hypotheses.extend(ht_hyp)

        # 4. Publishing time vs engagement
        tm_findings, tm_recos, tm_hyp = self._analyze_posting_time(samples)
        all_findings.extend(tm_findings)
        all_recommendations.extend(tm_recos)
        all_hypotheses.extend(tm_hyp)

        # 5. Topic engagement analysis
        tp_findings, tp_recos, tp_hyp = self._analyze_topics()
        all_findings.extend(tp_findings)
        all_recommendations.extend(tp_recos)
        all_hypotheses.extend(tp_hyp)

        # 6. Self-iteration: check previous suggestions
        tracker = _load_tracker()
        iter_findings = _evaluate_previous_suggestions(samples, tracker)
        all_findings.extend(iter_findings)

        # Build editorial_note from recommendations
        editorial_note = self._build_editorial_note(all_recommendations)

        result = StrategyResult(
            ran_at=started_at,
            lookback_days=self.lookback_days,
            total_samples=total_samples,
            findings=all_findings,
            recommendations=all_recommendations,
            hypothesis_queue=all_hypotheses,
            editorial_note=editorial_note,
        )
        self._result = result

        # Persist suggestions for self-iteration tracking
        self._persist_suggestions(result, tracker, started_at)

        return self._to_analysis_dict(result)

    def suggest(self) -> str:
        """Return an editorial_note string for the composer.

        The string contains actionable writing guidance distilled from
        the latest analysis run. If no analysis has been run yet, or
        no samples are available, returns the default fallback note.
        """
        if self._result is None:
            self.analyze()
        if self._result is None or not self._result.editorial_note:
            return _FALLBACK_NOTE
        return self._result.editorial_note

    # ------------------------------------------------------------------
    # Dimension: Hook type vs engagement
    # ------------------------------------------------------------------

    def _analyze_hooks(
        self,
        samples: List[EngagementSample],
    ) -> Tuple[List[Finding], List[Recommendation], List[HypothesisItem]]:
        """Classify hooks and correlate types with engagement."""
        findings: List[Finding] = []
        recommendations: List[Recommendation] = []
        hypotheses: List[HypothesisItem] = []

        hook_by_type: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for s in samples:
            hook_text = s.hook or s.title or ""
            hook_type = _classify_hook(hook_text)
            for platform in ("fb", "ig", "th"):
                if has_platform_engagement(s, platform):
                    ew = engagement_weight(s, platform)
                    hook_by_type[platform][hook_type].append(ew)

        for platform in ("fb", "ig", "th"):
            type_data = hook_by_type[platform]
            if not type_data:
                continue

            total_hooks = sum(len(v) for v in type_data.values())
            if total_hooks < MIN_SAMPLES_FOR_HYPOTHESIS:
                hypotheses.append(HypothesisItem(
                    question="{} 平台樣本不足，無法分析 Hook 類型與互動關聯".format(platform),
                    expected_signal="至少 {} 篇有標記 Hook 的貼文".format(MIN_SAMPLES_FOR_FINDING),
                    requires_data="{} 平台需要更多已發布且有 Hook 數據的樣本".format(platform),
                ))
                continue

            # Find best and worst hook types by average engagement
            avg_eng_by_type = {}
            for t, vals in type_data.items():
                if len(vals) >= MIN_SAMPLES_FOR_HYPOTHESIS:
                    avg_eng_by_type[t] = statistics.mean(vals)
            if not avg_eng_by_type:
                continue

            best_type = max(avg_eng_by_type, key=avg_eng_by_type.get)
            worst_type = min(avg_eng_by_type, key=avg_eng_by_type.get)
            best_val = avg_eng_by_type[best_type]
            worst_val = avg_eng_by_type[worst_type]

            # Only report if there is a meaningful gap
            if best_val <= 0:
                continue
            ratio = worst_val / best_val if best_val > 0 else 1.0
            if ratio > 0.8:
                continue

            # Confidence heuristic
            n_best = len(type_data[best_type])
            if n_best >= HOOK_HIGH_CONF_COUNT:
                confidence = "HIGH"
            else:
                confidence = "MED"

            findings.append(Finding(
                dimension="hook_type",
                observation=(
                    "{} 平台 {} 類 Hook 平均互動 {:.1f} "
                    "(n={})，為最高互動類別；{} 類最差 ({:.1f})。".format(
                        platform, best_type, best_val, n_best,
                        worst_type, worst_val
                    )
                ),
                confidence=confidence,
                evidence={
                    "platform": platform,
                    "best_type": best_type,
                    "best_avg_engagement": round(best_val, 2),
                    "best_sample_count": n_best,
                    "worst_type": worst_type,
                    "worst_avg_engagement": round(worst_val, 2),
                    "all_type_averages": {
                        t: round(v, 2) for t, v in sorted(
                            avg_eng_by_type.items(), key=lambda x: -x[1])
                    },
                },
                is_hypothesis=(confidence == "LOW"),
            ))

            recommendations.append(Recommendation(
                dimension="hook_type",
                suggestion=(
                    "優先採用 {} 做開場結構，"
                    "迴避 {} 模式。".format(best_type, worst_type)
                ),
                expected_impact="提高 Hook 點擊率與初始互動",
                confidence=confidence,
                target_field="hook",
                platform=platform,
            ))

        return findings, recommendations, hypotheses

    # ------------------------------------------------------------------
    # Dimension: Word count vs engagement
    # ------------------------------------------------------------------

    def _analyze_word_count(
        self,
        samples: List[EngagementSample],
    ) -> Tuple[List[Finding], List[Recommendation], List[HypothesisItem]]:
        """Correlate post length (char_count) with engagement per platform."""
        findings: List[Finding] = []
        recommendations: List[Recommendation] = []
        hypotheses: List[HypothesisItem] = []

        platforms = {
            "facebook": "fb",
            "instagram": "ig",
            "threads": "th",
        }
        platform_limits = {
            "facebook": (100, 1000),
            "instagram": (100, 2200),
            "threads": (20, 500),
        }

        for display_platform, short_platform in platforms.items():
            lengths: List[float] = []
            eng_vals: List[float] = []
            for s in samples:
                if has_platform_engagement(s, short_platform) and s.char_count > 0:
                    lengths.append(float(s.char_count))
                    eng_vals.append(engagement_weight(s, short_platform))

            if len(lengths) < MIN_SAMPLES_FOR_HYPOTHESIS:
                hypotheses.append(HypothesisItem(
                    question="{} 字數樣本不足，無法分析長度與互動的關係".format(display_platform),
                    expected_signal="至少 {} 篇有字數+互動數據".format(MIN_SAMPLES_FOR_FINDING),
                    requires_data="{} 需要更多已量測互動的貼文".format(display_platform),
                ))
                continue

            r = _correlation_trend(lengths, eng_vals)
            if r is None:
                continue

            # Bucket into short / medium / long
            limit = platform_limits.get(display_platform, (0, 9999))
            short_cut = limit[0] + (limit[1] - limit[0]) * 0.3
            long_cut = limit[0] + (limit[1] - limit[0]) * 0.7

            short_eng = [
                engagement_weight(s, short_platform)
                for s in samples
                if has_platform_engagement(s, short_platform)
                and 0 < s.char_count <= short_cut
            ]
            long_eng = [
                engagement_weight(s, short_platform)
                for s in samples
                if has_platform_engagement(s, short_platform)
                and s.char_count >= long_cut
            ]

            abs_r = abs(r)
            if abs_r > 0.5:
                confidence = "HIGH"
            elif abs_r > 0.3:
                confidence = "MED"
            else:
                confidence = "LOW"

            if r > 0.1:
                direction = "正相關"
            elif r < -0.1:
                direction = "負相關"
            else:
                direction = "無明顯相關"

            avg_short = statistics.mean(short_eng) if len(short_eng) >= 2 else None
            avg_long = statistics.mean(long_eng) if len(long_eng) >= 2 else None

            # Only generate recommendation if there is a meaningful difference
            if avg_short is not None and avg_long is not None and avg_long > avg_short * 1.2:
                findings.append(Finding(
                    dimension="word_count",
                    observation=(
                        "{} 字數與互動呈{} "
                        "(r={:.3f}, n={})。".format(
                            display_platform, direction, r, len(lengths))
                        + ("長文均值={:.1f}, 短文均值={:.1f}".format(avg_long, avg_short)
                           if avg_short is not None and avg_long is not None
                           else "")
                    ),
                    confidence=confidence,
                    evidence={
                        "platform": display_platform,
                        "correlation_r": round(r, 4),
                        "sample_count": len(lengths),
                        "avg_short_eng": round(avg_short, 2) if avg_short else None,
                        "avg_long_eng": round(avg_long, 2) if avg_long else None,
                        "short_char_cutoff": round(short_cut),
                        "long_char_cutoff": round(long_cut),
                    },
                    is_hypothesis=(confidence == "LOW"),
                ))

                if avg_long is not None and avg_short is not None and avg_long > avg_short:
                    recommendations.append(Recommendation(
                        dimension="word_count",
                        suggestion=(
                            "{} 建議將字數控制在 >{:.0f}字範圍，"
                            "該區間互動平均為 {:.1f}；"
                            "短文(<{:.0f}字)互動僅 {:.1f}。".format(
                                display_platform, long_cut, avg_long,
                                short_cut, avg_short)
                        ),
                        expected_impact="增加單篇互動總量",
                        confidence=confidence,
                        target_field="length",
                        platform=display_platform,
                    ))
                elif avg_short is not None and avg_long is not None and avg_short > avg_long:
                    recommendations.append(Recommendation(
                        dimension="word_count",
                        suggestion=(
                            "{} 建議控制字數在 {:.0f} 字以內，"
                            "該區間互動平均為 {:.1f}。".format(
                                display_platform, short_cut, avg_short)
                        ),
                        expected_impact="減少跳出、提高互動密度",
                        confidence=confidence,
                        target_field="length",
                        platform=display_platform,
                    ))
            elif avg_short is not None and avg_long is not None:
                # No meaningful difference -- still log as hypothesis
                findings.append(Finding(
                    dimension="word_count",
                    observation=(
                        "{} 字數與互動差距不大 "
                        "(長文={:.1f} vs 短文={:.1f})".format(
                            display_platform, avg_long, avg_short)
                    ),
                    confidence="LOW",
                    evidence={
                        "platform": display_platform,
                        "correlation_r": round(r, 4),
                        "avg_short_eng": round(avg_short, 2),
                        "avg_long_eng": round(avg_long, 2),
                    },
                    is_hypothesis=True,
                ))

        return findings, recommendations, hypotheses

    # ------------------------------------------------------------------
    # Dimension: Hashtag effectiveness
    # ------------------------------------------------------------------

    def _analyze_hashtags(
        self,
        samples: List[EngagementSample],
    ) -> Tuple[List[Finding], List[Recommendation], List[HypothesisItem]]:
        """Analyze hashtag count and specific tag effectiveness."""
        findings: List[Finding] = []
        recommendations: List[Recommendation] = []
        hypotheses: List[HypothesisItem] = []

        count_engagement: Dict[int, List[float]] = defaultdict(list)
        tag_engagement: Dict[str, List[float]] = defaultdict(list)

        for s in samples:
            if not s.hashtags:
                continue

            n_tags = len(s.hashtags)
            for platform in ("fb", "ig", "th"):
                if has_platform_engagement(s, platform):
                    ew = engagement_weight(s, platform)
                    count_engagement[n_tags].append(ew)

            # Tag-specific: aggregate engagement across platforms
            total_eng = 0.0
            count_platforms = 0
            for platform in ("fb", "ig", "th"):
                if has_platform_engagement(s, platform):
                    total_eng += engagement_weight(s, platform)
                    count_platforms += 1
            if count_platforms > 0:
                avg_eng = total_eng / count_platforms
                for tag in s.hashtags:
                    tag_engagement[tag].append(avg_eng)

        if not count_engagement:
            hypotheses.append(HypothesisItem(
                question="尚無含 hashtag 的貼文數據，無法分析 hashtag 效果",
                expected_signal="至少 1 篇含 hashtag 且有互動數據的貼文",
                requires_data="drafts.hashtags 非空的已發布貼文",
            ))
            return findings, recommendations, hypotheses

        # Hashtag count analysis
        avg_by_count = {}
        for c, vals in count_engagement.items():
            if len(vals) >= MIN_SAMPLES_FOR_HYPOTHESIS:
                avg_by_count[c] = statistics.mean(vals)
        if avg_by_count:
            best_count = max(avg_by_count, key=avg_by_count.get)
            worst_count = min(avg_by_count, key=avg_by_count.get)

            if best_count != worst_count or len(count_engagement) > 1:
                findings.append(Finding(
                    dimension="hashtag_count",
                    observation=(
                        "Hashtag 數量為 {} 個時平均互動最高 "
                        "({:.1f}, n={})".format(
                            best_count, avg_by_count[best_count],
                            len(count_engagement[best_count]))
                    ),
                    confidence="MED",
                    evidence={
                        "best_count": best_count,
                        "best_avg_engagement": round(avg_by_count[best_count], 2),
                        "hashtag_count_distribution": {
                            str(c): round(v, 2)
                            for c, v in sorted(avg_by_count.items())
                        },
                    },
                ))

                recommendations.append(Recommendation(
                    dimension="hashtag_count",
                    suggestion=(
                        "建議每篇貼文使用 {} 個 hashtag，"
                        "該數量區間互動表現最佳。".format(best_count)
                    ),
                    expected_impact="提升 hashtag 帶來的探索性觸及",
                    confidence="MED",
                    target_field="hashtag",
                ))

        # Individual tag analysis (only if enough data)
        tag_stats = {}
        for tag, vals in tag_engagement.items():
            if len(vals) >= MIN_SAMPLES_FOR_HYPOTHESIS:
                tag_stats[tag] = {
                    "avg_eng": statistics.mean(vals),
                    "count": len(vals),
                }
        if tag_stats:
            sorted_tags = sorted(tag_stats.items(), key=lambda x: -x[1]["avg_eng"])
            top_3 = sorted_tags[:3]
            bottom_3 = sorted_tags[-3:]

            if top_3:
                top_3_parts = []
                for t, d in top_3:
                    tag_clean = t.lstrip("#")
                    top_3_parts.append(
                        "#" + tag_clean + "(" + str(round(d["avg_eng"], 1)) + ")"
                    )
                freq_parts = []
                for t, d in top_3:
                    tag_clean = t.lstrip("#")
                    freq_parts.append("#" + tag_clean + "*" + str(d["count"]))
                findings.append(Finding(
                    dimension="hashtag_tag",
                    observation=(
                        "高互動 hashtag 前三："
                        + ", ".join(top_3_parts)
                        + "。已出現頻率：" + ", ".join(freq_parts)
                    ),
                    confidence="LOW",
                    evidence={
                        "top_tags": [
                            {"tag": t, "avg_engagement": round(d["avg_eng"], 2),
                             "count": d["count"]}
                            for t, d in top_3
                        ],
                        "bottom_tags": [
                            {"tag": t, "avg_engagement": round(d["avg_eng"], 2),
                             "count": d["count"]}
                            for t, d in bottom_3
                        ],
                    },
                    is_hypothesis=True,
                ))

        return findings, recommendations, hypotheses

    # ------------------------------------------------------------------
    # Dimension: Publishing time vs engagement
    # ------------------------------------------------------------------

    def _analyze_posting_time(
        self,
        samples: List[EngagementSample],
    ) -> Tuple[List[Finding], List[Recommendation], List[HypothesisItem]]:
        """Analyze which hours and days yield highest engagement."""
        findings: List[Finding] = []
        recommendations: List[Recommendation] = []
        hypotheses: List[HypothesisItem] = []

        hour_buckets: Dict[str, List[float]] = defaultdict(list)
        day_buckets: Dict[int, List[float]] = defaultdict(list)

        for s in samples:
            if s.posted_hour is None:
                continue
            total_eng = 0.0
            count_pl = 0
            for platform in ("fb", "ig", "th"):
                if has_platform_engagement(s, platform):
                    total_eng += engagement_weight(s, platform)
                    count_pl += 1
            if count_pl == 0:
                continue
            avg_eng_this_post = total_eng / count_pl

            # Hour bucket
            h = s.posted_hour
            if 0 <= h <= 5:
                bucket = "凌晨(00-05)"
            elif 6 <= h <= 9:
                bucket = "早晨(06-09)"
            elif 10 <= h <= 13:
                bucket = "午間(10-13)"
            elif 14 <= h <= 17:
                bucket = "下午(14-17)"
            elif 18 <= h <= 21:
                bucket = "晚間(18-21)"
            else:
                bucket = "深夜(22-23)"
            hour_buckets[bucket].append(avg_eng_this_post)

            # Day of week
            if s.posted_weekday is not None:
                day_buckets[s.posted_weekday].append(avg_eng_this_post)

        if not hour_buckets and not day_buckets:
            hypotheses.append(HypothesisItem(
                question="尚無發布時間數據，無法分析時段與互動關係",
                expected_signal="publish_log 表中有 posted_at 數據",
                requires_data="已發布貼文的發布時間記錄",
            ))
            return findings, recommendations, hypotheses

        # Hour analysis
        valid_hours = {}
        for b, v in hour_buckets.items():
            if len(v) >= MIN_SAMPLES_FOR_HYPOTHESIS:
                valid_hours[b] = v
        if valid_hours:
            best_hour = max(valid_hours, key=lambda b: statistics.mean(valid_hours[b]))
            worst_hour = min(valid_hours, key=lambda b: statistics.mean(valid_hours[b]))
            best_avg = statistics.mean(valid_hours[best_hour])
            worst_avg = statistics.mean(valid_hours[worst_hour])

            if best_avg > worst_avg * 1.3:
                if len(valid_hours[best_hour]) >= HOOK_HIGH_CONF_COUNT:
                    confidence = "HIGH"
                else:
                    confidence = "MED"
                findings.append(Finding(
                    dimension="posting_time",
                    observation=(
                        "最佳發布時段為 {} (互動均值 {:.1f})，"
                        "最差時段為 {} ({:.1f})。".format(
                            best_hour, best_avg, worst_hour, worst_avg)
                    ),
                    confidence=confidence,
                    evidence={
                        "best_hour_bucket": best_hour,
                        "best_hour_avg_eng": round(best_avg, 2),
                        "best_hour_sample_count": len(valid_hours[best_hour]),
                        "worst_hour_bucket": worst_hour,
                        "worst_hour_avg_eng": round(worst_avg, 2),
                    },
                ))
                best_time_range = best_hour.split("(")[1].rstrip(")")
                recommendations.append(Recommendation(
                    dimension="posting_time",
                    suggestion=(
                        "建議在 {} 時段發布，"
                        "該時段平均互動較最差時段高出 {:.0f}%。".format(
                            best_time_range,
                            (best_avg / worst_avg - 1) * 100)
                    ),
                    expected_impact="最大化即時互動與觸及",
                    confidence=confidence,
                    target_field="timing",
                ))

        # Day of week analysis
        day_names = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
        valid_days = {}
        for d, v in day_buckets.items():
            if len(v) >= MIN_SAMPLES_FOR_HYPOTHESIS:
                valid_days[d] = v
        if valid_days:
            best_day_num = max(valid_days, key=lambda d: statistics.mean(valid_days[d]))
            worst_day_num = min(valid_days, key=lambda d: statistics.mean(valid_days[d]))
            best_day_avg = statistics.mean(valid_days[best_day_num])
            worst_day_avg = statistics.mean(valid_days[worst_day_num])

            if best_day_avg > worst_day_avg * 1.3:
                findings.append(Finding(
                    dimension="posting_day",
                    observation=(
                        "最佳發布日為 {} (互動均值 {:.1f})，"
                        "最差為 {} ({:.1f})。".format(
                            day_names[best_day_num], best_day_avg,
                            day_names[worst_day_num], worst_day_avg)
                    ),
                    confidence="MED",
                    evidence={
                        "best_day_of_week": day_names[best_day_num],
                        "best_day_index": best_day_num,
                        "best_day_avg_eng": round(best_day_avg, 2),
                        "best_day_sample_count": len(valid_days[best_day_num]),
                        "worst_day_of_week": day_names[worst_day_num],
                        "worst_day_avg_eng": round(worst_day_avg, 2),
                    },
                ))

        return findings, recommendations, hypotheses

    # ------------------------------------------------------------------
    # Dimension: Topic engagement analysis
    # ------------------------------------------------------------------

    def _analyze_topics(
        self,
    ) -> Tuple[List[Finding], List[Recommendation], List[HypothesisItem]]:
        """Compare per-topic engagement and suggest weight adjustments."""
        findings: List[Finding] = []
        recommendations: List[Recommendation] = []
        hypotheses: List[HypothesisItem] = []

        topic_eng = _fetch_topic_engagement_summary(self.conn, self.lookback_days)
        if not topic_eng:
            hypotheses.append(HypothesisItem(
                question="尚無主題分類數據，無法分析主題權重",
                expected_signal="v_topic_engagement_x_platform 中有數據",
                requires_data="news_items 需有 topic_category 且有互動記錄",
            ))
            return findings, recommendations, hypotheses

        # Compute per-topic aggregate engagement score
        topic_scores: Dict[str, Dict] = {}
        for cat, data in topic_eng.items():
            n = data.get("sample_count", 0)
            if n < MIN_SAMPLES_FOR_HYPOTHESIS:
                continue
            fb_likes = data.get("fb_avg_likes") or 0
            ig_likes = data.get("ig_avg_likes") or 0
            th_likes = data.get("th_avg_likes") or 0
            avg_cross = (float(fb_likes) + float(ig_likes) + float(th_likes)) / 3.0
            topic_scores[cat] = {
                "avg_engagement": avg_cross,
                "sample_count": n,
                "fb_avg_likes": float(fb_likes),
                "ig_avg_likes": float(ig_likes),
                "th_avg_likes": float(th_likes),
            }

        if not topic_scores:
            return findings, recommendations, hypotheses

        # Current topic weights
        current_weights = _fetch_topic_weights(self.conn)

        sorted_topics = sorted(
            topic_scores.items(),
            key=lambda x: x[1]["avg_engagement"],
            reverse=True,
        )
        best_topic = sorted_topics[0]
        worst_topic = sorted_topics[-1]

        if best_topic[1]["avg_engagement"] <= 0:
            return findings, recommendations, hypotheses

        n_best = best_topic[1]["sample_count"]
        n_worst = worst_topic[1]["sample_count"]
        if n_best >= HOOK_HIGH_CONF_COUNT and n_worst >= HOOK_HIGH_CONF_COUNT:
            confidence = "HIGH"
        else:
            confidence = "MED"

        findings.append(Finding(
            dimension="topic",
            observation=(
                "高互動主題：{} ({:.1f} avg, "
                "n={})；低互動主題：{} "
                "({:.1f} avg, n={})。".format(
                    best_topic[0], best_topic[1]["avg_engagement"], n_best,
                    worst_topic[0], worst_topic[1]["avg_engagement"], n_worst)
            ),
            confidence=confidence,
            evidence={
                "best_topic": best_topic[0],
                "best_avg_engagement": round(best_topic[1]["avg_engagement"], 2),
                "best_topic_sample_count": n_best,
                "worst_topic": worst_topic[0],
                "worst_avg_engagement": round(worst_topic[1]["avg_engagement"], 2),
                "worst_topic_sample_count": n_worst,
                "topic_ranking": [
                    {"topic": t, "avg_engagement": round(d["avg_engagement"], 2),
                     "sample_count": d["sample_count"]}
                    for t, d in sorted_topics
                ],
            },
        ))

        # Suggest topic weight adjustment if current weight doesn't match performance
        best_current = current_weights.get(best_topic[0], 1.0)
        worst_current = current_weights.get(worst_topic[0], 1.0)
        if best_current <= 1.0 and worst_current >= 1.0:
            recommendations.append(Recommendation(
                dimension="topic",
                suggestion=(
                    "主題 {} 互動優異 (avg={:.1f})，"
                    "當前權重 {}，建議上調至 {:.1f}；"
                    "主題 {} 互動偏低 ({:.1f})，"
                    "當前權重 {}，建議下調至 {:.1f}。".format(
                        best_topic[0], best_topic[1]["avg_engagement"],
                        best_current, min(best_current * 1.2, 2.0),
                        worst_topic[0], worst_topic[1]["avg_engagement"],
                        worst_current, max(worst_current * 0.8, 0.3))
                ),
                expected_impact="確保高互動主題獲得更多曝光機會",
                confidence=confidence,
                target_field="topic",
            ))

        return findings, recommendations, hypotheses

    # ------------------------------------------------------------------
    # Editorial note builder
    # ------------------------------------------------------------------

    def _build_editorial_note(
        self,
        recommendations: List[Recommendation],
    ) -> str:
        """Compose a concise editorial_note from high-confidence recommendations.

        The note is a short paragraph in Chinese that guides the composer
        on what writing strategy to follow.
        """
        if not recommendations:
            return ""

        notes: List[str] = []
        seen_fields: set = set()

        # Priority: HIGH then MED
        sorted_recs = sorted(
            recommendations,
            key=lambda r: (
                0 if r.confidence == "HIGH" else (1 if r.confidence == "MED" else 2),
                r.target_field,
            ),
        )
        for rec in sorted_recs:
            if len(notes) >= 3:
                break
            if rec.target_field in seen_fields:
                continue
            seen_fields.add(rec.target_field)

            # Format into a compact suggestion
            pl = rec.platform
            if pl in ("fb", "ig", "th"):
                platform_short = pl.upper()
            else:
                platform_short = pl.replace("facebook", "FB").replace(
                    "instagram", "IG").replace("threads", "TH")
            if rec.platform != "all":
                platform_prefix = "[" + platform_short + "]"
            else:
                platform_prefix = ""
            # Remove the leading word "建議" since this is already a suggestion
            sug_body = rec.suggestion
            if sug_body.startswith("建議"):
                sug_body = sug_body[2:]
            notes.append(platform_prefix + sug_body)

        if not notes:
            return ""

        editorial = "本輪策略方向：\n" + "\n".join("- " + n for n in notes)
        return editorial

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_suggestions(
        self,
        result: StrategyResult,
        tracker: Dict[str, Any],
        ran_at: str,
    ) -> None:
        """Save the generated recommendations to the tracker for self-iteration."""
        if not result.recommendations:
            return

        suggestion_entries: List[Dict] = []
        for i, rec in enumerate(result.recommendations):
            entry = {
                "id": ran_at + "-" + str(i),
                "dimension": rec.dimension,
                "suggestion": rec.suggestion,
                "expected_impact": rec.expected_impact,
                "confidence": rec.confidence,
                "target_field": rec.target_field,
                "platform": rec.platform,
                "generated_at": ran_at,
                "adopted_at": None,  # None until Hsin marks it adopted
                "baseline_metrics": {},
            }

            # Record a baseline for this dimension
            samples = _fetch_engagement_samples(self.conn, self.lookback_days)
            if samples:
                baseline = _measure_current_for_field(samples, rec.target_field)
                if baseline:
                    entry["baseline_metrics"] = baseline

            suggestion_entries.append(entry)

        tracker.setdefault("suggestions", []).extend(suggestion_entries)
        tracker.setdefault("runs", []).append({
            "ran_at": ran_at,
            "findings_count": len(result.findings),
            "recommendations_count": len(result.recommendations),
            "hypothesis_count": len(result.hypothesis_queue),
        })
        _save_tracker(tracker)

    # ------------------------------------------------------------------
    # Dict serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _to_analysis_dict(result: StrategyResult) -> Dict[str, Any]:
        """Serialize StrategyResult to the public API dict format."""
        findings_out = []
        for f in result.findings:
            findings_out.append({
                "dimension": f.dimension,
                "observation": f.observation,
                "confidence": f.confidence,
                "evidence": f.evidence,
                "is_hypothesis": f.is_hypothesis,
                "sample_count": f.sample_count,
            })

        reco_out = []
        for r in result.recommendations:
            reco_out.append({
                "dimension": r.dimension,
                "suggestion": r.suggestion,
                "expected_impact": r.expected_impact,
                "confidence": r.confidence,
                "target_field": r.target_field,
                "platform": r.platform,
            })

        hyp_out = []
        for h in result.hypothesis_queue:
            hyp_out.append({
                "question": h.question,
                "expected_signal": h.expected_signal,
                "requires_data": h.requires_data,
                "timestamp": h.timestamp,
            })

        return {
            "ran_at": result.ran_at,
            "lookback_days": result.lookback_days,
            "total_samples": result.total_samples,
            "findings": findings_out,
            "recommendations": reco_out,
            "hypothesis_queue": hyp_out,
            "editorial_note": result.editorial_note,
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_strategy(
    conn: sqlite3.Connection,
    lookback_days: int = LOOKBACK_DAYS,
) -> StrategyResult:
    """Run one full content-strategy analysis cycle.

    Convenience wrapper that creates ContentStrategy, runs analyze(),
    and returns the raw StrategyResult (not the dict).
    """
    cs = ContentStrategy(conn, lookback_days=lookback_days)
    cs.analyze()
    return cs._result or StrategyResult(
        ran_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        lookback_days=lookback_days,
        total_samples=0,
        findings=[],
        recommendations=[],
        hypothesis_queue=[],
        editorial_note=_FALLBACK_NOTE,
    )


# ---------------------------------------------------------------------------
# Report / CLI
# ---------------------------------------------------------------------------

def format_report(
    result: StrategyResult,
    verbose: bool = False,
) -> str:
    """Format the analysis result as a human-readable markdown string."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines: List[str] = []
    lines.append("# Content Strategy Analyzer · " + today)
    lines.append("")
    lines.append("- ran_at: `" + result.ran_at + "`")
    lines.append("- lookback_days: " + str(result.lookback_days))
    lines.append("- total_samples: **" + str(result.total_samples) + "**")
    lines.append("- findings: **" + str(len(result.findings)) + "**")
    lines.append("- recommendations: **" + str(len(result.recommendations)) + "**")
    lines.append("- hypothesis_queue: **" + str(len(result.hypothesis_queue)) + "**")
    lines.append("")

    if result.editorial_note:
        lines.append("## Editorial Note (for composer)")
        lines.append("")
        lines.append(result.editorial_note)
        lines.append("")

    # Findings
    if result.findings:
        lines.append("## Findings")
        lines.append("")
        for i, f in enumerate(result.findings, 1):
            hyp_mark = " [HYPOTHESIS]" if f.is_hypothesis else ""
            lines.append("### " + str(i) + ". [" + f.dimension + "] " + f.confidence + hyp_mark)
            lines.append("")
            lines.append(f.observation)
            if verbose and f.evidence:
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(f.evidence, ensure_ascii=False, indent=2,
                                         default=str))
                lines.append("```")
            lines.append("")

    # Recommendations
    if result.recommendations:
        lines.append("## Recommendations")
        lines.append("")
        for i, r in enumerate(result.recommendations, 1):
            lines.append(
                str(i) + ". **[" + r.confidence + "] [" + r.dimension + "]** " + r.suggestion
            )
            lines.append("   - Expected impact: " + r.expected_impact)
            lines.append("   - Platform: " + r.platform)
            lines.append("")

    # Hypothesis queue
    if result.hypothesis_queue:
        lines.append("## Hypothesis Queue (insufficient data)")
        lines.append("")
        for i, h in enumerate(result.hypothesis_queue, 1):
            lines.append(str(i) + ". **" + h.question + "**")
            lines.append("   - Needs: " + h.requires_data)
            lines.append("   - Signal: " + h.expected_signal)
            lines.append("")

    lines.append("---")
    lines.append("_Auto-generated by `src/reflector/strategy.py`._")
    return "\n".join(lines)


def write_report(
    result: StrategyResult,
    base_dir: Optional[Path] = None,
) -> Path:
    """Write the markdown report to ``reports/strategy_<YYYY-MM-DD>.md``."""
    target_dir = Path(base_dir) if base_dir else _REPORTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = target_dir / ("strategy_" + today + ".md")
    out.write_text(format_report(result), encoding="utf-8")
    return out


def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lookback-days", type=int, default=LOOKBACK_DAYS,
        help="Lookback window for engagement data (default %d)" % LOOKBACK_DAYS,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Analyze but don't persist suggestions to the tracker file.",
    )
    parser.add_argument(
        "--no-report-file", action="store_true",
        help="Don't write reports/strategy_<DATE>.md (still prints stdout).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Include evidence JSON in the markdown report.",
    )
    args = parser.parse_args(argv)

    from src import db as dbmod
    dbmod.init_db()
    conn = dbmod.get_conn()
    try:
        cs = ContentStrategy(conn, lookback_days=args.lookback_days)
        result_dict = cs.analyze()
        editorial_note = cs.suggest()
    finally:
        conn.close()

    # Print structured summary
    print(json.dumps(result_dict, ensure_ascii=False, indent=2, default=str))
    print("\n" + "=" * 60)
    print("EDITORIAL NOTE FOR COMPOSER:")
    print(editorial_note)

    # Write markdown report
    if not args.no_report_file:
        conn2 = dbmod.get_conn()
        try:
            cs2 = ContentStrategy(conn2, lookback_days=args.lookback_days)
            cs2.analyze()
            if cs2._result:
                path_out = write_report(cs2._result)
                print("\nReport written: " + str(path_out))
        finally:
            conn2.close()

    return 0


if __name__ == "__main__":
    sys.exit(_main())
