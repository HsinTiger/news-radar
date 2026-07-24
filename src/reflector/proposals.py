"""News Radar · Phase 9 Item 2 · proposal-record write-path API.

Two storage layers, kept in lockstep by this module:

  1. **proposals.jsonl** — canonical, append-only, human-readable record.
     One file per ISO week at:
         data/05_reflect/proposals/YYYY-WW.jsonl
     One JSON object per line.

  2. **reflector_proposal_lineage** — sqlite mirror for cross-analyzer
     SQL queries (recent proposals from analyzer X, pending decisions,
     etc.). Schema in data/01_harvest/schema.sql §9 + migration file
     2026-04-27_phase9_proposal_lineage.sql.

Public API (used by Items 3-7 analyzers):

  - ``write_proposal(proposal: dict) -> str``
        Validates, appends to jsonl, inserts lineage row. Returns ``fire_id``.

  - ``read_proposals(week: str | None = None) -> list[dict]``
        Reads one ISO-week file (e.g. ``"2026-W17"``) or all weekly files
        when ``week is None``. Returns parsed dicts.

  - ``update_decision(fire_id, decision, comment) -> None``
        Updates ``hsin_decision`` / ``hsin_decision_at`` / ``hsin_decision_comment``
        on BOTH the jsonl line (full-file rewrite) AND the lineage row.

Atomicity contract:

  - jsonl append + lineage insert are wrapped so a lineage failure
    truncates the jsonl back to its pre-append length. This is a
    single-process / single-thread workload (cron-driven analyzers);
    no advisory file lock is taken.

  - ``update_decision`` rewrites the ENTIRE week-file via a tmp file +
    ``os.replace`` (atomic on POSIX), then commits the lineage UPDATE.
    Choice over in-place line edit: lines have variable length, in-place
    overwrite would shift bytes; full rewrite is simpler and the files
    are bounded (one week of proposals).

Validation:

  - Required top-level fields and enum-restricted fields per spec
    §3 Item 2 lines 134-156.
  - Invalid proposals raise ``ProposalValidationError`` BEFORE any
    side effect (file or DB). Analyzers should catch + log.

State-branch propagation: explicitly out of scope for Item 2. The
proposals files written by cron-context analyzers (Items 4+) will
eventually need to land in the state branch for dashboard consumption,
but the propagation path is deferred to whichever analyzer first
writes from cron — see audit
``PM_Radar/audits/2026-04-27_phase9_item2_proposals_jsonl.md``.

Spec  : PM_Radar/specs/phase_9_implementation_plan.md §3 Item 2
Canon : PM_Radar/roadmap/phase_9_unified_reflector.md §1.3
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# Module is intentionally light on news_radar imports so it can be unit-tested
# against an arbitrary tmp_path DB without dragging in the full init chain.
from .. import db as _db_mod  # for default DB_PATH; tests can override


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROPOSALS_DIR = _PROJECT_ROOT / "data" / "05_reflect" / "proposals"

VALID_ANALYZERS = frozenset({
    "harvest", "topic", "platform_policy", "scorer", "composer", "gate"
})
VALID_PLATFORMS = frozenset({"facebook", "instagram", "threads", "all"})
VALID_PROPOSAL_TYPES = frozenset({
    "sunset_feed",
    "adjust_weight",
    "adjust_cadence",
    "tune_threshold",
    "add_rule",
    "relax_gate",
})
VALID_TARGET_CONFIGS = frozenset({
    "feeds.yml",
    "topic_weights",
    "social_schedule",
    "thresholds.yml",
    "composer_rules.yml",
    "gate.yaml",
})
VALID_CONFIDENCE = frozenset({"HIGH", "MED", "LOW"})
VALID_DECISIONS = frozenset({"approve", "reject", "amend"})

_REQUIRED_TOP_KEYS = (
    "analyzer",
    "platform",
    "proposal_type",
    "evidence",
    "action",
    "boss_attention_required",
)
_REQUIRED_ACTION_KEYS = (
    "target_config",
    "field",
    "current_value",
    "proposed_value",
)
_REQUIRED_EVIDENCE_KEYS = ("sample_ids", "metrics", "confidence")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ProposalValidationError(ValueError):
    """Raised by validate_proposal when a proposal is malformed.

    Carries a user-readable message; analyzers should log and skip.
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _iso_week_for(fire_at_iso: str) -> str:
    """Return ``YYYY-Www`` for a given ISO-8601 timestamp string.

    Falls back to current UTC if parsing fails — defensive only;
    validation guarantees fire_at is set before this is called.
    """
    try:
        # Accept both '+00:00' and 'Z' suffixes.
        ts = fire_at_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
    except (ValueError, AttributeError):
        dt = datetime.now(timezone.utc)
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def _proposals_path_for_week(week: str, base_dir: Path = PROPOSALS_DIR) -> Path:
    return base_dir / f"{week}.jsonl"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_proposal(proposal: dict) -> None:
    """Raise ProposalValidationError on the first problem.

    Mutates nothing. Required-field check + enum check + shape check.
    """
    if not isinstance(proposal, dict):
        raise ProposalValidationError("proposal must be a dict")

    for k in _REQUIRED_TOP_KEYS:
        if k not in proposal:
            raise ProposalValidationError(f"missing required field: {k!r}")

    if proposal["analyzer"] not in VALID_ANALYZERS:
        raise ProposalValidationError(
            f"analyzer must be one of {sorted(VALID_ANALYZERS)}, "
            f"got {proposal['analyzer']!r}"
        )
    if proposal["platform"] not in VALID_PLATFORMS:
        raise ProposalValidationError(
            f"platform must be one of {sorted(VALID_PLATFORMS)}, "
            f"got {proposal['platform']!r}"
        )
    if proposal["proposal_type"] not in VALID_PROPOSAL_TYPES:
        raise ProposalValidationError(
            f"proposal_type must be one of {sorted(VALID_PROPOSAL_TYPES)}, "
            f"got {proposal['proposal_type']!r}"
        )
    if not isinstance(proposal["boss_attention_required"], bool):
        raise ProposalValidationError(
            "boss_attention_required must be bool"
        )

    evidence = proposal["evidence"]
    if not isinstance(evidence, dict):
        raise ProposalValidationError("evidence must be an object")
    for k in _REQUIRED_EVIDENCE_KEYS:
        if k not in evidence:
            raise ProposalValidationError(
                f"evidence missing required field: {k!r}"
            )
    if not isinstance(evidence["sample_ids"], list):
        raise ProposalValidationError("evidence.sample_ids must be a list")
    if not isinstance(evidence["metrics"], dict):
        raise ProposalValidationError("evidence.metrics must be an object")
    if evidence["confidence"] not in VALID_CONFIDENCE:
        raise ProposalValidationError(
            f"evidence.confidence must be one of {sorted(VALID_CONFIDENCE)}, "
            f"got {evidence['confidence']!r}"
        )

    action = proposal["action"]
    if not isinstance(action, dict):
        raise ProposalValidationError("action must be an object")
    for k in _REQUIRED_ACTION_KEYS:
        if k not in action:
            raise ProposalValidationError(
                f"action missing required field: {k!r}"
            )
    if action["target_config"] not in VALID_TARGET_CONFIGS:
        raise ProposalValidationError(
            f"action.target_config must be one of {sorted(VALID_TARGET_CONFIGS)}, "
            f"got {action['target_config']!r}"
        )
    if not isinstance(action["field"], str) or not action["field"]:
        raise ProposalValidationError(
            "action.field must be a non-empty string"
        )

    # Optional decision fields — if present, type-check.
    decision = proposal.get("hsin_decision")
    if decision is not None and decision not in VALID_DECISIONS:
        raise ProposalValidationError(
            f"hsin_decision must be null or one of {sorted(VALID_DECISIONS)}, "
            f"got {decision!r}"
        )


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------

