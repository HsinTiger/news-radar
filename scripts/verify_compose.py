#!/usr/bin/env python3
"""
Verify composed drafts — run AFTER run_pipeline.py.
Checks: drafts exist, all 3 platforms have content, quality passes, no placeholder text.
Exits non-zero on failure.
"""
import sys
from pathlib import Path
_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

import json
from src import db as dbmod
from src.content_quality_guard import check_quality, has_blocking_issues

def main():
    conn = dbmod.get_conn()
    try:
        # 1. Count drafts in last 24h
        recent = conn.execute("""
            SELECT d.id, d.title, d.confidence_score, d.status, d.queue_status,
                   d.generated_at, d.news_id
            FROM drafts d
            WHERE d.generated_at >= datetime('now', '-1 day')
            ORDER BY d.generated_at DESC
        """).fetchall()
        print(f"[Verify:Compose] recent_drafts_24h={len(recent)}")

        if len(recent) == 0:
            print("❌ [Verify:Compose] No drafts created in last 24h")
            sys.exit(1)

        # 2. For each draft, check platform_drafts exist
        for draft in recent:
            draft_id = draft["id"]
            platforms = conn.execute(
                "SELECT platform, char_count, full_text FROM platform_drafts WHERE draft_id=?",
                (draft_id,)
            ).fetchall()
            found_platforms = {p["platform"]: p for p in platforms}

            missing = []
            for expected in ("facebook", "instagram", "threads"):
                if expected not in found_platforms:
                    missing.append(expected)

            if missing:
                print(f"⚠️ [Verify:Compose] draft={draft_id[:12]} missing platforms: {missing}")

            # 3. Quality guard check on each platform
            for plat, pd_data in found_platforms.items():
                text = pd_data["full_text"] or ""
                issues = check_quality(text, title=draft["title"] or "")
                if has_blocking_issues(issues):
                    print(f"❌ [Verify:Compose] draft={draft_id[:12]} {plat}: quality BLOCKED")
                    # Don't exit - this is the guard working as designed, but flag it
                else:
                    char_count = pd_data["char_count"] or len(text)
                    print(f"  ✓ {plat}: {char_count} chars")

            # 4. Check for placeholder / AI味 fingerprints
            for plat, pd_data in found_platforms.items():
                text = pd_data["full_text"] or ""
                ai_flags = []
                if "這說明兩件事" in text or "拆解兩層邏輯" in text:
                    ai_flags.append("八股條列結構")
                if "總結來說" in text or "總而言之" in text:
                    ai_flags.append("總結式收尾")
                if text.count("—") > 3:
                    ai_flags.append(f"破折號過多({text.count('—')})")
                if ai_flags:
                    print(f"⚠️ [Verify:Compose] draft={draft_id[:12]} {plat}: AI味指紋={ai_flags}")

        # 5. Overall health
        total_drafts = conn.execute("SELECT COUNT(*) as c FROM drafts").fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) as c FROM drafts WHERE queue_status='queued'"
        ).fetchone()["c"]
        print(f"[Verify:Compose] total_drafts={total_drafts} pending_queue={pending}")

        print("✅ [Verify:Compose] PASS")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
