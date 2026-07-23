import json
import sqlite3
import sys
import types
from types import SimpleNamespace

from scripts import drain_substack
from substack_radar import compose
from substack_radar.draft_receipts import (
    reconcile_remote_receipts,
    store_remote_receipt,
)


def _source_db(path, source_id: str = "source-1") -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE news_items(id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO news_items(id) VALUES(?)", (source_id,))
    conn.commit()
    conn.close()


def test_remote_receipt_reconciles_sqlite_and_clears_itself(tmp_path) -> None:
    db_path = tmp_path / "news.db"
    receipt_path = tmp_path / "receipts.json"
    _source_db(db_path)
    store_remote_receipt(
        "source-1",
        12345,
        path=receipt_path,
        created_at="2099-01-01T00:00:00+00:00",
    )

    protected, applied = reconcile_remote_receipts(db_path, path=receipt_path)
    assert protected == set()
    assert applied == 1
    assert not receipt_path.exists()

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT substack_written_at,substack_draft_id,substack_drafted_at "
        "FROM news_items WHERE id='source-1'"
    ).fetchone()
    conn.close()
    assert row == (
        "2099-01-01T00:00:00+00:00",
        "12345",
        "2099-01-01T00:00:00+00:00",
    )


def test_receipt_without_source_row_stays_protected(tmp_path) -> None:
    db_path = tmp_path / "news.db"
    receipt_path = tmp_path / "receipts.json"
    _source_db(db_path, source_id="another-source")
    store_remote_receipt("source-1", 12345, path=receipt_path)

    protected, applied = reconcile_remote_receipts(db_path, path=receipt_path)
    assert protected == {"source-1"}
    assert applied == 0
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["receipts"][
        "source-1"
    ]["draft_id"] == "12345"


def test_conflicting_canonical_draft_id_is_not_overwritten(tmp_path) -> None:
    db_path = tmp_path / "news.db"
    receipt_path = tmp_path / "receipts.json"
    _source_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE news_items ADD COLUMN substack_draft_id TEXT")
    conn.execute(
        "UPDATE news_items SET substack_draft_id='canonical-1' WHERE id='source-1'"
    )
    conn.commit()
    conn.close()
    store_remote_receipt("source-1", "receipt-2", path=receipt_path)

    protected, applied = reconcile_remote_receipts(db_path, path=receipt_path)
    assert protected == {"source-1"}
    assert applied == 0
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT substack_draft_id FROM news_items WHERE id='source-1'"
    ).fetchone()[0] == "canonical-1"
    conn.close()


def test_malformed_receipt_fails_closed_without_overwrite(tmp_path) -> None:
    db_path = tmp_path / "news.db"
    receipt_path = tmp_path / "receipts.json"
    _source_db(db_path)
    receipt_path.write_text('{"schema_version":1,"receipts":{"source-1":{}}}', encoding="utf-8")
    original = receipt_path.read_text(encoding="utf-8")

    try:
        reconcile_remote_receipts(db_path, path=receipt_path)
    except ValueError as exc:
        assert "incomplete Substack receipt" in str(exc)
    else:
        raise AssertionError("malformed receipt must fail closed")
    assert receipt_path.read_text(encoding="utf-8") == original


def test_post_draft_stores_receipt_before_returning_success(monkeypatch, tmp_path) -> None:
    article = tmp_path / "article.md"
    article.write_text("# Title\n\n*Subtitle*\n\nUseful body", encoding="utf-8")
    monkeypatch.setenv("SUBSTACK_AUTO_DRAFT", "1")
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "cookie")
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", "https://example.substack.com")

    class FakeApi:
        def __init__(self, **_kwargs):
            pass

        def get_user_id(self):
            return 7

        def post_draft(self, _payload):
            return {"id": 98765}

    class FakePost:
        def __init__(self, **_kwargs):
            pass

        def from_markdown(self, _body, api=None):
            assert api is not None

        def get_draft(self):
            return {"draft": True}

    substack_module = types.ModuleType("substack")
    substack_module.Api = FakeApi
    post_module = types.ModuleType("substack.post")
    post_module.Post = FakePost
    monkeypatch.setitem(sys.modules, "substack", substack_module)
    monkeypatch.setitem(sys.modules, "substack.post", post_module)
    saved = []
    monkeypatch.setattr(
        compose,
        "store_remote_receipt",
        lambda source_id, draft_id: saved.append((source_id, draft_id)),
    )

    assert compose.push_to_substack_draft(
        article_md_path=article,
        title="Title",
        subtitle="Subtitle",
        source_id="source-1",
    ) == 98765
    assert saved == [("source-1", 98765)]


def test_pending_evidence_exit_is_marked_done_and_never_recomposed(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "news.db"
    done_path = tmp_path / "done.json"
    receipt_path = tmp_path / "receipts.json"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE news_items(
             id TEXT PRIMARY KEY,title TEXT,word_count INTEGER,url TEXT,
             clean_markdown TEXT,tags TEXT,feed_name TEXT,fetched_at TEXT,
             substack_drafted_at TEXT
           )"""
    )
    conn.execute(
        """INSERT INTO news_items VALUES(
             'source-1','Owner view',10,'manual://1','Useful source','[]',
             'user_substack','2099-01-01T00:00:00Z',NULL
           )"""
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(drain_substack, "DB", db_path)
    monkeypatch.setattr(drain_substack, "DONE_FILE", done_path)
    monkeypatch.setattr(drain_substack, "RECEIPTS_FILE", receipt_path)
    monkeypatch.setattr(sys, "argv", ["drain_substack.py", "--no-enrich"])
    calls = []

    def fake_run(*_args, **_kwargs):
        calls.append(1)
        return SimpleNamespace(returncode=drain_substack.REMOTE_DRAFT_EVIDENCE_PENDING)

    monkeypatch.setattr(drain_substack.subprocess, "run", fake_run)
    assert drain_substack.main() == 1
    assert json.loads(done_path.read_text(encoding="utf-8"))["done"] == ["source-1"]
    assert len(calls) == 1

    assert drain_substack.main() == 0
    assert len(calls) == 1
