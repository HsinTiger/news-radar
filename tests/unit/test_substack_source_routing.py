import sqlite3
from datetime import datetime, timedelta, timezone

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


def test_podcast_pool_default_excludes_candidates_older_than_seven_days(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "podcast.db"
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
    now = datetime.now(timezone.utc)
    rows = (
        (
            "old-long",
            "Eight-day-old long interview",
            "舊訪談" * 30000,
            "ai_model",
            "video",
            "YouTube Podcast",
            120000,
            (now - timedelta(days=8)).isoformat(),
            "fetched",
            '[]',
        ),
        (
            "fresh",
            "Fresh interview",
            "新訪談" * 2000,
            "ai_model",
            "video",
            "YouTube Podcast",
            8000,
            (now - timedelta(days=2)).isoformat(),
            "fetched",
            '[]',
        ),
    )
    conn.executemany("INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    monkeypatch.setattr(compose, "NEWS_DB_PATH", db_path)
    monkeypatch.setattr(compose, "_load_used", lambda: set())
    marked = []
    monkeypatch.setattr(compose, "_mark_used", marked.append)

    picked = compose.pick_podcast_interview()

    assert picked is not None and picked[0] == "fresh"
    assert marked == ["fresh"]


def test_podcast_pick_flushes_only_unreferenced_legacy_candidates(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "podcast-flush.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE news_items(
          id TEXT PRIMARY KEY,title TEXT,clean_markdown TEXT,topic_category TEXT,
          source_type TEXT,feed_name TEXT,word_count INTEGER,published_at TEXT,
          status TEXT,tags TEXT,substack_written_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE TABLE drafts(id TEXT PRIMARY KEY,news_id TEXT NOT NULL)"
    )
    now = datetime.now(timezone.utc)
    base = (
        "舊訪談" * 2000,
        "ai_model",
        "video",
        "YouTube Podcast",
        8000,
        (now - timedelta(days=8)).isoformat(),
        "fetched",
        "[]",
    )
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("stale-remove", "Stale unreferenced", *base, None),
    )
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("stale-written", "Stale historical article", *base, now.isoformat()),
    )
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("stale-social", "Stale source with social draft", *base, None),
    )
    conn.execute("INSERT INTO drafts VALUES('d1','stale-social')")
    conn.execute(
        "INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "fresh",
            "Fresh interview",
            "新訪談" * 2000,
            "ai_model",
            "video",
            "YouTube Podcast",
            8000,
            (now - timedelta(days=2)).isoformat(),
            "fetched",
            "[]",
            None,
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(compose, "NEWS_DB_PATH", db_path)
    monkeypatch.setattr(compose, "_load_used", lambda: set())
    monkeypatch.setattr(compose, "_mark_used", lambda _source_id: None)

    picked = compose.pick_podcast_interview()

    assert picked is not None and picked[0] == "fresh"
    conn = sqlite3.connect(db_path)
    remaining = {
        row[0] for row in conn.execute("SELECT id FROM news_items").fetchall()
    }
    quarantined = conn.execute(
        "SELECT source_id,reason FROM substack_podcast_quarantine"
    ).fetchall()
    conn.close()
    assert "stale-remove" not in remaining
    assert {"stale-written", "stale-social", "fresh"} <= remaining
    assert quarantined == [("stale-remove", "outside_7_day_window")]
