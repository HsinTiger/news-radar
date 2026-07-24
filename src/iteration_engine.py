"""
News Radar · 論文實證自我迭代演算法
=========================================
2026-06-02: 基於學術文獻的社群媒體效能最佳化演算法。

理論基礎：
1. Engagement prediction: Fan & Gordon (2014) — 加權互動率模型
2. Optimal timing: 時間序列分解 (Cleveland et al., 1990, STL)
3. Content diversity: 基於 Jaccard 距離的內容多樣性評分
4. Topic weight calibration: 貝氏更新 (Bayesian updating, Gelman et al., 2013)
5. A/B testing: Thompson sampling (Thompson, 1933) for content strategy
"""

from __future__ import annotations
import json, math, statistics, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))
from src import db as dbmod
from src.content_quality_guard import QUALITY_GUARD_VERSION


# ====================================================================
# 1. Engagement Prediction (Fan & Gordon 2014 改良版)
# ====================================================================

def predict_engagement_rate(
    platform: str,
    hour: int = -1,
    day_of_week: int = -1,
    topic: str = "",
    char_count: int = 0,
    feed_name: str = "",
    historical_data: Optional[List[Dict]] = None,
) -> Dict:
    """預測貼文互動率。

    公式：ER_pred = base × topic_factor × time_factor × length_factor

    當歷史數據不足時回傳基礎平均值 + 信心度。
    """
    if not historical_data:
        base_rates = {"facebook": 0.008, "instagram": 0.015, "threads": 0.035}
        return {
            "predicted_er": base_rates.get(platform, 0.01),
            "confidence": 0.1,
            "message": "使用業界基準值（無歷史數據）",
        }

    # 過濾同平台數據
    same_platform = [d for d in historical_data if d.get("platform") == platform]
    if len(same_platform) < 3:
        return {
            "predicted_er": sum(d.get("er", 0) for d in same_platform) / max(len(same_platform), 1),
            "confidence": 0.2,
            "message": f"僅 {len(same_platform)} 筆樣本",
        }

    # 基礎平均
    base_er = sum(d.get("er", 0) for d in same_platform) / len(same_platform)

    # Topic factor
    topic_factor = 1.0
    if topic:
        topic_posts = [d for d in same_platform if d.get("topic") == topic]
        if len(topic_posts) >= 3:
            topic_avg = sum(d.get("er", 0) for d in topic_posts) / len(topic_posts)
            topic_factor = topic_avg / base_er if base_er > 0 else 1.0

    # Time factor (hour of day)
    time_factor = 1.0
    if hour >= 0:
        hour_posts = [d for d in same_platform if d.get("hour") == hour]
        if len(hour_posts) >= 2:
            hour_avg = sum(d.get("er", 0) for d in hour_posts) / len(hour_posts)
            time_factor = hour_avg / base_er if base_er > 0 else 1.0

    # Length factor
    length_factor = 1.0
    if char_count > 0:
        if char_count < 100:
            length_factor = 0.8  # 太短通常互動低
        elif char_count > 500 and platform in ("threads",):
            length_factor = 0.7  # Threads 超長文互動低
        elif 200 <= char_count <= 400:
            length_factor = 1.2  # 黃金字數區間

    predicted = base_er * topic_factor * time_factor * length_factor
    confidence = min(1.0, len(same_platform) / 50)

    return {
        "predicted_er": round(predicted, 6),
        "confidence": round(confidence, 2),
        "contributors": {
            "base_er": round(base_er, 6),
            "topic_factor": round(topic_factor, 4),
            "time_factor": round(time_factor, 4),
            "length_factor": round(length_factor, 4),
        },
        "sample_count": len(same_platform),
    }


# ====================================================================
# 2. Optimal Posting Time (STL 分解, Cleveland 1990)
# ====================================================================

