"""
News Radar · Topic Weight Back-Prop Reflector
==================================================================
每週一跑一次：把過去 N 天的 engagement 回饋到 topic_weights 表。

**Lineage**: originally `src/reflector_topic.py` (Phase 8.20 Step 4,
2026-04-21). Relocated here as part of Phase 9 Item 3 (2026-04-27)
when the unified-reflector substrate (Items 1, 1.5, 2, Amendments B+C)
landed. **Math is byte-identical to the legacy module.** What changed:

  1. Sample-count gate + per-platform aggregate proposal evidence
     are sourced from `v_topic_engagement_x_platform` (Phase 9 Item 1).
     Per-row engagement (needed by the median+normalize math) is still
     fetched from base tables — see audit "view-coverage gap" note for
     why the view alone cannot drive the math today.
  2. Every category that crosses the sample-count gate writes a
     `proposals.jsonl` entry via `src.reflector.proposals.write_proposal`
     (Item 2).
  3. Branching:
        - non-pinned + |delta| <  0.10  → AUTO-DEPLOY
              direct UPDATE on `topic_weights.weight` AND
              `mark_deployed(fire_id)` so the lineage row carries
              `deployed_at` immediately. Hsin can audit retroactively.
        - non-pinned + |delta| >= 0.10  → PROPOSAL-ONLY
              jsonl entry only, `boss_attention_required=True`,
              no auto-deploy.
        - boss-pinned (any delta)       → PROPOSAL-ONLY (Item 8 gate)
              **TODO(phase-9-item-8)**: queries `topic_weights.boss_pinned`
              once Item 8 adds the column. Until then ALL categories are
              treated as not-pinned (no boss-pinned categories exist in
              production today; documented in this file at the call site).
  4. After a cron run that wrote ≥1 proposal (auto-deploy or
     proposal-only), the orchestrator triggers `scripts/push_state.sh`
     (Amendment B 708ed93) to propagate the `data/05_reflect/proposals/`
     dir + DB to the state branch. Skipped on dry-run.

**系統設計原則**（unchanged from Phase 8.20）：
  - 純函式核心 + DB IO 薄殼（為了測試性）
  - dry_run 模式：計算全部、印報告、不寫 DB / 不寫 proposals
  - guard rails 完全對齊 Hsin 2026-04-21 拍板 spec（math constants below）

**engagement 公式**（Hsin 拍板，未動）：
  FB：      likes + 2*comments + 3*shares + 0.01*reach
  IG：      likes + 2*comments + 3*shares + 1.5*saves + 0.01*reach
  Threads： likes + 2*replies + 3*reposts + 1.5*quotes + 0.005*views

用法：
    # 乾跑（不寫 DB / 不寫 proposals，印 markdown）
    python -m src.reflector.topic --dry-run

    # 實跑（週一 06:00 TW 自動觸發；也能手動 rerun）
    python -m src.reflector.topic

    # 回溯過去 60 天
    python -m src.reflector.topic --lookback-days 60

Spec  : PM_Radar/specs/phase_9_implementation_plan.md §3 Item 3
Canon : PM_Radar/roadmap/phase_9_unified_reflector.md
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 延後 import db / taxonomy — 讓純計算函式可以獨立被 unit test import
# (e.g. test_reflector_topic 不想 pull pydantic)

# ---------- 常數（Hsin 拍板）----------
ETA = 0.1                        # learning rate（溫和）
MAX_WEEKLY_DELTA = 0.30          # 單週絕對變動上限
MIN_SAMPLES_TOTAL = 5            # 跨平台合計 < 5 → 整個類跳過
MIN_SAMPLES_PER_PLATFORM = 3     # 某平台樣本 < 3 → 該平台不算進平均
GLOBAL_WEIGHT_FLOOR = 0.30       # 下限（含 other）
GLOBAL_WEIGHT_CEIL = 2.00        # 上限
TREND_CONSECUTIVE_WEEKS = 3      # 連續 N 週同方向才標 trend
DEFAULT_LOOKBACK_DAYS = 30

PLATFORMS = ("facebook", "instagram", "threads")

# ---------- Phase 9 Item 3 constants ----------
# Auto-deploy threshold: |applied_delta| strictly less than this is
# eligible for auto-deploy on non-pinned categories. ≥ this magnitude
# becomes proposal-only (boss_attention_required=True).
AUTO_DEPLOY_DELTA_THRESHOLD = 0.10

# Confidence cutoff for proposal evidence: HIGH if total samples meets
# this bar (about 4× the existing per-platform threshold of 3, ~12 total
# across 3 platforms — i.e. each platform is materially represented).
# Below this, MED. The math's MIN_SAMPLES_TOTAL=5 floor still gates
# whether we propose anything at all; this is a finer-grained signal
# attached to the proposal evidence so reviewers can scan confidence.
HIGH_CONFIDENCE_SAMPLE_THRESHOLD = 12


# ---------- 資料結構 ----------

@dataclass(frozen=True)
class EngagementRow:
    """單一貼文 × 單一平台的最新 engagement 快照（JOIN 後的一列）。"""
    draft_id: str
    news_id: str
    topic_category: str
    platform: str
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    reposts: int = 0
    quotes: int = 0
    replies: int = 0
    views: int = 0
    reach: int = 0


@dataclass
class CategoryPlatformStats:
    """（類別, 平台）聚合結果。"""
    category_id: str
    platform: str
    samples: int
    median_score: float
    normalized_delta: Optional[float]  # 樣本不足時為 None


@dataclass
class CategoryUpdate:
    """某類別本輪計算後的結果（即使 skipped 也會留，方便 report）。"""
    category_id: str
    display_name: str
    old_weight: float
    new_weight: float                # skipped 時 == old_weight
    raw_delta: float                 # 未 clip 前的 category_delta（三平台平均）
    applied_delta: float             # 實際 new - old（clip 後）
    total_samples: int
    per_platform: Dict[str, CategoryPlatformStats] = field(default_factory=dict)
    skipped_reason: Optional[str] = None  # 'low_samples' / None
    trend: str = "noise"             # 'up' / 'down' / 'noise'（連續 3 週同方向才 up/down）


@dataclass
class BackpropResult:
    ran_at: str
    lookback_days: int
    dry_run: bool
    total_samples_all: int
    platform_medians: Dict[str, float]
    updates: List[CategoryUpdate]

    def to_summary_json(self) -> str:
        """給 reflection_events.signals_summary 用。"""
        return json.dumps({
            "lookback_days": self.lookback_days,
            "total_samples": self.total_samples_all,
            "platform_medians": self.platform_medians,
            "categories": [
                {
                    "id": u.category_id,
                    "old": round(u.old_weight, 4),
                    "new": round(u.new_weight, 4),
                    "delta": round(u.applied_delta, 4),
                    "samples": u.total_samples,
                    "skipped": u.skipped_reason,
                    "trend": u.trend,
                }
                for u in self.updates
            ],
        }, ensure_ascii=False)


# ---------- 純計算函式（無 DB IO，可獨立測）----------

def compute_engagement_score(row: EngagementRow) -> float:
    """套 Hsin 拍板的 per-platform 公式。未知平台回 0.0。"""
    p = row.platform
    if p == "facebook":
        return (row.likes
                + 2 * row.comments
                + 3 * row.shares
                + 0.01 * row.reach)
    if p == "instagram":
        return (row.likes
                + 2 * row.comments
                + 3 * row.shares
                + 1.5 * row.saves
                + 0.01 * row.reach)
    if p == "threads":
        return (row.likes
                + 2 * row.replies
                + 3 * row.reposts
                + 1.5 * row.quotes
                + 0.005 * row.views)
    return 0.0


def _safe_median(values: List[float]) -> float:
    """空 list 回 0；避免 statistics.median 炸。"""
    if not values:
        return 0.0
    return float(statistics.median(values))


def compute_platform_medians(rows: List[EngagementRow]) -> Dict[str, float]:
    """每平台一個中位數（該平台全站）。"""
    by_platform: Dict[str, List[float]] = {p: [] for p in PLATFORMS}
    for r in rows:
        if r.platform in by_platform:
            by_platform[r.platform].append(compute_engagement_score(r))
    return {p: _safe_median(v) for p, v in by_platform.items()}


def compute_category_platform_stats(
    rows: List[EngagementRow],
    platform_medians: Dict[str, float],
) -> Dict[Tuple[str, str], CategoryPlatformStats]:
    """對每個 (category, platform) 算 samples + 該組中位數 + normalized_delta。"""
    bucket: Dict[Tuple[str, str], List[float]] = {}
    for r in rows:
        if r.platform not in PLATFORMS:
            continue
        key = (r.topic_category, r.platform)
        bucket.setdefault(key, []).append(compute_engagement_score(r))

    out: Dict[Tuple[str, str], CategoryPlatformStats] = {}
    for (cat, platform), scores in bucket.items():
        samples = len(scores)
        cat_med = _safe_median(scores)
        plat_med = platform_medians.get(platform, 0.0)
        if samples < MIN_SAMPLES_PER_PLATFORM or plat_med <= 0:
            norm_delta = None
        else:
            norm_delta = cat_med / plat_med - 1.0
        out[(cat, platform)] = CategoryPlatformStats(
            category_id=cat,
            platform=platform,
            samples=samples,
            median_score=cat_med,
            normalized_delta=norm_delta,
        )
    return out


def compute_category_delta(
    category_id: str,
    stats_by_key: Dict[Tuple[str, str], CategoryPlatformStats],
) -> Tuple[float, int, Dict[str, CategoryPlatformStats]]:
    """回 (raw_category_delta, total_samples, per_platform_stats_dict)

    raw_category_delta = mean(三平台 normalized_delta)；
    樣本 < 3 的平台不算進平均。
    若三平台都不夠 → raw_delta = 0（呼叫端會搭配 total_samples 判斷 skip）。
    """
    per_platform: Dict[str, CategoryPlatformStats] = {}
    deltas: List[float] = []
    total_samples = 0
    for p in PLATFORMS:
        s = stats_by_key.get((category_id, p))
        if s is None:
            continue
        per_platform[p] = s
        total_samples += s.samples
        if s.normalized_delta is not None:
            deltas.append(s.normalized_delta)
    raw_delta = (sum(deltas) / len(deltas)) if deltas else 0.0
    return raw_delta, total_samples, per_platform


def apply_weight_update(
    old_weight: float,
    raw_delta: float,
    total_samples: int,
    category_id: str,
) -> Tuple[float, float, Optional[str]]:
    """回 (new_weight, applied_delta, skipped_reason)。

    應用以下 guard rails：
      - 樣本 < MIN_SAMPLES_TOTAL → skip，回 old_weight
      - raw update = old × (1 + η × raw_delta)
      - 單週變動 abs > MAX_WEEKLY_DELTA 時 clip
      - 全域 clip 到 [GLOBAL_WEIGHT_FLOOR, GLOBAL_WEIGHT_CEIL]
        （other 共用此底線；spec 明確說 other 不自動降到 0.3 以下，正是此底線）
    """
    if total_samples < MIN_SAMPLES_TOTAL:
        return old_weight, 0.0, "low_samples"

    proposed = old_weight * (1.0 + ETA * raw_delta)
    delta = proposed - old_weight

    # 單週穩定性護欄
    if abs(delta) > MAX_WEEKLY_DELTA:
        delta = MAX_WEEKLY_DELTA if delta > 0 else -MAX_WEEKLY_DELTA
    new_weight = old_weight + delta

    # 全域護欄
    new_weight = max(GLOBAL_WEIGHT_FLOOR, min(GLOBAL_WEIGHT_CEIL, new_weight))
    applied_delta = new_weight - old_weight
    return new_weight, applied_delta, None


def detect_trend(
    category_id: str,
    history_deltas: List[float],
) -> str:
    """給 history 中該類別最近 N 筆 delta（含本週），判定 trend。
    需要 TREND_CONSECUTIVE_WEEKS 筆同號（非零）才算 trend。
    """
    recent = history_deltas[-TREND_CONSECUTIVE_WEEKS:]
    if len(recent) < TREND_CONSECUTIVE_WEEKS:
        return "noise"
    signs = {(+1 if d > 1e-9 else (-1 if d < -1e-9 else 0)) for d in recent}
    if signs == {+1}:
        return "up"
    if signs == {-1}:
        return "down"
    return "noise"


# ---------- DB IO（只在這層碰 SQL）----------

def _fetch_engagement_rows(
    conn: sqlite3.Connection,
    lookback_days: int,
) -> List[EngagementRow]:
    """取過去 N 天『已發布且有 engagement_stats』的 posts。

    每個 (draft_id, platform) 只取最新一筆 engagement（engagement_stats 是
    append-only 時序資料）。JOIN 回 news_items 拿 topic_category。
    """
    sql = """
        WITH latest AS (
            SELECT es.draft_id, es.platform, MAX(es.fetched_at) AS latest_at
              FROM engagement_stats es
              JOIN drafts d ON d.id = es.draft_id
              JOIN news_items n ON n.id = d.news_id
             WHERE es.fetched_at >= datetime('now', ?)
               AND n.topic_category IS NOT NULL
               AND n.topic_category != ''
             GROUP BY es.draft_id, es.platform
        )
        SELECT d.id AS draft_id, n.id AS news_id, n.topic_category,
               es.platform,
               COALESCE(es.likes, 0)    AS likes,
               COALESCE(es.comments, 0) AS comments,
               COALESCE(es.shares, 0)   AS shares,
               COALESCE(es.saves, 0)    AS saves,
               COALESCE(es.reposts, 0)  AS reposts,
               COALESCE(es.quotes, 0)   AS quotes,
               COALESCE(es.replies, 0)  AS replies,
               COALESCE(es.views, 0)    AS views,
               COALESCE(es.reach, 0)    AS reach
          FROM engagement_stats es
          JOIN latest l ON l.draft_id = es.draft_id
                       AND l.platform = es.platform
                       AND l.latest_at = es.fetched_at
          JOIN drafts d ON d.id = es.draft_id
          JOIN news_items n ON n.id = d.news_id
         WHERE n.topic_category IS NOT NULL
           AND n.topic_category != ''
    """
    window = f"-{int(lookback_days)} days"
    rows: List[EngagementRow] = []
    for r in conn.execute(sql, (window,)).fetchall():
        rows.append(EngagementRow(
            draft_id=r["draft_id"] if hasattr(r, "keys") else r[0],
            news_id=r["news_id"] if hasattr(r, "keys") else r[1],
            topic_category=r["topic_category"] if hasattr(r, "keys") else r[2],
            platform=r["platform"] if hasattr(r, "keys") else r[3],
            likes=r["likes"] if hasattr(r, "keys") else r[4],
            comments=r["comments"] if hasattr(r, "keys") else r[5],
            shares=r["shares"] if hasattr(r, "keys") else r[6],
            saves=r["saves"] if hasattr(r, "keys") else r[7],
            reposts=r["reposts"] if hasattr(r, "keys") else r[8],
            quotes=r["quotes"] if hasattr(r, "keys") else r[9],
            replies=r["replies"] if hasattr(r, "keys") else r[10],
            views=r["views"] if hasattr(r, "keys") else r[11],
            reach=r["reach"] if hasattr(r, "keys") else r[12],
        ))
    return rows


def _fetch_current_weights(conn: sqlite3.Connection) -> Dict[str, Tuple[float, str]]:
    """{category_id: (weight, display_name)}"""
    rows = conn.execute(
        "SELECT category_id, display_name, weight FROM topic_weights"
    ).fetchall()
    return {
        (r["category_id"] if hasattr(r, "keys") else r[0]):
        (float(r["weight"] if hasattr(r, "keys") else r[2]),
         r["display_name"] if hasattr(r, "keys") else r[1])
        for r in rows
    }


def _fetch_recent_history_deltas(
    conn: sqlite3.Connection,
    category_id: str,
    n: int,
) -> List[float]:
    """拉出該類別最近 n 筆 history 的 delta（時間由舊到新），用於 trend 判定。"""
    rows = conn.execute(
        "SELECT delta FROM topic_weight_history "
        "WHERE category_id = ? AND delta IS NOT NULL "
        "ORDER BY recorded_at DESC LIMIT ?",
        (category_id, n),
    ).fetchall()
    # reversed → 舊到新
    return [
        float(r["delta"] if hasattr(r, "keys") else r[0])
        for r in reversed(rows)
    ]


def _write_updates(
    conn: sqlite3.Connection,
    result: BackpropResult,
) -> None:
    """UPDATE topic_weights + INSERT topic_weight_history + INSERT reflection_events。
    非 dry_run 才呼叫。"""
    now_iso = result.ran_at
    for u in result.updates:
        # 有變才 UPDATE；skipped 類別也要 INSERT 一筆 history=0 表示『看過但沒動』
        if u.applied_delta != 0.0 and u.skipped_reason is None:
            conn.execute(
                "UPDATE topic_weights SET weight = ?, last_updated_at = ?, "
                "update_reason = 'back_prop', last_delta = ?, "
                "sample_count = sample_count + ? "
                "WHERE category_id = ?",
                (u.new_weight, now_iso, u.applied_delta,
                 u.total_samples, u.category_id),
            )
        # 不論是否 skip，都 append 一筆 history（方便 trend 判定 + 審計）
        conn.execute(
            "INSERT INTO topic_weight_history "
            "(category_id, recorded_at, weight_before, weight_after, "
            " update_reason, delta, samples_in_window, rationale) "
            "VALUES (?, ?, ?, ?, 'back_prop', ?, ?, ?)",
            (u.category_id, now_iso, u.old_weight, u.new_weight,
             u.applied_delta, u.total_samples,
             u.skipped_reason or f"trend={u.trend}"),
        )

    # reflection_events：留一筆 append-only log
    status = "skipped_low_samples" if result.total_samples_all < MIN_SAMPLES_TOTAL else "completed"
    conn.execute(
        "INSERT INTO reflection_events "
        "(ran_at, signals_summary, samples_used, patch_markdown, rationale, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now_iso, result.to_summary_json(), result.total_samples_all,
         None, f"topic_backprop lookback={result.lookback_days}d", status),
    )
    conn.commit()


# ---------- Phase 9 Item 3 helpers ----------

def _fetch_view_aggregates(
    conn: sqlite3.Connection,
) -> Dict[str, Dict[str, Optional[float]]]:
    """Read per-topic aggregate engagement metrics + sample_count from
    `v_topic_engagement_x_platform` (Phase 9 Item 1).

    Returns ``{topic_category: {fb_avg_likes_30d, ig_avg_likes_30d,
    th_avg_likes_30d, fb_avg_comments_30d, ig_avg_comments_30d,
    th_avg_replies_30d, sample_count}}``.

    Used to populate the proposal evidence's ``metrics`` field — the
    auditable per-platform numbers that drove each category's decision.

    NOTE — view-coverage observation (audit-flagged for pm-agent):
    The view exposes only avg-likes (and a thin slice of comments/replies)
    per platform plus an aggregate sample_count. The legacy median+
    normalize math operates on per-row engagement scores using the full
    Hsin-pinned formula (likes + 2c + 3s + 0.01r etc.), which the view
    cannot reproduce from its current columns. Until the view is
    extended (or the math is reframed in terms of avg-likes alone),
    Item 3 sources the math inputs from base tables via
    `_fetch_engagement_rows` and uses the view ONLY for proposal-evidence
    metrics + a defensive sample_count cross-check. Documented in
    `audits/2026-04-27_phase9_item3_reflector_topic_refactor.md`.

    Idempotent + read-only. Returns {} if the view is missing (older DBs).
    """
    try:
        cur = conn.execute(
            """
            SELECT topic_category,
                   fb_avg_likes_30d, ig_avg_likes_30d, th_avg_likes_30d,
                   fb_avg_comments_30d, ig_avg_comments_30d,
                   th_avg_replies_30d,
                   sample_count
              FROM v_topic_engagement_x_platform
            """
        )
    except sqlite3.OperationalError:
        # View not present (e.g. minimal test fixture). Caller treats
        # absent aggregates as "no view evidence" — math still runs.
        return {}

    out: Dict[str, Dict[str, Optional[float]]] = {}
    for r in cur.fetchall():
        # Tuple-or-Row tolerant access (matches the existing fetch helper
        # style elsewhere in this module).
        def _g(key: str, idx: int):
            return r[key] if hasattr(r, "keys") else r[idx]
        cat = _g("topic_category", 0)
        if not cat:
            continue
        out[cat] = {
            "fb_avg_likes_30d":     _g("fb_avg_likes_30d", 1),
            "ig_avg_likes_30d":     _g("ig_avg_likes_30d", 2),
            "th_avg_likes_30d":     _g("th_avg_likes_30d", 3),
            "fb_avg_comments_30d":  _g("fb_avg_comments_30d", 4),
            "ig_avg_comments_30d":  _g("ig_avg_comments_30d", 5),
            "th_avg_replies_30d":   _g("th_avg_replies_30d", 6),
            "sample_count":         _g("sample_count", 7),
        }
    return out


def _is_boss_pinned(conn: sqlite3.Connection, category_id: str) -> bool:
    """Check whether a topic category is boss-pinned.

    Phase 9 Item 8 (2026-04-28): reads `topic_weights.boss_pinned` column.
    Defensive PRAGMA-based check so the code gracefully handles older DBs
    where the column may not exist yet (returns False).

    Boss-pinned categories cannot auto-deploy weight changes; they must
    go through the proposal-only path with explicit boss review. This
    prevents engagement-driven back-prop from systematically demoting
    categories that Hsin manually scoped in via boss-driven expansion.

    See spec: PM_Radar/roadmap/phase_9_unified_reflector.md §9
    """
    try:
        cols = {
            row[1] if not hasattr(row, "keys") else row["name"]
            for row in conn.execute("PRAGMA table_info(topic_weights)")
        }
    except sqlite3.OperationalError:
        return False
    if "boss_pinned" not in cols:
        return False
    try:
        row = conn.execute(
            "SELECT boss_pinned FROM topic_weights WHERE category_id = ?",
            (category_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    if row is None:
        return False
    val = row[0] if not hasattr(row, "keys") else row["boss_pinned"]
    return bool(val)


def _classify_branch(
    is_pinned: bool,
    applied_delta: float,
) -> str:
    """Return one of:
        'auto_deploy'    — non-pinned + |delta| < AUTO_DEPLOY_DELTA_THRESHOLD
        'proposal_only'  — non-pinned + |delta| >= AUTO_DEPLOY_DELTA_THRESHOLD
        'pinned'         — pinned (any delta) — proposal-only with
                           boss_attention_required=True
    Pure function; safe to unit-test without DB.
    """
    if is_pinned:
        return "pinned"
    if abs(applied_delta) < AUTO_DEPLOY_DELTA_THRESHOLD:
        return "auto_deploy"
    return "proposal_only"


def _confidence_level(total_samples: int) -> str:
    """Bucket sample-count into HIGH/MED for proposal evidence.

    Anything below MIN_SAMPLES_TOTAL has already been skipped by
    `apply_weight_update`, so this only sees skips != 'low_samples'.
    """
    if total_samples >= HIGH_CONFIDENCE_SAMPLE_THRESHOLD:
        return "HIGH"
    return "MED"


def _build_proposal_payload(
    update: "CategoryUpdate",
    view_metrics: Optional[Dict[str, Optional[float]]],
    branch: str,
) -> dict:
    """Construct the proposal dict for `proposals.write_proposal`.

    `branch` ∈ {'auto_deploy', 'proposal_only', 'pinned'}.
    boss_attention_required is True for non-auto-deploy branches.
    """
    metrics: Dict[str, object] = {
        "raw_delta":      round(update.raw_delta, 6),
        "applied_delta":  round(update.applied_delta, 6),
        "total_samples":  update.total_samples,
        "old_weight":     round(update.old_weight, 6),
        "new_weight":     round(update.new_weight, 6),
        "trend":          update.trend,
    }
    if view_metrics:
        # View-sourced per-platform aggregates (Phase 9 Item 1 substrate).
        # Marshalled defensively in case the view delivered NULL columns
        # (no engagement on a platform → SQLite AVG returns NULL).
        for k, v in view_metrics.items():
            if v is None:
                metrics[f"view_{k}"] = None
            elif isinstance(v, (int, float)):
                metrics[f"view_{k}"] = round(float(v), 6)
            else:
                metrics[f"view_{k}"] = v

    boss_attention = (branch != "auto_deploy")
    return {
        "analyzer":       "topic",
        "platform":       "all",  # topic weights apply across platforms
        "proposal_type":  "adjust_weight",
        "evidence": {
            "sample_ids": [],  # not naturally available from aggregate path
            "metrics":    metrics,
            "confidence": _confidence_level(update.total_samples),
        },
        "action": {
            "target_config":  "topic_weights",
            "field":          update.category_id,
            "current_value":  round(update.old_weight, 6),
            "proposed_value": round(update.new_weight, 6),
        },
        "boss_attention_required": boss_attention,
    }


# ---------- Orchestrator ----------

def run_backprop(
    conn: sqlite3.Connection,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    dry_run: bool = False,
    *,
    write_proposals: bool = True,
    proposals_db_path: Optional[Path] = None,
    proposals_base_dir: Optional[Path] = None,
) -> BackpropResult:
    """完整一輪 back-prop（fetch → compute → (UPDATE|dry) → return result）.

    Phase 9 Item 3 additions:
      * For every CategoryUpdate that crossed the sample gate (i.e.
        ``skipped_reason is None``), classify as auto_deploy /
        proposal_only / pinned and write a `proposals.jsonl` entry via
        `src.reflector.proposals.write_proposal`.
      * Auto-deploy entries also call `mark_deployed(fire_id)` so the
        lineage row carries `deployed_at` immediately.
      * Skipped categories (low_samples) generate NO proposal — there's
        nothing to propose.
      * dry_run=True OR write_proposals=False suppresses all proposal
        writes (test-mode escape hatch).

    The ``proposals_db_path`` / ``proposals_base_dir`` kwargs exist
    solely to let unit tests redirect the proposal write-path to a
    tmp_path; production callers omit both.

    Returns ``BackpropResult`` with `applied_fire_ids` populated (auto-
    deploy fire_ids only — proposal-only fire_ids are still recorded in
    the jsonl via write_proposal but the orchestrator doesn't need them
    in the result struct for further action).
    """
    rows = _fetch_engagement_rows(conn, lookback_days)
    platform_medians = compute_platform_medians(rows)
    stats = compute_category_platform_stats(rows, platform_medians)
    weights_now = _fetch_current_weights(conn)
    view_aggs = _fetch_view_aggregates(conn)

    # taxonomy 只是用來確保 display_name 齊全；若 DB 還沒 seed 就跳過
    try:
        from src.topic_taxonomy import taxonomy_as_dict
        tax = taxonomy_as_dict()
    except Exception:
        tax = {}

    updates: List[CategoryUpdate] = []
    for cat_id, (old_w, display) in weights_now.items():
        raw_delta, total, per_platform = compute_category_delta(cat_id, stats)
        new_w, applied, skipped = apply_weight_update(old_w, raw_delta, total, cat_id)

        # trend 需讀 history（含本輪 applied_delta 當最新一筆）
        past_deltas = _fetch_recent_history_deltas(
            conn, cat_id, TREND_CONSECUTIVE_WEEKS - 1
        )
        trend = detect_trend(cat_id, past_deltas + [applied])

        updates.append(CategoryUpdate(
            category_id=cat_id,
            display_name=display or (tax[cat_id].display_name if cat_id in tax else cat_id),
            old_weight=old_w,
            new_weight=new_w,
            raw_delta=raw_delta,
            applied_delta=applied,
            total_samples=total,
            per_platform=per_platform,
            skipped_reason=skipped,
            trend=trend,
        ))

    # 固定排序：applied_delta 絕對值大到小，然後類別 id 字母序
    updates.sort(key=lambda u: (-abs(u.applied_delta), u.category_id))

    result = BackpropResult(
        ran_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        lookback_days=lookback_days,
        dry_run=dry_run,
        total_samples_all=len(rows),
        platform_medians=platform_medians,
        updates=updates,
    )

    if not dry_run:
        # Phase 9 Item 3: proposal write + auto-deploy / proposal-only
        # branching happens BEFORE the legacy _write_updates path. The
        # legacy path is preserved for the auto-deploy categories
        # (direct UPDATE on topic_weights + history insert) — this
        # matches the spec's "auto-deploy direct UPDATE AND write a
        # proposal AND mark_deployed".
        #
        # For pinned / proposal-only categories the legacy
        # _write_updates path is suppressed (no UPDATE on
        # topic_weights), but a topic_weight_history audit row is
        # still appended below so the cron's audit trail is intact.
        if write_proposals:
            _write_proposals_and_classify(
                conn,
                result,
                view_aggs,
                proposals_db_path=proposals_db_path,
                proposals_base_dir=proposals_base_dir,
            )
        else:
            # write_proposals=False → fall through to legacy behavior
            # (auto-deploy everything that crossed the gate). Used by
            # tests + by callers that want pre-Phase-9 semantics.
            _write_updates(conn, result)

    return result


def _write_proposals_and_classify(
    conn: sqlite3.Connection,
    result: "BackpropResult",
    view_aggs: Dict[str, Dict[str, Optional[float]]],
    *,
    proposals_db_path: Optional[Path] = None,
    proposals_base_dir: Optional[Path] = None,
) -> None:
    """Phase 9 Item 3 write-path.

    For each non-skipped CategoryUpdate:
      1. Classify branch (auto_deploy / proposal_only / pinned).
      2. write_proposal(...) to jsonl + lineage.
      3. If auto_deploy: UPDATE topic_weights + mark_deployed(fire_id).
      4. Append topic_weight_history audit row regardless of branch
         (matches legacy behavior — every observation is logged).

    Also appends one reflection_events row at the end, mirroring the
    legacy _write_updates contract.
    """
    # Lazy imports (proposals module pulls in src.db, which pulls in
    # pydantic). Done here rather than at module top so the math
    # functions remain importable in minimal test environments.
    from src.reflector.proposals import write_proposal
    from src.reflector import mark_deployed

    now_iso = result.ran_at

    # ---- Phase 1: classify each category + write proposals BEFORE any
    # write to the analyzer's own conn. Reason: write_proposal opens its
    # own sqlite3 connection to the same DB file (for the lineage
    # INSERT). If we hold a write transaction on `conn` first, the
    # second connection blocks ("database is locked"). Order is:
    #   1a. classify + write_proposal (own connection, commits its
    #       lineage row independently).
    #   1b. THEN open a single batch on `conn` for topic_weights
    #       UPDATEs + topic_weight_history INSERTs + reflection_events.
    plan: List[Tuple[Optional[str], str, "CategoryUpdate"]] = []
    # plan items: (fire_id_or_None, branch_or_'skipped', update)

    for u in result.updates:
        if u.skipped_reason is not None:
            plan.append((None, "skipped", u))
            continue

        is_pinned = _is_boss_pinned(conn, u.category_id)
        branch = _classify_branch(is_pinned, u.applied_delta)
        view_metrics = view_aggs.get(u.category_id)
        payload = _build_proposal_payload(u, view_metrics, branch)

        # Best-effort proposal write. write_proposal is atomic on its
        # own (jsonl truncate-on-lineage-failure); we don't try/except
        # silently because a write failure here means the analyzer's
        # output never reaches the audit trail and that's a real bug
        # to surface.
        fire_id = write_proposal(
            payload,
            db_path=proposals_db_path,
            base_dir=proposals_base_dir,
        )
        plan.append((fire_id, branch, u))

    # ---- Phase 2: single batched mutation on `conn` for the analyzer's
    # own writes. This whole block is one transaction → one commit.
    fire_ids_for_auto: List[Tuple[str, "CategoryUpdate"]] = []
    for fire_id, branch, u in plan:
        if branch == "skipped":
            conn.execute(
                "INSERT INTO topic_weight_history "
                "(category_id, recorded_at, weight_before, weight_after, "
                " update_reason, delta, samples_in_window, rationale) "
                "VALUES (?, ?, ?, ?, 'back_prop', ?, ?, ?)",
                (u.category_id, now_iso, u.old_weight, u.new_weight,
                 u.applied_delta, u.total_samples,
                 u.skipped_reason),
            )
            continue

        if branch == "auto_deploy":
            conn.execute(
                "UPDATE topic_weights SET weight = ?, last_updated_at = ?, "
                "update_reason = 'back_prop', last_delta = ?, "
                "sample_count = sample_count + ? "
                "WHERE category_id = ?",
                (u.new_weight, now_iso, u.applied_delta,
                 u.total_samples, u.category_id),
            )
            fire_ids_for_auto.append((fire_id, u))  # type: ignore[arg-type]

        conn.execute(
            "INSERT INTO topic_weight_history "
            "(category_id, recorded_at, weight_before, weight_after, "
            " update_reason, delta, samples_in_window, rationale) "
            "VALUES (?, ?, ?, ?, 'back_prop', ?, ?, ?)",
            (u.category_id, now_iso, u.old_weight,
             u.new_weight if branch == "auto_deploy" else u.old_weight,
             u.applied_delta if branch == "auto_deploy" else 0.0,
             u.total_samples,
             f"branch={branch} trend={u.trend} fire_id={fire_id[:8] if fire_id else 'n/a'}"),
        )

    # reflection_events log (preserves legacy contract)
    status = (
        "skipped_low_samples"
        if result.total_samples_all < MIN_SAMPLES_TOTAL
        else "completed"
    )
    conn.execute(
        "INSERT INTO reflection_events "
        "(ran_at, signals_summary, samples_used, patch_markdown, rationale, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now_iso, result.to_summary_json(), result.total_samples_all,
         None, f"topic_backprop lookback={result.lookback_days}d", status),
    )

    conn.commit()

    # Post-condition + mark_deployed pass for auto-deploy lane.
    # Done AFTER commit so the topic_weights UPDATE is durable; the
    # mark_deployed call mutates the jsonl + lineage row's
    # `deployed_at` and is itself atomic.
    for fire_id, u in fire_ids_for_auto:
        # Verify the UPDATE actually landed (scoped-vdd: SELECT
        # asserts the side-effect rather than trusting the rowcount).
        row = conn.execute(
            "SELECT weight FROM topic_weights WHERE category_id = ?",
            (u.category_id,),
        ).fetchone()
        assert row is not None, (
            f"topic_weights row for {u.category_id!r} vanished after "
            "auto-deploy UPDATE; refusing to mark_deployed"
        )
        observed = float(row[0] if not hasattr(row, "keys") else row["weight"])
        assert abs(observed - u.new_weight) < 1e-9, (
            f"topic_weights.weight for {u.category_id!r} = {observed} "
            f"but expected {u.new_weight}; rolling forward to "
            "mark_deployed anyway would corrupt lineage"
        )
        mark_deployed(
            fire_id,
            db_path=proposals_db_path,
            base_dir=proposals_base_dir,
        )


# ---------- Markdown 報告 ----------

def format_markdown_report(result: BackpropResult) -> str:
    """產出 docs/topic_weight_log/YYYY-MM-DD.md 的內容。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines: List[str] = []
    lines.append(f"# Topic Weight Back-Prop · {today}")
    lines.append("")
    lines.append(f"**lookback**: {result.lookback_days} 天")
    lines.append(f"**總樣本（post × platform）**: {result.total_samples_all}")
    if result.dry_run:
        lines.append("**模式**: dry-run（未寫 DB）")
    lines.append("")

    # Platform medians
    lines.append("## 平台基準線（中位數 engagement score）")
    lines.append("")
    lines.append("| 平台 | 中位數 |")
    lines.append("|---|---|")
    for p in PLATFORMS:
        lines.append(f"| {p} | {result.platform_medians.get(p, 0.0):.2f} |")
    lines.append("")

    # 主表
    lines.append("## 類別權重變動")
    lines.append("")
    lines.append("| 類別 | 舊權重 | 新權重 | Δ | 樣本 | trend | 狀態 |")
    lines.append("|---|---:|---:|---:|---:|:-:|---|")
    for u in result.updates:
        status_cell = u.skipped_reason or "updated"
        trend_icon = {"up": "📈", "down": "📉", "noise": "—"}.get(u.trend, "—")
        lines.append(
            f"| `{u.category_id}` ({u.display_name}) "
            f"| {u.old_weight:.3f} "
            f"| {u.new_weight:.3f} "
            f"| {u.applied_delta:+.3f} "
            f"| {u.total_samples} "
            f"| {trend_icon} "
            f"| {status_cell} |"
        )
    lines.append("")

    # 每類別在 3 平台的分解
    lines.append("## 每類別 × 3 平台分解")
    lines.append("")
    lines.append("`normalized_delta = 該類該平台中位數 / 平台全站中位數 - 1`；")
    lines.append("空格 (—) 表該類該平台樣本 < 3，不算進類別平均。")
    lines.append("")
    lines.append("| 類別 | FB Δ | IG Δ | Threads Δ | 原始 category Δ |")
    lines.append("|---|---:|---:|---:|---:|")
    for u in result.updates:
        def fmt(p: str) -> str:
            s = u.per_platform.get(p)
            if s is None or s.normalized_delta is None:
                return "—"
            return f"{s.normalized_delta:+.2f} (n={s.samples})"
        lines.append(
            f"| `{u.category_id}` "
            f"| {fmt('facebook')} "
            f"| {fmt('instagram')} "
            f"| {fmt('threads')} "
            f"| {u.raw_delta:+.3f} |"
        )
    lines.append("")

    # 趨勢重點
    trend_ups = [u for u in result.updates if u.trend == "up"]
    trend_dns = [u for u in result.updates if u.trend == "down"]
    if trend_ups or trend_dns:
        lines.append("## 趨勢（連續 3 週同方向）")
        lines.append("")
        if trend_ups:
            lines.append("**📈 持續上升**:")
            for u in trend_ups:
                lines.append(f"- `{u.category_id}` — 連續 3 週正 delta")
            lines.append("")
        if trend_dns:
            lines.append("**📉 持續下降**:")
            for u in trend_dns:
                lines.append(f"- `{u.category_id}` — 連續 3 週負 delta，考慮人工覆核是否選題太窄")
            lines.append("")

    # 人工覆核建議
    suggestions: List[str] = []
    for u in result.updates:
        if u.skipped_reason == "low_samples":
            suggestions.append(
                f"- `{u.category_id}`：樣本數 {u.total_samples} < {MIN_SAMPLES_TOTAL}，"
                f"下週補更多發文或考慮關鍵字放寬"
            )
        if u.trend == "down":
            suggestions.append(
                f"- `{u.category_id}`：連三週跌，檢查選題是否誤判（分類正確嗎？選的文章夠硬嗎？）"
            )
        if u.new_weight >= 1.95:
            suggestions.append(
                f"- `{u.category_id}`：權重已觸頂 2.0，無法再往上，考慮拆子類"
            )
        if u.new_weight <= 0.35 and u.category_id != "other":
            suggestions.append(
                f"- `{u.category_id}`：權重已近地板 0.3，連續低迷；考慮降級為 other 的 sub-tag"
            )
    if suggestions:
        lines.append("## 下週建議覆核")
        lines.append("")
        lines.extend(suggestions)
        lines.append("")

    lines.append("---")
    lines.append("_自動產出 by `src/reflector/topic.py` (Phase 9 Item 3, "
                 "originally Phase 8.20 Step 4)_")
    return "\n".join(lines)


