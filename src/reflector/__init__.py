"""News Radar · Phase 9 unified reflector package.

Created 2026-04-27 (Phase 9 Item 2). Will host the analyzer modules
introduced by Items 3-7:

  - topic.py     (Item 3 — refactor of legacy src/reflector_topic.py)
  - harvest.py   (Item 4)
  - composer.py  (Item 5)
  - scorer.py    (Item 6)
  - gate.py      (Item 7)
  - proposals.py (Item 2 — write-path API for proposals.jsonl + lineage)

All analyzers write through ``proposals.write_proposal``; no analyzer
opens the jsonl file or the lineage table directly.

The package-level helper ``mark_deployed`` (Item 2.5) is the deployment
counterpart: once Hsin approves a proposal and the analyzer applies it,
the analyzer calls ``mark_deployed(fire_id)`` to record the deploy
timestamp on both the jsonl record and the lineage row. Item 3 will
be the first caller.

Spec  : PM_Radar/specs/phase_9_implementation_plan.md §3
Canon : PM_Radar/roadmap/phase_9_unified_reflector.md
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import proposals as _proposals_mod
from .proposals import (
    PROPOSALS_DIR,
    _iso_week_for,
    _iter_jsonl,
    _proposals_path_for_week,
    _resolve_db_path,
    _resolve_proposals_dir,
    _utcnow_iso,
)


def mark_deployed(
    fire_id: str,
    deployed_at: Optional[str] = None,
    *,
    db_path: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> None:
    """Mark a previously-written proposal as deployed.

    Updates BOTH the proposals jsonl entry's ``deployed_at`` field AND
    the ``reflector_proposal_lineage`` table's ``deployed_at`` column.

    The week-file containing the proposal is located by reading
    ``fire_at`` from the lineage row and deriving its ISO week — no
    glob scan over all week-files. If the lineage row does not exist,
    a ``LookupError`` is raised (you cannot deploy what was never
    proposed).

    Atomicity:
      - JSONL rewrite first (full week-file via tmp + ``os.replace``,
        matching ``proposals.update_decision``).
      - Lineage UPDATE second, in its own transaction.
      - On lineage UPDATE failure, the jsonl is restored from a pre-
        edit byte snapshot captured before the rewrite. The lineage
        exception is re-raised so the caller knows the deploy didn't
        commit.
      - On lineage UPDATE returning rowcount=0 (lineage row vanished
        between the lookup and the update — should never happen in
        single-process cron context), same rollback path runs.

    Args:
        fire_id: the proposal's UUID-v4 fire_id (assigned by
            ``write_proposal``).
        deployed_at: ISO-8601 UTC timestamp; defaults to the same
            format ``_utcnow_iso`` produces (``...+00:00``).
        db_path / base_dir: test-only overrides; production calls
            should omit these.

    Raises:
        LookupError: if fire_id is absent from the lineage table, or
            present in lineage but missing from its derived week-file
            (data drift — refuses to half-commit).
    """
    deployed_at = deployed_at or _utcnow_iso()

    resolved_db = _resolve_db_path(db_path)
    proposals_dir = _resolve_proposals_dir(base_dir)

    # 1. Look up fire_at in lineage so we can derive the right week-file.
    with sqlite3.connect(str(resolved_db)) as conn:
        row = conn.execute(
            "SELECT fire_at FROM reflector_proposal_lineage WHERE fire_id = ?",
            (fire_id,),
        ).fetchone()
    if row is None:
        raise LookupError(
            f"fire_id {fire_id!r} not found in reflector_proposal_lineage; "
            "cannot mark deployed (was the proposal ever written?)"
        )
    fire_at = row[0]
    week = _iso_week_for(fire_at)
    target_file = _proposals_path_for_week(week, base_dir=proposals_dir)

    if not target_file.exists():
        raise LookupError(
            f"lineage row for {fire_id!r} indicates ISO week {week} but "
            f"week-file {target_file} does not exist (data drift)"
        )

    # 2. Snapshot the file bytes pre-edit, for rollback.
    pre_snapshot = target_file.read_bytes()

    # 3. Read + mutate the matching record.
    records = list(_iter_jsonl(target_file))
    matched = False
    for r in records:
        if r.get("fire_id") == fire_id:
            r["deployed_at"] = deployed_at
            matched = True
    if not matched:
        raise LookupError(
            f"fire_id {fire_id!r} present in lineage but absent from "
            f"week-file {target_file} (data drift; refusing to half-commit)"
        )

    # 4. Atomic rewrite (tmp + os.replace).
    tmp_path = target_file.with_suffix(target_file.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_file)

    # 5. Lineage UPDATE; on any failure, restore jsonl from snapshot.
    try:
        with sqlite3.connect(str(resolved_db)) as conn:
            cur = conn.execute(
                """
                UPDATE reflector_proposal_lineage
                   SET deployed_at = ?
                 WHERE fire_id = ?
                """,
                (deployed_at, fire_id),
            )
            if cur.rowcount == 0:
                raise LookupError(
                    f"fire_id {fire_id!r} vanished from lineage between "
                    "lookup and UPDATE (concurrent delete?); rolled back"
                )
            conn.commit()
    except Exception:
        # Restore jsonl from pre-edit snapshot. Best-effort: if even
        # the restore fails, the original exception still propagates.
        try:
            target_file.write_bytes(pre_snapshot)
        except OSError:
            pass
        raise
