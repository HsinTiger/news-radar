"""Phase 8.20 Step 4：驗證 src/reflector_topic.py 的 math + DB IO。

兩類測試：
  A. Pure 函式（無 DB 依賴）— engagement 公式、中位數、normalized_delta、
     apply_weight_update guard rails、detect_trend。
  B. End-to-end：synthetic engagement_stats 餵進 in-memory SQLite，跑
     run_backprop，驗 topic_weights / topic_weight_history / reflection_events
     三張表寫入正確。
"""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

# 讓測試能 import src.reflector_topic（不經過 pydantic-依賴的 src.db）
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.reflector_topic import (  # noqa: E402
    EngagementRow,
    compute_engagement_score,
    compute_platform_medians,
    compute_category_platform_stats,
    compute_category_delta,
    apply_weight_update,
    detect_trend,
    run_backprop,
    format_markdown_report,
    ETA,
    MAX_WEEKLY_DELTA,
    MIN_SAMPLES_TOTAL,
    MIN_SAMPLES_PER_PLATFORM,
    GLOBAL_WEIGHT_FLOOR,
    GLOBAL_WEIGHT_CEIL,
)


# ======================================================================
# A. 純函式測試
# ======================================================================

def test_engagement_score_facebook():
    # Hsin 拍板：likes + 2*comments + 3*shares + 0.01*reach
    row = EngagementRow(
        draft_id="d", news_id="n", topic_category="ai_model", platform="facebook",
        likes=100, comments=20, shares=5, reach=1000,
    )
    expected = 100 + 2 * 20 + 3 * 5 + 0.01 * 1000  # = 165
    assert compute_engagement_score(row) == expected


def test_engagement_score_instagram_uses_saves():
    # IG 比 FB 多 1.5*saves
    row = EngagementRow(
        draft_id="d", news_id="n", topic_category="ai_model", platform="instagram",
        likes=100, comments=20, shares=5, saves=10, reach=1000,
    )
    expected = 100 + 2 * 20 + 3 * 5 + 1.5 * 10 + 0.01 * 1000  # = 180
    assert compute_engagement_score(row) == expected


def test_engagement_score_threads_uses_reposts_quotes_views():
    # Threads：likes + 2*replies + 3*reposts + 1.5*quotes + 0.005*views
    row = EngagementRow(
        draft_id="d", news_id="n", topic_category="ai_model", platform="threads",
        likes=100, replies=20, reposts=5, quotes=3, views=2000,
    )
    expected = 100 + 2 * 20 + 3 * 5 + 1.5 * 3 + 0.005 * 2000  # = 169.5
    assert abs(compute_engagement_score(row) - expected) < 1e-9


def test_engagement_score_unknown_platform_returns_zero():
    row = EngagementRow(
        draft_id="d", news_id="n", topic_category="ai_model", platform="tiktok",
        likes=999, comments=999,
    )
    assert compute_engagement_score(row) == 0.0


def test_platform_medians_basic():
    rows = [
        EngagementRow("d1", "n1", "ai_model", "facebook", likes=10),
        EngagementRow("d2", "n2", "ai_model", "facebook", likes=20),
        EngagementRow("d3", "n3", "ai_model", "facebook", likes=30),
    ]
    m = compute_platform_medians(rows)
    assert m["facebook"] == 20.0
    assert m["instagram"] == 0.0  # 空 list 回 0


def test_platform_medians_empty_rows():
    assert compute_platform_medians([]) == {"facebook": 0.0, "instagram": 0.0, "threads": 0.0}


def test_category_platform_stats_skips_low_sample_platform():
    # 2 筆（< MIN_SAMPLES_PER_PLATFORM=3）→ normalized_delta 應為 None
    rows = [
        EngagementRow("d1", "n1", "ai_model", "facebook", likes=100),
        EngagementRow("d2", "n2", "ai_model", "facebook", likes=200),
    ]
    medians = compute_platform_medians(rows)
    stats = compute_category_platform_stats(rows, medians)
    s = stats[("ai_model", "facebook")]
    assert s.samples == 2
    assert s.normalized_delta is None