def _resolve_db_path(db_path: Optional[Path]) -> Path:
    return Path(db_path) if db_path else Path(_db_mod.DB_PATH)


def _resolve_proposals_dir(base_dir: Optional[Path]) -> Path:
    return Path(base_dir) if base_dir else PROPOSALS_DIR


def write_proposal(
    proposal: dict,
    *,
    db_path: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> str:
    """Validate, normalize, append to jsonl, insert lineage row.

    Mutates a shallow copy of ``proposal`` to fill in defaults
    (``fire_id``, ``fire_at``, NULL decision/deployed fields) — the
    caller's dict is untouched.

    Returns the assigned ``fire_id``.

    Atomicity:
      - jsonl append happens first; on lineage-insert failure, the
        appended line is truncated back via the captured pre-append size.
      - On jsonl write failure (after fsync but before lineage), the
        lineage row is not inserted; the orphaned jsonl line stays —
        log it and move on (analyzer should retry next cycle).

    The ``db_path`` / ``base_dir`` kwargs exist solely for unit tests;
    production calls use module defaults.
    """
    # 1. Normalize: fill defaults onto a shallow copy.
    out = dict(proposal)
    out.setdefault("fire_id", str(uuid.uuid4()))
    out.setdefault("fire_at", _utcnow_iso())
    out.setdefault("hsin_decision", None)
    out.setdefault("hsin_decision_at", None)
    out.setdefault("hsin_decision_comment", None)
    out.setdefault("deployed_at", None)

    # 2. Validate (post-defaulting so analyzer code can omit fire_id/fire_at).
    validate_proposal(out)

    # 3. Resolve paths.
    proposals_dir = _resolve_proposals_dir(base_dir)
    proposals_dir.mkdir(parents=True, exist_ok=True)
    identity = (
        out["analyzer"],
        out["platform"],
        out["proposal_type"],
        out["action"]["target_config"],
        out["action"]["field"],
        out["action"]["current_value"],
        out["action"]["proposed_value"],
    )
    for existing in read_proposals(base_dir=proposals_dir):
        action = existing.get("action")
        if (
            existing.get("hsin_decision") is None
            and not existing.get("deployed_at")
            and isinstance(action, dict)
            and (
                existing.get("analyzer"),
                existing.get("platform"),
                existing.get("proposal_type"),
                action.get("target_config"),
                action.get("field"),
                action.get("current_value"),
                action.get("proposed_value"),
            )
            == identity
        ):
            return str(existing["fire_id"])
    week = _iso_week_for(out["fire_at"])
    target_path = _proposals_path_for_week(week, base_dir=proposals_dir)

    line = json.dumps(out, ensure_ascii=False, sort_keys=True) + "\n"

    # 4. Capture pre-append byte length (0 if file does not yet exist) so
    #    we can roll back on lineage failure.
    pre_size = target_path.stat().st_size if target_path.exists() else 0

    # 5. Append jsonl.
    with target_path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())

    # 6. Insert lineage row, with rollback of the jsonl append on failure.
    try:
        resolved_db = _resolve_db_path(db_path)
        with sqlite3.connect(str(resolved_db)) as conn:
            conn.execute(
                """
                INSERT INTO reflector_proposal_lineage
                  (fire_id, fire_at, analyzer, proposal_type, target_config,
                   hsin_decision, hsin_decision_at, deployed_at, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    out["fire_id"],
                    out["fire_at"],
                    out["analyzer"],
                    out["proposal_type"],
                    out["action"]["target_config"],
                    out["hsin_decision"],
                    out["hsin_decision_at"],
                    out["deployed_at"],
                    json.dumps(out["evidence"], ensure_ascii=False),
                ),
            )
            conn.commit()
    except Exception:
        # Roll back the jsonl append: truncate file back to pre-append size.
        try:
            with target_path.open("r+b") as f:
                f.truncate(pre_size)
            if pre_size == 0 and target_path.exists():
                # The file was newly-created by our open("a"); leave the
                # zero-byte file in place — harmless and matches "append-only,
                # week-file existence is independent of content".
                pass
        except OSError:
            # Rollback is best-effort; the orphaned line is documented in
            # the audit. The original lineage exception still propagates.
            pass
        raise

    return out["fire_id"]


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

def _iter_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            out.append(json.loads(raw))
    return out


def read_proposals(
    week: Optional[str] = None,
    *,
    base_dir: Optional[Path] = None,
) -> list[dict]:
    """Read one ISO-week file, or all weeks if ``week`` is None.

    ``week`` format: ``YYYY-Www`` (e.g. ``"2026-W17"``).
    """
    proposals_dir = _resolve_proposals_dir(base_dir)
    if not proposals_dir.exists():
        return []

    if week is not None:
        path = _proposals_path_for_week(week, base_dir=proposals_dir)
        return list(_iter_jsonl(path))

    # All weeks — sort filenames so order is deterministic.
    out: list[dict] = []
    for path in sorted(proposals_dir.glob("*.jsonl")):
        out.extend(_iter_jsonl(path))
    return out


# ---------------------------------------------------------------------------
# Decision update
# ---------------------------------------------------------------------------

def update_decision(
    fire_id: str,
    decision: str,
    comment: Optional[str] = None,
    *,
    decision_at: Optional[str] = None,
    db_path: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> None:
    """Record Hsin's decision on both the jsonl row and the lineage table.

    ``decision`` must be one of ``approve|reject|amend``.

    JSONL update strategy: full-file rewrite via tmp + ``os.replace``.
    Rationale: lines have variable byte length, in-place overwrite would
    shift bytes; week-files are bounded (one week's worth of analyzer
    output), so a full rewrite is simple and atomic on POSIX.

    Lineage table update is a single UPDATE statement.
    """
    if decision not in VALID_DECISIONS:
        raise ProposalValidationError(
            f"decision must be one of {sorted(VALID_DECISIONS)}, "
            f"got {decision!r}"
        )

    proposals_dir = _resolve_proposals_dir(base_dir)
    decision_at = decision_at or _utcnow_iso()

    # 1. Locate the jsonl line. Scan all week-files until found.
    target_file: Optional[Path] = None
    target_records: list[dict] = []
    for path in sorted(proposals_dir.glob("*.jsonl")):
        records = list(_iter_jsonl(path))
        if any(r.get("fire_id") == fire_id for r in records):
            target_file = path
            target_records = records
            break

    if target_file is None:
        raise LookupError(
            f"fire_id {fire_id!r} not found in any proposals jsonl under "
            f"{proposals_dir}"
        )

    # 2. Snapshot + mutate the matching record.
    pre_snapshot = target_file.read_bytes()
    for r in target_records:
        if r.get("fire_id") == fire_id:
            r["hsin_decision"] = decision
            r["hsin_decision_at"] = decision_at
            r["hsin_decision_comment"] = comment

    # 3. Atomic rewrite via tmp + os.replace.
    tmp_path = target_file.with_suffix(target_file.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for r in target_records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_file)

    # 4. Lineage UPDATE. Restore JSONL if the DB write does not commit.
    resolved_db = _resolve_db_path(db_path)
    try:
        with sqlite3.connect(str(resolved_db)) as conn:
            cur = conn.execute(
                """
                UPDATE reflector_proposal_lineage
                   SET hsin_decision = ?,
                       hsin_decision_at = ?
                 WHERE fire_id = ?
                """,
                (decision, decision_at, fire_id),
            )
            if cur.rowcount == 0:
                raise LookupError(
                    f"fire_id {fire_id!r} found in jsonl but not in "
                    "reflector_proposal_lineage; refusing to half-commit"
                )
            conn.commit()
    except Exception:
        try:
            target_file.write_bytes(pre_snapshot)
        except OSError:
            pass
        raise
