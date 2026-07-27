"""Bounded, evidence-backed Meta recovery helpers.

Recovery mode is intentionally separate from normal live automation. It ranks
fresh candidates with the approved historical topic weights, records the
hypothesis attached to every platform post, and gives the publisher a durable
marker that excludes legacy queue entries from a recovery run.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
# Correctness and primary evidence are the owner's first-order constraints.
# A bounded selection bonus lets an in-scope official record survive the
# two-candidate Recovery scan budget even when a secondary account of the same
# event carries a historically stronger topic label.  Public-impact and
# ceremonial-content weights still decide among primary records and prevent
# routine event notices from automatically winning.
PRIMARY_RECORD_SELECTION_MULTIPLIER = 1.5
TAIWAN_RELEVANCE_MARKERS = (
    "台灣", "臺灣", "全台", "全臺", "台股", "臺股", "加權指數",
    "立法院", "立院", "行政院", "政院", "總統府", "監察院", "監院",
    "司法院", "國防部", "衛福部", "食藥署", "金管會", "證交所",
    "櫃買中心", "健保", "勞保",
    "民進黨", "國民黨", "民眾黨", "藍綠", "藍白", "凱道",
    "台北", "臺北", "新北", "桃園", "台中", "臺中", "台南", "臺南",
    "高雄", "基隆", "新竹", "苗栗", "彰化", "南投", "雲林", "嘉義",
    "屏東", "宜蘭", "花蓮", "台東", "臺東", "澎湖", "金門縣", "馬祖", "連江縣",
    "台積電", "聯電",
    "聯發科", "鴻海", "廣達", "緯創", "台達電", "日月光", "環球晶", "華邦電",
    "南亞科", "國巨", "台塑",
)
TAIWAN_OFFICIAL_FEED_MARKERS = (
    "行政院", "食藥署", "證交所", "中央銀行", "金管會", "財政部",
)
HIGH_PUBLIC_IMPACT_MARKERS = (
    "食安", "回收", "不合格", "預算", "稅", "關稅", "保險", "醫療", "隱私",
    "詐騙", "交保", "起訴", "判決", "搜索", "裁罰", "停產", "停電", "缺藥",
    "薪資", "房價", "房租", "電價", "通勤", "台股", "臺股", "股價", "融資",
    "抽驗", "稽查", "上架產品清單", "高鐵",
)
CEREMONIAL_POLITICS_MARKERS = (
    "接見", "拜會", "出席", "參訪", "勉勵", "祝賀", "合影", "致詞",
    "射擊賽", "多多享用", "推廣活動", "嘉年華", "開幕典禮", "啟動儀式",
)


def is_recovery_mode() -> bool:
    return os.environ.get("AUTOMATION_MODE", "").strip().lower() == "recovery"


def platform_uses_carousel(
    platform: str,
    *,
    recovery: bool | None = None,
) -> bool:
    """Return whether this platform may publish carousel cards.

    Recovery changes one primary variable per platform: Instagram tests visual
    utility, while Facebook and Threads test native feed posts. Live mode keeps
    the legacy carousel-first behaviour.
    """

    if recovery is None:
        recovery = is_recovery_mode()
    canonical = {
        "fb": "facebook",
        "facebook": "facebook",
        "ig": "instagram",
        "instagram": "instagram",
        "threads": "threads",
    }.get(str(platform).strip().lower(), str(platform).strip().lower())
    return not recovery or canonical == "instagram"


def visible_carousel_for_platform(
    platform: str,
    carousel: Any,
    *,
    recovery: bool | None = None,
) -> Any:
    """Return only card content that will really be visible on the platform."""

    if carousel is None or not platform_uses_carousel(
        platform, recovery=recovery
    ):
        return None
    return carousel


def content_format_for_platform(
    platform: str,
    *,
    carousel_available: bool,
    recovery: bool | None = None,
) -> str:
    return (
        "carousel"
        if carousel_available
        and platform_uses_carousel(platform, recovery=recovery)
        else "feed"
    )


def _policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_age_windows(path: Path = POLICY_PATH) -> tuple[float, float]:
    """Return the normal and official-primary reserve age limits in hours."""

    editorial = _policy(path)["recovery"]["editorial_policy"]
    normal = float(editorial["max_source_age_hours"])
    reserve = float(editorial.get("reserve_max_source_age_hours", normal))
    if normal <= 0 or reserve < normal:
        raise ValueError(
            "reserve_max_source_age_hours must be >= max_source_age_hours > 0"
        )
    return normal, reserve


def _published_time(row: Any) -> datetime | None:
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


def recovery_source_tier(
    row: Any,
    *,
    now: datetime | None = None,
    windows: tuple[float, float] | None = None,
) -> str | None:
    """Classify a candidate as owner, fresh, bounded primary reserve, or ineligible."""

    feed_name = str(_row_value(row, "feed_name", "") or "")
    tags = str(_row_value(row, "tags", "") or "")
    if feed_name == "user_submission" or "user_submission" in tags:
        return "owner"

    published = _published_time(row)
    if published is None:
        return None
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    normal_hours, reserve_hours = windows or source_age_windows()
    age = reference - published
    if age < timedelta(hours=-6):
        return None
    if age <= timedelta(hours=normal_hours):
        return "fresh"

    url = str(_row_value(row, "url", "") or "")
    if (
        age <= timedelta(hours=reserve_hours)
        and "primary-record" in tags
        and url.startswith(("https://", "http://"))
    ):
        return "official_primary_reserve"
    return None


def experiment_type_for(platform: str, topic: str | None) -> str:
    if platform == "instagram":
        # 89/104 audited legacy IG posts were already carousels and both
        # carousel/image cohorts had median reach zero.  The new variable is
        # save/share utility, not the carousel container itself.
        return "utility"
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
        return f"Instagram visual utility test: {hypothesis}"
    return f"Threads daily recovery test: {hypothesis}"


def editorial_mandate_for(platforms: Iterable[str], topic: str | None) -> str:
    lines = [
        "TAIWAN DAILY RECOVERY (highest priority):",
        "- Scope: Taiwan politics/accountability, food safety, public policy, markets, or economy with a direct Taiwan consequence.",
        "- Hook: strongest verified actor + consequence in the first 45 Chinese characters; sharp is allowed, fabrication is not.",
        "- Evidence: name the primary record in the first factual paragraph; one immediately adjacent paragraph may carry the same record, but a changed subject/document must be named again. Omit unsupported details.",
        "- Accountability: apply the same evidence/response/correction standard to every political party and public official.",
        "- Structure: visible fact/interpretation boundary, who pays or benefits, one usable next check, and one answerable question.",
        "RECOVERY EXPERIMENT (primary hypothesis; keep other goals secondary):",
    ]
    if topic == "tw_stocks":
        lines.extend([
            "- TW stocks: distinguish index performance from any reader's own portfolio; never imply that every investor, holding, or pension gained with the index.",
            "- TW stocks: keep only 2-3 source-backed figures, use their original units, and make the utility a same-period comparison of the reader's return or sector exposure.",
            "- TW stocks: do not add retirement funds, corporate asset allocation, foreign investors, or other affected groups unless the source explicitly names them.",
            "- TW stocks: the action must be usable now: compare the reader's same-period return or sector exposure with the official figures. Do not tell readers to wait for next week's report.",
            "- TW stocks: close with a measurable personal-result or allocation question addressed directly to the reader, not a generic prediction about whether the market will keep rising.",
            "- TW stocks: for a trading-status notice, lead with the old restriction versus the effective-date change, omit rule numbers unless essential, and tell holders which order method or liquidity signal they can check now.",
        ])
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
    content_format: str | Mapping[str, str],
    created_at: str,
    policy_path: Path = POLICY_PATH,
) -> None:
    recovery = _policy(policy_path)["recovery"]
    baselines = recovery["baselines"]
    for platform in sorted(set(platforms)):
        experiment_type = experiment_type_for(platform, topic)
        baseline = baselines[platform]
        platform_format = (
            content_format.get(platform, "feed")
            if isinstance(content_format, Mapping)
            else content_format
        )
        if platform_format not in {"feed", "carousel", "reel"}:
            raise ValueError(
                f"Unsupported recovery content format for {platform}: "
                f"{platform_format}"
            )
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
                platform_format,
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
        title = str(_row_value(row, "title", "") or "")
        feed_name = str(_row_value(row, "feed_name", "") or "")
        # A classifier label is not geographical evidence.  For example, a
        # Korean leverage story can hit the generic ``融資餘額`` keyword and be
        # labelled tw_stocks.  Require the headline (the actual attention
        # proposition) or an official-source identity to make the Taiwan link
        # explicit.  A currency conversion or incidental body mention is not
        # enough to turn foreign news into a Taiwan daily story.
        lowered = title.lower()
        return (
            "taiwan" in lowered
            or any(marker in title for marker in TAIWAN_RELEVANCE_MARKERS)
            or any(marker in feed_name for marker in TAIWAN_OFFICIAL_FEED_MARKERS)
        )

    candidate_rows = list(rows)
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    reference_time = reference_time.astimezone(timezone.utc)
    age_windows = source_age_windows()
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
        campaign_headcount = "集會" in title and "到場" in title
        if (
            any(marker in title for marker in CEREMONIAL_POLITICS_MARKERS)
            or campaign_headcount
        ):
            return 0.45
        if any(marker in title for marker in HIGH_PUBLIC_IMPACT_MARKERS):
            return 1.25
        return 1.0

    def primary_record_weight(row: Any) -> float:
        feed_name = str(_row_value(row, "feed_name", "") or "")
        tags = str(_row_value(row, "tags", "") or "")
        tier = str(_row_value(row, "feed_tier", "") or "").lower()
        authority, _ = source_authority(feed_name, tags, tier)
        return (
            PRIMARY_RECORD_SELECTION_MULTIPLIER
            if "primary-record" in tags or authority >= 45
            else 1.0
        )

    def in_scope(row: Any) -> bool:
        topic = estimated_topic(row)
        return topic in allowed_topics and taiwan_relevant(row, topic)

    ranked = [
        row
        for row in candidate_rows
        if owner_submitted(row)
        or (
            recovery_source_tier(
                row,
                now=reference_time,
                windows=age_windows,
            )
            == "fresh"
            and in_scope(row)
        )
    ]
    has_fresh_primary = any(
        "primary-record" in str(_row_value(row, "tags", "") or "")
        and str(_row_value(row, "url", "") or "").startswith(
            ("https://", "http://")
        )
        for row in ranked
        if not owner_submitted(row)
    )
    if not has_fresh_primary:
        ranked.extend(
            row
            for row in candidate_rows
            if recovery_source_tier(
                row,
                now=reference_time,
                windows=age_windows,
            )
            == "official_primary_reserve"
            and in_scope(row)
        )
    ranked.sort(
        key=lambda row: str(_row_value(row, "published_at", "") or ""),
        reverse=True,
    )
    ranked.sort(
        key=lambda row: (
            estimated_topic_weight(row)
            * estimated_source_weight(row)
            * public_impact_weight(row)
            * primary_record_weight(row)
        ),
        reverse=True,
    )
    deduplicated: list[Any] = []
    seen_titles: set[str] = set()
    for row in ranked:
        title_key = re.sub(
            r"[^0-9A-Za-z\u4e00-\u9fff]+",
            "",
            str(_row_value(row, "title", "") or "").casefold(),
        )
        if title_key and title_key in seen_titles:
            continue
        if title_key:
            seen_titles.add(title_key)
        deduplicated.append(row)
    return deduplicated


__all__ = [
    "content_format_for_platform",
    "EXPERIMENT_TYPES",
    "experiment_type_for",
    "editorial_mandate_for",
    "hypothesis_for",
    "is_recovery_mode",
    "platform_uses_carousel",
    "rank_candidates",
    "record_experiments",
    "recovery_source_tier",
    "source_age_windows",
    "visible_carousel_for_platform",
]
