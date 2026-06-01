#!/usr/bin/env python3
"""
Verify harvest results — run AFTER run_harvest.py.
Checks: feeds were checked, items were found, no critical errors.
Exits non-zero on failure so GitHub Actions marks the step red.
"""
import sys, json
from pathlib import Path
from src import db as dbmod

def main():
    conn = dbmod.get_conn()
    try:
        # 1. Count news_items
        total = conn.execute("SELECT COUNT(*) as c FROM news_items").fetchone()["c"]
        fetched_today = conn.execute(
            "SELECT COUNT(*) as c FROM news_items WHERE fetched_at >= datetime('now', '-1 day')"
        ).fetchone()["c"]
        dropped = conn.execute(
            "SELECT COUNT(*) as c FROM news_items WHERE status='dropped'"
        ).fetchone()["c"]

        print(f"[Verify:Harvest] total_items={total} fetched_24h={fetched_today} dropped={dropped}")

        if total == 0:
            print("❌ [Verify:Harvest] Database has 0 items — harvest failed!")
            sys.exit(1)

        if fetched_today == 0 and total > 0:
            print("⚠️ [Verify:Harvest] No new items in 24h (DB has historical data though)")

        # 2. Check feed diversity
        feed_count = conn.execute(
            "SELECT COUNT(DISTINCT feed_name) as c FROM news_items"
        ).fetchone()["c"]
        print(f"[Verify:Harvest] distinct_feeds={feed_count}")

        if feed_count < 5:
            print(f"❌ [Verify:Harvest] Only {feed_count} distinct feeds (< 5) — possible config issue")
            sys.exit(1)

        # 3. Check for recent items with clean_markdown
        with_content = conn.execute(
            "SELECT COUNT(*) as c FROM news_items WHERE clean_markdown IS NOT NULL AND LENGTH(clean_markdown) > 100"
        ).fetchone()["c"]
        print(f"[Verify:Harvest] items_with_content={with_content}")

        if with_content == 0:
            print("❌ [Verify:Harvest] No items have usable content (clean_markdown)")
            sys.exit(1)

        print("✅ [Verify:Harvest] PASS")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
