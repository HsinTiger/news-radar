#!/usr/bin/env python3
"""Compose against a disposable canonical-state copy without publishing.

This is the only supported out-of-slot editorial canary. It never receives
Meta credentials, never pushes runtime state, disables token refresh and
preview writes, and replaces the publisher with a hard failure.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db as dbmod  # noqa: E402
from src.content_quality_guard import QUALITY_GUARD_VERSION  # noqa: E402


PLATFORMS = {"facebook", "instagram", "threads"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _latest_quality(
    conn: sqlite3.Connection,
    *,
    platform: str,
    since_iso: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH ranked AS (
          SELECT q.*,
                 ROW_NUMBER() OVER(
                   PARTITION BY q.draft_id,q.platform ORDER BY q.id DESC
                 ) AS rn
            FROM content_quality_evaluations q
           WHERE q.platform=? AND q.guard_version=?
             AND datetime(q.checked_at) >= datetime(?)
        )
        SELECT draft_id,platform,decision,attempt,guard_version,issue_codes_json
          FROM ranked WHERE rn=1 ORDER BY id
        """,
        (platform, QUALITY_GUARD_VERSION, since_iso),
    ).fetchall()
    return [
        {
            "draft_id": row["draft_id"],
            "platform": row["platform"],
            "decision": row["decision"],
            "attempt": row["attempt"],
            "guard_version": row["guard_version"],
            "issue_codes": json.loads(row["issue_codes_json"] or "[]"),
        }
        for row in rows
    ]


async def run_setup_canary(
    *,
    source_db: Path,
    platform: str,
    report_path: Path,
) -> dict[str, Any]:
    # The script name is a runtime contract, not a suggestion.  Lock the
    # canary to the same Recovery/editorial path as the production workflow so
    # a local invocation cannot accidentally exercise the legacy long prompt
    # and report misleading evidence.
    os.environ["AUTOMATION_MODE"] = "recovery"
    os.environ["EDITORIAL_MODE"] = "1"
    os.environ["EDITOR_ENFORCE"] = "1"
    if platform not in PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    if not source_db.is_file():
        raise FileNotFoundError(source_db)

    canonical_before = _sha256(source_db)
    with tempfile.TemporaryDirectory(prefix="news-radar-recovery-setup-") as temp_dir:
        temp_db = Path(temp_dir) / "news_radar.db"
        shutil.copy2(source_db, temp_db)

        import run_pipeline

        dbmod.DB_PATH = temp_db
        run_pipeline.dbmod.DB_PATH = temp_db
        run_pipeline.refresh_threads_token = lambda: None
        run_pipeline.save_md_draft = lambda *args, **kwargs: None
        run_pipeline.save_archive_md = lambda *args, **kwargs: None

        async def forbidden_publish(*args, **kwargs):
            raise AssertionError("publisher invoked during setup-only canary")

        run_pipeline._publish_platform = forbidden_publish

        conn = dbmod.get_conn()
        publish_before = conn.execute("SELECT COUNT(*) FROM publish_log").fetchone()[0]
        queued_before = dbmod.count_queued_pending_for_platforms(
            conn,
            {platform},
            recovery_only=True,
        )
        conn.close()

        started_at = datetime.now(timezone.utc).isoformat()
        original_argv = sys.argv[:]
        try:
            sys.argv = [
                "run_pipeline.py",
                "--compose-only",
                "--buffer-target",
                str(queued_before + 1),
                "--platforms",
                platform,
            ]
            await run_pipeline.main()
        finally:
            sys.argv = original_argv

        conn = dbmod.get_conn()
        publish_after = conn.execute("SELECT COUNT(*) FROM publish_log").fetchone()[0]
        quality = _latest_quality(
            conn,
            platform=platform,
            since_iso=started_at,
        )
        queued_after = dbmod.count_queued_pending_for_platforms(
            conn,
            {platform},
            recovery_only=True,
        )
        experiments = conn.execute(
            """
            SELECT COUNT(*) FROM recovery_experiments
             WHERE platform=? AND datetime(created_at) >= datetime(?)
            """,
            (platform, started_at),
        ).fetchone()[0]
        conn.close()

    canonical_after = _sha256(source_db)
    publish_unchanged = publish_before == publish_after
    canonical_unchanged = canonical_before == canonical_after
    publish_ready = any(
        row["decision"] in {"pass", "warn"} for row in quality
    )
    status = (
        "pass"
        if publish_unchanged
        and canonical_unchanged
        and publish_ready
        and experiments > 0
        and queued_after > queued_before
        else "held"
    )
    if not canonical_unchanged or not publish_unchanged:
        hold_reason = "state_invariant_failed"
    elif not quality:
        hold_reason = "no_current_guard_evaluation"
    elif not publish_ready:
        hold_reason = "quality_held"
    elif experiments <= 0:
        hold_reason = "missing_recovery_experiment"
    elif queued_after <= queued_before:
        hold_reason = "queue_not_advanced"
    else:
        hold_reason = "ready"
    report = {
        "status": status,
        "hold_reason": hold_reason,
        "setup_only": True,
        "automation_mode": os.environ["AUTOMATION_MODE"],
        "platform": platform,
        "started_at": started_at,
        "guard_version": QUALITY_GUARD_VERSION,
        "canonical_db_unchanged": canonical_unchanged,
        "publish_log_unchanged": publish_unchanged,
        "publish_log_before": publish_before,
        "publish_log_after": publish_after,
        "queued_before": queued_before,
        "queued_after": queued_after,
        "new_experiments": experiments,
        "quality": quality,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument(
        "--source-db",
        type=Path,
        default=ROOT / "data" / "01_harvest" / "news_radar.db",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "reports" / "recovery_setup_canary.json",
    )
    args = parser.parse_args()
    report = asyncio.run(
        run_setup_canary(
            source_db=args.source_db.resolve(),
            platform=args.platform,
            report_path=args.report.resolve(),
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
