#!/usr/bin/env python3
"""Evaluate the governed scheduler and emit GitHub Actions outputs."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schedule_policy import decide_schedule, load_policy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/01_harvest/news_radar.db"))
    parser.add_argument(
        "--policy", type=Path, default=Path("config/social_automation_policy.json")
    )
    parser.add_argument("--now", help="Optional ISO timestamp for deterministic replay")
    parser.add_argument("--output", type=Path, default=Path("reports/schedule_decision.json"))
    args = parser.parse_args()

    policy = load_policy(args.policy)
    now = datetime.fromisoformat(args.now) if args.now else None
    conn = sqlite3.connect(f"file:{args.db.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        decision = decide_schedule(conn, policy, now=now)
    finally:
        conn.close()
    payload = decision.to_dict()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with Path(output_file).open("a", encoding="utf-8") as handle:
            handle.write(f"dispatch={'true' if decision.dispatch else 'false'}\n")
            handle.write(f"platforms={','.join(decision.platforms)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
