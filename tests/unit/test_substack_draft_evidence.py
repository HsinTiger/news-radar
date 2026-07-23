import argparse
import asyncio
import sqlite3

from substack_radar import compose


def test_local_written_and_remote_draft_are_distinct_evidence(monkeypatch, tmp_path):
    db_path = tmp_path / "news.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE news_items(id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO news_items(id) VALUES('source-1')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(compose, "NEWS_DB_PATH", db_path)

    compose._record_substack_evidence("source-1")
    conn = sqlite3.connect(db_path)
    written_at, draft_id, drafted_at = conn.execute(
        "SELECT substack_written_at,substack_draft_id,substack_drafted_at FROM news_items"
    ).fetchone()
    conn.close()
    assert written_at
    assert draft_id is None
    assert drafted_at is None

    compose._record_substack_evidence("source-1", draft_id=12345)
    conn = sqlite3.connect(db_path)
    written_at_2, draft_id, drafted_at = conn.execute(
        "SELECT substack_written_at,substack_draft_id,substack_drafted_at FROM news_items"
    ).fetchone()
    conn.close()
    assert written_at_2 == written_at
    assert draft_id == "12345"
    assert drafted_at


def test_required_remote_draft_fails_before_expensive_compose(monkeypatch):
    for name in (
        "SUBSTACK_AUTO_DRAFT",
        "SUBSTACK_COOKIES_STRING",
        "SUBSTACK_PUBLICATION_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    args = argparse.Namespace(
        mode="morning",
        no_draft=False,
        require_substack_draft=True,
    )
    assert asyncio.run(compose._run_inner(args)) == 5
