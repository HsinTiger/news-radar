"""
News Radar · Integration test

確認 schema.sql → init_db → upsert_news → news_exists 的 round-trip
在一個臨時 DB 上可以跑通。
"""
from __future__ import annotations

from src import db as dbmod
from src.schema import NewsItem


def test_upsert_and_exists(tmp_db):
    item = NewsItem(
        id="roundtrip-1",
        feed_name="UnitTest",
        feed_tier="primary",
        url="https://example.com/article",
        title="Round-trip test article",
        published_at="2026-04-19T00:00:00+00:00",
        fetched_at="2026-04-19T00:00:00+00:00",
        language="en",
        clean_markdown="some markdown",
        word_count=150,
        tags=["ai"],
        status="fetched",
    )

    with dbmod.get_conn() as conn:
        assert dbmod.news_exists(conn, item.id) is False
        inserted = dbmod.upsert_news(conn, item)
        assert inserted is True
        assert dbmod.news_exists(conn, item.id) is True

        # 再 upsert 同一 id 應跳過
        inserted_again = dbmod.upsert_news(conn, item)
        assert inserted_again is False
