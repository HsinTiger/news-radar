"""Phase 9 Item 3 (originally Phase 8.20 Step 4):
驗證 src/reflector/topic.py 的 math + DB IO + auto-deploy / proposal-only branching.

三類測試：
  A. Pure 函式（無 DB 依賴）— engagement 公式、中位數、normalized_delta、
     apply_weight_update guard rails、detect_trend、_classify_branch、
     _confidence_level。
  B. End-to-end (legacy / write_proposals=False)：synthetic engagement_stats
     餵進 in-memory SQLite，跑 run_backprop，驗 topic_weights /
     topic_weight_history / reflection_events 三張表寫入正確。Math regression.
  C. Phase 9 Item 3 — auto-deploy + proposal-only paths against jsonl +
     reflector_proposal_lineage table.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

# 讓測試能 import src.reflector.topic（不經過 pydantic-依賴的 src.db）
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.reflector.topic import (  # noqa: E402
    EngagementRow,
    compute_engagement_score,
    compute_platform_medians,
    compute_category_platform_stats,
    compute_category_delta,
    apply_weight_update,
    detect_trend,
    run_backprop,
    format_markdown_report,
    _classify_branch,
    _confidence_level,
    _is_boss_pinned,
    AUTO_DEPLOY_DELTA_THRESHOLD,
    HIGH_CONFIDENCE_SAMPLE_THRESHOLD,
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


def test_e2e_real_run_legacy_path_updates_db():
    """Legacy fallback path: write_proposals=False → direct UPDATE on
    topic_weights via _write_updates (pre-Phase-9 behavior).

    Validates math regression: even with the new dispatch wrapper, when
    legacy semantics are explicitly requested, the topic_weights table
    receives the same update it always did, history gets one row per
    category, and a single reflection_events row is written.
    """
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

    run_backprop(conn, lookback_days=30, dry_run=False, write_proposals=False)

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


def test_e2e_skips_categories_without_samples_legacy_path():
    """沒發文的類別（ai_agent / supply_chain）應 skip，不該被 UPDATE。
    Legacy fallback path."""
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

    run_backprop(conn, lookback_days=30, dry_run=False, write_proposals=False)

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


# ======================================================================
# C. Phase 9 Item 3 — auto-deploy + proposal-only branching
# ======================================================================

def test_classify_branch_pure():
    """Pure-function gate logic. No DB."""
    # Non-pinned, small delta → auto_deploy
    assert _classify_branch(False, 0.05) == "auto_deploy"
    assert _classify_branch(False, -0.05) == "auto_deploy"
    # Non-pinned, large delta → proposal_only
    assert _classify_branch(False, AUTO_DEPLOY_DELTA_THRESHOLD) == "proposal_only"
    assert _classify_branch(False, AUTO_DEPLOY_DELTA_THRESHOLD + 0.01) == "proposal_only"
    assert _classify_branch(False, -0.15) == "proposal_only"
    # Pinned with any delta → pinned
    assert _classify_branch(True, 0.0) == "pinned"
    assert _classify_branch(True, 0.05) == "pinned"
    assert _classify_branch(True, 0.5) == "pinned"


def test_confidence_level_pure():
    assert _confidence_level(HIGH_CONFIDENCE_SAMPLE_THRESHOLD) == "HIGH"
    assert _confidence_level(HIGH_CONFIDENCE_SAMPLE_THRESHOLD - 1) == "MED"
    assert _confidence_level(5) == "MED"  # MIN_SAMPLES_TOTAL


def test_is_boss_pinned_returns_false_when_column_absent():
    """Item 3 forward-compat: until Item 8 adds boss_pinned column,
    every category must classify as not-pinned."""
    conn = _make_conn_with_schema()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(topic_weights)").fetchall()}
    assert "boss_pinned" not in cols, (
        "test fixture should reflect production schema BEFORE Item 8; "
        "if this fails, Item 8 has shipped and the helper now needs a "
        "real-pinned-row test"
    )
    for cat in ("ai_model", "ai_agent", "supply_chain", "other"):
        assert _is_boss_pinned(conn, cat) is False


def test_is_boss_pinned_returns_true_when_column_present_and_set():
    """Forward-compat exercise of the Item 8 lane: simulate the column
    existing + value=1 by ALTERing the test DB. Documents the shape
    Item 8 must respect."""
    conn = _make_conn_with_schema()
    conn.execute("ALTER TABLE topic_weights ADD COLUMN boss_pinned INTEGER DEFAULT 0")
    conn.execute(
        "UPDATE topic_weights SET boss_pinned = 1 WHERE category_id = 'ai_model'"
    )
    conn.commit()
    assert _is_boss_pinned(conn, "ai_model") is True
    assert _is_boss_pinned(conn, "ai_agent") is False


def _seed_high_engagement_category(conn):
    """Seed ai_model + other so ai_model produces a SMALL +Δ that lands
    inside the auto-deploy band (|Δ| < AUTO_DEPLOY_DELTA_THRESHOLD).

    Tuning rationale (recorded so future tweaks remain calibrated):
      - ai_model raw scores per platform ≈ 150 (post = likes 150)
      - other  raw scores per platform ≈ 100 (post = likes 100)
      - plat_median ≈ 100 (other has 6 posts vs ai_model 4)
      - ai_model norm_delta ≈ 150/100 - 1 = 0.50
      - eta × raw_delta = 0.1 × 0.50 = 0.05
      - applied_delta on old_w=1.70 → 1.70 × 0.05 = 0.085  (< 0.10 threshold)
    """
    for i in range(4):
        _seed_post(conn, f"d_ai_{i}", f"n_ai_{i}", "ai_model")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(conn, f"d_ai_{i}", p, likes=150)
    for i in range(6):
        _seed_post(conn, f"d_o_{i}", f"n_o_{i}", "other")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(conn, f"d_o_{i}", p, likes=100)
    conn.commit()


def test_auto_deploy_path_writes_proposal_updates_weight_marks_deployed(tmp_path):
    """Phase 9 Item 3 auto-deploy lane:
    non-pinned category + |delta| < 0.10 → topic_weights UPDATEd AND
    proposal jsonl entry written AND lineage row's deployed_at populated
    AND jsonl entry's deployed_at populated.

    Uses an on-disk tmp DB rather than :memory: because write_proposal
    opens its own sqlite3.connect for the lineage INSERT — :memory: DBs
    are connection-private and the second connect would see an empty DB.
    """
    proposals_dir = tmp_path / "proposals"
    db_file = tmp_path / "test.db"
    on_disk = sqlite3.connect(str(db_file))
    on_disk.row_factory = sqlite3.Row
    # Replay schema + seed onto the on-disk DB.
    schema = (_ROOT / "data" / "01_harvest" / "schema.sql").read_text(encoding="utf-8")
    on_disk.executescript(schema)
    for col_ddl in [("publish_at", "TEXT"), ("queue_status", "TEXT")]:
        cols = [r[1] for r in on_disk.execute("PRAGMA table_info(drafts)").fetchall()]
        if col_ddl[0] not in cols:
            on_disk.execute(f"ALTER TABLE drafts ADD COLUMN {col_ddl[0]} {col_ddl[1]}")
    for col, ddl in [("topic_category", "TEXT"), ("topic_confidence", "REAL"),
                     ("topic_rationale", "TEXT"), ("weighted_score", "REAL")]:
        cols = [r[1] for r in on_disk.execute(
            "PRAGMA table_info(news_items)"
        ).fetchall()]
        if col not in cols:
            on_disk.execute(f"ALTER TABLE news_items ADD COLUMN {col} {ddl}")
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc).isoformat(timespec="seconds")
    for cat, w in [("ai_model", 1.70), ("ai_agent", 1.60),
                   ("supply_chain", 1.40), ("other", 0.70)]:
        on_disk.execute(
            "INSERT INTO topic_weights (category_id, display_name, weight, "
            "last_updated_at, update_reason, sample_count) VALUES (?,?,?,?,?,?)",
            (cat, cat, w, now, "initial_seed", 0),
        )
    on_disk.commit()
    # Re-seed engagement on the on-disk DB
    _seed_high_engagement_category(on_disk)

    before_w = on_disk.execute(
        "SELECT weight FROM topic_weights WHERE category_id='ai_model'"
    ).fetchone()[0]

    result = run_backprop(
        on_disk, lookback_days=30, dry_run=False,
        write_proposals=True,
        proposals_db_path=db_file,
        proposals_base_dir=proposals_dir,
    )

    # 1. ai_model UPDATEd (auto-deploy lane fires).
    after_w = on_disk.execute(
        "SELECT weight FROM topic_weights WHERE category_id='ai_model'"
    ).fetchone()[0]
    ai = next(u for u in result.updates if u.category_id == "ai_model")
    assert abs(ai.applied_delta) < AUTO_DEPLOY_DELTA_THRESHOLD, (
        f"test fixture must produce a small delta to exercise auto-deploy; "
        f"got {ai.applied_delta}"
    )
    assert after_w > before_w, (
        f"auto-deploy must raise topic_weights.weight; "
        f"{before_w} → {after_w}"
    )

    # 2. lineage row exists with deployed_at populated for ai_model fire_id.
    lineage_rows = on_disk.execute(
        "SELECT fire_id, deployed_at FROM reflector_proposal_lineage "
        "WHERE analyzer = 'topic'"
    ).fetchall()
    assert len(lineage_rows) >= 1
    ai_lineage = [r for r in lineage_rows if r["deployed_at"] is not None]
    assert len(ai_lineage) >= 1, (
        "auto-deploy lane must populate deployed_at on at least one lineage row"
    )

    # 3. proposals jsonl exists with matching fire_id and deployed_at populated.
    week_files = list(proposals_dir.glob("*.jsonl"))
    assert week_files, "auto-deploy must write at least one proposals jsonl line"
    # Build fire_id → record map
    fire_to_record: dict = {}
    for wf in week_files:
        for line in wf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            fire_to_record[rec["fire_id"]] = rec
    deployed_jsonl = [r for r in fire_to_record.values()
                      if r.get("deployed_at") is not None]
    assert deployed_jsonl, (
        "auto-deploy must populate deployed_at on at least one jsonl record"
    )
    # The auto-deployed jsonl record must NOT have boss_attention_required.
    for rec in deployed_jsonl:
        assert rec["boss_attention_required"] is False, (
            f"auto-deploy lane must set boss_attention_required=False; "
            f"got record {rec['fire_id']}"
        )
        assert rec["analyzer"] == "topic"
        assert rec["proposal_type"] == "adjust_weight"
        assert rec["action"]["target_config"] == "topic_weights"


def test_proposal_only_path_large_delta_does_not_update_weight(tmp_path):
    """Phase 9 Item 3 proposal-only lane (large delta):
    non-pinned + |delta| ≥ 0.10 → topic_weights UNCHANGED, jsonl entry
    written with boss_attention_required=True, lineage deployed_at NULL."""
    db_file = tmp_path / "test.db"
    proposals_dir = tmp_path / "proposals"
    on_disk = sqlite3.connect(str(db_file))
    on_disk.row_factory = sqlite3.Row
    schema = (_ROOT / "data" / "01_harvest" / "schema.sql").read_text(encoding="utf-8")
    on_disk.executescript(schema)
    for col_ddl in [("publish_at", "TEXT"), ("queue_status", "TEXT")]:
        cols = [r[1] for r in on_disk.execute(
            "PRAGMA table_info(drafts)"
        ).fetchall()]
        if col_ddl[0] not in cols:
            on_disk.execute(f"ALTER TABLE drafts ADD COLUMN {col_ddl[0]} {col_ddl[1]}")
    for col, ddl in [("topic_category", "TEXT"), ("topic_confidence", "REAL"),
                     ("topic_rationale", "TEXT"), ("weighted_score", "REAL")]:
        cols = [r[1] for r in on_disk.execute(
            "PRAGMA table_info(news_items)"
        ).fetchall()]
        if col not in cols:
            on_disk.execute(f"ALTER TABLE news_items ADD COLUMN {col} {ddl}")
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc).isoformat(timespec="seconds")
    # Seed ai_model with weight FAR enough that even after eta-clip the
    # category gate clamps to MAX_WEEKLY_DELTA = 0.30 → triggers
    # proposal-only lane (≥ 0.10).
    for cat, w in [("ai_model", 1.00), ("other", 0.70)]:
        on_disk.execute(
            "INSERT INTO topic_weights (category_id, display_name, weight, "
            "last_updated_at, update_reason, sample_count) VALUES (?,?,?,?,?,?)",
            (cat, cat, w, now, "initial_seed", 0),
        )
    on_disk.commit()

    # Seed engagement with extreme top-vs-baseline gap so eta×raw_delta
    # hits the +0.30 weekly clip (applied_delta == 0.30 ≥ 0.10).
    for i in range(4):
        _seed_post(on_disk, f"d_ai_{i}", f"n_ai_{i}", "ai_model")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(on_disk, f"d_ai_{i}", p,
                             likes=10000, comments=2000, shares=600,
                             saves=400, reposts=200, quotes=100,
                             replies=1000, views=200000, reach=400000)
    for i in range(6):
        _seed_post(on_disk, f"d_o_{i}", f"n_o_{i}", "other")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(on_disk, f"d_o_{i}", p,
                             likes=1, comments=0, shares=0)
    on_disk.commit()

    before_w = on_disk.execute(
        "SELECT weight FROM topic_weights WHERE category_id='ai_model'"
    ).fetchone()[0]

    result = run_backprop(
        on_disk, lookback_days=30, dry_run=False,
        write_proposals=True,
        proposals_db_path=db_file,
        proposals_base_dir=proposals_dir,
    )
    ai = next(u for u in result.updates if u.category_id == "ai_model")
    assert abs(ai.applied_delta) >= AUTO_DEPLOY_DELTA_THRESHOLD, (
        f"test fixture must trigger weekly clip → big delta to exercise "
        f"proposal-only lane; got {ai.applied_delta}"
    )

    # Weight UNCHANGED (proposal-only).
    after_w = on_disk.execute(
        "SELECT weight FROM topic_weights WHERE category_id='ai_model'"
    ).fetchone()[0]
    assert after_w == before_w, (
        f"proposal-only lane must NOT change topic_weights.weight; "
        f"{before_w} → {after_w}"
    )

    # jsonl entry exists with boss_attention_required=True, deployed_at NULL.
    week_files = list(proposals_dir.glob("*.jsonl"))
    assert week_files
    records = []
    for wf in week_files:
        for line in wf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    ai_records = [r for r in records
                  if r["action"]["field"] == "ai_model"]
    assert ai_records, "proposal-only lane must write a jsonl entry"
    for r in ai_records:
        assert r["boss_attention_required"] is True
        assert r["deployed_at"] is None
        assert r["evidence"]["confidence"] in {"HIGH", "MED"}

    # Lineage row for the ai_model fire_id has deployed_at NULL.
    # (Other categories may legitimately auto-deploy; we only assert
    # the specific proposal-only lane's lineage shape.)
    ai_fire_ids = [r["fire_id"] for r in ai_records]
    placeholders = ",".join("?" for _ in ai_fire_ids)
    lineage = on_disk.execute(
        f"SELECT fire_id, deployed_at FROM reflector_proposal_lineage "
        f"WHERE fire_id IN ({placeholders})",
        ai_fire_ids,
    ).fetchall()
    assert lineage
    assert all(r["deployed_at"] is None for r in lineage), (
        "proposal-only lane must not populate lineage.deployed_at "
        "for the proposal-only fire_id"
    )


def test_proposal_only_path_pinned_category(tmp_path, monkeypatch):
    """Phase 9 Item 3 pinned-category branch: simulate the Item 8
    forward-compat scenario by monkeypatching `_is_boss_pinned` so a
    small-delta non-pinned-DB-row still classifies as 'pinned'.

    The point of this test is to pin the contract — when Item 8 ships
    and a real boss_pinned=1 row exists, the analyzer must take the
    proposal-only lane regardless of delta magnitude. Without this
    test the pinned branch is dead code until Item 8.
    """
    from src.reflector import topic as topic_mod

    db_file = tmp_path / "test.db"
    proposals_dir = tmp_path / "proposals"
    on_disk = sqlite3.connect(str(db_file))
    on_disk.row_factory = sqlite3.Row
    schema = (_ROOT / "data" / "01_harvest" / "schema.sql").read_text(encoding="utf-8")
    on_disk.executescript(schema)
    for col_ddl in [("publish_at", "TEXT"), ("queue_status", "TEXT")]:
        cols = [r[1] for r in on_disk.execute(
            "PRAGMA table_info(drafts)"
        ).fetchall()]
        if col_ddl[0] not in cols:
            on_disk.execute(f"ALTER TABLE drafts ADD COLUMN {col_ddl[0]} {col_ddl[1]}")
    for col, ddl in [("topic_category", "TEXT"), ("topic_confidence", "REAL"),
                     ("topic_rationale", "TEXT"), ("weighted_score", "REAL")]:
        cols = [r[1] for r in on_disk.execute(
            "PRAGMA table_info(news_items)"
        ).fetchall()]
        if col not in cols:
            on_disk.execute(f"ALTER TABLE news_items ADD COLUMN {col} {ddl}")
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc).isoformat(timespec="seconds")
    for cat, w in [("ai_model", 1.70), ("other", 0.70)]:
        on_disk.execute(
            "INSERT INTO topic_weights (category_id, display_name, weight, "
            "last_updated_at, update_reason, sample_count) VALUES (?,?,?,?,?,?)",
            (cat, cat, w, now, "initial_seed", 0),
        )
    on_disk.commit()
    _seed_high_engagement_category(on_disk)  # produces small +Δ on ai_model

    before_w = on_disk.execute(
        "SELECT weight FROM topic_weights WHERE category_id='ai_model'"
    ).fetchone()[0]

    # Force ai_model to look pinned without ALTERing schema.
    def _fake_pinned(conn, category_id):
        return category_id == "ai_model"
    monkeypatch.setattr(topic_mod, "_is_boss_pinned", _fake_pinned)

    run_backprop(
        on_disk, lookback_days=30, dry_run=False,
        write_proposals=True,
        proposals_db_path=db_file,
        proposals_base_dir=proposals_dir,
    )

    # Weight UNCHANGED — pinned branch never deploys.
    after_w = on_disk.execute(
        "SELECT weight FROM topic_weights WHERE category_id='ai_model'"
    ).fetchone()[0]
    assert after_w == before_w, (
        f"pinned branch must not deploy regardless of delta; "
        f"{before_w} → {after_w}"
    )

    # jsonl entry on ai_model with boss_attention_required=True.
    week_files = list(proposals_dir.glob("*.jsonl"))
    records = []
    for wf in week_files:
        for line in wf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    ai_records = [r for r in records if r["action"]["field"] == "ai_model"]
    assert ai_records
    for r in ai_records:
        assert r["boss_attention_required"] is True
        assert r["deployed_at"] is None


def test_dry_run_writes_no_proposals(tmp_path):
    """dry_run=True must suppress all jsonl + lineage writes even when
    write_proposals=True (the default)."""
    db_file = tmp_path / "test.db"
    proposals_dir = tmp_path / "proposals"
    on_disk = sqlite3.connect(str(db_file))
    on_disk.row_factory = sqlite3.Row
    schema = (_ROOT / "data" / "01_harvest" / "schema.sql").read_text(encoding="utf-8")
    on_disk.executescript(schema)
    for col_ddl in [("publish_at", "TEXT"), ("queue_status", "TEXT")]:
        cols = [r[1] for r in on_disk.execute(
            "PRAGMA table_info(drafts)"
        ).fetchall()]
        if col_ddl[0] not in cols:
            on_disk.execute(f"ALTER TABLE drafts ADD COLUMN {col_ddl[0]} {col_ddl[1]}")
    for col, ddl in [("topic_category", "TEXT"), ("topic_confidence", "REAL"),
                     ("topic_rationale", "TEXT"), ("weighted_score", "REAL")]:
        cols = [r[1] for r in on_disk.execute(
            "PRAGMA table_info(news_items)"
        ).fetchall()]
        if col not in cols:
            on_disk.execute(f"ALTER TABLE news_items ADD COLUMN {col} {ddl}")
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc).isoformat(timespec="seconds")
    for cat, w in [("ai_model", 1.70), ("other", 0.70)]:
        on_disk.execute(
            "INSERT INTO topic_weights (category_id, display_name, weight, "
            "last_updated_at, update_reason, sample_count) VALUES (?,?,?,?,?,?)",
            (cat, cat, w, now, "initial_seed", 0),
        )
    on_disk.commit()
    _seed_high_engagement_category(on_disk)

    run_backprop(
        on_disk, lookback_days=30, dry_run=True,
        write_proposals=True,
        proposals_db_path=db_file,
        proposals_base_dir=proposals_dir,
    )

    # No proposals dir contents.
    if proposals_dir.exists():
        files = list(proposals_dir.glob("*.jsonl"))
        for f in files:
            assert f.stat().st_size == 0, (
                "dry_run must not append to proposals jsonl"
            )

    # No lineage rows.
    n = on_disk.execute(
        "SELECT COUNT(*) FROM reflector_proposal_lineage"
    ).fetchone()[0]
    assert n == 0


def test_view_aggregates_picked_up_when_view_present(tmp_path):
    """v_topic_engagement_x_platform aggregates flow into proposal evidence
    metrics. Validates that Item 1 substrate connects to Item 3 output."""
    db_file = tmp_path / "test.db"
    proposals_dir = tmp_path / "proposals"
    on_disk = sqlite3.connect(str(db_file))
    on_disk.row_factory = sqlite3.Row
    schema = (_ROOT / "data" / "01_harvest" / "schema.sql").read_text(encoding="utf-8")
    on_disk.executescript(schema)
    # Bring in views.sql (Item 1 substrate).
    views = (_ROOT / "data" / "01_harvest" / "views.sql").read_text(encoding="utf-8")
    for col_ddl in [("publish_at", "TEXT"), ("queue_status", "TEXT")]:
        cols = [r[1] for r in on_disk.execute(
            "PRAGMA table_info(drafts)"
        ).fetchall()]
        if col_ddl[0] not in cols:
            on_disk.execute(f"ALTER TABLE drafts ADD COLUMN {col_ddl[0]} {col_ddl[1]}")
    for col, ddl in [("topic_category", "TEXT"), ("topic_confidence", "REAL"),
                     ("topic_rationale", "TEXT"), ("weighted_score", "REAL")]:
        cols = [r[1] for r in on_disk.execute(
            "PRAGMA table_info(news_items)"
        ).fetchall()]
        if col not in cols:
            on_disk.execute(f"ALTER TABLE news_items ADD COLUMN {col} {ddl}")
    on_disk.executescript(views)
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc).isoformat(timespec="seconds")
    for cat, w in [("ai_model", 1.70), ("other", 0.70)]:
        on_disk.execute(
            "INSERT INTO topic_weights (category_id, display_name, weight, "
            "last_updated_at, update_reason, sample_count) VALUES (?,?,?,?,?,?)",
            (cat, cat, w, now, "initial_seed", 0),
        )
    on_disk.commit()

    # Need queue_status='published' OR status='published' on drafts
    # for v_post_engagement_aggregated to pick up the rows.
    _seed_high_engagement_category(on_disk)

    run_backprop(
        on_disk, lookback_days=30, dry_run=False,
        write_proposals=True,
        proposals_db_path=db_file,
        proposals_base_dir=proposals_dir,
    )

    week_files = list(proposals_dir.glob("*.jsonl"))
    records = []
    for wf in week_files:
        for line in wf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    assert records, "auto-deploy must write at least one proposal"
    ai = next((r for r in records
               if r["action"]["field"] == "ai_model"), None)
    assert ai is not None
    metrics = ai["evidence"]["metrics"]
    # The view-prefixed keys MUST be present (even if value is None).
    assert "view_sample_count" in metrics, (
        "Phase 9 Item 1 substrate (v_topic_engagement_x_platform) must "
        "feed into the proposal evidence metrics; absence indicates the "
        "view is not being queried"
    )


def test_boss_pinned_column_with_actual_migration(tmp_path):
    """Phase 9 Item 8: test _is_boss_pinned with the actual boss_pinned
    column (migration 2026-04-28_phase9_boss_pinned.sql applied).

    This verifies:
      1. Migration adds the column successfully
      2. policy_regulate is set to boss_pinned=1
      3. Other categories default to boss_pinned=0
      4. _is_boss_pinned reads the column correctly
    """
    db_file = tmp_path / "test.db"
    on_disk = sqlite3.connect(str(db_file))
    on_disk.row_factory = sqlite3.Row

    schema = (_ROOT / "data" / "01_harvest" / "schema.sql").read_text(encoding="utf-8")
    on_disk.executescript(schema)

    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc).isoformat(timespec="seconds")

    # Seed topic_weights BEFORE applying migration (so policy_regulate exists
    # when the migration's UPDATE runs).
    for cat, w in [("policy_regulate", 1.20), ("ai_model", 1.70),
                   ("ai_agent", 1.60), ("other", 0.70)]:
        on_disk.execute(
            "INSERT INTO topic_weights (category_id, display_name, weight, "
            "last_updated_at, update_reason, sample_count) VALUES (?,?,?,?,?,?)",
            (cat, cat, w, now, "initial_seed", 0),
        )
    on_disk.commit()

    # Now apply the Item 8 migration (which will add the column and UPDATE
    # policy_regulate to boss_pinned=1)
    migration = (_ROOT / "data" / "01_harvest" / "migrations" /
                 "2026-04-28_phase9_boss_pinned.sql").read_text(encoding="utf-8")
    on_disk.executescript(migration)

    # Verify the migration applied: boss_pinned column exists
    cols = {r[1] for r in on_disk.execute(
        "PRAGMA table_info(topic_weights)"
    ).fetchall()}
    assert "boss_pinned" in cols, (
        "Migration 2026-04-28_phase9_boss_pinned.sql must add boss_pinned column"
    )

    # Verify policy_regulate is pinned, others are not
    assert _is_boss_pinned(on_disk, "policy_regulate") is True, (
        "Migration must set policy_regulate.boss_pinned = 1"
    )
    assert _is_boss_pinned(on_disk, "ai_model") is False, (
        "ai_model should default to boss_pinned = 0"
    )
    assert _is_boss_pinned(on_disk, "ai_agent") is False
    assert _is_boss_pinned(on_disk, "other") is False


def test_boss_pinned_column_with_actual_db_prevents_auto_deploy(tmp_path):
    """Phase 9 Item 8: end-to-end test verifying that a boss_pinned=1
    category takes the proposal-only path even with a small delta.

    Uses the actual migration to add the column and set policy_regulate=1.
    Produces a small positive delta on policy_regulate and verifies:
      1. topic_weights.weight is NOT updated
      2. A proposal jsonl entry is written with boss_attention_required=True
      3. lineage.deployed_at remains NULL (not auto-deployed)
    """
    db_file = tmp_path / "test.db"
    proposals_dir = tmp_path / "proposals"
    on_disk = sqlite3.connect(str(db_file))
    on_disk.row_factory = sqlite3.Row

    schema = (_ROOT / "data" / "01_harvest" / "schema.sql").read_text(encoding="utf-8")
    on_disk.executescript(schema)

    # Add columns from Phase 8.18 and Phase 8.20
    for col_ddl in [("publish_at", "TEXT"), ("queue_status", "TEXT")]:
        cols = [r[1] for r in on_disk.execute("PRAGMA table_info(drafts)").fetchall()]
        if col_ddl[0] not in cols:
            on_disk.execute(f"ALTER TABLE drafts ADD COLUMN {col_ddl[0]} {col_ddl[1]}")
    for col, ddl in [("topic_category", "TEXT"), ("topic_confidence", "REAL"),
                     ("topic_rationale", "TEXT"), ("weighted_score", "REAL")]:
        cols = [r[1] for r in on_disk.execute(
            "PRAGMA table_info(news_items)"
        ).fetchall()]
        if col not in cols:
            on_disk.execute(f"ALTER TABLE news_items ADD COLUMN {col} {ddl}")

    # Apply Item 8 migration BEFORE seeding rows (so the UPDATE in the
    # migration doesn't fail with "no matching row")
    migration = (_ROOT / "data" / "01_harvest" / "migrations" /
                 "2026-04-28_phase9_boss_pinned.sql").read_text(encoding="utf-8")
    on_disk.executescript(migration)

    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc).isoformat(timespec="seconds")

    # Seed policy_regulate with initial weight and other
    for cat, w in [("policy_regulate", 1.50), ("other", 0.70)]:
        on_disk.execute(
            "INSERT INTO topic_weights (category_id, display_name, weight, "
            "last_updated_at, update_reason, sample_count) VALUES (?,?,?,?,?,?)",
            (cat, cat, w, now, "initial_seed", 0),
        )
    on_disk.commit()

    # Update policy_regulate to boss_pinned since it now defaults to 0
    # (the migration's UPDATE ran before these rows existed)
    on_disk.execute(
        "UPDATE topic_weights SET boss_pinned = 1 WHERE category_id = ?",
        ("policy_regulate",),
    )
    on_disk.commit()

    # Seed engagement with policy_regulate showing good performance
    # but small delta (like _seed_high_engagement_category pattern)
    for i in range(4):
        _seed_post(on_disk, f"d_pol_{i}", f"n_pol_{i}", "policy_regulate")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(on_disk, f"d_pol_{i}", p, likes=150)

    for i in range(6):
        _seed_post(on_disk, f"d_o_{i}", f"n_o_{i}", "other")
        for p in ("facebook", "instagram", "threads"):
            _seed_engagement(on_disk, f"d_o_{i}", p, likes=100)
    on_disk.commit()

    before_w = on_disk.execute(
        "SELECT weight FROM topic_weights WHERE category_id='policy_regulate'"
    ).fetchone()[0]

    result = run_backprop(
        on_disk, lookback_days=30, dry_run=False,
        write_proposals=True,
        proposals_db_path=db_file,
        proposals_base_dir=proposals_dir,
    )

    # Verify policy_regulate has a small delta (would auto-deploy if not pinned)
    pol = next(u for u in result.updates if u.category_id == "policy_regulate")
    assert abs(pol.applied_delta) < AUTO_DEPLOY_DELTA_THRESHOLD, (
        f"test fixture must produce a small delta to verify pinned path "
        f"prevents auto-deploy; got {pol.applied_delta}"
    )

    # Weight UNCHANGED (pinned → proposal-only, no auto-deploy)
    after_w = on_disk.execute(
        "SELECT weight FROM topic_weights WHERE category_id='policy_regulate'"
    ).fetchone()[0]
    assert after_w == before_w, (
        f"boss_pinned=1 category must not auto-deploy even with small delta; "
        f"{before_w} → {after_w}"
    )

    # Proposal jsonl with boss_attention_required=True
    week_files = list(proposals_dir.glob("*.jsonl"))
    assert week_files, "boss_pinned category must write a proposal"
    records = []
    for wf in week_files:
        for line in wf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))

    pol_records = [r for r in records
                   if r["action"]["field"] == "policy_regulate"]
    assert pol_records, "policy_regulate proposal must exist"
    for r in pol_records:
        assert r["boss_attention_required"] is True, (
            "boss_pinned category must have boss_attention_required=True"
        )
        assert r["deployed_at"] is None, (
            "boss_pinned category must not be deployed_at (proposal-only)"
        )

    # Lineage row has deployed_at NULL
    lineage = on_disk.execute(
        "SELECT fire_id, deployed_at FROM reflector_proposal_lineage "
        "WHERE analyzer = 'topic' AND (evidence_json LIKE '%policy_regulate%' "
        "OR target_config LIKE '%policy_regulate%')"
    ).fetchall()
    if lineage:  # lineage row may or may not exist (depends on write_proposal logic)
        for r in lineage:
            assert r["deployed_at"] is None, (
                "pinned proposal must not have deployed_at set"
            )
