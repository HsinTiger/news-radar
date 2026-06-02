"""
News Radar · Engagement Feedback Loop
========================================
2026-06-02: 把 engagement 數據反饋回 pipeline，形成閉環。

流程：
  publish → poll engagement → analyze →
  produce editorial_note → next compose uses it
"""
from __future__ import annotations
import json, os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent.parent
import sys; sys.path.insert(0, str(_HERE))

from src import db as dbmod
from src.analytics_engine import AnalyticsEngine
from src.iteration_engine import calibrate_topic_weights, generate_system_recommendations


FEEDBACK_DIR = _HERE / "data" / "05_reflect" / "feedback"
FEEDBACK_FILE = FEEDBACK_DIR / "latest.json"
TOPIC_ADJUSTMENTS_FILE = FEEDBACK_DIR / "topic_adjustments.json"
ENGAGEMENT_REPORT_FILE = FEEDBACK_DIR / "engagement_report.json"


def ensure_dirs():
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)


def collect_engagement_summary(days: int = 7) -> Dict:
    """收集近期互動數據摘要。

    Returns: dict with per-platform & overall stats
    """
    conn = dbmod.get_conn()
    rows = conn.execute("""
        SELECT e.platform,
               e.likes, e.comments, e.shares,
               e.saves, e.reposts, e.reach, e.views,
               d.title as draft_title,
               d.generated_at
        FROM engagement_stats e
        JOIN drafts d ON d.id = e.draft_id
        WHERE e.fetched_at >= datetime('now', ?, 'localtime')
        ORDER BY e.fetched_at DESC
    """, (f"-{days} days",)).fetchall()
    conn.close()

    if not rows:
        return {"status": "no_data", "days": days, "total_posts": 0}

    from src.analytics_engine import engagement_rate

    summary = {"days": days, "total_posts": len(rows), "platforms": {}}
    platform_data = {}
    for r in rows:
        pf = r["platform"]
        if pf not in platform_data:
            platform_data[pf] = {"count": 0, "likes": 0, "comments": 0, "ers": []}
        platform_data[pf]["count"] += 1
        platform_data[pf]["likes"] += r["likes"] or 0
        platform_data[pf]["comments"] += r["comments"] or 0
        er = engagement_rate(
            pf, likes=r["likes"] or 0, comments=r["comments"] or 0,
            shares=r["shares"] or 0, saves=r["saves"] or 0,
            reposts=r["reposts"] or 0, reach=r["reach"] or 0, views=r["views"] or 0,
        )
        platform_data[pf]["ers"].append(er)

    for pf, data in platform_data.items():
        summary["platforms"][pf] = {
            "posts": data["count"],
            "total_likes": data["likes"],
            "total_comments": data["comments"],
            "avg_er": round(sum(data["ers"]) / len(data["ers"]), 6) if data["ers"] else 0,
            "best_er": round(max(data["ers"]), 6) if data["ers"] else 0,
            "worst_er": round(min(data["ers"]), 6) if data["ers"] else 0,
        }

    # Overall
    all_ers = [er for pf in platform_data.values() for er in pf["ers"]]
    summary["overall"] = {
        "avg_er": round(sum(all_ers) / len(all_ers), 6) if all_ers else 0,
        "total_likes": sum(pf["likes"] for pf in platform_data.values()),
        "total_comments": sum(pf["comments"] for pf in platform_data.values()),
    }

    return summary


def produce_editorial_note() -> str:
    """根據近期數據產出 composer editorial_note。

    這條 note 會在下一次 run_pipeline 時被 composer 使用。
    """
    parts = []

    # 1. Check engagement trend
    summary = collect_engagement_summary(days=3)
    if summary.get("status") != "no_data":
        for pf, data in summary.get("platforms", {}).items():
            if data["avg_er"] < 0.01:
                parts.append(f"{pf} 互動率偏低 ({data['avg_er']:.4f})，建議調整內容風格")
            elif data["avg_er"] > 0.05:
                parts.append(f"{pf} 互動率表現不錯 ({data['avg_er']:.4f})，維持當前風格")

    # 2. Check topic calibration
    try:
        cal = calibrate_topic_weights()
        for r in cal.get("recommendations", [])[:2]:
            parts.append(f"主題「{r['topic']}」建議{r['action']}")
    except Exception:
        pass

    # 3. Thompson sample
    try:
        from src.iteration_engine import ContentThompsonSampler
        sampler = ContentThompsonSampler()
        sampler.load_from_db()
        topic, conf = sampler.recommend_topic()
        if topic != "general" and conf > 0.6:
            parts.append(f"建議本輪優先選用「{topic}」題材")
    except Exception:
        pass

    return "；".join(parts) if parts else "按既有風格自由發揮"


def write_feedback():
    """寫入 feedback 檔案，供 dashboard 和 pipeline 讀取。"""
    ensure_dirs()

    # Engagement summary
    summary = collect_engagement_summary(days=7)
    with open(ENGAGEMENT_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Topic adjustments
    try:
        cal = calibrate_topic_weights()
        with open(TOPIC_ADJUSTMENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(cal, f, ensure_ascii=False, indent=2)
    except Exception as e:
        with open(TOPIC_ADJUSTMENTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"status": "error", "message": str(e)}, f)

    # Overall recommendations
    try:
        recs = generate_system_recommendations()
        recs["engagement"] = summary
        recs["editorial_note"] = produce_editorial_note()
        with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump({"status": "error", "message": str(e)}, f)

    print(f"[FeedbackLoop] 已寫入 feedback 檔案")
    print(f"  └─ engagement: {ENGAGEMENT_REPORT_FILE}")
    print(f"  └─ topics: {TOPIC_ADJUSTMENTS_FILE}")
    print(f"  └─ recommendations: {FEEDBACK_FILE}")
    return summary


def read_feedback() -> Dict:
    """讀取最新的 feedback 檔案。"""
    ensure_dirs()
    if FEEDBACK_FILE.exists():
        try:
            return json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"status": "error", "message": "feedback 檔案損毀"}
    return {"status": "no_feedback"}


# ====================================================================
# CLI
# ====================================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Engagement Feedback Loop")
    parser.add_argument("--mode", choices=["report", "note", "write"], default="write")
    args = parser.parse_args()

    if args.mode == "write":
        result = write_feedback()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.mode == "note":
        note = produce_editorial_note()
        print(json.dumps({"editorial_note": note, "generated_at":
                          datetime.now(timezone.utc).isoformat()},
                         ensure_ascii=False, indent=2))
    elif args.mode == "report":
        result = read_feedback()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
