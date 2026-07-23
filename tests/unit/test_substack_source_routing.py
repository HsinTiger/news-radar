import sqlite3
from datetime import datetime, timezone

from substack_radar import compose


def test_automatic_substack_pool_excludes_owner_routed_submissions(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "news.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE news_items(
          id TEXT,title TEXT,clean_markdown TEXT,topic_category TEXT,
          source_type TEXT,feed_name TEXT,word_count INTEGER,published_at TEXT,
          status TEXT,tags TEXT
        )
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "meta-owner",
            "Meta-only owner submission",
            "高密度內容" * 1000,
            "ai_application",
            "article",
            "user_submission",
            10000,
            now,
            "fetched",
            '["user_submission","platform:threads"]',
        ),
    )
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "public-rss",
            "Public source",
            "公開來源內容" * 100,
            "ai_application",
            "article",
            "Public RSS",
            1000,
            now,
            "fetched",
            "[]",
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(compose, "NEWS_DB_PATH", db_path)
    monkeypatch.setattr(compose, "_load_used", lambda: set())
    marked = []
    monkeypatch.setattr(compose, "_mark_used", marked.append)
    picked = compose._pick_top_from_pool(window_days=3, label="RoutingTest")
    assert picked is not None and picked[0] == "public-rss"
    assert marked == ["public-rss"]