def test_category_platform_stats_computes_delta_at_threshold():
    # 3 筆剛好達 MIN_SAMPLES_PER_PLATFORM；且類別中位數 > 平台中位數
    rows = [
        EngagementRow("d1", "n1", "ai_model", "facebook", likes=200),
        EngagementRow("d2", "n2", "ai_model", "facebook", likes=300),
        EngagementRow("d3", "n3", "ai_model", "facebook", likes=400),
        # 加幾筆別的類別拉低全站中位數
        EngagementRow("d4", "n4", "other", "facebook", likes=50),
        EngagementRow("d5", "n5", "other", "facebook", likes=60),
        EngagementRow("d6", "n6", "other", "facebook", likes=70),
    ]
    medians = compute_platform_medians(rows)
    stats = compute_category_platform_stats(rows, medians)
    s = stats[("ai_model", "facebook")]
    assert s.samples == 3
    # ai_model 中位數 300；全站中位數 (50,60,70,200,300,400) → (70+200)/2 = 135
    # normalized_delta = 300/135 - 1 ≈ 1.2222
    assert s.normalized_delta is not None
    assert s.normalized_delta > 1.0


def test_category_delta_averages_three_platforms():
    rows = []
    # ai_model 在 FB / IG / Threads 各 3 筆，表現明顯優於全站中位數
    # 每平台：ai_model 中位數 500，全站中位數 ≈ 310 → norm_delta ≈ 0.61
    # （全站含 ai_model 自己：sorted [80,100,120,500,500,500]，median=(120+500)/2=310）
    for p in ("facebook", "instagram", "threads"):
        rows += [
            EngagementRow(f"d1{p}", "n", "ai_model", p, likes=500),
            EngagementRow(f"d2{p}", "n", "ai_model", p, likes=500),
            EngagementRow(f"d3{p}", "n", "ai_model", p, likes=500),
            # 非 ai_model 樣本撐底
            EngagementRow(f"d4{p}", "n", "other", p, likes=80),
            EngagementRow(f"d5{p}", "n", "other", p, likes=100),
            EngagementRow(f"d6{p}", "n", "other", p, likes=120),
        ]
    medians = compute_platform_medians(rows)
    stats = compute_category_platform_stats(rows, medians)
    raw_delta, total_samples, per_plat = compute_category_delta("ai_model", stats)
    # 三平台 norm_delta 相等且 > 0；平均還是 > 0.3
    assert total_samples == 9  # 3 × 3 平台
    assert raw_delta > 0.3, f"expected raw_delta > 0.3, got {raw_delta}"


def test_category_delta_skips_sparse_platform():
    # ai_model 在 FB 樣本 2（< 3），在 IG/Threads 各 3 → 平均只算後兩者
    rows = []
    rows += [
        EngagementRow("d1", "n", "ai_model", "facebook", likes=500),
        EngagementRow("d2", "n", "ai_model", "facebook", likes=500),
    ]
    for p in ("instagram", "threads"):
        rows += [
            EngagementRow(f"d1{p}", "n", "ai_model", p, likes=50),
            EngagementRow(f"d2{p}", "n", "ai_model", p, likes=50),
            EngagementRow(f"d3{p}", "n", "ai_model", p, likes=50),
        ]
    # 塞非 ai_model 把各平台中位數撐起來到差不多 50
    for p in ("facebook", "instagram", "threads"):
        rows += [
            EngagementRow(f"d4{p}", "n", "other", p, likes=40),
            EngagementRow(f"d5{p}", "n", "other", p, likes=50),
            EngagementRow(f"d6{p}", "n", "other", p, likes=60),
        ]
    medians = compute_platform_medians(rows)
    stats = compute_category_platform_stats(rows, medians)
    raw_delta, total, per_plat = compute_category_delta("ai_model", stats)
    # FB 樣本 2 → 其 norm_delta 為 None；不算進平均
    # 平均只看 IG/Threads，該兩平台 ai_model 中位數 50 vs 平台中位數 ≈ 50 → ~0
    assert abs(raw_delta) < 0.3
    assert "facebook" in per_plat  # 仍收在 per_plat（為了 report 顯示）
    assert per_plat["facebook"].normalized_delta is None


