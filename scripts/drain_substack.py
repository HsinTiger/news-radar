#!/usr/bin/env python3
"""Drain user-submitted Substack sources into drafts (local, token-free).

The Substack manual-submit frontend (GitHub Pages) → substack-submit.yml (cloud)
writes a row into the state-branch DB with feed_name='user_substack'. The Mac's
hourly news_radar_compose.sh pulls that state DB into the local DB. THIS script
is the final link: it finds those submissions and composes a Substack draft for
each via substack_radar/compose.py (Claude/Gemini CLI — free, high quality).

Already-drafted ids are tracked in data/substack_drafts/.substack_submissions.json
so each submission is composed exactly once.

Usage:
    python scripts/drain_substack.py            # compose all new submissions
    python scripts/drain_substack.py --dry-run  # list candidates, compose nothing
    python scripts/drain_substack.py --mark <id> # mark an id done without composing
"""
from __future__ import annotations
import argparse, json, sqlite3, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "01_harvest" / "news_radar.db"
DONE_FILE = REPO / "data" / "substack_drafts" / ".substack_submissions.json"
COMPOSE = REPO / "substack_radar" / "compose.py"
PY = REPO / ".venv" / "bin" / "python"


def _load_done() -> set:
    if DONE_FILE.exists():
        try:
            return set(json.loads(DONE_FILE.read_text()).get("done", []))
        except Exception:
            return set()
    return set()


def _save_done(done: set):
    DONE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DONE_FILE.write_text(json.dumps({"done": sorted(done)}, ensure_ascii=False, indent=2))


def _candidates() -> list:
    if not DB.exists():
        print(f"[drain] DB not found: {DB}")
        return []
    conn = sqlite3.connect(str(DB))
    try:
        rows = conn.execute(
            "SELECT id, title, word_count FROM news_items "
            "WHERE feed_name='user_substack' "
            "  AND clean_markdown IS NOT NULL AND LENGTH(clean_markdown) > 100 "
            "ORDER BY fetched_at ASC"
        ).fetchall()
    finally:
        conn.close()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mark", type=str, help="mark this id as done without composing")
    args = ap.parse_args()

    done = _load_done()

    if args.mark:
        done.add(args.mark)
        _save_done(done)
        print(f"[drain] marked done: {args.mark}")
        return 0

    rows = _candidates()
    pending = [r for r in rows if r[0] not in done]
    print(f"[drain] {len(rows)} user_substack item(s), {len(pending)} pending compose")
    for rid, title, wc in pending:
        print(f"  · {rid[:12]}  {wc:>6}w  {title[:50]}")

    if args.dry_run:
        print("[drain] dry-run — composed nothing.")
        return 0

    composed = 0
    for rid, title, wc in pending:
        print(f"[drain] composing {rid[:12]} …")
        r = subprocess.run(
            [str(PY), "-u", str(COMPOSE), "morning", "--news-id", rid],
            cwd=str(REPO),
        )
        if r.returncode == 0:
            done.add(rid)
            _save_done(done)          # persist after each success (crash-safe)
            composed += 1
        else:
            print(f"[drain] ⚠️ compose failed for {rid[:12]} (rc={r.returncode}); will retry next run")
    print(f"[drain] done. composed {composed}/{len(pending)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
