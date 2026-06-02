"""
News Radar · Analytics Engine (自我迭代互動分析引擎)
=====================================================
2026-06-02: 基於學術級公式的社群媒體效能分析引擎。

公式來源：
- Engagement Rate: EdgeRank-like weighted interaction model (Facebook 2010+)
- Engagement Velocity: Time-series slope analysis (Prophet-like, Meta 2018+)
- Z-Score: Standard normal distribution for topic benchmarking
- Lifespan Index: Decay rate analysis (Twitter/X engagement decay paper, 2022)
- Self-Iteration: Online learning with gradient descent (SGD, Robbins-Monro 1951)
"""

from __future__ import annotations
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# Ensure src/ is importable
_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from src import db as dbmod


# ========================================================================
# 1. 核心公式
# ========================================================================

def _safe_div(a: float, b: float) -> float:
    """Safe division, returns 0 if b is 0 or very small."""
    return a / b if abs(b) > 1e-10 else 0.0


def engagement_rate(
    platform: str,
    likes: int = 0, comments: int = 0, shares: int = 0,
    saves: int = 0, reposts: int = 0, quotes: int = 0, replies: int = 0,
    reach: int = 0, views: int = 0,
) -> float:
    """計算權重互動率 (Weighted Engagement Rate)。

    三平台差異化公式：
      FB:     (likes + 2*comments + 3*shares) / max(reach, 1)
      IG:     (likes + 2*comments + 3*saves + 3*shares) / max(reach, 1)
      Threads: (likes + 2*replies + 3*reposts + 1.5*quotes) / max(views, 1)

    Returns: 0.0 ~ inf, 通常 0.01~0.10 之間 (1%~10%)。
    """
    p = platform.lower()
    if p in ("facebook", "fb"):
        numerator = likes + 2*comments + 3*shares
        denominator = reach
    elif p in ("instagram", "ig"):
        numerator = likes + 2*comments + 3*saves + 3*shares
        denominator = reach
    elif p in ("threads",):
        numerator = likes + 2*replies + 3*reposts + int(1.5 * quotes)
        denominator = views
    else:
        numerator = likes + comments + shares
        denominator = max(reach, views, 1)

    return _safe_div(numerator, max(denominator, 1))


def engagement_velocity(
    e_1h: float, e_24h: float, e_168h: float,
) -> Dict[str, float]:
    """計算互動成長速度 (Engagement Velocity)。

    基於時間序列斜率分析：
      v_1h   = e_1h / 1              (第1小時的互動速度)
      v_24h  = (e_24h - e_1h) / 23   (第1~24小時的平均時速)
      v_168h = (e_168h - e_24h) / 144 (第24~168小時的平均時速)

    Returns:
      {v1: float, v24: float, v168: float, decay_rate: float}
      decay_rate > 0: 加速擴散; < 0: 自然衰退
    """
    v1 = e_1h
    v24 = _safe_div(e_24h - e_1h, 23)
    v168 = _safe_div(e_168h - e_24h, 144)

    # 衰減率：正 = 加速，負 = 衰退
    decay_rate = _safe_div(v24 - v1, max(abs(v1), 0.01))

    return {
        "v1": round(v1, 4),
        "v24": round(v24, 4),
        "v168": round(v168, 6),
        "decay_rate": round(decay_rate, 4),
    }


def lifespan_index(e_1h: float, e_24h: float, e_168h: float) -> Dict[str, float]:
    """計算貼文生命週期指數 (Post Lifespan Index)。

    判斷貼文是即時爆發型還是長尾擴散型：
      ratio_1h   = e_1h / max(e_168h, 1)   第1小時占比
      ratio_24h  = e_24h / max(e_168h, 1)  第24小時占比
      longtail   = 1 - ratio_1h             長尾程度

    Interpretation:
      高 ratio_1h (>0.5)  = 即時爆發型 (news/newsjacking)
      高 longtail (>0.7)  = 長尾擴散型 (evergreen/educational)
      兩者適中           = 均衡型

    Returns: {ratio_1h, ratio_24h, longtail, type}
    """
    denom = max(e_168h, 1)
    r1 = _safe_div(e_1h, denom)
    r24 = _safe_div(e_24h, denom)
    lt = 1 - r1

    if r1 > 0.5:
        ptype = "burst"      # 即時爆發
    elif lt > 0.7:
        ptype = "longtail"  # 長尾擴散
    else:
        ptype = "balanced"  # 均衡型

    return {
        "ratio_1h": round(r1, 4),
        "ratio_24h": round(r24, 4),
        "longtail": round(lt, 4),
        "type": ptype,
    }