def test_apply_weight_update_low_samples_skips():
    # total=4 < 5 → skip
    new_w, delta, reason = apply_weight_update(
        old_weight=1.70, raw_delta=0.5, total_samples=4, category_id="ai_model"
    )
    assert new_w == 1.70
    assert delta == 0.0
    assert reason == "low_samples"


def test_apply_weight_update_applies_eta():
    # samples=10, raw_delta=+0.5 → proposed = 1.0 × (1 + 0.1 × 0.5) = 1.05
    new_w, delta, reason = apply_weight_update(
        old_weight=1.00, raw_delta=0.5, total_samples=10, category_id="ai_model"
    )
    assert reason is None
    assert abs(new_w - 1.05) < 1e-9
    assert abs(delta - 0.05) < 1e-9


def test_apply_weight_update_weekly_delta_clip_positive():
    # 巨大 raw_delta → 單週 clip 到 +0.3
    new_w, delta, reason = apply_weight_update(
        old_weight=1.00, raw_delta=100.0, total_samples=10, category_id="ai_model"
    )
    assert reason is None
    assert abs(delta - MAX_WEEKLY_DELTA) < 1e-9
    assert abs(new_w - 1.30) < 1e-9


def test_apply_weight_update_weekly_delta_clip_negative():
    new_w, delta, reason = apply_weight_update(
        old_weight=1.50, raw_delta=-100.0, total_samples=10, category_id="ai_model"
    )
    assert reason is None
    assert abs(delta + MAX_WEEKLY_DELTA) < 1e-9
    assert abs(new_w - 1.20) < 1e-9


def test_apply_weight_update_global_floor():
    # 舊權重 0.32 + 負 delta 會到 0.02 → clip 到 0.3
    new_w, delta, reason = apply_weight_update(
        old_weight=0.32, raw_delta=-100.0, total_samples=10, category_id="other"
    )
    # 單週 clip 會先把 delta 限到 -0.3 → 0.32 - 0.3 = 0.02 → 再被 global floor clip 到 0.3
    assert reason is None
    assert new_w == GLOBAL_WEIGHT_FLOOR
    # applied_delta 是實際 new - old = 0.3 - 0.32 = -0.02（非 -0.3）
    assert abs(delta + 0.02) < 1e-9


def test_apply_weight_update_global_ceil():
    new_w, delta, reason = apply_weight_update(
        old_weight=1.80, raw_delta=100.0, total_samples=10, category_id="ai_model"
    )
    assert reason is None
    assert new_w == GLOBAL_WEIGHT_CEIL  # 1.80 + 0.3 = 2.10 → clip 到 2.00
    assert abs(delta - 0.20) < 1e-9


def test_detect_trend_all_up():
    assert detect_trend("ai_model", [0.05, 0.02, 0.08]) == "up"


def test_detect_trend_all_down():
    assert detect_trend("ai_model", [-0.05, -0.02, -0.08]) == "down"


def test_detect_trend_mixed():
    assert detect_trend("ai_model", [0.05, -0.02, 0.08]) == "noise"


def test_detect_trend_insufficient_history():
    # 只 2 筆，不夠判定
    assert detect_trend("ai_model", [0.05, 0.05]) == "noise"


def test_detect_trend_ignores_tiny_values():
    # 幾乎為零的 delta 不算方向
    assert detect_trend("ai_model", [1e-12, 0.05, 0.05]) == "noise"


