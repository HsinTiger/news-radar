"""News Radar · Phase 9 Item 4 · Harvest analyzer.

Daily cron entry-point. Reads the per-feed substrate view
``v_feed_yield_7d`` (Phase 9 Item 1) and emits per-feed proposals to
``proposals.jsonl`` via :func:`src.reflector.proposals.write_proposal`.

Two proposal lanes:

  * **sunset_feed** — `engagement_yield_ratio < SUNSET_YIELD_THRESHOLD`
    AND feed older than its cadence-aware grace period (4 weeks for
    standard cadence, 8 weeks for low-cadence official sources) AND
    `publish_count_7d >= MIN_SAMPLES_THRESHOLD` AND not boss-pinned.
  * **sunset_feed (investigation flavor)** — `publish_count_7d == 0`
    AND the feed has historical published rows (i.e. `news_items` has at
    least one `status='published'` row for this `feed_name` outside the
    7-day window). Distinguished in the proposal `evidence.metrics`
    via the ``signal: "zero_publish_with_history"`` marker.

Both lanes write `boss_attention_required=True` — sunset is a
destructive operation and gets explicit Hsin sign-off, never
auto-deployed (cf. Item 3 which has both auto-deploy + proposal-only
lanes; Item 4 has proposal-only only).

NULL ``avg_score_7d`` handling (Cowork ruling 2026-04-27, spec
§3 Item 4 line 201):
  Pre-Phase-8.20 rows lack `weighted_score`, which propagates as NULL
  through `AVG(weighted_score)` in `v_feed_yield_7d`. We treat that as
  "insufficient data, no proposal" — do NOT coerce to 0, do NOT propose
  sunset on this basis. Emit a debug log line indicating the skip.

Cadence-aware grace period (per phase_9_unified_reflector.md §8.3):
  Official feeds (Fed press, ECB, BoJ, etc.) publish < 4 items/week.
  Computing a 7-day yield ratio against them in the first month would
  be a coin-flip. We bump the minimum-age-before-sunset gate to 8 weeks
  for any feed whose config has ``source_tier: official`` (or whose
  cadence is otherwise < 4/week). Cadence is sourced from the
  ``config/config.yaml`` ``feeds:`` list; without a usable cadence
  signal we default to "low cadence, 8-week threshold" (conservative).

Boss-pinned check:
  Currently a forward-compat stub (see ``_is_feed_boss_pinned``). Item 8
  is responsible for making boss-pin a real signal. Until then this
  helper returns False uniformly, and the boss-pinned-skip code path is
  exercised only via test mocks. Same pattern as `topic.py::_is_boss_pinned`.

Per-run reports/ markdown (Task C absorption, 2026-04-26 audit):
  After each non-dry-run cycle the analyzer writes a human-readable
  summary at ``reports/harvest_<YYYY-MM-DD>.md`` listing every feed in
  ``v_feed_yield_7d`` with verdict + gate trace. This subsumes the
  parked task tracked under ``tools/dryrun_official_feeds.py``'s
  reports-not-written gap — that tool's role is satisfied by this
  analyzer's natural output.

Spec  : PM_Radar/specs/phase_9_implementation_plan.md §3 Item 4
Canon : PM_Radar/roadmap/phase_9_unified_reflector.md §8.3
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants — Hsin-pinned thresholds (spec §3 Item 4)
# ---------------------------------------------------------------------------
SUNSET_YIELD_THRESHOLD = 0.05         # engagement_yield_ratio < this → candidate
GRACE_PERIOD_DAYS = 28                # standard 4-week grace
LOW_CADENCE_GRACE_DAYS = 56           # 8-week grace for low-cadence feeds
LOW_CADENCE_PUBLISHES_PER_WEEK = 4    # < 4/week → low-cadence
MIN_SAMPLES_THRESHOLD = 3             # publish_count_7d ≥ this for sunset

# Confidence cutoff: HIGH if sample count comfortably exceeds the
# cadence-adjusted threshold (≥ 2× minimum), MED otherwise.
HIGH_CONFIDENCE_SAMPLE_FACTOR = 2

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.yaml"
_REPORTS_DIR = _PROJECT_ROOT / "reports"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeedYieldRow:
    """One row from ``v_feed_yield_7d`` (or test fixture)."""
    feed_name: str
    publish_count_7d: int
    fetch_count_7d: int
    avg_score_7d: Optional[float]
    engagement_yield_ratio: Optional[float]


@dataclass
class FeedConfig:
    """Subset of feed config relevant to the harvest analyzer.

    `feed_added_at` is ISO-8601 (or None for pre-Phase-8.24 feeds).
    `expected_cadence_per_week` is derived from `source_tier`/`source_class`
    when the config lacks an explicit cadence column (which it does today).
    """
    feed_name: str
    feed_added_at: Optional[datetime]
    source_tier: Optional[str] = None      # primary / secondary / official
    source_class: Optional[str] = None     # official / ...
    expected_cadence_per_week: Optional[float] = None


@dataclass
class FeedVerdict:
    """One feed's evaluation + outcome, surfaces into the markdown report."""
    feed_name: str
    verdict: str              # "sunset" / "investigation" / "skip:..." / "ok"
    reason: str               # human-readable trace
    fire_id: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HarvestResult:
    ran_at: str
    dry_run: bool
    feeds_evaluated: int
    sunset_count: int
    investigation_count: int
    skipped_count: int
    verdicts: List[FeedVerdict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure helpers (no DB IO; unit-testable in isolation)
# ---------------------------------------------------------------------------

def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def derive_expected_cadence(feed_cfg: FeedConfig) -> Optional[float]:
    """Return expected publishes/week or None if unknown.

    Today ``config/config.yaml`` has no explicit cadence field. Heuristic:
      * ``source_tier == "official"`` (Fed press, ECB, BoJ, WHO, EU comm.)
        → ~1/week (0.5–2/week empirical band per
        `feeds_international_official_sources.md`).
      * Anything else: unknown → caller treats as "use standard 4-week
        grace, but if explicit cadence lookups are wired in later, this
        helper is the single point to update."

    Returns None for "no signal"; caller treats None as standard cadence.
    """
    if feed_cfg.expected_cadence_per_week is not None:
        return float(feed_cfg.expected_cadence_per_week)
    if (feed_cfg.source_tier or "").lower() == "official":
        return 1.0
    if (feed_cfg.source_class or "").lower() == "official":
        return 1.0
    return None


def grace_days_for(feed_cfg: FeedConfig) -> int:
    """Return the cadence-aware grace period in days for this feed.

    Rule (spec §3 Item 4 line 199, canonical §8.3):
      * Expected cadence ≥ 4/week → 28 days (standard).
      * Expected cadence < 4/week → 56 days (8 weeks).
      * Unknown cadence → 56 days (conservative default — if we don't
        know how often it should publish, we don't get to flag it
        early).
    """
    cadence = derive_expected_cadence(feed_cfg)
    if cadence is None:
        return LOW_CADENCE_GRACE_DAYS
    if cadence < LOW_CADENCE_PUBLISHES_PER_WEEK:
        return LOW_CADENCE_GRACE_DAYS
    return GRACE_PERIOD_DAYS


def feed_age_days(feed_cfg: FeedConfig, *, now: Optional[datetime] = None) -> Optional[int]:
    """Days since ``feed_added_at``. None if the field is absent (pre-Phase-8.24)."""
    if feed_cfg.feed_added_at is None:
        return None
    now_dt = now or datetime.now(timezone.utc)
    if feed_cfg.feed_added_at.tzinfo is None:
        added = feed_cfg.feed_added_at.replace(tzinfo=timezone.utc)
    else:
        added = feed_cfg.feed_added_at
    return int((now_dt - added).total_seconds() // 86400)


def confidence_for(samples: int, threshold: int) -> str:
    """HIGH if samples >= 2 × threshold, MED otherwise."""
    if samples >= threshold * HIGH_CONFIDENCE_SAMPLE_FACTOR:
        return "HIGH"
    return "MED"


# ---------------------------------------------------------------------------
# Config loader (feeds.yml-equivalent — currently embedded in config.yaml)
# ---------------------------------------------------------------------------

def load_feed_configs(
    config_path: Optional[Path] = None,
) -> Dict[str, FeedConfig]:
    """Parse ``config/config.yaml``'s ``feeds:`` block into a name → FeedConfig
    map. Best-effort: missing fields default to None.

    Note: spec mentions ``feeds.yml`` but production today carries the
    feed list inside ``config/config.yaml`` under the ``feeds:`` key.
    The proposal `target_config` still uses the spec's canonical name
    ``"feeds.yml"`` so the deployment surface is decoupled from the
    storage location.
    """
    path = Path(config_path) if config_path else _CONFIG_PATH
    if not path.exists():
        logger.warning("[harvest] feeds config not found at %s; "
                       "treating all feeds as unconfigured", path)
        return {}
    try:
        import yaml  # local import to keep module importable in tests
    except Exception:  # pragma: no cover — yaml is in requirements
        logger.warning("[harvest] PyYAML unavailable; cannot read feed config")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[harvest] failed to parse %s: %s", path, exc)
        return {}

    out: Dict[str, FeedConfig] = {}
    for entry in (data.get("feeds") or []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name:
            continue
        added_at_raw = entry.get("feed_added_at")
        added_at = _parse_iso(added_at_raw) if isinstance(added_at_raw, str) else None
        # YAML parser may have already coerced ISO strings to datetime.
        if isinstance(added_at_raw, datetime):
            added_at = added_at_raw
        cadence = entry.get("expected_cadence_per_week")
        out[name] = FeedConfig(
            feed_name=name,
            feed_added_at=added_at,
            source_tier=entry.get("source_tier"),
            source_class=entry.get("source_class"),
            expected_cadence_per_week=(
                float(cadence) if isinstance(cadence, (int, float)) else None
            ),
        )
    return out


# ---------------------------------------------------------------------------
# DB IO
# ---------------------------------------------------------------------------

def _fetch_feed_yield_rows(conn: sqlite3.Connection) -> List[FeedYieldRow]:
    """Read every row of ``v_feed_yield_7d``.

    Returns an empty list if the view is missing (older DBs that haven't
    sourced ``views.sql`` yet — defensive only, production runs init_db
    before invoking the analyzer).
    """
    try:
        cur = conn.execute(
            """
            SELECT feed_name,
                   publish_count_7d,
                   fetch_count_7d,
                   avg_score_7d,
                   engagement_yield_ratio
              FROM v_feed_yield_7d
            """
        )
    except sqlite3.OperationalError as exc:
        logger.warning("[harvest] v_feed_yield_7d unreadable: %s", exc)
        return []

    out: List[FeedYieldRow] = []
    for r in cur.fetchall():
        def _g(key: str, idx: int):
            return r[key] if hasattr(r, "keys") else r[idx]
        out.append(FeedYieldRow(
            feed_name=_g("feed_name", 0),
            publish_count_7d=int(_g("publish_count_7d", 1) or 0),
            fetch_count_7d=int(_g("fetch_count_7d", 2) or 0),
            avg_score_7d=(
                float(_g("avg_score_7d", 3))
                if _g("avg_score_7d", 3) is not None else None
            ),
            engagement_yield_ratio=(
                float(_g("engagement_yield_ratio", 4))
                if _g("engagement_yield_ratio", 4) is not None else None
            ),
        ))
    return out


def _has_historical_publish(
    conn: sqlite3.Connection,
    feed_name: str,
) -> bool:
    """Return True if `news_items` has any `status='published'` row for this
    feed older than the 7-day window. Used to disambiguate

      "feed is silent because Hsin just added it" (NO investigation)
      "feed is silent right now but used to publish" (investigation)
    """
    try:
        row = conn.execute(
            """
            SELECT 1
              FROM news_items
             WHERE feed_name = ?
               AND status = 'published'
               AND published_at < datetime('now', '-7 days')
             LIMIT 1
            """,
            (feed_name,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _is_feed_boss_pinned(
    conn: sqlite3.Connection,
    feed_name: str,
    feed_cfg: Optional[FeedConfig],
) -> bool:
    """Forward-compat boss-pin gate.

    TODO(phase-9-item-8): once Item 8 introduces a feed-level boss-pin
    signal — most likely either:
      (a) ``boss_pinned: true`` field on individual feeds in
          ``config/config.yaml``, OR
      (b) a ``feeds_boss_pinned`` table mirroring topic_weights' pattern
    this helper picks it up. Until then, no feeds are pinned and the
    sunset path runs against everyone (still gated by grace + sample
    + yield).

    Test-only: monkeypatching this helper exercises the eventual
    pinned-skip behavior; see test_reflector_harvest.py.
    """
    # Path (a) — config-driven flag, defensive read so unit tests can
    # opt in by passing a feed_cfg with a ``boss_pinned`` attribute
    # (currently unused; FeedConfig has no such field).
    if feed_cfg is not None and getattr(feed_cfg, "boss_pinned", False):
        return True

    # Path (b) — DB-driven flag, mirroring _is_boss_pinned in topic.py.
    # Defensive PRAGMA check: the day Item 8 adds either a column on a
    # `feeds` table or a separate `feeds_boss_pinned` table, this gate
    # picks it up without code change.
    try:
        cols = {
            row[1] if not hasattr(row, "keys") else row["name"]
            for row in conn.execute("PRAGMA table_info(feeds)")
        }
    except sqlite3.OperationalError:
        return False
    if "boss_pinned" not in cols:
        return False
    try:
        row = conn.execute(
            "SELECT boss_pinned FROM feeds WHERE feed_name = ? OR name = ?",
            (feed_name, feed_name),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    if row is None:
        return False
    val = row[0] if not hasattr(row, "keys") else row["boss_pinned"]
    return bool(val)


# ---------------------------------------------------------------------------
# Proposal payload builder
# ---------------------------------------------------------------------------

def _build_sunset_payload(
    yield_row: FeedYieldRow,
    feed_cfg: FeedConfig,
    age_days: Optional[int],
    grace_days: int,
    cadence_per_week: Optional[float],
) -> dict:
    """Construct the proposal dict for a sunset_feed proposal.

    Confidence: HIGH if `publish_count_7d >= 2 × MIN_SAMPLES_THRESHOLD`;
    MED otherwise. The proposal evidence carries every metric that drove
    the decision so the boss can audit retroactively.
    """
    metrics = {
        "engagement_yield_ratio": (
            round(yield_row.engagement_yield_ratio, 6)
            if yield_row.engagement_yield_ratio is not None else None
        ),
        "publish_count_7d":         yield_row.publish_count_7d,
        "fetch_count_7d":           yield_row.fetch_count_7d,
        "avg_score_7d": (
            round(yield_row.avg_score_7d, 6)
            if yield_row.avg_score_7d is not None else None
        ),
        "feed_age_days":            age_days,
        "grace_days_applied":       grace_days,
        "expected_cadence_per_week": cadence_per_week,
        "signal":                   "low_yield_sunset",
        "feed_added_at":            (
            feed_cfg.feed_added_at.isoformat() if feed_cfg.feed_added_at else None
        ),
        "source_tier":              feed_cfg.source_tier,
    }
    return {
        "analyzer":      "harvest",
        "platform":      "all",
        "proposal_type": "sunset_feed",
        "evidence": {
            "sample_ids": [],  # feed-level proposal, no draft IDs
            "metrics":    metrics,
            "confidence": confidence_for(
                yield_row.publish_count_7d, MIN_SAMPLES_THRESHOLD
            ),
        },
        "action": {
            "target_config":  "feeds.yml",
            "field":          yield_row.feed_name,
            "current_value":  "active",
            "proposed_value": "sunset",
        },
        "boss_attention_required": True,  # sunset always needs Hsin sign-off
    }


def _build_investigation_payload(
    yield_row: FeedYieldRow,
    feed_cfg: FeedConfig,
    age_days: Optional[int],
    cadence_per_week: Optional[float],
) -> dict:
    """Investigation lane: 0 publishes in 7d but feed has historical
    publishes — surface for Hsin to look at (could be platform breakage,
    could be source going dark, could be benign quiet period).

    Modeled as a `sunset_feed` proposal_type with a distinguishing
    ``signal: "zero_publish_with_history"`` metric. Item 2's
    VALID_PROPOSAL_TYPES set doesn't include "investigation" yet; if Item
    8/9 adds it, switch this lane to that type. Until then this is the
    least-surprise mapping and keeps the proposal-substrate validator
    happy.
    """
    metrics = {
        "engagement_yield_ratio": (
            round(yield_row.engagement_yield_ratio, 6)
            if yield_row.engagement_yield_ratio is not None else None
        ),
        "publish_count_7d":         yield_row.publish_count_7d,
        "fetch_count_7d":           yield_row.fetch_count_7d,
        "avg_score_7d": (
            round(yield_row.avg_score_7d, 6)
            if yield_row.avg_score_7d is not None else None
        ),
        "feed_age_days":            age_days,
        "expected_cadence_per_week": cadence_per_week,
        "signal":                   "zero_publish_with_history",
        "feed_added_at":            (
            feed_cfg.feed_added_at.isoformat() if feed_cfg.feed_added_at else None
        ),
        "source_tier":              feed_cfg.source_tier,
    }
    return {
        "analyzer":      "harvest",
        "platform":      "all",
        "proposal_type": "sunset_feed",
        "evidence": {
            "sample_ids": [],
            "metrics":    metrics,
            "confidence": "MED",  # zero-publish signal is intrinsically uncertain
        },
        "action": {
            "target_config":  "feeds.yml",
            "field":          yield_row.feed_name,
            "current_value":  "active",
            "proposed_value": "investigate",  # NOT sunset — boss decides
        },
        "boss_attention_required": True,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def evaluate_feed(
    yield_row: FeedYieldRow,
    feed_cfg: Optional[FeedConfig],
    *,
    has_history: bool,
    is_pinned: bool,
    now: Optional[datetime] = None,
) -> FeedVerdict:
    """Pure decision function. Maps (yield_row, feed_cfg, has_history,
    is_pinned) → FeedVerdict (verdict + reason + metrics).

    Does NOT write proposals. The orchestrator (`run_harvest`) consumes
    the verdict and dispatches to write_proposal if the verdict warrants.

    Verdict values:
      * "sunset"        — propose sunset_feed (low_yield_sunset)
      * "investigation" — propose sunset_feed (zero_publish_with_history)
      * "skip:null_score" — NULL avg_score_7d, no proposal
      * "skip:grace"      — within cadence-aware grace period
      * "skip:samples"    — publish_count_7d < MIN_SAMPLES_THRESHOLD
      * "skip:pinned"     — boss-pinned, no sunset proposal
      * "skip:unconfigured" — feed in view but not in config (no age info)
      * "skip:ok"         — yield is healthy, no action needed
      * "skip:zero_no_history" — zero publish but no historical record
                                 (probably a brand-new feed, leave alone)
    """
    cfg = feed_cfg or FeedConfig(
        feed_name=yield_row.feed_name, feed_added_at=None
    )
    cadence = derive_expected_cadence(cfg)
    age_days = feed_age_days(cfg, now=now)
    grace_days = grace_days_for(cfg)

    # Path A: zero publish + historical publish → investigation
    if yield_row.publish_count_7d == 0:
        if has_history:
            metrics = {
                "engagement_yield_ratio": yield_row.engagement_yield_ratio,
                "publish_count_7d": 0,
                "fetch_count_7d": yield_row.fetch_count_7d,
                "feed_age_days": age_days,
                "expected_cadence_per_week": cadence,
            }
            return FeedVerdict(
                feed_name=yield_row.feed_name,
                verdict="investigation",
                reason="zero publish in 7d but feed has historical published rows",
                metrics=metrics,
            )
        return FeedVerdict(
            feed_name=yield_row.feed_name,
            verdict="skip:zero_no_history",
            reason="zero publish in 7d and no historical record (likely new feed)",
            metrics={"feed_age_days": age_days},
        )

    # Path B: sunset evaluation. Order of gates matters for clarity in
    # the report — NULL check first (data drift), then samples, then
    # grace, then pinned, then yield.

    # B1. NULL avg_score_7d → insufficient data (Cowork ruling).
    if yield_row.avg_score_7d is None:
        logger.debug(
            "[harvest] feed=%r skip:null_score "
            "(avg_score_7d is NULL — pre-Phase-8.20 row drift, NOT a view bug)",
            yield_row.feed_name,
        )
        return FeedVerdict(
            feed_name=yield_row.feed_name,
            verdict="skip:null_score",
            reason="avg_score_7d is NULL (insufficient data per Cowork 2026-04-27)",
            metrics={"publish_count_7d": yield_row.publish_count_7d},
        )

    # B2. Sample threshold.
    if yield_row.publish_count_7d < MIN_SAMPLES_THRESHOLD:
        return FeedVerdict(
            feed_name=yield_row.feed_name,
            verdict="skip:samples",
            reason=(
                f"publish_count_7d={yield_row.publish_count_7d} "
                f"< MIN_SAMPLES_THRESHOLD={MIN_SAMPLES_THRESHOLD}"
            ),
            metrics={"publish_count_7d": yield_row.publish_count_7d},
        )

    # B3. Yield must actually be low to even consider sunset.
    if (yield_row.engagement_yield_ratio is None
            or yield_row.engagement_yield_ratio >= SUNSET_YIELD_THRESHOLD):
        return FeedVerdict(
            feed_name=yield_row.feed_name,
            verdict="skip:ok",
            reason=(
                f"engagement_yield_ratio="
                f"{yield_row.engagement_yield_ratio} >= {SUNSET_YIELD_THRESHOLD} "
                "(healthy)"
            ),
            metrics={
                "engagement_yield_ratio": yield_row.engagement_yield_ratio,
                "publish_count_7d": yield_row.publish_count_7d,
            },
        )

    # B4. Grace period (cadence-aware).
    if age_days is None:
        # Feed in view but not configured — we can't tell its age, so we
        # don't get to sunset it. Surface in the report as unconfigured.
        return FeedVerdict(
            feed_name=yield_row.feed_name,
            verdict="skip:unconfigured",
            reason="feed has no feed_added_at in config; cannot evaluate grace period",
            metrics={"publish_count_7d": yield_row.publish_count_7d},
        )
    if age_days < grace_days:
        return FeedVerdict(
            feed_name=yield_row.feed_name,
            verdict="skip:grace",
            reason=(
                f"feed_age_days={age_days} < grace_days={grace_days} "
                f"(cadence={cadence})"
            ),
            metrics={
                "feed_age_days": age_days,
                "grace_days": grace_days,
                "expected_cadence_per_week": cadence,
            },
        )

    # B5. Boss-pinned skip.
    if is_pinned:
        return FeedVerdict(
            feed_name=yield_row.feed_name,
            verdict="skip:pinned",
            reason="feed is boss-pinned; sunset proposals require explicit unpin",
            metrics={"publish_count_7d": yield_row.publish_count_7d},
        )

    # B6. All gates passed → sunset.
    return FeedVerdict(
        feed_name=yield_row.feed_name,
        verdict="sunset",
        reason=(
            f"yield={yield_row.engagement_yield_ratio:.4f} < "
            f"{SUNSET_YIELD_THRESHOLD}, age={age_days}d ≥ {grace_days}d, "
            f"samples={yield_row.publish_count_7d} ≥ {MIN_SAMPLES_THRESHOLD}"
        ),
        metrics={
            "engagement_yield_ratio": yield_row.engagement_yield_ratio,
            "publish_count_7d": yield_row.publish_count_7d,
            "fetch_count_7d": yield_row.fetch_count_7d,
            "feed_age_days": age_days,
            "grace_days_applied": grace_days,
            "expected_cadence_per_week": cadence,
        },
    )


def run_harvest(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    write_proposals: bool = True,
    proposals_db_path: Optional[Path] = None,
    proposals_base_dir: Optional[Path] = None,
    config_path: Optional[Path] = None,
    reports_dir: Optional[Path] = None,
    write_report: bool = True,
    now: Optional[datetime] = None,
) -> HarvestResult:
    """One full harvest-analyzer cycle.

    Order of operations:
      1. Read ``v_feed_yield_7d`` rows.
      2. Load ``config/config.yaml`` feed configs (best-effort).
      3. For each yield row:
         a. Look up feed config (None if absent).
         b. Check historical-publish presence (only if publish_count_7d == 0).
         c. Evaluate via `evaluate_feed`.
         d. If verdict is "sunset" or "investigation" AND not dry-run AND
            write_proposals, call `write_proposal`. Capture fire_id.
      4. Build markdown report (Task C absorption) and write to
         `reports/harvest_<YYYY-MM-DD>.md` unless suppressed.

    Returns HarvestResult with per-feed verdicts.

    Test-only kwargs (`proposals_db_path`, `proposals_base_dir`,
    `config_path`, `reports_dir`, `write_report`, `now`) let tests redirect
    side effects to a tmp_path. Production callers omit them.
    """
    rows = _fetch_feed_yield_rows(conn)
    feed_configs = load_feed_configs(config_path)
    now_dt = now or datetime.now(timezone.utc)

    verdicts: List[FeedVerdict] = []
    sunset_count = 0
    investigation_count = 0
    skipped_count = 0

    for yield_row in rows:
        cfg = feed_configs.get(yield_row.feed_name)
        # Cheap optimization — only hit the historical-publish query when
        # the zero-publish lane could actually fire.
        has_history = (
            _has_historical_publish(conn, yield_row.feed_name)
            if yield_row.publish_count_7d == 0 else False
        )
        is_pinned = _is_feed_boss_pinned(conn, yield_row.feed_name, cfg)

        verdict = evaluate_feed(
            yield_row, cfg,
            has_history=has_history,
            is_pinned=is_pinned,
            now=now_dt,
        )

        # Side-effect dispatch.
        if verdict.verdict == "sunset":
            sunset_count += 1
            if (not dry_run) and write_proposals:
                cadence = derive_expected_cadence(cfg or FeedConfig(
                    feed_name=yield_row.feed_name, feed_added_at=None
                ))
                age = feed_age_days(cfg or FeedConfig(
                    feed_name=yield_row.feed_name, feed_added_at=None
                ), now=now_dt)
                grace = grace_days_for(cfg or FeedConfig(
                    feed_name=yield_row.feed_name, feed_added_at=None
                ))
                payload = _build_sunset_payload(yield_row, cfg or FeedConfig(
                    feed_name=yield_row.feed_name, feed_added_at=None
                ), age, grace, cadence)
                fire_id = _safe_write_proposal(
                    payload, proposals_db_path, proposals_base_dir,
                    feed_name=yield_row.feed_name,
                )
                verdict.fire_id = fire_id
        elif verdict.verdict == "investigation":
            investigation_count += 1
            if (not dry_run) and write_proposals:
                cadence = derive_expected_cadence(cfg or FeedConfig(
                    feed_name=yield_row.feed_name, feed_added_at=None
                ))
                age = feed_age_days(cfg or FeedConfig(
                    feed_name=yield_row.feed_name, feed_added_at=None
                ), now=now_dt)
                payload = _build_investigation_payload(
                    yield_row, cfg or FeedConfig(
                        feed_name=yield_row.feed_name, feed_added_at=None
                    ),
                    age, cadence,
                )
                fire_id = _safe_write_proposal(
                    payload, proposals_db_path, proposals_base_dir,
                    feed_name=yield_row.feed_name,
                )
                verdict.fire_id = fire_id
        else:
            skipped_count += 1

        verdicts.append(verdict)

    result = HarvestResult(
        ran_at=now_dt.isoformat(timespec="seconds"),
        dry_run=dry_run,
        feeds_evaluated=len(rows),
        sunset_count=sunset_count,
        investigation_count=investigation_count,
        skipped_count=skipped_count,
        verdicts=verdicts,
    )

    # Report writing — Task C absorption.
    if write_report and not dry_run:
        try:
            report_path = write_markdown_report(result, base_dir=reports_dir)
            logger.info("[harvest] report written: %s", report_path)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("[harvest] failed to write report: %s", exc)

    return result


def _safe_write_proposal(
    payload: dict,
    db_path: Optional[Path],
    base_dir: Optional[Path],
    *,
    feed_name: str,
) -> Optional[str]:
    """Wrap write_proposal so a single bad feed doesn't kill the whole run.

    write_proposal is itself atomic (rollback on lineage failure). This
    wrapper just catches per-feed exceptions, logs, and returns None so
    the orchestrator can keep going.
    """
    try:
        from src.reflector.proposals import write_proposal
    except Exception as exc:  # pragma: no cover — defensive
        logger.error("[harvest] cannot import write_proposal: %s", exc)
        return None
    try:
        fire_id = write_proposal(
            payload,
            db_path=db_path,
            base_dir=base_dir,
        )
    except Exception as exc:
        logger.error(
            "[harvest] write_proposal failed for feed=%r: %s",
            feed_name, exc,
        )
        return None
    return fire_id


# ---------------------------------------------------------------------------
# Markdown report (Task C absorption)
# ---------------------------------------------------------------------------

def format_markdown_report(result: HarvestResult) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines: List[str] = []
    lines.append(f"# Harvest Analyzer · {today}")
    lines.append("")
    lines.append(f"- ran_at: `{result.ran_at}`")
    lines.append(f"- feeds_evaluated: **{result.feeds_evaluated}**")
    lines.append(f"- sunset proposals: **{result.sunset_count}**")
    lines.append(f"- investigation proposals: **{result.investigation_count}**")
    lines.append(f"- skipped: **{result.skipped_count}**")
    if result.dry_run:
        lines.append("- mode: **dry-run** (no proposals written)")
    lines.append("")

    if result.verdicts:
        lines.append("## Per-feed verdicts")
        lines.append("")
        lines.append("| feed | verdict | reason | fire_id |")
        lines.append("|---|---|---|---|")
        for v in sorted(result.verdicts,
                        key=lambda x: (x.verdict, x.feed_name)):
            fire_short = (v.fire_id[:8] + "…") if v.fire_id else "—"
            lines.append(
                f"| `{v.feed_name}` | {v.verdict} | {v.reason} | `{fire_short}` |"
            )
        lines.append("")

    sunsets = [v for v in result.verdicts if v.verdict == "sunset"]
    invests = [v for v in result.verdicts if v.verdict == "investigation"]
    if sunsets:
        lines.append("## Sunset candidates (require Hsin sign-off)")
        lines.append("")
        for v in sunsets:
            lines.append(f"- **`{v.feed_name}`** — {v.reason}")
        lines.append("")
    if invests:
        lines.append("## Investigation queue (zero publish, has history)")
        lines.append("")
        for v in invests:
            lines.append(f"- **`{v.feed_name}`** — {v.reason}")
        lines.append("")

    lines.append("---")
    lines.append("_Auto-generated by `src/reflector/harvest.py` "
                 "(Phase 9 Item 4)._")
    return "\n".join(lines)


def write_markdown_report(
    result: HarvestResult,
    base_dir: Optional[Path] = None,
) -> Path:
    """Write the markdown report to ``reports/harvest_<YYYY-MM-DD>.md``."""
    target_dir = Path(base_dir) if base_dir else _REPORTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = target_dir / f"harvest_{today}.md"
    out.write_text(format_markdown_report(result), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# State-branch propagation (Phase 9 Item 4 sub-task; mirrors topic.py)
# ---------------------------------------------------------------------------

def _maybe_push_state_branch(result: HarvestResult) -> None:
    """Trigger condition: if any proposal was written this run, invoke
    ``scripts/push_state.sh`` (Amendment B `708ed93`) to propagate the
    proposals dir + DB to the state branch.

    Skipped when:
      * dry_run (no side effects).
      * No proposals were written (sunset_count + investigation_count == 0).
      * PUSH_STATE env var is unset.

    Same env-var-gated pattern as `topic.py::_maybe_push_state_branch`.
    """
    import os
    import subprocess

    if result.dry_run:
        return
    proposed = result.sunset_count + result.investigation_count
    if proposed == 0:
        return
    if os.getenv("PUSH_STATE", "0") not in {"1", "true", "yes"}:
        return

    script = _PROJECT_ROOT / "scripts" / "push_state.sh"
    if not script.exists():
        print(
            f"[reflector.harvest] push_state.sh not found at {script}; "
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
            print("[reflector.harvest] state-branch propagation OK")
        else:
            print(
                f"[reflector.harvest] push_state.sh exited "
                f"{proc.returncode}; stderr tail:\n"
                f"{proc.stderr[-1000:]}",
                file=sys.stderr,
            )
    except Exception as exc:  # pragma: no cover — defensive
        print(
            f"[reflector.harvest] push_state.sh invocation failed: {exc}",
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
        help="Don't write reports/harvest_<DATE>.md (still prints stdout)."
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Emit DEBUG-level log lines (incl. NULL-score skip details)."
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
        result = run_harvest(
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
