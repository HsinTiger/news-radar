#!/usr/bin/env python3
"""
Verify publish results — run AFTER publishing to Meta platforms.
Checks: publish_log success rate, platform post IDs, no duplicate posts.
Exits non-zero on critical failures.
"""
import sys
from src import db as dbmod

def main():
    conn = dbmod.get_conn()
    try:
        # 1. Recent publish attempts
        recent = conn.execute("""
            SELECT p.id, p.draft_id, p.platform, p.posted_at, p.success, p.error_message,
                   d.title
            FROM publish_log p
            JOIN drafts d ON d.id = p.draft_id
            WHERE p.posted_at >= datetime('now', '-1 day')
            ORDER BY p.posted_at DESC
        """).fetchall()
        print(f"[Verify:Publish] recent_attempts_24h={len(recent)}")

        successes = [r for r in recent if r["success"]]
        failures = [r for r in recent if not r["success"]]
        print(f"[Verify:Publish] successes={len(successes)} failures={len(failures)}")

        # 2. Check for platform-specific post IDs
        for s in successes[:10]:
            platform = s["platform"]
            draft_id = s["draft_id"][:12]
            post_id = conn.execute(
                "SELECT platform_post_id FROM publish_log WHERE id=?",
                (s["id"],)
            ).fetchone()
            pid = post_id["platform_post_id"] if post_id else None
            print(f"  ✓ {platform} draft={draft_id} post_id={'✅' if pid else '❌ MISSING'}")

            if not pid:
                print(f"⚠️ [Verify:Publish] {platform} draft={draft_id}: No platform_post_id!")

        # 3. Check for duplicate published rows
        dups = conn.execute("""
            SELECT draft_id, platform, COUNT(*) as c
            FROM publish_log
            WHERE success=1
            GROUP BY draft_id, platform
            HAVING c > 1
        """).fetchall()
        for d in dups:
            print(f"⚠️ [Verify:Publish] draft={d['draft_id'][:12]} {d['platform']}: {d['c']} publish rows! (idempotency check)")

        # 4. Check for recent publish failures
        if failures:
            for f in failures[:5]:
                print(f"  ❌ {f['platform']} draft={f['draft_id'][:12]}: {f['error_message'] or 'no error msg'}")

        # 5. Overall publish health
        total_published = conn.execute(
            "SELECT COUNT(*) as c FROM publish_log WHERE success=1"
        ).fetchone()["c"]
        total_failed = conn.execute(
            "SELECT COUNT(*) as c FROM publish_log WHERE success=0"
        ).fetchone()["c"]
        print(f"[Verify:Publish] lifetime: {total_published} success / {total_failed} failed")

        if total_failed > total_published * 2 and total_published > 0:
            print("❌ [Verify:Publish] Failure rate > 66% — something is wrong!")
            sys.exit(1)

        # Not a failure if there are simply no attempts yet
        if len(recent) == 0:
            print("ℹ️ [Verify:Publish] No publish attempts in last 24h (may be normal for new setup)")
        else:
            print("✅ [Verify:Publish] PASS")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
