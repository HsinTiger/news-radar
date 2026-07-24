"""Bounded, evidence-backed Meta recovery helpers.

Recovery mode is intentionally separate from normal live automation. It ranks
fresh candidates with the approved historical topic weights, records the
hypothesis attached to every platform post, and gives the publisher a durable
marker that excludes legacy queue entries from a recovery run.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.gather import source_authority
from src.topic_classifier import classify_topic_keyword, match_disambiguation


PROJECT_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = PROJECT_ROOT / "config" / "social_automation_policy.json"
EXPERIMENT_TYPES = {"interest", "trust", "utility", "format"}
SOURCE_TIER_MULTIPLIERS = {
    "primary": 1.2,
    "secondary": 1.0,
}
SOURCE_TYPE_MULTIPLIERS = {
    "article": 1.0,
    "rss_summary": 1.0,
    "forum": 0.95,
    "video": 0.9,
    "social": 0.75,
}
SOURCE_AUTHORITY_MULTIPLIERS = {
    50: 1.35,
    45: 1.30,
    40: 1.20,
    30: 1.15,
    20: 1.05,
    10: 1.00,
}
INHERENTLY_TAIWAN_TOPICS = {"tw_politics", "tw_stocks"}
TAIWAN_RELEVANCE_MARKERS = (
    "台灣", "臺灣", "台股", "新台幣", "國人", "民眾", "消費者", "納稅人",
    "勞工", "投資人", "立法院", "行政院", "總統府", "食藥署", "衛福部",
    "金管會", "證交所", "櫃買中心", "中央銀行", "財政部", "台積電", "聯電",
    "聯發科", "鴻海", "廣達", "緯創", "台達電", "日月光", "環球晶", "華邦電",
    "南亞科", "國巨", "台塑",
)
DIRECT_PUBLIC_INTEREST_MARKERS = (
    "食安", "食品", "食藥", "回收", "消保", "消費警訊", "詐騙", "個資",
    "健保", "勞保", "勞動", "最低工資", "通勤", "房價", "房租", "電價",
    "水價", "油價", "薪資", "總預算", "台股", "臺股",
)
TAIWAN_OFFICIAL_FEED_MARKERS = (
    "行政院", "食藥署", "證交所", "中央銀行", "金管會", "財政部",
)
HIGH_PUBLIC_IMPACT_MARKERS = (
    "食安", "回收", "不合格", "預算", "稅", "關稅", "保險", "醫療", "隱私",
    "詐騙", "交保", "起訴", "判決", "搜索", "裁罰", "停產", "停電", "缺藥",
    "薪資", "房價", "房租", "電價", "通勤", "台股", "臺股", "股價", "融資",
)
CEREMONIAL_POLITICS_MARKERS = (
    "接見", "拜會", "出席", "參訪", "勉勵", "祝賀", "合影", "致詞",
)


def is_recovery_mode() -> bool:
    return os.environ.get("AUTOMATION_MODE", "").strip().lower() == "recovery"


def _policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def experiment_type_for(platform: str, topic: str | None) -> str:
    if platform == "instagram":
        return "format"
    if topic in {
        "current_affairs",
        "military_defense",
        "policy_geopolitics",
        "tw_politics",
    }:
        return "trust"
    if topic in {
        "tech_product_launch",
        "ai_application",
        "ai_model",
        "ai_agent",
    }:
        return "utility"
    return "interest"


def hypothesis_for(platform: str, experiment_type: str) -> str:
    hypotheses = {
        "interest": "A narrower human-impact topic will beat generic cross-posted news on qualified attention.",
        "trust": "Named attribution plus explicit fact-versus-interpretation language will improve trust signals and follower conversion.",
        "utility": "A concrete consequence and one usable reader takeaway will earn more saves, replies, or follows than abstract analysis.",
        "format": "A platform-native carousel with one idea per card will earn nonzero distribution and more saves than caption-first cross-posting.",
    }
    hypothesis = hypotheses[experiment_type]
    if platform == "facebook":
        return f"Facebook explainer test: {hypothesis}"
    if platform == "instagram":
        return f"Instagram visual test: {hypothesis}"
    return f"Threads daily recovery test: {hypothesis}"


def editorial_mandate_for(platforms: Iterable[str], topic: str | None) -> str:
    lines = [
        "TAIWAN DAILY RECOVERY (highest priority):",
        "- Scope: Taiwan politics/accountability, food safety, public policy, markets, or economy with a direct Taiwan consequence.",
        "- Hook: strongest verified actor + consequence in the first 45 Chinese characters; sharp is allowed, fabrication is not.",
        "- Evidence: name the primary record or named publisher in every factual paragraph; expose conflicts and omit unsupported details.",
        "- Accountability: apply the same evidence/response/correction standard to every political party and public official.",
        "- Structure: visible fact/interpretation boundary, who pays or benefits, one usable next check, and one answerable question.",
        "RECOVERY EXPERIMENT (primary hypothesis; keep other goals secondary):",
    ]
    for platform in sorted(set(platforms)):
        experiment_type = experiment_type_for(platform, topic)
        lines.append(
            f"- {platform}: type={experiment_type}; "
            f"hypothesis={hypothesis_for(platform, experiment_type)}"
        )
    return "\n".join(lines)


def record_experiments(
    conn: sqlite3.Connection,
    *,
    draft_id: str,
    platforms: Iterable[str],
    topic: str | None,
    content_format: str,
    created_at: str,
    policy_path: Path = POLICY_PATH,
) -> None:
    recovery = _policy(policy_path)["recovery"]
    baselines = recovery["baselines"]
    for platform in sorted(set(platforms)):
        experiment_type = experiment_type_for(platform, topic)
        baseline = baselines[platform]
        conn.execute(
            """
            INSERT INTO recovery_experiments(
              id,draft_id,platform,experiment_type,hypothesis,
              baseline_followers,baseline_primary_metric,baseline_primary_value,
              baseline_captured_at,content_format,topic,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(draft_id,platform) DO UPDATE SET
              experiment_type=excluded.experiment_type,
              hypothesis=excluded.hypothesis,
              baseline_followers=excluded.baseline_followers,
              baseline_primary_metric=excluded.baseline_primary_metric,
              baseline_primary_value=excluded.baseline_primary_value,
              baseline_captured_at=excluded.baseline_captured_at,
              content_format=excluded.content_format,
              topic=excluded.topic
            """,
            (
                f"recovery_{draft_id}_{platform}",
                draft_id,
                platform,
                experiment_type,
                hypothesis_for(platform, experiment_type),
                baseline["followers"],
                baseline["primary_metric"],
                baseline["primary_value"],
                baseline["captured_at"],
                content_format,
                topic,
                created_at,
            ),
        )
    conn.commit()


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def rank_candidates(
    conn: sqlite3.Connection,
    rows: Sequence[Any],
    *,
    now: datetime | None = None,
) -> list[Any]:
    """Apply robust topic and configured source-priority weights before composition.

    New rows normally have no ``weighted_score`` yet, which made the old topic
    policy ineffective at selection time. Recovery mode estimates the category
    with deterministic classifier paths, then uses the configured feed tier and
    source type as bounded multipliers. This preserves exploration while making
    a primary article outrank a secondary article inside the same topic.
    """

    policy = _policy()
    allowed_topics = set(
        policy["recovery"]["editorial_policy"]["allowed_topics"]
    )
    max_source_age = timedelta(
        hours=float(
            policy["recovery"]["editorial_policy"]["max_source_age_hours"]
        )
    )
    weights = {
        row["category_id"]: float(row["weight"])
        for row in conn.execute("SELECT category_id,weight FROM topic_weights")
    }

    topic_cache: dict[int, str] = {}

    def estimated_topic(row: Any) -> str:
        cache_key = id(row)
        if cache_key in topic_cache:
            return topic_cache[cache_key]
        category = _row_value(row, "topic_category")
        if not category:
            title = str(_row_value(row, "title", "") or "")
            content = str(_row_value(row, "clean_markdown", "") or "")
            match = match_disambiguation(title, content)
            if match is None:
                match = classify_topic_keyword(title, content)
            category = match.category_id if match is not None else "other"
        value = str(category)
        topic_cache[cache_key] = value
        return value

    def estimated_topic_weight(row: Any) -> float:
        return weights.get(estimated_topic(row), weights.get("other", 0.3))

    def owner_submitted(row: Any) -> bool:
        feed_name = str(_row_value(row, "feed_name", "") or "")
        tags = str(_row_value(row, "tags", "") or "")
        return feed_name == "user_submission" or "user_submission" in tags

    def taiwan_relevant(row: Any, category: str) -> bool:
        if category in INHERENTLY_TAIWAN_TOPICS:
            return True
        title = str(_row_value(row, "title", "") or "")
        feed_name = str(_row_value(row, "feed_name", "") or "")
        lowered = title.lower()
        return (
            "taiwan" in lowered
            or any(marker in title for marker in TAIWAN_RELEVANCE_MARKERS)
            or (
                category == "current_affairs"
                and any(marker in title for marker in DIRECT_PUBLIC_INTEREST_MARKERS)
            )
            or any(marker in feed_name for marker in TAIWAN_OFFICIAL_FEED_MARKERS)
        )

    def published_time(row: Any) -> datetime | None:
        value = str(_row_value(row, "published_at", "") or "")
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    candidate_rows = list(rows)
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    reference_time = reference_time.astimezone(timezone.utc)
    freshness_cutoff = reference_time - max_source_age
    future_tolerance = reference_time + timedelta(hours=6)

    def estimated_source_weight(row: Any) -> float:
        tier = str(_row_value(row, "feed_tier", "") or "").lower()
        feed_name = str(_row_value(row, "feed_name", "") or "")
        tags = str(_row_value(row, "tags", "") or "")
        source_type = str(
            _row_value(row, "source_type", "article") or "article"
        ).lower()
        authority, _ = source_authority(feed_name, tags, tier)
        return (
            SOURCE_AUTHORITY_MULTIPLIERS.get(authority, 0.9)
            * SOURCE_TIER_MULTIPLIERS.get(tier, 0.9)
            * SOURCE_TYPE_MULTIPLIERS.get(source_type, 0.9)
        )

    def public_impact_weight(row: Any) -> float:
        title = str(_row_value(row, "title", "") or "")
        if any(marker in title for marker in CEREMONIAL_POLITICS_MARKERS):
            return 0.45
        if any(marker in title for marker in HIGH_PUBLIC_IMPACT_MARKERS):
            return 1.25
        return 1.0

    ranked = [
        row
        for row in candidate_rows
        if owner_submitted(row)
        or (
            (
                freshness_cutoff
                <= (published_time(row) or datetime.min.replace(tzinfo=timezone.utc))
                <= future_tolerance
            )
            and
            estimated_topic(row) in allowed_topics
            and taiwan_relevant(row, estimated_topic(row))
        )
    ]
    ranked.sort(
        key=lambda row: str(_row_value(row, "published_at", "") or ""),
        reverse=True,
    )
    ranked.sort(
        key=lambda row: (
            estimated_topic_weight(row)
            * estimated_source_weight(row)
            * public_impact_weight(row)
        ),
        reverse=True,
    )
    return ranked


__all__ = [
    "EXPERIMENT_TYPES",
    "experiment_type_for",
    "editorial_mandate_for",
    "hypothesis_for",
    "is_recovery_mode",
    "rank_candidates",
    "record_experiments",
]
