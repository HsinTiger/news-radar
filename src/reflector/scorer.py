"""News Radar · Phase 9 Item 6 · Scorer threshold analyzer.

Daily cron entry-point. Reads ``v_post_engagement_aggregated`` (Phase 9
Item 1, extended in Item 1.6) and emits per-platform AUTO_PUBLISH
threshold proposals to ``proposals.jsonl`` via Item 2's
``write_proposal``.

Algorithm (per platform, FB / IG / Threads independently):

  1. Pull every published draft from the last 30 days for this platform
     via the substrate view.
  2. Drop rows where the platform's engagement columns are ALL NULL —
     the engagement worker hasn't polled them yet, so they don't carry
     a curve-fitting signal. Document the skip count in the report.
  3. Sample-size gate: < 30 polled-published rows → SKIP this platform.
     Write a lineage row only (no jsonl proposal) tagged with
     ``reason=insufficient_samples``.
  4. Bucket each row's ``weighted_score`` to nearest 0.05 (banker's
     rounding via ``round()`` — see `_bucket_score()` for the
     intentional choice).
  5. Compute ``engagement_weight`` per row using the Hsin-pinned formula
     (Phase 8.20 design, codified in :mod:`src.reflector._engagement`).
  6. For every candidate threshold T in [0.30, 1.00) at 0.05 step, score:

        engagement_per_published_post(T) = mean(weight | weighted_score >= T)

     The mean-of-tail is the "engagement per post that we WOULD publish
     under threshold T" — i.e. the per-published-post quality. Maximizing
     this picks the threshold where the published cohort has the
     strongest engagement signal.

     **Spec interpretation note** (audit-flagged for PM ratification):
     the spec text reads
     ``mean(...) × (count_at_or_above_T / total_count)`` and labels it
     "balances per-post quality with publish volume." Algebraically that
     product equals ``sum_in_tail / N``, which is monotonically
     non-increasing in T for non-negative weights — the unconstrained
     optimum is always GRID_LO and the formula does NOT in fact
     balance. The "engagement_per_published_post" name in the spec
     matches the mean-of-tail interpretation we use here. We retain a
     volume-aware sample threshold (`count_in_tail >= MIN_TAIL_FOR_FIT`)
     so the optimizer can't pick a threshold where the right tail is
     too sparse to be statistically defensible. Sub-threshold tails
     score 0 (i.e. don't win).

     Documented in Item 6 audit (design choice §) for explicit PM
     sign-off before Item 7 lands.
  7. The optimum T_unc maximizes score(T). Clamp to [0.50, 0.95]:
       * T_unc < 0.50 → propose 0.50, set ``bound_hit = "lower"``.
       * T_unc > 0.95 → propose 0.95, set ``bound_hit = "upper"``.
       * else                ``bound_hit = None``.
  8. Compare proposed T against current per-platform AUTO_PUBLISH (read
     from ``config/thresholds.yml`` with global fallback).
  9. Noise floor: ``|delta| < 0.02`` → no jsonl proposal. Write a
     lineage row tagged ``reason=below_noise_floor``.
  10. Calibration phase (Phase 9 §8.4): every surviving proposal is
      PROPOSAL-ONLY. ``boss_attention_required=True`` always.
      ``mark_deployed`` is never called from this module. Phase 9
      graduation flips this gate; until then auto-deploy is disabled.

Skip records (insufficient_samples / below_noise_floor) are first-class
lineage entries — Hsin / dashboard surfaces "didn't propose because no
samples" in audit views. They do NOT land in the jsonl (which carries
only actionable proposals).

Sanity bounds and noise floor are constants near the top of this module
so they're easy to tune via PR review when the calibration data
warrants.

Per-run report: every non-dry-run cycle writes a markdown digest at
``reports/scorer_<YYYY-MM-DD>.md`` with one section per platform listing
the proposed threshold, gate trace, bucket histogram, and any skip
reason. Same Task-C absorption pattern as Item 4.

Spec  : PM_Radar/specs/phase_9_implementation_plan.md §3 Item 6
Canon : PM_Radar/roadmap/phase_9_unified_reflector.md §8.3 / §8.4
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ._engagement import engagement_weight, has_any_engagement

# ---------------------------------------------------------------------------
# Constants — Hsin-pinned (spec §3 Item 6 acceptance criteria)
# ---------------------------------------------------------------------------

PLATFORMS: Tuple[str, ...] = ("fb", "ig", "threads")

# Map our short platform codes → engagement_weight() platform argument.
_ENG_PLATFORM = {"fb": "facebook", "ig": "instagram", "threads": "threads"}

# Sample-size gate (spec §3 Item 6 step 3)
MIN_SAMPLES_FOR_FIT = 30

# Bucket granularity (step 4)
BUCKET_STEP = 0.05

# Threshold candidate grid for the optimizer. Range is generous on both
# sides so the unconstrained optimum can fall outside the sanity bounds
# and trigger `bound_hit`. The clamp is applied separately.
GRID_LO = 0.30
GRID_HI = 1.00  # exclusive — last candidate is GRID_HI - BUCKET_STEP = 0.95
NUM_GRID_POINTS = int(round((GRID_HI - GRID_LO) / BUCKET_STEP))  # = 14

# Sanity bounds (step 9)
SANITY_LO = 0.50
SANITY_HI = 0.95

# Noise floor (step 10)
NOISE_FLOOR_DELTA = 0.02

# Confidence cutoff (spec §3 Item 6 evidence.confidence rule)
HIGH_CONF_SAMPLE_FLOOR = 60

# Minimum number of rows in the right tail for a candidate threshold to
# be eligible — guards against an optimum where the tail is too sparse
# to be statistically defensible (e.g. T=0.95 with only 2 polled rows
# above 0.95). Sub-threshold tails score 0 in the optimizer (never win).
MIN_TAIL_FOR_FIT = 5

# 30-day window per spec
WINDOW_DAYS = 30

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_THRESHOLDS_PATH = _PROJECT_ROOT / "config" / "thresholds.yml"
_REPORTS_DIR = _PROJECT_ROOT / "reports"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DraftEngagementRow:
    """A subset of ``v_post_engagement_aggregated`` columns relevant to
    the scorer analyzer. Constructed by the DB IO layer or by tests.

    Carries every per-platform column unconditionally; whether the row
    is "polled" for a given platform is determined by
    ``has_any_engagement(row, platform)``.
    """
    draft_id: str
    weighted_score: Optional[float]
    published_at: Optional[str]
    fb_likes: Optional[float] = None
    fb_comments: Optional[float] = None
    fb_shares: Optional[float] = None
    fb_reach: Optional[float] = None
    ig_likes: Optional[float] = None
    ig_comments: Optional[float] = None
    ig_shares: Optional[float] = None
    ig_saves: Optional[float] = None
    ig_reach: Optional[float] = None
    th_likes: Optional[float] = None
    th_replies: Optional[float] = None
    th_reposts: Optional[float] = None
    th_quotes: Optional[float] = None
    th_views: Optional[float] = None

    def as_mapping(self) -> Dict[str, Any]:
        """Return a dict mirror of the row for ``engagement_weight``.

        ``engagement_weight`` accepts any Mapping; this avoids forcing
        callers to reach into the dataclass via ``__dict__``.
        """
        return {
            "fb_likes": self.fb_likes, "fb_comments": self.fb_comments,
            "fb_shares": self.fb_shares, "fb_reach": self.fb_reach,
            "ig_likes": self.ig_likes, "ig_comments": self.ig_comments,
            "ig_shares": self.ig_shares, "ig_saves": self.ig_saves,
            "ig_reach": self.ig_reach,
            "th_likes": self.th_likes, "th_replies": self.th_replies,
            "th_reposts": self.th_reposts, "th_quotes": self.th_quotes,
            "th_views": self.th_views,
        }


@dataclass
class PlatformVerdict:
    """Per-platform analyzer outcome surfaced in the markdown report."""
    platform: str
    sample_count: int                # rows that survived NULL filter
    excluded_unpolled: int           # rows excluded because never polled
    current_threshold: float
    proposed_threshold: Optional[float] = None  # None if skipped
    delta: Optional[float] = None
    bound_hit: Optional[str] = None  # "lower" / "upper" / None
    skip_reason: Optional[str] = None  # "insufficient_samples" / "below_noise_floor" / None
    fire_id: Optional[str] = None
    confidence: Optional[str] = None
    engagement_per_post_at_current: Optional[float] = None
    engagement_per_post_at_proposed: Optional[float] = None
    bucket_histogram: Dict[float, int] = field(default_factory=dict)


@dataclass
class ScorerResult:
    ran_at: str
    dry_run: bool
    verdicts: List[PlatformVerdict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure helpers (no IO; unit-testable in isolation)
# ---------------------------------------------------------------------------

def _bucket_score(score: float, step: float = BUCKET_STEP) -> float:
    """Round `score` to the nearest 0.05 bucket.

    Uses Python's built-in ``round()`` (banker's rounding — half to
    even). Choice over half-up: banker's avoids systematic upward bias
    on bucket boundaries when the input distribution is symmetric, which
    matters for the curve-fit objective. 0.7250 → 0.7000 (banker's, tied
    to even thousandth above the step), 0.7251 → 0.7500.

    Always returns a float rounded to 6 decimal places to dodge IEEE
    float drift like 0.7000000000000001.
    """
    n = round(score / step)
    return round(n * step, 6)


def _is_polled_for_platform(row: DraftEngagementRow, platform: str) -> bool:
    """Whether the row carries any engagement signal for `platform`.

    Wraps ``has_any_engagement`` against the dataclass mirror.
    """
    return has_any_engagement(row.as_mapping(), _ENG_PLATFORM[platform])


def compute_engagement_weight_for(
    row: DraftEngagementRow, platform: str
) -> float:
    """Hsin-pinned engagement weight for `row` × `platform`.

    Convenience wrapper so call sites in this module don't need to
    import ``engagement_weight`` directly.
    """
    return engagement_weight(row.as_mapping(), _ENG_PLATFORM[platform])


def _objective_at(
    candidates: Sequence[Tuple[float, float]],  # (score, weight) pairs
    threshold: float,
) -> Tuple[float, float, int]:
    """Return (objective, mean_weight, count) for one threshold.

    objective = mean(weight | s >= T) — the mean-of-tail, i.e.
    "engagement per post that we'd publish under T". A right tail with
    fewer than MIN_TAIL_FOR_FIT rows scores 0 (sparse-tail guard) so
    the optimizer can't park on a threshold whose evidence is too thin.

    Empty right tail → (0.0, 0.0, 0).
    """
    if not candidates:
        return 0.0, 0.0, 0
    in_tail = [w for (s, w) in candidates if s >= threshold]
    if not in_tail:
        return 0.0, 0.0, 0
    if len(in_tail) < MIN_TAIL_FOR_FIT:
        # Sparse tail — defensible mean but indefensible volume; don't
        # let the optimizer prefer this point.
        return 0.0, sum(in_tail) / len(in_tail), len(in_tail)
    mean_w = sum(in_tail) / len(in_tail)
    return mean_w, mean_w, len(in_tail)


def find_optimum_threshold(
    pairs: Sequence[Tuple[float, float]],
) -> Tuple[float, float]:
    """Grid-search the optimum unconstrained threshold T_unc.

    Returns (T_unc, objective_at_T_unc). ``T_unc`` is the bucket-aligned
    value in [GRID_LO, GRID_HI - BUCKET_STEP] that maximizes the
    objective. Ties broken by preferring the LOWER threshold (more
    publishing — biased toward higher coverage when quality is
    indistinguishable).

    Empty input returns (GRID_LO, 0.0); caller is expected to gate on
    sample size before calling.
    """
    if not pairs:
        return GRID_LO, 0.0
    best_t = GRID_LO
    best_obj = -math.inf
    # Iterate from low to high so ties resolve to the LOWER threshold
    # via strict `>`.
    for i in range(NUM_GRID_POINTS):
        t = round(GRID_LO + i * BUCKET_STEP, 6)
        obj, _mean, _n = _objective_at(pairs, t)
        if obj > best_obj:
            best_obj = obj
            best_t = t
    if best_obj == -math.inf:
        best_obj = 0.0
    return best_t, best_obj


def clamp_to_sanity(t_unc: float) -> Tuple[float, Optional[str]]:
    """Apply the [SANITY_LO, SANITY_HI] clamp.

    Returns (proposed_T, bound_hit) where bound_hit is "lower" / "upper"
    / None.
    """
    if t_unc < SANITY_LO:
        return SANITY_LO, "lower"
    if t_unc > SANITY_HI:
        return SANITY_HI, "upper"
    return round(t_unc, 6), None


def confidence_for(sample_count: int, bound_hit: Optional[str]) -> str:
    """HIGH if sample_count >= HIGH_CONF_SAMPLE_FLOOR AND no bound hit.
    MED otherwise.

    Rationale: a clamp means the data wanted to go further than our
    sanity rails — that's a flag for human review, not a HIGH-confidence
    auto-tune.
    """
    if sample_count >= HIGH_CONF_SAMPLE_FLOOR and bound_hit is None:
        return "HIGH"
    return "MED"


def bucket_histogram(
    pairs: Sequence[Tuple[float, float]],
) -> Dict[float, int]:
    """Return a bucket → count mapping. Buckets are pre-rounded scores
    in `pairs[0]` (already bucketed by the orchestrator)."""
    out: Dict[float, int] = {}
    for s, _w in pairs:
        out[s] = out.get(s, 0) + 1
    return out


# ---------------------------------------------------------------------------
# thresholds.yml IO (resolution order helper)
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLD = 0.70


def read_current_threshold(
    platform: str,
    *,
    config_path: Optional[Path] = None,
) -> float:
    """Read the current AUTO_PUBLISH threshold for `platform`.

    Resolution order (documented in `config/thresholds.yml`):
      1. ``per_platform.<plat>.AUTO_PUBLISH`` if present.
      2. Top-level ``AUTO_PUBLISH`` if present.
      3. Hard-coded 0.70 (matches today's `run_pipeline.py` constant).

    `platform` accepts the short code (`fb`/`ig`/`threads`) — no
    re-mapping needed since the YAML uses the same shape.
    """
    path = Path(config_path) if config_path else _THRESHOLDS_PATH
    if not path.exists():
        logger.warning(
            "[scorer] thresholds.yml not found at %s; using default %s",
            path, _DEFAULT_THRESHOLD,
        )
        return _DEFAULT_THRESHOLD
    try:
        import yaml
    except Exception:  # pragma: no cover — yaml is a hard dep
        logger.warning("[scorer] PyYAML missing; defaulting threshold")
        return _DEFAULT_THRESHOLD
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[scorer] failed to parse %s: %s", path, exc)
        return _DEFAULT_THRESHOLD

    pp = data.get("per_platform") or {}
    plat_block = pp.get(platform)
    if isinstance(plat_block, dict) and "AUTO_PUBLISH" in plat_block:
        try:
            return float(plat_block["AUTO_PUBLISH"])
        except (TypeError, ValueError):
            pass
    if "AUTO_PUBLISH" in data:
        try:
            return float(data["AUTO_PUBLISH"])
        except (TypeError, ValueError):
            pass
    return _DEFAULT_THRESHOLD


# ---------------------------------------------------------------------------
# DB IO
# ---------------------------------------------------------------------------

def fetch_recent_published_rows(
    conn: sqlite3.Connection,
    *,
    window_days: int = WINDOW_DAYS,
) -> List[DraftEngagementRow]:
    """Read every published draft from the last `window_days` days.

    Reads `v_post_engagement_aggregated`. Returns an empty list if the
    view is missing (older DBs that haven't sourced views.sql yet).
    """
    sql = f"""
        SELECT draft_id, weighted_score, published_at,
               fb_likes, fb_comments, fb_shares, fb_reach,
               ig_likes, ig_comments, ig_shares, ig_saves, ig_reach,
               th_likes, th_replies, th_reposts, th_quotes, th_views
          FROM v_post_engagement_aggregated
         WHERE published_at IS NOT NULL
           AND published_at > datetime('now', '-{window_days} days')
    """
    try:
        cur = conn.execute(sql)
    except sqlite3.OperationalError as exc:
        logger.warning(
            "[scorer] v_post_engagement_aggregated unreadable: %s", exc
        )
        return []

    out: List[DraftEngagementRow] = []
    for r in cur.fetchall():
        def _g(key: str, idx: int):
            return r[key] if hasattr(r, "keys") else r[idx]
        out.append(DraftEngagementRow(
            draft_id=_g("draft_id", 0),
            weighted_score=(
                float(_g("weighted_score", 1))
                if _g("weighted_score", 1) is not None else None
            ),
            published_at=_g("published_at", 2),
            fb_likes=_g("fb_likes", 3),
            fb_comments=_g("fb_comments", 4),
            fb_shares=_g("fb_shares", 5),
            fb_reach=_g("fb_reach", 6),
            ig_likes=_g("ig_likes", 7),
            ig_comments=_g("ig_comments", 8),
            ig_shares=_g("ig_shares", 9),
            ig_saves=_g("ig_saves", 10),
            ig_reach=_g("ig_reach", 11),
            th_likes=_g("th_likes", 12),
            th_replies=_g("th_replies", 13),
            th_reposts=_g("th_reposts", 14),
            th_quotes=_g("th_quotes", 15),
            th_views=_g("th_views", 16),
        ))
    return out


# ---------------------------------------------------------------------------
# Per-platform analysis
# ---------------------------------------------------------------------------

def evaluate_platform(
    rows: Sequence[DraftEngagementRow],
    platform: str,
    *,
    current_threshold: float,
) -> PlatformVerdict:
    """Pure decision function for one platform.

    Returns a PlatformVerdict with verdict-relevant fields populated.
    Does NOT write proposals. Caller (`run_scorer`) consumes the verdict
    and dispatches to write_proposal / lineage as appropriate.
    """
    polled: List[DraftEngagementRow] = []
    excluded = 0
    for r in rows:
        if r.weighted_score is None:
            # Drafts without a score can't be bucketed — exclude silently.
            # (These are pre-Phase-8.20 rows; same data drift as Item 4.)
            excluded += 1
            continue
        if not _is_polled_for_platform(r, platform):
            excluded += 1
            continue
        polled.append(r)

    sample_count = len(polled)

    # Sample-size gate
    if sample_count < MIN_SAMPLES_FOR_FIT:
        return PlatformVerdict(
            platform=platform,
            sample_count=sample_count,
            excluded_unpolled=excluded,
            current_threshold=current_threshold,
            skip_reason="insufficient_samples",
        )

    # Build (bucketed_score, engagement_weight) pairs
    pairs: List[Tuple[float, float]] = []
    for r in polled:
        bucket = _bucket_score(r.weighted_score)  # type: ignore[arg-type]
        w = compute_engagement_weight_for(r, platform)
        pairs.append((bucket, w))

    # Find unconstrained optimum
    t_unc, _obj_at_unc = find_optimum_threshold(pairs)
    proposed, bound_hit = clamp_to_sanity(t_unc)

    delta = round(proposed - current_threshold, 6)

    # Compute reference-line metrics for the evidence payload
    _, _, _ = _objective_at(pairs, current_threshold)
    obj_curr, _, _ = _objective_at(pairs, current_threshold)
    obj_prop, _, _ = _objective_at(pairs, proposed)

    verdict = PlatformVerdict(
        platform=platform,
        sample_count=sample_count,
        excluded_unpolled=excluded,
        current_threshold=current_threshold,
        proposed_threshold=proposed,
        delta=delta,
        bound_hit=bound_hit,
        confidence=confidence_for(sample_count, bound_hit),
        engagement_per_post_at_current=round(obj_curr, 6),
        engagement_per_post_at_proposed=round(obj_prop, 6),
        bucket_histogram=bucket_histogram(pairs),
    )

    # Noise floor — unchanged proposals get a lineage skip record, no jsonl.
    if abs(delta) < NOISE_FLOOR_DELTA:
        verdict.skip_reason = "below_noise_floor"

    return verdict


# ---------------------------------------------------------------------------
# Proposal payload + lineage-skip builders
# ---------------------------------------------------------------------------

def _format_reason(verdict: PlatformVerdict) -> str:
    """One-line human-readable rationale, surfaced in the dashboard's
    boss-pinned approval queue.

    The dashboard truncates ``evidence.metrics`` to its first two keys, so
    raw metrics alone don't tell the reviewer WHY a proposal moved. This
    string compresses the load-bearing decision drivers (sample size,
    tail-mean engagement delta, clamp status) into one sentence so a
    reviewer can sanity-check without expanding the JSON.

    Kept stable & deterministic — no float-format surprises across runs.
    """
    parts: List[str] = []
    parts.append(
        f"{verdict.sample_count} polled samples in last {WINDOW_DAYS}d"
    )
    cur_obj = verdict.engagement_per_post_at_current
    prop_obj = verdict.engagement_per_post_at_proposed
    if cur_obj is not None and prop_obj is not None:
        try:
            lift = prop_obj - cur_obj
            sign = "+" if lift >= 0 else ""
            parts.append(
                f"tail-mean engagement {cur_obj:.2f} → {prop_obj:.2f} "
                f"({sign}{lift:.2f} per published post)"
            )
        except (TypeError, ValueError):
            pass
    if verdict.bound_hit:
        parts.append(
            f"clamped at sanity {verdict.bound_hit} bound "
            f"({SANITY_LO:.2f}–{SANITY_HI:.2f})"
        )
    return "; ".join(parts) + "."


def _build_threshold_payload(verdict: PlatformVerdict) -> dict:
    """Construct the jsonl proposal dict for an actionable verdict."""
    metrics = {
        "sample_count": verdict.sample_count,
        "excluded_unpolled": verdict.excluded_unpolled,
        "current_threshold": verdict.current_threshold,
        "proposed_threshold": verdict.proposed_threshold,
        "delta": verdict.delta,
        "engagement_per_post_at_current":
            verdict.engagement_per_post_at_current,
        "engagement_per_post_at_proposed":
            verdict.engagement_per_post_at_proposed,
        "total_engagement_lift_estimate": (
            round((verdict.proposed_threshold - verdict.current_threshold)
                  * verdict.sample_count, 6)
            if verdict.proposed_threshold is not None else None
        ),
        "bound_hit": verdict.bound_hit,
        "window_days": WINDOW_DAYS,
    }
    return {
        "analyzer": "scorer",
        "platform": _ENG_PLATFORM[verdict.platform],
        "proposal_type": "tune_threshold",
        "evidence": {
            "sample_ids": [],  # curve-level proposal, not draft-level
            "metrics": metrics,
            "reason": _format_reason(verdict),
            "confidence": verdict.confidence or "MED",
        },
        "action": {
            "target_config": "thresholds.yml",
            "field": f"per_platform.{verdict.platform}.AUTO_PUBLISH",
            "current_value": verdict.current_threshold,
            "proposed_value": verdict.proposed_threshold,
        },
        # Calibration phase: ALWAYS proposal-only. Phase 9 graduation
        # (§8.4) flips this gate; until then no auto-deploy.
        "boss_attention_required": True,
    }


def _write_skip_lineage_row(
    verdict: PlatformVerdict,
    *,
    db_path: Optional[Path],
) -> Optional[str]:
    """Persist a "didn't propose, here's why" record to the lineage table.

    These are first-class skip records — Hsin / dashboard surfaces them
    in audit views ("scorer didn't propose for FB this run because <30
    polled samples"). No jsonl entry is written.

    Schema: same lineage table as actionable proposals; recognized by
    ``analyzer='scorer'`` + ``evidence_json`` carrying `reason` and
    ``deployed_at IS NULL`` permanently. Returns the synthesized
    ``fire_id`` so the orchestrator can reference it from the markdown
    report.
    """
    import uuid
    from . import proposals as _p_mod

    fire_id = str(uuid.uuid4())
    fire_at = _p_mod._utcnow_iso()
    evidence = {
        "reason": verdict.skip_reason,
        "sample_count": verdict.sample_count,
        "excluded_unpolled": verdict.excluded_unpolled,
        "current_threshold": verdict.current_threshold,
        "proposed_threshold": verdict.proposed_threshold,
        "delta": verdict.delta,
        "boss_attention_required": False,
    }

    resolved_db = _p_mod._resolve_db_path(db_path)
    try:
        with sqlite3.connect(str(resolved_db)) as conn:
            conn.execute(
                """
                INSERT INTO reflector_proposal_lineage
                  (fire_id, fire_at, analyzer, proposal_type, target_config,
                   hsin_decision, hsin_decision_at, deployed_at, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fire_id,
                    fire_at,
                    "scorer",
                    "tune_threshold",
                    "thresholds.yml",
                    None, None, None,
                    json.dumps(evidence, ensure_ascii=False),
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.error(
            "[scorer] skip-lineage insert failed for platform=%r reason=%r: %s",
            verdict.platform, verdict.skip_reason, exc,
        )
        return None
    return fire_id


def _safe_write_proposal(
    payload: dict,
    *,
    db_path: Optional[Path],
    base_dir: Optional[Path],
    platform: str,
) -> Optional[str]:
    """Wrap write_proposal so a single bad platform doesn't kill the run."""
    try:
        from .proposals import write_proposal
    except Exception as exc:  # pragma: no cover
        logger.error("[scorer] cannot import write_proposal: %s", exc)
        return None
    try:
        return write_proposal(payload, db_path=db_path, base_dir=base_dir)
    except Exception as exc:
        logger.error(
            "[scorer] write_proposal failed for platform=%r: %s",
            platform, exc,
        )
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_scorer(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    write_proposals: bool = True,
    proposals_db_path: Optional[Path] = None,
    proposals_base_dir: Optional[Path] = None,
    thresholds_path: Optional[Path] = None,
    reports_dir: Optional[Path] = None,
    write_report: bool = True,
    now: Optional[datetime] = None,
) -> ScorerResult:
    """One full scorer-analyzer cycle.

    Order of operations:
      1. Read 30-day window of v_post_engagement_aggregated rows.
      2. For each of FB / IG / Threads:
         a. Evaluate via `evaluate_platform`.
         b. If `skip_reason` is set: write a lineage-skip row (no jsonl).
         c. Else: write a tune_threshold proposal via Item 2.
      3. Build markdown report (Task C absorption); write unless
         suppressed.

    Test-only kwargs let tests redirect side effects to tmp_path.
    """
    rows = fetch_recent_published_rows(conn)
    now_dt = now or datetime.now(timezone.utc)

    verdicts: List[PlatformVerdict] = []
    for plat in PLATFORMS:
        current = read_current_threshold(plat, config_path=thresholds_path)
        verdict = evaluate_platform(rows, plat, current_threshold=current)

        if verdict.skip_reason:
            if (not dry_run) and write_proposals:
                fire_id = _write_skip_lineage_row(
                    verdict, db_path=proposals_db_path
                )
                verdict.fire_id = fire_id
        else:
            if (not dry_run) and write_proposals:
                payload = _build_threshold_payload(verdict)
                fire_id = _safe_write_proposal(
                    payload,
                    db_path=proposals_db_path,
                    base_dir=proposals_base_dir,
                    platform=plat,
                )
                verdict.fire_id = fire_id

        verdicts.append(verdict)

    result = ScorerResult(
        ran_at=now_dt.isoformat(timespec="seconds"),
        dry_run=dry_run,
        verdicts=verdicts,
    )

    if write_report and not dry_run:
        try:
            report_path = write_markdown_report(result, base_dir=reports_dir)
            logger.info("[scorer] report written: %s", report_path)
        except Exception as exc:  # pragma: no cover
            logger.warning("[scorer] failed to write report: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def format_markdown_report(result: ScorerResult) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines: List[str] = []
    lines.append(f"# Scorer Analyzer · {today}")
    lines.append("")
    lines.append(f"- ran_at: `{result.ran_at}`")
    lines.append(f"- platforms_evaluated: **{len(result.verdicts)}**")
    proposals = sum(
        1 for v in result.verdicts
        if v.skip_reason is None and v.proposed_threshold is not None
    )
    skips = sum(1 for v in result.verdicts if v.skip_reason)
    lines.append(f"- actionable proposals: **{proposals}**")
    lines.append(f"- skipped (lineage-only): **{skips}**")
    if result.dry_run:
        lines.append("- mode: **dry-run** (no proposals written)")
    lines.append("")

    for v in result.verdicts:
        lines.append(f"## {v.platform}")
        lines.append("")
        lines.append(f"- sample_count (polled): **{v.sample_count}**")
        lines.append(f"- excluded (unpolled / no score): {v.excluded_unpolled}")
        lines.append(f"- current_threshold: `{v.current_threshold}`")
        if v.skip_reason:
            lines.append(f"- skip_reason: **{v.skip_reason}**")
            if v.proposed_threshold is not None:
                lines.append(
                    f"- (would-have proposed: `{v.proposed_threshold}`, "
                    f"delta: `{v.delta}`)"
                )
        else:
            lines.append(f"- proposed_threshold: **`{v.proposed_threshold}`**")
            lines.append(f"- delta: `{v.delta}`")
            if v.bound_hit:
                lines.append(f"- bound_hit: **{v.bound_hit}** "
                             "(unconstrained optimum exceeded sanity rails)")
            lines.append(f"- confidence: {v.confidence}")
            lines.append(
                f"- engagement_per_post: "
                f"`{v.engagement_per_post_at_current}` (current) → "
                f"`{v.engagement_per_post_at_proposed}` (proposed)"
            )
        if v.fire_id:
            lines.append(f"- lineage fire_id: `{v.fire_id[:8]}…`")
        if v.bucket_histogram:
            lines.append("")
            lines.append("### Bucket histogram")
            lines.append("")
            lines.append("| weighted_score bucket | count |")
            lines.append("|---|---|")
            for b in sorted(v.bucket_histogram):
                lines.append(f"| `{b:.2f}` | {v.bucket_histogram[b]} |")
        lines.append("")

    lines.append("---")
    lines.append("_Auto-generated by `src/reflector/scorer.py` "
                 "(Phase 9 Item 6). Calibration-phase: every proposal is "
                 "PROPOSAL-ONLY pending Hsin sign-off._")
    return "\n".join(lines)


def write_markdown_report(
    result: ScorerResult,
    base_dir: Optional[Path] = None,
) -> Path:
    target_dir = Path(base_dir) if base_dir else _REPORTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = target_dir / f"scorer_{today}.md"
    out.write_text(format_markdown_report(result), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# State-branch propagation (env-var-gated; mirrors topic.py / harvest.py)
# ---------------------------------------------------------------------------

def _maybe_push_state_branch(result: ScorerResult) -> None:
    """Trigger condition: any actionable proposal OR any skip lineage row
    was written this cycle → invoke ``scripts/push_state.sh`` to
    propagate proposals dir + DB to the state branch.

    Skipped when:
      * dry_run.
      * No fire_ids were produced (every platform errored at insert).
      * PUSH_STATE env var unset (default — local dev never pushes).
    """
    import os
    import subprocess

    if result.dry_run:
        return
    fired = sum(1 for v in result.verdicts if v.fire_id)
    if fired == 0:
        return
    if os.getenv("PUSH_STATE", "0") not in {"1", "true", "yes"}:
        return

    script = _PROJECT_ROOT / "scripts" / "push_state.sh"
    if not script.exists():
        print(
            f"[reflector.scorer] push_state.sh not found at {script}; "
            "skipping state-branch propagation",
            file=sys.stderr,
        )
        return

    try:
        proc = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode == 0:
            print("[reflector.scorer] state-branch propagation OK")
        else:
            print(
                f"[reflector.scorer] push_state.sh exited {proc.returncode}; "
                f"stderr tail:\n{proc.stderr[-1000:]}",
                file=sys.stderr,
            )
    except Exception as exc:  # pragma: no cover
        print(
            f"[reflector.scorer] push_state.sh invocation failed: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't write proposals or report; just print summary."
    )
    parser.add_argument(
        "--no-report-file", action="store_true",
        help="Don't write reports/scorer_<DATE>.md (still prints stdout)."
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Emit DEBUG-level log lines."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s · %(message)s",
    )

    from src import db as dbmod
    dbmod.init_db()
    conn = dbmod.get_conn()
    try:
        result = run_scorer(
            conn,
            dry_run=args.dry_run,
            write_report=not args.no_report_file,
        )
    finally:
        conn.close()

    md = format_markdown_report(result)
    print(md)

    _maybe_push_state_branch(result)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
