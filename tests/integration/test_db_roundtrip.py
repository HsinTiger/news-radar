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


def test_upsert_skips_same_url_with_a_different_lineage_id(tmp_db):
    first = NewsItem(
        id="manual-lineage-id",
        feed_name="user_substack",
        feed_tier="primary",
        url="https://www.youtube.com/watch?v=duplicate",
        title="Manually submitted source",
        published_at="2026-08-07T00:00:00+00:00",
        fetched_at="2026-08-07T00:00:00+00:00",
        language="en",
        clean_markdown="manual source text",
        word_count=20,
        tags=["manual"],
        status="fetched",
    )
    harvested = first.model_copy(
        update={
            "id": "sha1-url-id",
            "feed_name": "YouTube Podcast",
            "clean_markdown": "new transcript" * 100,
            "word_count": 200,
        }
    )

    with dbmod.get_conn() as conn:
        assert dbmod.upsert_news(conn, first) is True
        assert dbmod.upsert_news(conn, harvested) is False
        rows = conn.execute(
            "SELECT id, feed_name, clean_markdown FROM news_items WHERE url=?",
            (first.url,),
        ).fetchall()

    assert [tuple(row) for row in rows] == [
        ("manual-lineage-id", "user_substack", "manual source text")
    ]
