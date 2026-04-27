"""
News Radar · Composer Rule Analyzer (Phase 9 Item 5)
=====================================================
Weekly LLM augmentor extracting body rules (top-Q vs bot-Q diffs) and hook rules
(first-N-chars patterns) per platform. Outputs to per-platform rules files in
`config/platforms/{fb,ig,threads}_v2.md`.

**Framing A calibration**: Proposal-only path (no auto-deploy). Every proposal
requires Hsin approval via Settings UI.

**LLM augmentor budget** (Hsin 2026-04-26):
  - Soft cap: $0.50/week
  - Hard cap: 50,000 input tokens/week
  - Alert at 80%
  - Truncated output if exceeding hard cap (truncated: true flag)

**Hook layer** (per spec §8.3 row 5):
  - FB: first 100 chars (substr limit per Meta)
  - IG: first line (title-like content)
  - Threads: first 30 chars

**Body + hook dimensions**:
  - Body rules: language patterns, specificity, tone detected from content
  - Hook rules: opening structures, punctuation, question patterns that correlate
    with engagement

Spec: PM_Radar/roadmap/phase_9_unified_reflector.md §3 Item 5 + §8.3 + Q-A4
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import random

# Lazy imports (pydantic, llm_brain pulled here, not at module top)


# ---------- Constants ----------

PLATFORMS = ("facebook", "instagram", "threads")

# Per-platform hook length (first-N-chars pattern)
HOOK_LENGTHS = {
    "facebook":  100,
    "instagram": None,  # first line (newline-delimited)
    "threads":   30,
}

# Engagement quartile thresholds
TOP_QUARTILE = 4
BOT_QUARTILE = 1

# Sample size requirements (Phase 9 Item 6 interpretation)
MIN_SAMPLES_PER_QUARTILE_PER_PLATFORM = 2  # at least 2 top-Q and 2 bot-Q per platform

# Token budget (Hsin 2026-04-26 Q-A4)
LLM_SOFT_CAP_USD = 0.50
LLM_HARD_CAP_INPUT_TOKENS = 50_000
LLM_ALERT_THRESHOLD = 0.80


# ---------- Data structures ----------

@dataclass(frozen=True)
class DraftSample:
    """Single draft from v_drafts_with_outcome."""
    draft_id: str
    news_id: str
    news_title: str
    news_body: str
    topic_category: str
    published_at: str
    engagement_quartile: int
    fb_likes: Optional[int] = None
    ig_likes: Optional[int] = None
    th_likes: Optional[int] = None


@dataclass
class PlatformHooks:
    """Hook (first-N-chars) pairs for a draft × platform."""
    platform: str
    draft_id: str
    hook_text: str  # first N chars or first line
    engagement_quartile: int


@dataclass
class AnalyzerResult:
    """Result of one composer analyzer run."""
    ran_at: str
    lookback_days: int
    dry_run: bool
    samples_scanned: int
    proposals_written: int
    token_usage: Optional[Dict[str, int]] = field(default_factory=dict)
    alerts: List[str] = field(default_factory=list)


# ---------- Pure fetch helpers ----------

def _fetch_drafts_with_outcome(
    conn: sqlite3.Connection,
    lookback_days: int = 14,
) -> List[DraftSample]:
    """Fetch v_drafts_with_outcome: all drafts from past N days with engagement
    & quartile computed per topic_category."""
    sql = """
        SELECT draft_id, news_id, title AS news_title, body AS news_body,
               topic_category, published_at, engagement_quartile,
               fb_likes, ig_likes, th_likes
          FROM v_drafts_with_outcome
         WHERE published_at >= datetime('now', ?)
    """
    window = f"-{int(lookback_days)} days"
    rows: List[DraftSample] = []
    try:
        for r in conn.execute(sql, (window,)).fetchall():
            rows.append(DraftSample(
                draft_id=r["draft_id"] if hasattr(r, "keys") else r[0],
                news_id=r["news_id"] if hasattr(r, "keys") else r[1],
                news_title=r["news_title"] if hasattr(r, "keys") else r[2],
                news_body=r["news_body"] if hasattr(r, "keys") else r[3],
                topic_category=r["topic_category"] if hasattr(r, "keys") else r[4],
                published_at=r["published_at"] if hasattr(r, "keys") else r[5],
                engagement_quartile=r["engagement_quartile"] if hasattr(r, "keys") else r[6],
                fb_likes=r["fb_likes"] if hasattr(r, "keys") else r[7],
                ig_likes=r["ig_likes"] if hasattr(r, "keys") else r[8],
                th_likes=r["th_likes"] if hasattr(r, "keys") else r[9],
            ))
    except sqlite3.OperationalError:
        # View may not exist on older DBs
        pass
    return rows


# ---------- Sampler logic ----------

def _get_hook_for_platform(draft: DraftSample, platform: str) -> str:
    """Extract hook text (first N chars or first line) per platform."""
    text = draft.news_title or draft.news_body or ""

    if platform == "facebook":
        # First 100 chars
        return text[:100]
    elif platform == "instagram":
        # First line (up to newline or 100 chars)
        lines = text.split("\n", 1)
        return lines[0][:100]
    elif platform == "threads":
        # First 30 chars
        return text[:30]
    return ""


def sample_top_bot_quartiles_per_platform(
    drafts: List[DraftSample],
) -> Dict[Tuple[str, str], Tuple[List[DraftSample], List[DraftSample]]]:
    """
    Sample top-Q (quartile=4) and bot-Q (quartile=1) drafts per
    (topic_category, platform) pair.

    Returns: {(topic_category, platform): (top_q_samples, bot_q_samples)}

    Empty lists for pairs with insufficient samples. Caller filters.
    """
    # Group by topic_category to understand quartile distribution
    by_topic = {}
    for d in drafts:
        if d.topic_category not in by_topic:
            by_topic[d.topic_category] = []
        by_topic[d.topic_category].append(d)

    # Per topic × platform, filter by quartile
    result: Dict[Tuple[str, str], Tuple[List[DraftSample], List[DraftSample]]] = {}
    for topic_cat, topic_drafts in by_topic.items():
        for platform in PLATFORMS:
            # Only include drafts that have engagement on this platform
            # (heuristic: if the platform_likes column is not null)
            platform_col = {"facebook": "fb_likes", "instagram": "ig_likes", "threads": "th_likes"}[platform]

            # Filter to drafts with engagement data on this platform
            platform_drafts = [
                d for d in topic_drafts
                if getattr(d, platform_col, None) is not None
            ]

            top_q = [d for d in platform_drafts if d.engagement_quartile == TOP_QUARTILE]
            bot_q = [d for d in platform_drafts if d.engagement_quartile == BOT_QUARTILE]

            result[(topic_cat, platform)] = (top_q, bot_q)

    return result


# ---------- LLM augmentor placeholder ----------

def analyze_with_llm(
    top_q_samples: List[DraftSample],
    bot_q_samples: List[DraftSample],
    platform: str,
    topic_category: str,
) -> Optional[Dict]:
    """
    Call LLM to extract body rules + hook rules from top vs bot quartile samples.

    TODO: This is a PLACEHOLDER. Hsin reviews/edits the prompt template before
    first cron fire (Mon 06:00 TW).

    Returns dict with:
      - body_rules: List[str]  (language pattern observations)
      - hook_rules: List[str]  (opening structure patterns)
      - rationale: str
      - token_usage: Dict[str, int]  {input, output}

    Or None if insufficient samples / token budget exceeded.
    """
    # Mock implementation for tests (no real API calls)
    # Production: would call src.llm_brain or similar
    return None


# ---------- Proposal writer ----------

def _build_proposal_payload(
    body_rules: List[str],
    hook_rules: List[str],
    platform: str,
    topic_category: str,
    evidence_draft_ids: Dict[str, List[str]],  # {quartile: [draft_ids]}
) -> dict:
    """Construct proposal dict for src.reflector.proposals.write_proposal."""
    return {
        "analyzer": "composer",
        "platform": platform,
        "proposal_type": "composer_rules",
        "evidence": {
            "sample_ids": evidence_draft_ids.get("top_q", []) + evidence_draft_ids.get("bot_q", []),
            "metrics": {
                "topic_category": topic_category,
                "top_q_count": len(evidence_draft_ids.get("top_q", [])),
                "bot_q_count": len(evidence_draft_ids.get("bot_q", [])),
            },
            "confidence": "MED",  # Preliminary; Hsin refines
        },
        "action": {
            "target_config": f"config/platforms/{platform}_v2.md",
            "field": f"{topic_category}_rules",
            "body_rules": body_rules,
            "hook_rules": hook_rules,
        },
        "boss_attention_required": True,  # Calibration phase: all proposals require approval
    }


# ---------- Main orchestrator ----------

def run_analyzer(
    conn: sqlite3.Connection,
    lookback_days: int = 14,
    dry_run: bool = False,
) -> AnalyzerResult:
    """
    Complete one composer analyzer cycle.

    1. Fetch v_drafts_with_outcome
    2. Sample top/bot-Q per platform per topic
    3. Call LLM for each (topic, platform) pair
    4. Write proposals (no auto-deploy)
    """
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Fetch data
    drafts = _fetch_drafts_with_outcome(conn, lookback_days)
    samples_count = len(drafts)

    if samples_count < 5:
        # Insufficient data for meaningful analysis
        return AnalyzerResult(
            ran_at=started_at,
            lookback_days=lookback_days,
            dry_run=True,  # Suppress all writes
            samples_scanned=samples_count,
            proposals_written=0,
            alerts=["Insufficient engagement data (<5 samples); skipping analyzer"],
        )

    # Sample per platform
    quartile_samples = sample_top_bot_quartiles_per_platform(drafts)

    proposals_written = 0

    for (topic_cat, platform), (top_q, bot_q) in quartile_samples.items():
        # Skip if insufficient samples
        if len(top_q) < MIN_SAMPLES_PER_QUARTILE_PER_PLATFORM or len(bot_q) < MIN_SAMPLES_PER_QUARTILE_PER_PLATFORM:
            continue

        # Call LLM (mock in tests, real in production)
        llm_result = analyze_with_llm(top_q, bot_q, platform, topic_cat)
        if llm_result is None:
            continue

        body_rules = llm_result.get("body_rules", [])
        hook_rules = llm_result.get("hook_rules", [])

        if not body_rules and not hook_rules:
            continue  # No actionable rules extracted

        # Build + write proposal
        if not dry_run:
            from src.reflector.proposals import write_proposal

            payload = _build_proposal_payload(
                body_rules,
                hook_rules,
                platform,
                topic_cat,
                {
                    "top_q": [d.draft_id for d in top_q],
                    "bot_q": [d.draft_id for d in bot_q],
                },
            )
            try:
                fire_id = write_proposal(payload)
                proposals_written += 1
            except Exception as e:
                print(f"[composer] proposal write failed: {e}", file=sys.stderr)

    return AnalyzerResult(
        ran_at=started_at,
        lookback_days=lookback_days,
        dry_run=dry_run,
        samples_scanned=samples_count,
        proposals_written=proposals_written,
        token_usage={},  # Populated by LLM calls if enabled
    )


# ---------- CLI ----------

def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-days", type=int, default=14,
                        help="Lookback window for v_drafts_with_outcome")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute but don't write proposals")
    args = parser.parse_args(argv)

    # Connect to DB
    from src import db as dbmod
    dbmod.init_db()
    conn = dbmod.get_conn()
    try:
        result = run_analyzer(
            conn,
            lookback_days=args.lookback_days,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    # Report
    print(f"[composer] Ran at {result.ran_at}")
    print(f"[composer] Samples scanned: {result.samples_scanned}")
    print(f"[composer] Proposals written: {result.proposals_written}")
    if result.alerts:
        for alert in result.alerts:
            print(f"[composer] ALERT: {alert}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(_main())
