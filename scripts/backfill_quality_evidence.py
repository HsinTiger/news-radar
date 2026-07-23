#!/usr/bin/env python3
"""Backfill deterministic per-platform quality evidence without changing drafts.

Historical ``rewrite`` findings are observations only. This command never
changes queue/status fields and therefore cannot retroactively fail or publish
content. It stores rule metadata and a text SHA-256, not the post body.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db as dbmod
from src.content_quality_guard import check_quality


def backfill(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    rows = conn.execute(
        """
        SELECT pd.draft_id,pd.platform,pd.full_text,pd.final_text,
               d.news_id,COALESCE(n.title,d.title,'') AS source_title
          FROM platform_drafts pd
          JOIN drafts d ON d.id=pd.draft_id
          LEFT JOIN news_items n ON n.id=d.news_id
         WHERE pd.platform IN ('facebook','instagram','threads')
         ORDER BY pd.draft_id,pd.platform
        """
    ).fetchall()
    checked_at = datetime.now(timezone.utc).isoformat()
    decisions: dict[str, Counter] = defaultdict(Counter)
    inserted = 0
    for row in rows:
        full_text = row["final_text"] or row["full_text"] or ""
        issues = check_quality(full_text, title=row["source_title"] or "")
        severities = {issue.severity for issue in issues}
        decision = (
            "block" if "block" in severities else
            "rewrite" if "rewrite" in severities else
            "warn" if "warn" in severities else
            "pass"
        )
        decisions[row["platform"]][decision] += 1
        if not dry_run:
            _decision, was_inserted = dbmod.record_quality_evaluation(
                conn,
                draft_id=row["draft_id"],
                news_id=row["news_id"],
                platform=row["platform"],
                stage="backfill",
                attempt=1,
                full_text=full_text,
                issues=issues,
                checked_at=checked_at,
                commit=False,
            )
            inserted += int(was_inserted)
    if not dry_run:
        conn.commit()
    return {
        "ok": True,
        "dry_run": dry_run,
        "evaluated": len(rows),
        "inserted": inserted,
        "decisions": {
            platform: dict(sorted(counts.items()))
            for platform, counts in sorted(decisions.items())
        },
        "status_mutations": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(dbmod.DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    is_default_db = args.db.resolve() == Path(dbmod.DB_PATH).resolve()
    if is_default_db and not args.dry_run:
        # Run the repository's complete idempotent migration chain. This also
        # keeps the subsequent operational sync compatible with older Release DBs.
        dbmod.init_db()
        conn = dbmod.get_conn()
    else:
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
    try:
        if not args.dry_run:
            conn.executescript(Path(dbmod.SCHEMA_PATH).read_text(encoding="utf-8"))
            if not is_default_db:
                dbmod._migrate_log_scale_engagement(conn)
        result = backfill(conn, dry_run=args.dry_run)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