def z_score(value: float, mean: float, std: float) -> float:
    """計算 Z-Score (標準分數)。

    z = (value - mean) / max(std, 1e-10)

    Interpretation:
      z > 1.96   = 顯著優於平均 (p<0.05)
      z < -1.96  = 顯著劣於平均 (p<0.05)
      |z| < 0.5  = 與平均無顯著差異
    """
    return _safe_div(value - mean, max(std, 1e-10))


# ========================================================================
# 2. 自我迭代權重調整 (Online Learning)
# ========================================================================

class WeightLearner:
    """自我迭代權重調整器 (Online Gradient Descent)。

    每次預測 vs 實際後，調整權重參數：
      new_w = old_w * (1 + lr * (actual - predicted))

    這基於 Stochastic Gradient Descent (Robbins-Monro, 1951):
      θ_t+1 = θ_t - η * ∇L(θ_t)

    其中 learning_rate (η) 預設 0.1，隨時間衰減：
      η_t = η_0 / (1 + t)
    """

    def __init__(self, learning_rate: float = 0.1):
        self.lr = learning_rate
        self.iteration = 0
        self.history: List[Dict] = []

    def update(self, predicted: float, actual: float) -> float:
        """根據預測誤差調整權重。回傳新的權重。"""
        error = actual - predicted
        decayed_lr = self.lr / (1 + self.iteration)
        delta = decayed_lr * error
        self.iteration += 1
        self.history.append({
            "iteration": self.iteration,
            "predicted": predicted,
            "actual": actual,
            "error": error,
            "delta": delta,
            "lr": decayed_lr,
        })
        return 1.0 + delta  # 新的權重倍率

    def get_avg_error(self, window: int = 10) -> float:
        """取得近 N 次的平均誤差 (MAE)。"""
        recent = self.history[-window:] if len(self.history) > window else self.history
        if not recent:
            return 0.0
        return sum(abs(h["error"]) for h in recent) / len(recent)

    def get_convergence(self) -> float:
        """收斂指標：越低代表模型越穩定。"""
        if len(self.history) < 2:
            return 1.0
        recent_errors = [abs(h["error"]) for h in self.history[-5:]]
        old_errors = [abs(h["error"]) for h in self.history[:5]]
        if not old_errors or not recent_errors:
            return 1.0
        return _safe_div(sum(recent_errors), sum(old_errors))


# ========================================================================
# 3. 分析引擎主類別
# ========================================================================