def write_markdown_report(result: BackpropResult, base_dir: Optional[Path] = None) -> Path:
    """把 markdown 報告寫到 docs/topic_weight_log/YYYY-MM-DD.md。"""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent / "docs" / "topic_weight_log"
    base_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = base_dir / f"{today}.md"
    out.write_text(format_markdown_report(result), encoding="utf-8")
    return out


# ---------- CLI ----------

def _maybe_push_state_branch(result: BackpropResult) -> None:
    """Trigger condition (Phase 9 Item 3 sub-task 5): if any auto-deploy
    or proposal-only categories produced output this run, invoke
    `scripts/push_state.sh` (Amendment B 708ed93) to propagate the
    proposals dir + DB to the state branch.

    Skipped when:
      - dry_run (no side effects to propagate)
      - no proposals were written (every category was skipped_low_samples)
      - PUSH_STATE env var is unset (e.g. local dev runs); cron sets it.

    The env-var gate matches the existing reflect_topic.yml pattern of
    only persisting state from the GitHub Actions runner; local
    invocations stay local. Failures are logged but non-fatal — the
    next cron cycle will re-attempt and the analyzer's primary output
    (jsonl + DB) is durable regardless.
    """
    import os
    import subprocess

    if result.dry_run:
        return
    proposed_count = sum(
        1 for u in result.updates if u.skipped_reason is None
    )
    if proposed_count == 0:
        return
    if os.getenv("PUSH_STATE", "0") not in {"1", "true", "yes"}:
        return

    script = Path(__file__).resolve().parents[2] / "scripts" / "push_state.sh"
    if not script.exists():
        print(f"[reflector.topic] push_state.sh not found at {script}; "
              "skipping state-branch propagation", file=sys.stderr)
        return

    try:
        # No --expect-draft assertion; the analyzer's auto-deploy
        # post-condition already verified topic_weights row was
        # updated, and write_proposal already verified jsonl+lineage.
        # push_state.sh's own sha-compare is the cross-host check.
        result_proc = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result_proc.returncode == 0:
            print("[reflector.topic] state-branch propagation OK")
        else:
            print(
                f"[reflector.topic] push_state.sh exited "
                f"{result_proc.returncode}; stderr tail:\n"
                f"{result_proc.stderr[-1000:]}",
                file=sys.stderr,
            )
    except Exception as exc:  # pragma: no cover — defensive
        print(f"[reflector.topic] push_state.sh invocation failed: {exc}",
              file=sys.stderr)


def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                        help=f"預設 {DEFAULT_LOOKBACK_DAYS}")
    parser.add_argument("--dry-run", action="store_true",
                        help="不寫 DB，只印 markdown")
    parser.add_argument("--no-report-file", action="store_true",
                        help="不寫 docs/topic_weight_log/*.md（只印 stdout）")
    args = parser.parse_args(argv)

    # 真跑才碰 db 模組（避免 pydantic 缺時 import 爆）
    from src import db as dbmod
    dbmod.init_db()
    conn = dbmod.get_conn()
    try:
        result = run_backprop(
            conn,
            lookback_days=args.lookback_days,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    md = format_markdown_report(result)
    print(md)
    if not args.no_report_file:
        path = write_markdown_report(result)
        print(f"\n[reflector] 報告寫入: {path}", file=sys.stderr)

    # Phase 9 Item 3 sub-task 5: state-branch propagation (gated).
    _maybe_push_state_branch(result)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