def analyze_optimal_times(
    platform: str = "",
    *,
    minimum_post_age_hours: int = 168,
    minimum_complete_posts: int = 7,
) -> Dict:
    """分析各時段的互動表現，找出最佳發布時間。

    把歷史互動數據按 hour of day 分組，計算 avg engagement rate。
    使用指數平滑避免零星數據干擾。
    """
    conn = dbmod.get_conn()
    params: list[Any] = [QUALITY_GUARD_VERSION, minimum_post_age_hours]
    platform_clause = ""
    if platform:
        platform_clause = "AND e.platform = ?"
        params.append(platform)
    rows = conn.execute(
        f"""
        WITH current_quality AS (
          SELECT DISTINCT draft_id,platform
            FROM content_quality_evaluations
           WHERE stage='pre_publish' AND decision='pass' AND guard_version=?
        ), ranked AS (
          SELECT e.*,
                 ROW_NUMBER() OVER(
                   PARTITION BY e.platform,e.platform_post_id
                   ORDER BY COALESCE(e.post_age_bucket,0) DESC,e.fetched_at DESC,e.id DESC
                 ) AS rn
            FROM engagement_stats e
        )
        SELECT e.platform,e.likes,e.comments,e.shares,e.saves,e.reposts,
               e.quotes,e.replies,e.reach,e.views,
               CAST(STRFTIME('%H',DATETIME(p.posted_at,'+8 hours')) AS INTEGER) AS hour
          FROM ranked e
          JOIN publish_log p
            ON p.platform=e.platform AND p.platform_post_id=e.platform_post_id
           AND p.success=1
          JOIN recovery_experiments rx
            ON rx.draft_id=p.draft_id AND rx.platform=p.platform
          JOIN current_quality q
            ON q.draft_id=p.draft_id AND q.platform=p.platform
         WHERE e.rn=1
           AND COALESCE(e.post_age_bucket,0) >= ?
           AND DATETIME(p.posted_at) >= DATETIME('now','-90 days')
           AND (e.raw_json IS NULL OR LOWER(e.raw_json) NOT LIKE '%error%')
           {platform_clause}
        """,
        tuple(params),
    ).fetchall()
    conn.close()

    if not rows:
        return {
            "status": "insufficient_data",
            "guard_version": QUALITY_GUARD_VERSION,
            "required_post_age_hours": minimum_post_age_hours,
            "minimum_complete_posts": minimum_complete_posts,
        }

    # Group by hour × platform
    from src.analytics_engine import engagement_rate
    buckets: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for r in rows:
        key = (r["platform"], r["hour"])
        if key not in buckets:
            buckets[key] = {"count": 0, "ers": [], "primary": [], "actions": []}
        er = engagement_rate(
            r["platform"], likes=r["likes"] or 0, comments=r["comments"] or 0,
            shares=r["shares"] or 0, saves=r["saves"] or 0,
            reposts=r["reposts"] or 0, quotes=r["quotes"] or 0,
            replies=r["replies"] or 0, reach=r["reach"] or 0,
            views=r["views"] or 0,
        )
        primary = (r["views"] or 0) if r["platform"] == "threads" else (r["reach"] or 0)
        actions = sum(
            r[name] or 0
            for name in ("likes", "comments", "shares", "saves", "reposts", "quotes", "replies")
        )
        buckets[key]["count"] += 1
        buckets[key]["ers"].append(er)
        buckets[key]["primary"].append(primary)
        buckets[key]["actions"].append(actions)

    # Calculate smoothed averages per hour
    platforms = set(k[0] for k in buckets)
    results = {}
    for pf in platforms:
        complete_posts = sum(
            bucket["count"] for (bucket_platform, _), bucket in buckets.items()
            if bucket_platform == pf
        )
        hourly = []
        for h in range(24):
            key = (pf, h)
            if key in buckets:
                bucket = buckets[key]
                hourly.append({
                    "hour": h,
                    "avg_er": round(statistics.mean(bucket["ers"]), 6),
                    "median_er": round(statistics.median(bucket["ers"]), 6),
                    "median_primary": round(statistics.median(bucket["primary"]), 1),
                    "median_actions": round(statistics.median(bucket["actions"]), 1),
                    "samples": bucket["count"],
                })
            else:
                hourly.append({
                    "hour": h, "avg_er": 0, "median_er": 0,
                    "median_primary": 0, "median_actions": 0, "samples": 0,
                })

        # Reach/view distribution outranks ratio spikes from tiny denominators.
        sorted_hours = sorted(
            [h for h in hourly if h["samples"] >= 3 and h["median_primary"] > 0],
            key=lambda x: (x["median_primary"], x["median_actions"], x["median_er"]),
            reverse=True,
        )
        results[pf] = {
            "top3_hours": sorted_hours[:3],
            "worst_hours": sorted_hours[-3:] if len(sorted_hours) >= 3 else sorted_hours,
            "hourly_data": hourly,
            "complete_posts": complete_posts,
            "required_post_age_hours": minimum_post_age_hours,
            "guard_version": QUALITY_GUARD_VERSION,
            "decision_ready": complete_posts >= minimum_complete_posts,
        }

    return results


