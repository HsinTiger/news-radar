"""
News Radar · Topic Weight Back-Prop Reflector（Phase 8.20 Step 4）
==================================================================
每週一跑一次：把過去 N 天的 engagement 回饋到 topic_weights 表。

**系統設計原則**：
  - 純函式核心 + DB IO 薄殼（為了測試性）
    → compute_engagement_score / median / normalized_delta 全都 pure，可獨立測
    → run_backprop 才碰 SQL
  - 冪等：同一天跑兩次，第二次會把 history 裡今天那筆 UPDATE 覆蓋（以 recorded_at 為 PK 不行，用 (category_id, DATE(recorded_at)) 當 dedup key 由呼叫端決定——我們的 launchd/GH Actions cron 一週一次，這邊不做 dedup，信任 cron 排程）
  - dry_run 模式：計算全部、印報告、不寫 DB（給 Hsin 人工驗算用）
  - guard rails 完全對齊 Hsin 2026-04-21 拍板 spec：
      · 樣本數（跨平台合計）< 5 的類別不調
      · 單週變動 abs(new - old) > 0.3 時 clip
      · 連續 3 週 delta 同方向才視為趨勢（report-only，不改 math）
      · other 類別永遠不自動降到 0.3 以下
      · 全域 clip 到 [0.3, 2.0]

**engagement 公式**（Hsin 拍板）：
  FB：      likes + 2*comments + 3*shares + 0.01*reach
  IG：      likes + 2*comments + 3*shares + 1.5*saves + 0.01*reach
  Threads： likes + 2*replies + 3*reposts + 1.5*quotes + 0.005*views

**正規化**：每平台分開用該平台『全站中位數』當基準線，避免 Threads 天生
exposure 高就把 Threads 上表現好的類別過度獎勵。

**類別 delta**：三平台 normalized_delta 的平均（某平台樣本 < 3 → 該平台不算
進平均；三平台都不夠 → 該類整個跳過）。

用法：
    # 乾跑（不寫 DB，印 markdown）
    python -m src.reflector_topic --dry-run

    # 實跑（週一 06:00 TW 自動觸發；也能手動 rerun）
    python -m src.reflector_topic

    # 回溯過去 60 天
    python -m src.reflector_topic --lookback-days 60

—— 2026-04-21 overnight, Cowork Claude
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


# ---------- Orchestrator ----------

def run_backprop(
    conn: sqlite3.Connection,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    dry_run: bool = False,
) -> BackpropResult:
    """完整一輪 back-prop（fetch → compute → (UPDATE|dry) → return result）。"""
    rows = _fetch_engagement_rows(conn, lookback_days)
    platform_medians = compute_platform_medians(rows)
    stats = compute_category_platform_stats(rows, platform_medians)
    weights_now = _fetch_current_weights(conn)

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
        _write_updates(conn, result)

    return result


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
    lines.append("_自動產出 by `src/reflector_topic.py` (Phase 8.20 Step 4)_")
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
    return 0


if __name__ == "__main__":
    sys.exit(_main())
