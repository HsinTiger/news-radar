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
from pathlib import Path
from typing import Any, Iterable, Sequence

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
    "forum": 0.95,
    "video": 0.9,
    "social": 0.75,
}


def is_recovery_mode() -> bool:
    return os.environ.get("AUTOMATION_MODE", "").strip().lower() == "recovery"


def _policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def experiment_type_for(platform: str, topic: str | None) -> str:
    if platform == "instagram":
        return "format"
    if topic in {"current_affairs", "military_defense", "policy_geopolitics"}:
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
    lines = ["RECOVERY EXPERIMENT (primary hypothesis; keep other goals secondary):"]
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
) -> list[Any]:
    """Apply robust topic and configured source-priority weights before composition.

    New rows normally have no ``weighted_score`` yet, which made the old topic
    policy ineffective at selection time. Recovery mode estimates the category
    with deterministic classifier paths, then uses the configured feed tier and
    source type as bounded multipliers. This preserves exploration while making
    a primary article outrank a secondary article inside the same topic.
    """

    weights = {
        row["category_id"]: float(row["weight"])
        for row in conn.execute("SELECT category_id,weight FROM topic_weights")
    }

    def estimated_topic_weight(row: Any) -> float:
        category = _row_value(row, "topic_category")
        if not category:
            title = str(_row_value(row, "title", "") or "")
            content = str(_row_value(row, "clean_markdown", "") or "")
            match = match_disambiguation(title, content)
            if match is None:
                match = classify_topic_keyword(title, content)
            category = match.category_id if match is not None else "other"
        return weights.get(str(category), weights.get("other", 0.7))

    def estimated_source_weight(row: Any) -> float:
        tier = str(_row_value(row, "feed_tier", "") or "").lower()
        source_type = str(
            _row_value(row, "source_type", "article") or "article"
        ).lower()
        return SOURCE_TIER_MULTIPLIERS.get(tier, 0.9) * SOURCE_TYPE_MULTIPLIERS.get(
            source_type, 0.9
        )

    ranked = list(rows)
    ranked.sort(
        key=lambda row: str(_row_value(row, "published_at", "") or ""),
        reverse=True,
    )
    ranked.sort(
        key=lambda row: estimated_topic_weight(row) * estimated_source_weight(row),
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