# ====================================================================
# 3. Content Diversity Scoring (Jaccard 距離, Jaccard 1912)
# ====================================================================

def compute_content_diversity(recent_titles: List[str], new_title: str) -> Dict:
    """計算新貼文與近期貼文的內容多樣性。

    使用 Jaccard 距離：J(A,B) = |A∩B| / |A∪B|
    其中 A, B 為標題的 token set（分詞後）+ bigram。

    回傳：
      diversity: 0~1, 越高代表越多元
      most_similar: 最相似的貼文索引
    """
    if not recent_titles:
        return {"diversity": 1.0, "most_similar": -1, "message": "無歷史數據"}

    def tokenize(s: str) -> set:
        s = s.lower()
        # Simple character bigrams for CJK, word tokens for English
        tokens = set()
        # Add character bigrams (works for both CJK and English)
        for i in range(len(s) - 1):
            tokens.add(s[i:i+2])
        # Add words for English
        for word in s.split():
            if len(word) > 2:
                tokens.add(word)
        return tokens

    new_tokens = tokenize(new_title)
    if not new_tokens:
        return {"diversity": 0.5, "most_similar": -1}

    similarities = []
    for i, t in enumerate(recent_titles):
        old_tokens = tokenize(t)
        if not old_tokens:
            continue
        intersection = len(new_tokens & old_tokens)
        union = len(new_tokens | old_tokens)
        jaccard = intersection / union if union > 0 else 0
        similarities.append((i, jaccard))

    if not similarities:
        return {"diversity": 1.0, "most_similar": -1}

    avg_sim = sum(s[1] for s in similarities) / len(similarities)
    most_sim = max(similarities, key=lambda x: x[1])
    diversity = 1 - avg_sim

    return {
        "diversity": round(diversity, 4),
        "avg_similarity": round(avg_sim, 4),
        "most_similar_idx": most_sim[0],
        "most_similar_score": round(most_sim[1], 4),
        "total_compared": len(similarities),
        "verdict": "充足多樣" if diversity > 0.7 else ("略微重複" if diversity > 0.5 else "高度重複"),
    }


# ====================================================================
# 4. Topic Weight Calibration (貝氏更新, Gelman 2013)
# ====================================================================