# ======================================================================
# B. End-to-end with synthetic SQLite
# ======================================================================

def _make_conn_with_schema() -> sqlite3.Connection:
    """建 in-memory DB + 跑 schema.sql（不經過 src.db，避免 pydantic 依賴）。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = (_ROOT / "data" / "01_harvest" / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    # Phase 8.18 migrations（schema.sql 沒包的）
    for col_ddl in [("publish_at", "TEXT"), ("queue_status", "TEXT")]:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(drafts)").fetchall()]
        if col_ddl[0] not in cols:
            conn.execute(f"ALTER TABLE drafts ADD COLUMN {col_ddl[0]} {col_ddl[1]}")
    # Phase 8.20 news_items migrations
    for col, ddl in [("topic_category", "TEXT"), ("topic_confidence", "REAL"),
                     ("topic_rationale", "TEXT"), ("weighted_score", "REAL")]:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(news_items)").fetchall()]
        if col not in cols:
            conn.execute(f"ALTER TABLE news_items ADD COLUMN {col} {ddl}")
    # seed topic_weights 最小子集
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for cat, w in [("ai_model", 1.70), ("ai_agent", 1.60), ("supply_chain", 1.40),
                   ("other", 0.70)]:
        conn.execute(
            "INSERT INTO topic_weights (category_id, display_name, weight, "
            "last_updated_at, update_reason, sample_count) VALUES (?,?,?,?,?,?)",
            (cat, cat, w, now, "initial_seed", 0),
        )
    conn.commit()
    return conn


def _seed_post(conn, draft_id, news_id, category, published_days_ago=5):
    """建一則 news_item + draft（最小欄位）。"""
    from datetime import datetime, timezone, timedelta
    pub = (datetime.now(timezone.utc) - timedelta(days=published_days_ago)
           ).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO news_items (id, feed_name, feed_tier, url, title, "
        "published_at, fetched_at, status, topic_category) "
        "VALUES (?, 'test', 'primary', ?, ?, ?, ?, 'published', ?)",
        (news_id, f"https://example/{news_id}", f"title {news_id}",
         pub, pub, category),
    )
    conn.execute(
        "INSERT INTO drafts (id, news_id, persona_version, generated_at, status) "
        "VALUES (?, ?, '1.1', ?, 'published')",
        (draft_id, news_id, pub),
    )


def _seed_engagement(conn, draft_id, platform, **stats):
    """append-only 塞一筆 engagement_stats。"""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO engagement_stats (draft_id, platform, platform_post_id, "
        "fetched_at, likes, comments, shares, saves, reposts, quotes, replies, "
        "views, reach) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (draft_id, platform, f"{platform}_{draft_id}_pid", now,
         stats.get("likes", 0), stats.get("comments", 0), stats.get("shares", 0),
         stats.get("saves", 0), stats.get("reposts", 0), stats.get("quotes", 0),
         stats.get("replies", 0), stats.get("views", 0), stats.get("reach", 0)),
    )


def test_e2e_dryrun_no_db_writes():
    conn = _make_conn_with_schema()
    # seed 一組 ai_model 表現超強的資料（3 平台各 4 筆 → 樣本夠）
    for i in range(4):
        _seed_post(conn, f"d_ai_{i}", f"n_ai_{i}", "ai_model")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(conn, f"d_ai_{i}", p,
                             likes=500, comments=100, shares=30, saves=20,
                             reposts=10, quotes=5, replies=50, views=10000, reach=20000)
    # 再塞一批 other 中等表現，把平台中位數撐起來
    for i in range(6):
        _seed_post(conn, f"d_o_{i}", f"n_o_{i}", "other")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(conn, f"d_o_{i}", p,
                             likes=50, comments=5, shares=1, saves=1,
                             reposts=0, quotes=0, replies=2, views=500, reach=1000)
    conn.commit()

    before_weights = {r[0]: r[2] for r in conn.execute(
        "SELECT category_id, display_name, weight FROM topic_weights"
    ).fetchall()}

    result = run_backprop(conn, lookback_days=30, dry_run=True)

    # dry_run=True → weights 不該被改
    after_weights = {r[0]: r[2] for r in conn.execute(
        "SELECT category_id, display_name, weight FROM topic_weights"
    ).fetchall()}
    assert before_weights == after_weights

    # history 也不該有新 row
    hist_count = conn.execute(
        "SELECT COUNT(*) FROM topic_weight_history"
    ).fetchone()[0]
    assert hist_count == 0

    # result 裡 ai_model 的 new_weight 應該被提高（applied_delta > 0）
    ai = [u for u in result.updates if u.category_id == "ai_model"][0]
    assert ai.applied_delta > 0, f"ai_model should get +Δ, got {ai.applied_delta}"
    assert ai.total_samples == 12  # 4 posts × 3 platforms


def test_e2e_real_run_updates_db():
    conn = _make_conn_with_schema()
    # ai_model 超強
    for i in range(4):
        _seed_post(conn, f"d_ai_{i}", f"n_ai_{i}", "ai_model")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(conn, f"d_ai_{i}", p,
                             likes=500, comments=100, shares=30, saves=20,
                             reposts=10, quotes=5, replies=50, views=10000, reach=20000)
    for i in range(6):
        _seed_post(conn, f"d_o_{i}", f"n_o_{i}", "other")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(conn, f"d_o_{i}", p,
                             likes=50, comments=5, shares=1, saves=1,
                             reposts=0, quotes=0, replies=2, views=500, reach=1000)
    conn.commit()

    before_w = conn.execute(
        "SELECT weight FROM topic_weights WHERE category_id='ai_model'"
    ).fetchone()[0]

    run_backprop(conn, lookback_days=30, dry_run=False)

    after_w = conn.execute(
        "SELECT weight FROM topic_weights WHERE category_id='ai_model'"
    ).fetchone()[0]
    assert after_w > before_w, f"expected ai_model weight to rise, {before_w} → {after_w}"

    # history 應至少有 4 筆（4 個類別都被觀察，不論是否有 update）
    hist_count = conn.execute(
        "SELECT COUNT(*) FROM topic_weight_history"
    ).fetchone()[0]
    assert hist_count == 4

    # reflection_events 應有一筆
    ev_count = conn.execute(
        "SELECT COUNT(*) FROM reflection_events"
    ).fetchone()[0]
    assert ev_count == 1


def test_e2e_skips_categories_without_samples():
    """沒發文的類別（ai_agent / supply_chain）應 skip，不該被 UPDATE。"""
    conn = _make_conn_with_schema()
    # 只有 ai_model 有樣本，其它類別零樣本
    for i in range(4):
        _seed_post(conn, f"d_{i}", f"n_{i}", "ai_model")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(conn, f"d_{i}", p, likes=200, comments=20)
    conn.commit()

    before_agent = conn.execute(
        "SELECT weight FROM topic_weights WHERE category_id='ai_agent'"
    ).fetchone()[0]

    run_backprop(conn, lookback_days=30, dry_run=False)

    after_agent = conn.execute(
        "SELECT weight FROM topic_weights WHERE category_id='ai_agent'"
    ).fetchone()[0]
    assert before_agent == after_agent


def test_format_markdown_report_contains_expected_sections():
    conn = _make_conn_with_schema()
    # 最簡 seed
    for i in range(3):
        _seed_post(conn, f"d_{i}", f"n_{i}", "ai_model")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(conn, f"d_{i}", p, likes=100)
    conn.commit()
    result = run_backprop(conn, lookback_days=30, dry_run=True)
    md = format_markdown_report(result)

    # 基本 section
    assert "Topic Weight Back-Prop" in md
    assert "平台基準線" in md
    assert "類別權重變動" in md
    assert "每類別 × 3 平台分解" in md
    assert "`ai_model`" in md