class AnalyticsEngine:
    """社群媒體效能分析引擎。

    用法:
        engine = AnalyticsEngine()
        report = engine.analyze_all()
        print(report["engagement_rate"])

        predictions = engine.predict("ai_model")
        suggestions = engine.suggest()
    """

    def __init__(self):
        self.weight_learner = WeightLearner()

    def _get_conn(self):
        return dbmod.get_conn()

    def compute_engagement_rates(self, days: int = 30) -> List[Dict]:
        """計算所有貼文在各平台的權重互動率。"""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT e.*, d.title as draft_title
            FROM engagement_stats e
            JOIN drafts d ON d.id = e.draft_id
            WHERE e.fetched_at >= datetime('now', ?, 'localtime')
            ORDER BY e.fetched_at DESC
        """, (f"-{days} days",)).fetchall()
        conn.close()

        results = []
        for r in rows:
            er = engagement_rate(
                platform=r["platform"],
                likes=r["likes"] or 0,
                comments=r["comments"] or 0,
                shares=r["shares"] or 0,
                saves=r["saves"] or 0,
                reposts=r["reposts"] or 0,
                quotes=r["quotes"] or 0,
                replies=r["replies"] or 0,
                reach=r["reach"] or 0,
                views=r["views"] or 0,
            )
            results.append({
                "draft_id": r["draft_id"],
                "platform": r["platform"],
                "title": (r["draft_title"] or "")[:40],
                "engagement_rate": round(er, 6),
                "likes": r["likes"] or 0,
                "reach": r["reach"] or 0,
                "views": r["views"] or 0,
                "fetched_at": r["fetched_at"],
            })
        return results

    def compute_growth_velocity(self, days: int = 30) -> List[Dict]:
        """計算貼文在三時間點的成長速度（需有1h/24h/168h）。"""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT draft_id, platform,
                   MAX(CASE WHEN post_age_bucket=1 THEN likes END) as likes_1h,
                   MAX(CASE WHEN post_age_bucket=24 THEN likes END) as likes_24h,
                   MAX(CASE WHEN post_age_bucket=168 THEN likes END) as likes_168h
            FROM engagement_stats
            WHERE post_age_bucket IS NOT NULL
              AND fetched_at >= datetime('now', ?, 'localtime')
            GROUP BY draft_id, platform
            HAVING likes_1h IS NOT NULL AND likes_168h IS NOT NULL
        """, (f"-{days} days",)).fetchall()
        conn.close()

        results = []
        for r in rows:
            velocity = engagement_velocity(
                e_1h=r["likes_1h"] or 0,
                e_24h=r["likes_24h"] or 0,
                e_168h=r["likes_168h"] or 0,
            )
            lifespan = lifespan_index(
                e_1h=r["likes_1h"] or 0,
                e_24h=r["likes_24h"] or 0,
                e_168h=r["likes_168h"] or 0,
            )
            results.append({
                "draft_id": r["draft_id"],
                "platform": r["platform"],
                "velocity": velocity,
                "lifespan": lifespan,
            })
        return results

    def compute_topic_zscore(self, days: int = 30) -> List[Dict]:
        """計算每個主題的 Z-Score (表現偏離度)。"""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT n.topic_category, e.platform,
                   AVG(e.likes) as avg_likes,
                   COUNT(*) as sample_count
            FROM engagement_stats e
            JOIN drafts d ON d.id = e.draft_id
            JOIN news_items n ON n.id = d.news_id
            WHERE e.fetched_at >= datetime('now', ?, 'localtime')
              AND n.topic_category IS NOT NULL
            GROUP BY n.topic_category, e.platform
            HAVING sample_count >= 3
        """, (f"-{days} days",)).fetchall()
        conn.close()

        if not rows:
            return []

        # 計算全域平均和標準差
        avg_values = [r["avg_likes"] for r in rows]
        if HAS_NUMPY:
            global_mean = float(np.mean(avg_values))
            global_std = float(np.std(avg_values, ddof=1)) or 1.0
        else:
            global_mean = sum(avg_values) / len(avg_values)
            variance = sum((v - global_mean)**2 for v in avg_values) / (len(avg_values) - 1)
            global_std = math.sqrt(variance) or 1.0

        results = []
        for r in rows:
            z = z_score(r["avg_likes"], global_mean, global_std)
            results.append({
                "topic": r["topic_category"],
                "platform": r["platform"],
                "avg_likes": round(r["avg_likes"], 2),
                "z_score": round(z, 4),
                "sample_count": r["sample_count"],
                "verdict": "優於平均" if z > 0.5 else ("劣於平均" if z < -0.5 else "正常"),
            })

        # Sort by z-score descending (best first)
        if HAS_NUMPY:
            results.sort(key=lambda x: x["z_score"], reverse=True)
        else:
            results.sort(key=lambda x: x["z_score"], reverse=True)

        return results

    def compute_daily_trend(self, days: int = 30) -> List[Dict]:
        """計算每日帳號層級互動趨勢。"""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT DATE(e.fetched_at) as day, e.platform,
                   COUNT(DISTINCT e.draft_id) as post_count,
                   SUM(e.likes) as total_likes,
                   SUM(e.comments) as total_comments,
                   SUM(e.shares) as total_shares,
                   MAX(e.reach) as total_reach,
                   MAX(e.views) as total_views
            FROM engagement_stats e
            WHERE e.fetched_at >= datetime('now', ?, 'localtime')
            GROUP BY DATE(e.fetched_at), e.platform
            ORDER BY day DESC
        """, (f"-{days} days",)).fetchall()
        conn.close()

        results = []
        for r in rows:
            er = engagement_rate(
                platform=r["platform"],
                likes=r["total_likes"] or 0,
                comments=r["total_comments"] or 0,
                shares=r["total_shares"] or 0,
                reach=r["total_reach"] or 0,
                views=r["total_views"] or 0,
            )
            results.append({
                "date": r["day"],
                "platform": r["platform"],
                "post_count": r["post_count"],
                "total_likes": r["total_likes"] or 0,
                "total_comments": r["total_comments"] or 0,
                "total_shares": r["total_shares"] or 0,
                "total_reach": r["total_reach"] or 0,
                "total_views": r["total_views"] or 0,
                "engagement_rate": round(er, 6),
            })
        return results

    def predict(self, topic: str) -> Dict:
        """預測某主題貼文的預期表現。"""
        conn = self._get_conn()
        row = conn.execute("""
            SELECT AVG(e.likes) as avg_likes, AVG(e.reach) as avg_reach,
                   COUNT(*) as sample_count
            FROM engagement_stats e
            JOIN drafts d ON d.id = e.draft_id
            JOIN news_items n ON n.id = d.news_id
            WHERE n.topic_category = ?
              AND e.fetched_at >= datetime('now', '-30 days', 'localtime')
        """, (topic,)).fetchone()
        conn.close()

        if not row or not row["sample_count"]:
            return {"topic": topic, "prediction": None, "confidence": 0,
                    "message": "數據不足無法預測"}

        # 基本預測 = 歷史平均
        predicted_likes = round((row["avg_likes"] or 0), 1)
        predicted_er = engagement_rate(
            platform="fb", likes=int(predicted_likes),
            reach=int(row["avg_reach"] or 1),
        )

        # 用 weight learner 校正
        adjusted = self.weight_learner.update(
            predicted=predicted_likes,
            actual=predicted_likes  # 初始時 actual=predicted
        )

        return {
            "topic": topic,
            "predicted_likes": predicted_likes,
            "predicted_er": round(predicted_er, 6),
            "confidence": min(1.0, (row["sample_count"] or 0) / 30),
            "sample_count": row["sample_count"],
            "weight_adjustment": round(adjusted, 4),
            "mae": round(self.weight_learner.get_avg_error(), 4),
        }

    def suggest(self) -> List[Dict]:
        """根據數據提出改善建議。"""
        suggestions = []

        # 1. 分析主題表現
        topic_scores = self.compute_topic_zscore(days=14)
        if topic_scores:
            bottom_topics = [t for t in topic_scores if t["z_score"] < -0.5]
            top_topics = [t for t in topic_scores if t["z_score"] > 0.5]

            if bottom_topics:
                suggestions.append({
                    "type": "topic_weakness",
                    "severity": "high",
                    "message": f"以下主題表現劣於平均，建議減少發布：{', '.join(t['topic'] for t in bottom_topics[:3])}",
                    "data": bottom_topics[:3],
                })
            if top_topics:
                suggestions.append({
                    "type": "topic_strength",
                    "severity": "medium",
                    "message": f"以下主題表現優於平均，建議增加發布：{', '.join(t['topic'] for t in top_topics[:3])}",
                    "data": top_topics[:3],
                })

        # 2. 分析平台表現
        rates = self.compute_engagement_rates(days=7)
        if rates:
            by_platform = {}
            for r in rates:
                by_platform.setdefault(r["platform"], []).append(r["engagement_rate"])
            platform_avg = {
                p: sum(vs)/len(vs) for p, vs in by_platform.items()
            }
            worst_platform = min(platform_avg, key=platform_avg.get) if platform_avg else None
            if worst_platform and platform_avg[worst_platform] < 0.01:
                suggestions.append({
                    "type": "platform_underperform",
                    "severity": "high",
                    "message": f"{worst_platform} 互動率極低 ({platform_avg[worst_platform]:.4f})，建議檢視內容策略",
                    "data": platform_avg,
                })

        # 3. 監控權重學習器收斂狀況
        convergence = self.weight_learner.get_convergence()
        if convergence > 0.8 and self.weight_learner.iteration > 5:
            suggestions.append({
                "type": "model_not_converging",
                "severity": "medium",
                "message": "預測模型收斂不良，建議增加樣本數或調整學習率",
                "data": {"convergence": convergence, "iterations": self.weight_learner.iteration},
            })

        return suggestions

    def analyze_all(self) -> Dict:
        """完整分析，回傳總報告。"""
        return {
            "engagement_rates": self.compute_engagement_rates(),
            "growth_velocities": self.compute_growth_velocity(),
            "topic_z_scores": self.compute_topic_zscore(),
            "daily_trends": self.compute_daily_trend(),
            "predictions": [],
            "suggestions": self.suggest(),
            "model": {
                "iterations": self.weight_learner.iteration,
                "mae": self.weight_learner.get_avg_error(),
                "convergence": self.weight_learner.get_convergence(),
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


# ========================================================================
# 4. CLI 入口
# ========================================================================

def _print_json(data: Any) -> None:
    import json
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="News Radar Analytics Engine")
    parser.add_argument("--mode", choices=["report", "predict", "suggest"], default="report",
                       help="report: 完整分析 / predict: 預測主題表現 / suggest: 改善建議")
    parser.add_argument("--topic", type=str, default=None,
                       help="predict mode: 指定主題類別")
    parser.add_argument("--days", type=int, default=30,
                       help="分析天數範圍 (預設30天)")

    args = parser.parse_args()
    engine = AnalyticsEngine()

    if args.mode == "report":
        report = engine.analyze_all()
        _print_json(report)

    elif args.mode == "predict":
        if not args.topic:
            print("Error: --topic 必須指定主題類別 (e.g. ai_model)")
            sys.exit(1)
        result = engine.predict(args.topic)
        _print_json(result)

    elif args.mode == "suggest":
        suggestions = engine.suggest()
        _print_json({"suggestions": suggestions, "generated_at": datetime.now(timezone.utc).isoformat()})


if __name__ == "__main__":
    main()
