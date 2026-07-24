from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src import db as dbmod
from src.recovery_mode import (
    content_format_for_platform,
    editorial_mandate_for,
    platform_uses_carousel,
    rank_candidates,
    record_experiments,
    visible_carousel_for_platform,
)


NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def test_recovery_carousel_scope_is_instagram_only() -> None:
    cards = object()
    assert platform_uses_carousel("instagram", recovery=True)
    assert platform_uses_carousel("ig", recovery=True)
    assert not platform_uses_carousel("facebook", recovery=True)
    assert not platform_uses_carousel("threads", recovery=True)
    assert visible_carousel_for_platform(
        "instagram", cards, recovery=True
    ) is cards
    assert visible_carousel_for_platform("facebook", cards, recovery=True) is None
    assert platform_uses_carousel("facebook", recovery=False)


def test_recovery_content_format_is_platform_specific() -> None:
    assert content_format_for_platform(
        "instagram", carousel_available=True, recovery=True
    ) == "carousel"
    assert content_format_for_platform(
        "facebook", carousel_available=True, recovery=True
    ) == "feed"
    assert content_format_for_platform(
        "threads", carousel_available=True, recovery=True
    ) == "feed"
    assert content_format_for_platform(
        "facebook", carousel_available=True, recovery=False
    ) == "carousel"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE topic_weights(category_id TEXT PRIMARY KEY,weight REAL);
        INSERT INTO topic_weights VALUES('current_affairs',1.6);
        INSERT INTO topic_weights VALUES('tw_politics',1.65);
        INSERT INTO topic_weights VALUES('policy_geopolitics',1.35);
        INSERT INTO topic_weights VALUES('tech_product_launch',1.55);
        INSERT INTO topic_weights VALUES('other',0.7);
        CREATE TABLE recovery_experiments(
          id TEXT PRIMARY KEY,draft_id TEXT,platform TEXT,experiment_type TEXT,
          hypothesis TEXT,baseline_followers INTEGER,baseline_primary_metric TEXT,
          baseline_primary_value REAL,baseline_captured_at TEXT,content_format TEXT,
          actual_format TEXT,actual_format_at TEXT,topic TEXT,created_at TEXT,
          UNIQUE(draft_id,platform)
        );
        """
    )
    return conn


def test_rank_candidates_applies_weight_before_freshness() -> None:
    conn = _conn()
    rows = [
        {"title": "一般消息", "clean_markdown": "沒有特定分類", "published_at": "2026-07-24T10:00:00Z"},
        {"title": "台灣食安事件擴大", "clean_markdown": "食安與產品召回", "published_at": "2026-07-24T09:00:00Z"},
    ]
    ranked = rank_candidates(conn, rows, now=NOW)
    assert ranked[0]["title"] == "台灣食安事件擴大"


def test_rank_candidates_prefers_primary_source_inside_same_topic() -> None:
    conn = _conn()
    rows = [
        {
            "title": "較新的台灣二手報導",
            "topic_category": "current_affairs",
            "feed_tier": "secondary",
            "source_type": "article",
            "published_at": "2026-07-24T10:00:00Z",
        },
        {
            "title": "較早的台灣第一手來源",
            "topic_category": "current_affairs",
            "feed_tier": "primary",
            "feed_name": "食藥署 本署新聞",
            "tags": '["official","primary-record"]',
            "source_type": "article",
            "published_at": "2026-07-24T09:00:00Z",
        },
    ]
    ranked = rank_candidates(conn, rows, now=NOW)
    assert ranked[0]["title"] == "較早的台灣第一手來源"


def test_official_market_record_survives_recovery_scope_gate() -> None:
    conn = _conn()
    row = {
        "title": (
            "本週發行量加權股價指數漲幅約為2.30%，"
            "上市股票總市值達142.58兆元"
        ),
        "clean_markdown": "證交所公布本週市場統計。",
        "feed_tier": "primary",
        "feed_name": "證交所 官方訊息",
        "tags": '["official","primary-record"]',
        "source_type": "article",
        "published_at": "2026-07-24T10:00:00Z",
    }

    assert rank_candidates(conn, [row], now=NOW) == [row]


def test_primary_record_survives_bounded_scan_ahead_of_media_reaction() -> None:
    conn = _conn()
    rows = [
        {
            "title": "食藥署公布重新上架產品清單　藍：沒有真相不准上架",
            "topic_category": "tw_politics",
            "feed_tier": "primary",
            "feed_name": "中央社 政治",
            "tags": '["taiwan","politics","news"]',
            "source_type": "article",
            "published_at": "2026-07-24T10:00:00Z",
        },
        {
            "title": "美公布301調查新稅率 台灣2,231項產品豁免關稅",
            "topic_category": "policy_geopolitics",
            "feed_tier": "primary",
            "feed_name": "行政院 本院新聞",
            "tags": '["taiwan","official","government","primary-record"]',
            "source_type": "article",
            "published_at": "2026-07-24T09:00:00Z",
        },
    ]

    ranked = rank_candidates(conn, rows, now=NOW)

    assert ranked[0]["feed_name"] == "行政院 本院新聞"


def test_rank_candidates_downranks_social_source() -> None:
    conn = _conn()
    rows = [
        {
            "title": "台灣社群轉述",
            "topic_category": "current_affairs",
            "feed_tier": "primary",
            "source_type": "social",
            "published_at": "2026-07-24T10:00:00Z",
        },
        {
            "title": "台灣正式文章",
            "topic_category": "current_affairs",
            "feed_tier": "secondary",
            "source_type": "article",
            "published_at": "2026-07-24T09:00:00Z",
        },
    ]
    ranked = rank_candidates(conn, rows, now=NOW)
    assert ranked[0]["title"] == "台灣正式文章"


def test_rank_candidates_fail_closed_outside_taiwan_editorial_scope() -> None:
    conn = _conn()
    rows = [
        {
            "title": "New AI model launches overseas",
            "topic_category": "tech_product_launch",
            "feed_tier": "primary",
            "source_type": "article",
            "published_at": "2026-07-24T10:00:00Z",
        },
        {
            "title": "食藥署公布台灣食品回收批號",
            "topic_category": "current_affairs",
            "feed_tier": "primary",
            "source_type": "article",
            "published_at": "2026-07-24T09:00:00Z",
        },
    ]
    ranked = rank_candidates(conn, rows, now=NOW)
    assert [row["title"] for row in ranked] == ["食藥署公布台灣食品回收批號"]


def test_global_disaster_from_taiwan_media_is_not_automatically_taiwan_relevant() -> None:
    conn = _conn()
    row = {
        "title": "南亞暴雨釀洪災上百人下落不明",
        "topic_category": "current_affairs",
        "feed_name": "公視新聞 PTS",
        "published_at": "2026-07-24T10:00:00Z",
    }
    assert rank_candidates(conn, [row], now=NOW) == []


def test_foreign_leverage_story_cannot_enter_via_tw_stocks_keyword() -> None:
    conn = _conn()
    row = {
        "title": "南韓大學生5倍槓桿炒股 行情反轉暴跌血本無歸",
        "clean_markdown": (
            "南韓年輕人以韓元高槓桿投資，融資餘額創新高，"
            "報導只將金額換算成台幣，並無其他市場關聯。"
        ),
        "feed_name": "公視新聞 PTS",
        "tags": '["taiwan","news","public-broadcast"]',
        "published_at": "2026-07-24T10:00:00Z",
    }
    assert rank_candidates(conn, [row], now=NOW) == []


def test_public_consequence_outranks_ceremonial_politics() -> None:
    conn = _conn()
    rows = [
        {
            "title": "立法院長接見訪賓並合影",
            "topic_category": "current_affairs",
            "feed_tier": "primary",
            "published_at": "2026-07-24T10:00:00Z",
        },
        {
            "title": "台灣食安不合格產品回收批號公布",
            "topic_category": "current_affairs",
            "feed_tier": "secondary",
            "published_at": "2026-07-24T09:00:00Z",
        },
    ]
    assert rank_candidates(conn, rows, now=NOW)[0]["title"] == "台灣食安不合格產品回收批號公布"


def test_public_consequence_outranks_promotional_events_and_rally_headcount() -> None:
    conn = _conn()
    rows = [
        {
            "title": "國防部空氣軟槍射擊賽25日登場",
            "topic_category": "tw_politics",
            "feed_tier": "primary",
            "published_at": "2026-07-24T11:00:00Z",
        },
        {
            "title": "凱道集會至少12位縣市首長到場",
            "topic_category": "tw_politics",
            "feed_tier": "primary",
            "published_at": "2026-07-24T10:30:00Z",
        },
        {
            "title": "台中油品抽驗次數各說各話",
            "topic_category": "tw_politics",
            "feed_tier": "secondary",
            "published_at": "2026-07-24T10:00:00Z",
        },
    ]

    ranked = rank_candidates(conn, rows, now=NOW)

    assert ranked[0]["title"] == "台中油品抽驗次數各說各話"


def test_future_dated_row_cannot_evict_current_taiwan_candidates() -> None:
    conn = _conn()
    rows = [
        {
            "title": "台灣食安回收公告",
            "topic_category": "current_affairs",
            "published_at": NOW.isoformat(),
        },
        {
            "title": "台灣未來日期污染資料",
            "topic_category": "current_affairs",
            "published_at": (NOW + timedelta(days=3650)).isoformat(),
        },
    ]
    ranked = rank_candidates(conn, rows, now=NOW)
    assert [row["title"] for row in ranked] == ["台灣食安回收公告"]


def test_entirely_stale_batch_cannot_redefine_freshness_window() -> None:
    conn = _conn()
    rows = [
        {
            "title": "台灣食安舊公告",
            "topic_category": "current_affairs",
            "published_at": (NOW - timedelta(days=10)).isoformat(),
        },
        {
            "title": "台股舊消息",
            "topic_category": "tw_stocks",
            "published_at": (NOW - timedelta(days=9)).isoformat(),
        },
    ]
    assert rank_candidates(conn, rows, now=NOW) == []


def test_rank_candidates_keeps_owner_submission_for_one_off_post() -> None:
    conn = _conn()
    row = {
        "title": "Owner viewpoint",
        "topic_category": "other",
        "feed_name": "user_submission",
        "tags": '["user_submission"]',
        "published_at": "2026-07-24T10:00:00Z",
    }
    assert rank_candidates(conn, [row], now=NOW) == [row]


def test_record_experiments_is_platform_specific() -> None:
    conn = _conn()
    record_experiments(
        conn,
        draft_id="draft-1",
        platforms={"threads", "instagram"},
        topic="tech_product_launch",
        content_format={"instagram": "carousel", "threads": "feed"},
        created_at="2026-07-24T00:00:00Z",
    )
    rows = conn.execute(
        "SELECT platform,experiment_type,baseline_followers,content_format "
        "FROM recovery_experiments ORDER BY platform"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("instagram", "utility", 9, "carousel"),
        ("threads", "utility", 3748, "feed"),
    ]
    dbmod.mark_recovery_actual_format(
        conn, "draft-1", "instagram", "feed", "2026-07-24T01:00:00Z"
    )
    assert conn.execute(
        "SELECT actual_format FROM recovery_experiments WHERE platform='instagram'"
    ).fetchone()[0] == "feed"


def test_editorial_mandate_matches_persisted_experiment_type() -> None:
    mandate = editorial_mandate_for(
        {"threads", "instagram"}, "tech_product_launch"
    )
    assert "threads: type=utility" in mandate
    assert "instagram: type=utility" in mandate
    assert "Instagram visual utility test" in mandate
    assert "same evidence/response/correction standard" in mandate


def test_tw_stocks_mandate_requires_portfolio_comparison_not_index_hype() -> None:
    mandate = editorial_mandate_for({"threads"}, "tw_stocks")

    assert "distinguish index performance" in mandate
    assert "2-3 source-backed figures" in mandate
    assert "retirement funds" in mandate
    assert "Do not tell readers to wait" in mandate
    assert "measurable personal-result" in mandate
