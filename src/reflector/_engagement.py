"""News Radar · Phase 9 reflector · shared engagement-weight helper.

Hsin-pinned engagement formulas (Phase 8.20 design, verbatim — these are
NOT a parameter, they encode Hsin's product judgment about how to weight
attention signals across platforms):

  FB:      likes + 2*comments + 3*shares + 0.01*reach
  IG:      likes + 2*comments + 3*shares + 1.5*saves + 0.01*reach
  Threads: likes + 2*replies  + 3*reposts + 1.5*quotes + 0.005*views

This helper centralizes the formula so Items 5 / 6 / 7 (composer / scorer /
gate analyzers) all derive engagement weight identically. Live caller
today: `src/reflector/scorer.py` (Phase 9 Item 6, 2026-04-28).

Placement rationale: the formula is a pure function of a row dict; it
has zero IO and zero dependencies on the rest of the reflector package.
A standalone leaf module is the smallest surface that lets cross-
analyzer imports succeed without dragging in `proposals.py` (which
imports `src.db`). Items 5/7 can ``from src.reflector._engagement
import engagement_weight`` without paying that cost.

Per-row column-name conventions match
``v_post_engagement_aggregated`` (Phase 9 Item 1):

  fb_likes / fb_comments / fb_shares / fb_reach
  ig_likes / ig_comments / ig_shares / ig_saves / ig_reach
  th_likes / th_replies  / th_reposts / th_quotes / th_views

Missing or NULL columns coerce to 0 in the formula. Callers responsible
for upstream NULL handling (e.g. Item 6 excludes drafts where ALL
platform engagement columns are NULL — that's a "not yet polled" signal,
distinct from "polled and got 0").

Spec  : PM_Radar/specs/phase_9_implementation_plan.md §3 Item 6
Canon : PM_Radar/roadmap/phase_9_unified_reflector.md §8.3 (per-platform cuts)
"""
from __future__ import annotations

from typing import Any, Mapping

VALID_PLATFORMS = ("facebook", "instagram", "threads")


def _g(row: Mapping[str, Any], key: str) -> float:
    """Coerce row[key] to float, treating None / missing as 0.0."""
    v = row.get(key) if hasattr(row, "get") else (
        row[key] if key in row.keys() else None  # type: ignore[attr-defined]
    )
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def engagement_weight(row: Mapping[str, Any], platform: str) -> float:
    """Return the Hsin-pinned engagement-weight scalar for one row × platform.

    Args:
        row: a mapping that exposes the platform's engagement columns
            via dict-style access (sqlite3.Row, dict, dataclass-as-dict,
            etc.). Missing or NULL keys are treated as 0.
        platform: one of ``facebook`` / ``instagram`` / ``threads``.

    Returns:
        A non-negative float. Always finite given finite inputs.

    Raises:
        ValueError: if `platform` is not one of the three platforms.
    """
    p = platform.lower()
    if p == "facebook" or p == "fb":
        return (
            _g(row, "fb_likes")
            + 2.0 * _g(row, "fb_comments")
            + 3.0 * _g(row, "fb_shares")
            + 0.01 * _g(row, "fb_reach")
        )
    if p == "instagram" or p == "ig":
        return (
            _g(row, "ig_likes")
            + 2.0 * _g(row, "ig_comments")
            + 3.0 * _g(row, "ig_shares")
            + 1.5 * _g(row, "ig_saves")
            + 0.01 * _g(row, "ig_reach")
        )
    if p == "threads" or p == "th":
        return (
            _g(row, "th_likes")
            + 2.0 * _g(row, "th_replies")
            + 3.0 * _g(row, "th_reposts")
            + 1.5 * _g(row, "th_quotes")
            + 0.005 * _g(row, "th_views")
        )
    raise ValueError(
        f"engagement_weight: unsupported platform {platform!r}; "
        f"expected one of {VALID_PLATFORMS}"
    )


def has_any_engagement(row: Mapping[str, Any], platform: str) -> bool:
    """Return True if any of `platform`'s engagement columns is non-NULL.

    Used by Item 6 to distinguish "polled with 0 engagement" (include in
    curve fitting) from "never polled" (exclude). The latter manifests
    as ALL columns being NULL on `v_post_engagement_aggregated`.
    """
    p = platform.lower()
    if p in ("facebook", "fb"):
        keys = ("fb_likes", "fb_comments", "fb_shares", "fb_reach")
    elif p in ("instagram", "ig"):
        keys = ("ig_likes", "ig_comments", "ig_shares", "ig_saves", "ig_reach")
    elif p in ("threads", "th"):
        keys = ("th_likes", "th_replies", "th_reposts", "th_quotes", "th_views")
    else:
        raise ValueError(
            f"has_any_engagement: unsupported platform {platform!r}"
        )
    for k in keys:
        v = row.get(k) if hasattr(row, "get") else None
        if v is not None:
            return True
    return False