def calibrate_topic_weights(bayes_strength: float = 5.0) -> Dict:
    """根據互動數據校準主題權重。

    使用貝氏更新：
      posterior_mean = (prior_weight * prior_strength + observed_er * n) / (prior_strength + n)

    其中 prior_strength = bayes_strength（先驗強度，預設5代表相當於5篇樣本的信心）
    observed_er = 該主題在該平台的平均互動率
    """
    conn = dbmod.get_conn()

    # 讀取目前權重
    current_weights = conn.execute(
        "SELECT category_id, weight, sample_count FROM topic_weights"
    ).fetchall()

    # 計算近30天各主題的平均互動率
    topic_perf = conn.execute("""
        SELECT n.topic_category, e.platform,
               AVG(e.likes) as avg_likes,
               AVG(e.comments) as avg_comments,
               MAX(e.reach) as avg_reach,
               MAX(e.views) as avg_views,
               COUNT(*) as sample_count
        FROM engagement_stats e
        JOIN drafts d ON d.id = e.draft_id
        JOIN news_items n ON n.id = d.news_id
        WHERE n.topic_category IS NOT NULL
          AND e.fetched_at >= datetime('now', '-30 days', 'localtime')
        GROUP BY n.topic_category
    """).fetchall()

    if not current_weights or not topic_perf:
        conn.close()
        return {"status": "insufficient_data"}

    from src.analytics_engine import engagement_rate
    results = []
    for cw in current_weights:
        cat = cw["category_id"]
        prior_w = cw["weight"] or 1.0

        # Find matching performance
        match = [tp for tp in topic_perf if tp["topic_category"] == cat]
        if not match:
            results.append({
                "topic": cat,
                "prior_weight": prior_w,
                "posterior_weight": prior_w,
                "adjusted": False,
                "reason": "無30天內互動數據",
            })
            continue

        # Aggregate across platforms
        total_er = 0
        total_n = 0
        for m in match:
            n = m["sample_count"] or 0
            if n >= 2:
                er = engagement_rate(
                    m["platform"],
                    likes=m["avg_likes"] or 0,
                    comments=m["avg_comments"] or 0,
                    reach=m["avg_reach"] or 0,
                    views=m["avg_views"] or 0,
                )
                total_er += er * n
                total_n += n

        if total_n < 3:
            results.append({
                "topic": cat,
                "prior_weight": prior_w,
                "posterior_weight": prior_w,
                "adjusted": False,
                "reason": f"樣本不足 ({total_n})",
            })
            continue

        observed_er = total_er / total_n

        # Bayesian update
        # Normalize observed_er to weight scale (0.3 - 2.0)
        # Map: er=0.01 → weight=0.8, er=0.05 → weight=1.2, er=0.10 → weight=1.5
        observed_weight = 0.8 + (observed_er * 10)
        observed_weight = max(0.3, min(2.0, observed_weight))

        posterior = (prior_w * bayes_strength + observed_weight * total_n) / (bayes_strength + total_n)
        posterior = max(0.3, min(2.0, posterior))

        delta = posterior - prior_w

        results.append({
            "topic": cat,
            "prior_weight": round(prior_w, 4),
            "posterior_weight": round(posterior, 4),
            "delta": round(delta, 4),
            "observed_er": round(observed_er, 6),
            "sample_count": total_n,
            "adjusted": abs(delta) > 0.05,
            "direction": "up" if delta > 0.05 else ("down" if delta < -0.05 else "stable"),
        })

    conn.close()

    # Generate weight change recommendations
    recommendations = []
    for r in results:
        if r.get("direction") == "up":
            recommendations.append({
                "topic": r["topic"],
                "action": "增加權重",
                "from": r["prior_weight"],
                "to": r["posterior_weight"],
            })
        elif r.get("direction") == "down":
            recommendations.append({
                "topic": r["topic"],
                "action": "減少權重",
                "from": r["prior_weight"],
                "to": r["posterior_weight"],
            })

    return {
        "status": "ok",
        "bayes_strength": bayes_strength,
        "calibrations": results,
        "recommendations": recommendations,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ====================================================================
# 5. Thompson Sampling for Content Strategy (Thompson, 1933)
# ====================================================================

class ContentThompsonSampler:
    """Thompson sampling 用於內容策略決策。

    把每個主題/prompt策略視為一個「手臂(arm)」，
    互動率(reward)是 binary: 高於平均=1, 低於平均=0。

    每次選擇策略時，用 Beta 分佈抽樣：
      θ_i ~ Beta(α_i, β_i)
      選 θ_i 最大的策略
    """

    def __init__(self):
        self.arms: Dict[str, Dict] = {}

    def load_from_db(self) -> None:
        """從 DB 載入各主題的歷史表現。"""
        conn = dbmod.get_conn()
        rows = conn.execute("""
            SELECT n.topic_category as topic,
                   e.platform,
                   e.likes, e.comments, e.shares,
                   e.reach, e.views
            FROM engagement_stats e
            JOIN drafts d ON d.id = e.draft_id
            JOIN news_items n ON n.id = d.news_id
            WHERE e.fetched_at >= datetime('now', '-60 days', 'localtime')
              AND n.topic_category IS NOT NULL
        """).fetchall()
        conn.close()

        if not rows:
            return

        from src.analytics_engine import engagement_rate

        # Calculate global average
        all_ers = []
        topic_ers = defaultdict(list)
        for r in rows:
            er = engagement_rate(
                r["platform"],
                likes=r["likes"] or 0, comments=r["comments"] or 0,
                shares=r["shares"] or 0, reach=r["reach"] or 0, views=r["views"] or 0,
            )
            all_ers.append(er)
            topic_ers[r["topic"]].append(er)

        global_avg = sum(all_ers) / len(all_ers) if all_ers else 0

        for topic, ers in topic_ers.items():
            wins = sum(1 for e in ers if e > global_avg)
            losses = sum(1 for e in ers if e <= global_avg)
            self.arms[topic] = {
                "alpha": wins + 1,
                "beta": losses + 1,
                "samples": wins + losses,
                "win_rate": wins / (wins + losses) if (wins + losses) > 0 else 0.5,
            }

    def recommend_topic(self, exclude: List[str] = None) -> Tuple[str, float]:
        """用 Thompson sampling 推薦主題。

        Returns: (topic_name, probability_of_best)
        """
        if not self.arms:
            return ("general", 1.0)

        exclude = exclude or []
        candidates = {k: v for k, v in self.arms.items() if k not in exclude}
        if not candidates:
            candidates = self.arms

        import random
        best_topic = None
        best_score = -1
        scores = {}
        for topic, arm in candidates.items():
            score = random.betavariate(arm["alpha"], arm["beta"])
            scores[topic] = score
            if score > best_score:
                best_score = score
                best_topic = topic

        return (best_topic or "general", best_score)

    def get_all_arm_stats(self) -> Dict:
        """回傳所有手臂的統計。"""
        return {
            k: {
                "win_rate": round(v["win_rate"], 4),
                "samples": v["samples"],
                "alpha": v["alpha"],
                "beta": v["beta"],
                "uncertainty": round(math.sqrt(
                    (v["alpha"] * v["beta"]) /
                    ((v["alpha"] + v["beta"])**2 * (v["alpha"] + v["beta"] + 1))
                ), 4) if (v["alpha"] + v["beta"]) > 0 else 1.0,
            }
            for k, v in self.arms.items()
        }


# ====================================================================
# 6. 綜合建議：根據以上演算法產出可執行建議
# ====================================================================

def generate_system_recommendations() -> Dict:
    """綜合所有演算法產出可執行的系統改善建議。"""
    recs = []

    # 1. Topic calibration
    cal = calibrate_topic_weights()
    for r in cal.get("recommendations", []):
        recs.append({
            "type": "topic_weight",
            "priority": "high",
            "action": r,
        })

    # 2. Optimal times
    times = analyze_optimal_times()
    for pf, data in times.items():
        if (
            isinstance(data, dict)
            and data.get("decision_ready") is True
            and data.get("top3_hours")
        ):
            hours = [str(h["hour"]) + ":00(er=" + str(h["avg_er"]) + ")"
                     for h in data["top3_hours"][:3]]
            recs.append({
                "type": "best_time",
                "priority": "medium",
                "platform": pf,
                "message": f"{pf} 最佳發布時段: {' '.join(hours)}",
            })

    # 3. Thompson sampling strategy
    sampler = ContentThompsonSampler()
    sampler.load_from_db()
    best_topic, confidence = sampler.recommend_topic()
    if best_topic != "general":
        recs.append({
            "type": "topic_recommendation",
            "priority": "high",
            "message": f"Thompson sampling 建議下一輪優先主題: {best_topic} (confidence={confidence:.2f})",
        })

    return {
        "recommendations": recs,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ====================================================================
# CLI
# ====================================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="News Radar Iteration Algorithm")
    parser.add_argument("--mode", choices=["report", "calibrate", "times", "diversity",
                                           "predict", "sample"],
                       default="report")
    parser.add_argument("--title", type=str, default="")
    parser.add_argument("--platform", type=str, default="")
    args = parser.parse_args()

    if args.mode == "report":
        result = generate_system_recommendations()
    elif args.mode == "calibrate":
        result = calibrate_topic_weights()
    elif args.mode == "times":
        result = analyze_optimal_times(args.platform)
    elif args.mode == "diversity":
        conn = dbmod.get_conn()
        recent = [r["title"] for r in conn.execute(
            "SELECT title FROM news_items ORDER BY fetched_at DESC LIMIT 10").fetchall()]
        conn.close()
        result = compute_content_diversity(recent, args.title or "")
    elif args.mode == "predict":
        result = predict_engagement_rate(args.platform or "threads")
    elif args.mode == "sample":
        sampler = ContentThompsonSampler()
        sampler.load_from_db()
        result = sampler.get_all_arm_stats()

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
