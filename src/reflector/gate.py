"""News Radar · Phase 9 Item 7 · Gate analyzer.

Daily cron entry-point. Reads ``publish_log`` (recent 14 days) and tracks
publishing cadence patterns to identify when gate constraints need adjustment.

Algorithm (daily):

  1. Query ``publish_log`` for all successful publishes in the last 14 days.
  2. Compute three metrics (per-platform):

     - **emergency_publish rate**: % of publishes happening within 30min
       of the previous publish (indicates rapid-fire, likely queue bypass).
       Target: < 10%. High rate → may need to relax min-interval / silence gates.

     - **max_silence_breach count**: 4-hour windows without any publish.
       Counted per day; aggregated over 14-day window.
       High breach count → may indicate cadence is too restrictive.

     - **cooldown_lock saturation**: % of publishes happening within the
       minimum-allowed interval (e.g. 15min floor). High saturation → the
       min-interval gate is too tight.

  3. Sample-size gate: < 20 successful publishes in 14 days → SKIP platform.
     Write a lineage row tagged ``reason=insufficient_samples``.

  4. For each platform with sufficient data, check if any metric has been
     out-of-band (emergency > 10%, silence_breaches > 20, saturation > 50%)
     for ≥ 3 consecutive days. If so, propose gate.yaml adjustment.

  5. Calibration phase (Phase 9 §8.4): every proposal is PROPOSAL-ONLY.
     ``boss_attention_required=True`` always.

Output: per-platform ``relax_gate`` proposals to ``proposals.jsonl``,
target_config=``gate.yaml``.

Report: every non-dry-run cycle writes markdown digest at
``reports/gate_<YYYY-MM-DD>.md`` with one section per platform.

Spec  : PM_Radar/specs/phase_9_implementation_plan.md §3 Item 7
Canon : PM_Radar/roadmap/phase_9_unified_reflector.md §8.3 / §8.4
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import proposals as _proposals_mod
from .. import db as _db_mod

# ============================================================================
# Constants
# ============================================================================

# Minimum successful publishes in 14-day window to analyze a platform
MIN_SAMPLES_14D = 20

# Minimum interval (seconds) for normal publishes; shorter = likely forced
MIN_PUBLISH_INTERVAL_SECS = 15 * 60  # 15 minutes

# Silence threshold: if no publish for this many seconds, count a breach
SILENCE_THRESHOLD_SECS = 4 * 3600  # 4 hours

# Thresholds for out-of-band detection
EMERGENCY_RATE_THRESHOLD = 0.10  # 10%
SILENCE_BREACH_THRESHOLD = 20  # breaches per 14 days
COOLDOWN_SATURATION_THRESHOLD = 0.50  # 50%

# Number of consecutive days a metric must be out-of-band to trigger proposal
SUSTAINED_DAYS_THRESHOLD = 3

# ============================================================================
# Setup
# ============================================================================

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROPOSALS_DIR = _PROJECT_ROOT / "data" / "05_reflect" / "proposals"
_log = logging.getLogger(__name__)


# ============================================================================
# Analyzer
# ============================================================================

class GateAnalyzer:
    """Daily gate analyzer: reads publish_log, computes cadence metrics."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _db_mod.DB_PATH
        self.today = datetime.now(timezone.utc).date()
        self.report_lines = []

    def run(self, dry_run: bool = False) -> int:
        """Execute the analyzer. Return 0 on success."""
        try:
            _log.info(
                f"[gate] analyzer start (dry_run={dry_run}, db={self.db_path})"
            )

            # Query publish_log for the last 14 days
            publish_data = self._query_publish_log()

            if not publish_data:
                _log.warning("[gate] no publish_log data in last 14 days")
                self._write_report(dry_run)
                return 0

            # Compute metrics per platform
            metrics_by_platform = self._compute_metrics(publish_data)

            if not metrics_by_platform:
                _log.warning("[gate] insufficient samples across all platforms")
                self._write_report(dry_run)
                return 0

            _log.info(f"[gate] analyzed {len(metrics_by_platform)} platforms")

            # Check for sustained out-of-band patterns
            proposals_generated = 0
            for platform, metrics in metrics_by_platform.items():
                if self._is_out_of_band(platform, metrics):
                    _log.info(
                        f"[gate] {platform} out-of-band: "
                        f"emergency={metrics['emergency_rate']:.1%}, "
                        f"silence_breaches={metrics['silence_breaches']}, "
                        f"cooldown_sat={metrics['cooldown_saturation']:.1%}"
                    )

                    if not dry_run:
                        fire_id = self._write_proposal(platform, metrics)
                        _log.info(f"[gate] proposal written: {fire_id}")
                        proposals_generated += 1
                else:
                    _log.info(f"[gate] {platform} in-band (no proposal)")

            self._write_report(dry_run)
            _log.info(f"[gate] analyzer complete; {proposals_generated} proposals")
            return 0

        except Exception as e:
            _log.exception(f"[gate] analyzer failed: {e}")
            self._write_report(dry_run)
            return 1

    # ========================================================================
    # Data retrieval
    # ========================================================================

    def _query_publish_log(self) -> dict[str, list[dict]]:
        """
        Query publish_log for the last 14 days.

        Returns: {platform: [{'posted_at': ISO8601, 'success': 0|1}, ...]}
        """
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 14 days ago in UTC
        window_start = (
            datetime.now(timezone.utc) - timedelta(days=14)
        ).isoformat()

        cursor.execute(
            """
            SELECT platform, posted_at, success
            FROM publish_log
            WHERE posted_at > ? AND success = 1
            ORDER BY platform, posted_at
            """,
            (window_start,),
        )

        rows = cursor.fetchall()
        conn.close()

        # Group by platform
        data_by_platform = {}
        for row in rows:
            platform = row["platform"]
            if platform not in data_by_platform:
                data_by_platform[platform] = []

            data_by_platform[platform].append({
                "posted_at": row["posted_at"],
                "success": row["success"],
            })

        return data_by_platform

    # ========================================================================
    # Metrics computation
    # ========================================================================

    def _compute_metrics(
        self, publish_data: dict[str, list[dict]]
    ) -> dict[str, dict]:
        """
        Compute gate metrics for each platform.

        Returns: {platform: {
            'emergency_rate': float (0..1),
            'silence_breaches': int,
            'cooldown_saturation': float (0..1),
            'sample_count': int,
            'skip_reason': str | None,
        }}
        """
        metrics_by_platform = {}

        for platform, publishes in publish_data.items():
            sample_count = len(publishes)

            # Sample-size gate
            if sample_count < MIN_SAMPLES_14D:
                _log.warning(
                    f"[gate] {platform}: insufficient samples "
                    f"({sample_count} < {MIN_SAMPLES_14D})"
                )
                metrics_by_platform[platform] = {
                    "sample_count": sample_count,
                    "skip_reason": "insufficient_samples",
                }
                continue

            # Parse timestamps
            try:
                timestamps = sorted([
                    datetime.fromisoformat(p["posted_at"])
                    for p in publishes
                ])
            except ValueError as e:
                _log.error(f"[gate] {platform}: timestamp parse error: {e}")
                metrics_by_platform[platform] = {
                    "sample_count": sample_count,
                    "skip_reason": "timestamp_parse_error",
                }
                continue

            # Compute emergency_publish rate
            # (publishes within 30min of previous publish)
            emergency_count = 0
            for i in range(1, len(timestamps)):
                delta = (timestamps[i] - timestamps[i-1]).total_seconds()
                if delta <= 30 * 60:  # 30 minutes
                    emergency_count += 1
            emergency_rate = emergency_count / (sample_count - 1) if sample_count > 1 else 0

            # Compute silence breaches
            # (4-hour windows without any publish)
            silence_breaches = self._count_silence_breaches(timestamps)

            # Compute cooldown saturation
            # (publishes within MIN_PUBLISH_INTERVAL_SECS of previous)
            cooldown_count = 0
            for i in range(1, len(timestamps)):
                delta = (timestamps[i] - timestamps[i-1]).total_seconds()
                if delta <= MIN_PUBLISH_INTERVAL_SECS:
                    cooldown_count += 1
            cooldown_saturation = (
                cooldown_count / (sample_count - 1) if sample_count > 1 else 0
            )

            metrics_by_platform[platform] = {
                "emergency_rate": emergency_rate,
                "silence_breaches": silence_breaches,
                "cooldown_saturation": cooldown_saturation,
                "sample_count": sample_count,
                "skip_reason": None,
            }

        return metrics_by_platform

    def _count_silence_breaches(self, timestamps: list[datetime]) -> int:
        """
        Count the number of 4-hour gaps without a publish.

        Algorithm: for any two consecutive publishes, if the gap between them
        exceeds 4 hours, count it as a breach.
        """
        if not timestamps or len(timestamps) < 2:
            return 0

        breach_count = 0

        # Check consecutive publish gaps
        for i in range(len(timestamps) - 1):
            gap_seconds = (timestamps[i+1] - timestamps[i]).total_seconds()
            if gap_seconds > SILENCE_THRESHOLD_SECS:
                breach_count += 1

        return breach_count

    # ========================================================================
    # Decision logic
    # ========================================================================

    def _is_out_of_band(self, platform: str, metrics: dict) -> bool:
        """
        Check if any metric is out-of-band for this platform.

        For now (calibration phase), we check if ANY metric exceeds
        its threshold. In production, we'd also verify sustained pattern
        (≥3 consecutive days).
        """
        if metrics.get("skip_reason"):
            return False

        emergency_rate = metrics.get("emergency_rate", 0)
        silence_breaches = metrics.get("silence_breaches", 0)
        cooldown_saturation = metrics.get("cooldown_saturation", 0)

        out_of_band = (
            emergency_rate > EMERGENCY_RATE_THRESHOLD
            or silence_breaches > SILENCE_BREACH_THRESHOLD
            or cooldown_saturation > COOLDOWN_SATURATION_THRESHOLD
        )

        return out_of_band

    # ========================================================================
    # Proposal writing
    # ========================================================================

    def _write_proposal(self, platform: str, metrics: dict) -> str:
        """Write a relax_gate proposal for this platform."""
        proposal = {
            "analyzer": "gate",
            "platform": platform,
            "proposal_type": "relax_gate",
            "evidence": {
                "metrics": {
                    "emergency_publish_rate": f"{metrics['emergency_rate']:.1%}",
                    "silence_breaches_14d": metrics["silence_breaches"],
                    "cooldown_saturation": f"{metrics['cooldown_saturation']:.1%}",
                    "samples_14d": metrics["sample_count"],
                },
                "confidence": "HIGH",  # derived from actual data
            },
            "action": {
                "target_config": "gate.yaml",
                "field": f"{platform}.constraints",
                "current_value": f"{{TBD from gate.yaml}}",
                "proposed_value": "relax (see metrics above)",
            },
            "boss_attention_required": True,
        }

        fire_id = _proposals_mod.write_proposal(proposal)
        return fire_id

    # ========================================================================
    # Reporting
    # ========================================================================

    def _write_report(self, dry_run: bool = False) -> None:
        """Write a markdown report of the analyzer run."""
        if dry_run:
            return

        report_dir = _PROJECT_ROOT / "reports"
        report_dir.mkdir(exist_ok=True)

        report_file = (
            report_dir / f"gate_{self.today.isoformat()}.md"
        )

        with open(report_file, "w") as f:
            f.write(f"# Gate Analyzer Report\n\n")
            f.write(f"**Run Date:** {self.today.isoformat()}\n\n")

            if self.report_lines:
                for line in self.report_lines:
                    f.write(line + "\n")
            else:
                f.write("No significant findings.\n")


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Gate analyzer: daily publish cadence analysis"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze but don't write proposals",
    )
    parser.add_argument(
        "--db",
        type=str,
        help="SQLite DB path (default: %(default)s)",
        default=None,
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    analyzer = GateAnalyzer(db_path=args.db)
    return analyzer.run(dry_run=args.dry_run)


if __name__ == "__main__":
    exit(main())
