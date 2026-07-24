from __future__ import annotations

import sqlite3

from src.gather import (
    factcheck_note,
    gather_brief,
    has_authoritative_corroboration,
    requires_authoritative_corroboration,
    source_authority,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE news_items(
          id TEXT PRIMARY KEY,title TEXT,feed_name TEXT,url TEXT,
          clean_markdown TEXT,topic_category TEXT,tags TEXT,feed_tier TEXT,
          source_type TEXT,published_at TEXT,fetched_at TEXT
        );
        """
    )
    return conn


def _insert(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    title: str,
    feed_name: str,
    tags: str = "[]",
    feed_tier: str = "secondary",
    days_old: int = 0,
    content: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO news_items VALUES(
          ?,?,?,?, ?,?,?,?,?,datetime('now', ?),datetime('now')
        )
        """,
        (
            item_id,
            title,
            feed_name,
            f"https://example.test/{item_id}",
            content if content is not None else "這是足夠長的來源本文摘要。" * 20,
            "current_affairs",
            tags,
            feed_tier,
            "article",
            f"-{days_old} day",
        ),
    )


def test_unrelated_factcheck_on_same_broad_beat_is_not_attached() -> None:
    conn = _conn()
    _insert(
        conn,
        item_id="fruit",
        title="錯誤網傳台灣最多農藥的十種水果 食安危機影片",
        feed_name="MyGoPen 查核",
        tags='["factcheck"]',
    )
    assert factcheck_note(
        conn,
        "八年只查中聯苯駢芘一次 盧秀燕慰勉食安處影片",
    ) == ""


def test_same_politician_and_party_do_not_make_different_events_related() -> None:
    conn = _conn()
    _insert(
        conn,
        item_id="food-safety",
        title="毒油案受害人數增加 蔣萬安批民進黨政府沒有作為",
        feed_name="中央社 政治",
        feed_tier="primary",
    )

    brief = gather_brief(
        conn,
        "street-seed",
        "蔣萬安稱不參加街頭集會 民進黨團批評",
        topic_category="tw_politics",
    )

    assert brief == ""


def test_related_context_prefers_primary_record_over_secondary_media() -> None:
    conn = _conn()
    _insert(
        conn,
        item_id="secondary",
        title="中聯問題油品下架 食品回收批號公布",
        feed_name="某新聞網",
    )
    _insert(
        conn,
        item_id="official",
        title="中聯問題油品下架 食藥署公布食品回收批號",
        feed_name="食藥署 本署新聞",
        tags='["official","primary-record"]',
        feed_tier="primary",
    )
    brief = gather_brief(
        conn,
        "seed",
        "中聯問題油品回收 食藥署公布批號",
        topic_category="current_affairs",
    )
    assert brief.index("官方第一手｜食藥署") < brief.index("具名次要媒體｜某新聞網")


def test_stale_archive_item_cannot_enter_context_after_recent_fetch() -> None:
    conn = _conn()
    _insert(
        conn,
        item_id="stale",
        title="中聯問題油品下架 食藥署公布食品回收批號",
        feed_name="食藥署 本署新聞",
        tags='["official","primary-record"]',
        feed_tier="primary",
        days_old=30,
    )
    assert gather_brief(
        conn,
        "seed",
        "中聯問題油品回收 食藥署公布批號",
        topic_category="current_affairs",
    ) == ""


def test_high_risk_secondary_claim_requires_authoritative_corroboration() -> None:
    conn = _conn()
    assert requires_authoritative_corroboration(
        title="市長被控八年只查一次問題食品",
        content="食安事件涉及苯駢芘超標",
        feed_name="某新聞網",
        tags='["politics"]',
        feed_tier="secondary",
    )
    _insert(
        conn,
        item_id="official-proof",
        title="問題食品苯駢芘超標 食藥署公布調查",
        feed_name="食藥署 本署新聞",
        tags='["official","primary-record"]',
        feed_tier="primary",
    )
    assert has_authoritative_corroboration(
        conn,
        "seed",
        "問題食品苯駢芘超標 調查公布",
    )


def test_official_primary_record_does_not_need_secondary_corroboration() -> None:
    conn = _conn()
    assert not requires_authoritative_corroboration(
        title="食藥署公布問題食品回收批號",
        content="檢驗結果為苯駢芘超標",
        feed_name="食藥署 本署新聞",
        tags='["official","primary-record"]',
        feed_tier="primary",
    )


def test_title_only_authority_is_not_publishable_corroboration() -> None:
    conn = _conn()
    _insert(
        conn,
        item_id="official-title-only",
        title="問題食品苯駢苘超標 食藥署公布調查",
        feed_name="食藥署 本署新聞",
        tags='["official","primary-record"]',
        feed_tier="primary",
        content="只有標題",
    )
    assert not has_authoritative_corroboration(
        conn,
        "seed",
        "問題食品苯駢苘超標 調查公布",
    )


def test_source_authority_matches_recovery_contract_order() -> None:
    official = source_authority(
        "食藥署 本署新聞", '["official","primary-record"]', "primary"
    )[0]
    disclosure = source_authority(
        "證交所 官方訊息", '["official","disclosure","primary-record"]', "primary"
    )[0]
    wire = source_authority("中央社 政治", '["taiwan","news"]', "primary")[0]
    factcheck = source_authority(
        "台灣事實查核中心 TFC", '["factcheck","official"]', "primary"
    )[0]
    media = source_authority("某新聞網", '["news"]', "secondary")[0]
    assert official > disclosure > wire > factcheck > media


def test_related_source_survives_busy_feed_window() -> None:
    conn = _conn()
    _insert(
        conn,
        item_id="related-wire",
        title="美國公布301調查 台灣適用百分之十關稅",
        feed_name="中央社 國際",
        tags='["news"]',
        feed_tier="primary",
        days_old=1,
    )
    for index in range(150):
        _insert(
            conn,
            item_id=f"noise-{index}",
            title=f"完全無關的產業消息第{index}則",
            feed_name="高流量新聞源",
        )

    brief = gather_brief(
        conn,
        "seed",
        "美國公布301調查結果 台灣適用百分之十關稅",
        topic_category="policy_geopolitics",
    )

    assert "中央社 國際" in brief
