import sqlite3
import sys
import types
from types import SimpleNamespace

from scripts import drain_substack
from substack_radar import compose
from substack_radar import draft_receipts


def _install_api(monkeypatch, api_class) -> None:
    module = types.ModuleType("substack")
    module.Api = api_class
    monkeypatch.setitem(sys.modules, "substack", module)


def test_publication_identity_rejects_a_publication_homepage() -> None:
    assert compose._published_identity(
        {"url": "https://writer.substack.com"},
        draft_id=314,
        publication_url="https://writer.substack.com",
    ) is None


def test_publish_existing_draft_uses_upstream_sequence_and_public_readback(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "cookie")
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", "https://writer.substack.com")
    calls = []

    class FakeApi:
        def __init__(self, **_kwargs):
            pass

        def prepublish_draft(self, draft_id):
            calls.append(("prepublish", draft_id))
            return {"ok": True}

        def publish_draft(self, draft_id, send=True, share_automatically=False):
            calls.append(("publish", draft_id, send, share_automatically))
            return {
                "id": draft_id,
                "canonical_url": "https://writer.substack.com/p/deep-analysis",
            }

    _install_api(monkeypatch, FakeApi)
    monkeypatch.setattr(compose, "get_remote_receipt", lambda _source_id: None)
    monkeypatch.setattr(compose, "_public_url_is_live", lambda _url: True)
    saved = []
    monkeypatch.setattr(
        compose,
        "store_publish_intent",
        lambda source_id, draft_id: saved.append(("intent", source_id, draft_id)),
    )
    monkeypatch.setattr(
        compose,
        "store_publication_receipt",
        lambda source_id, draft_id, post_id, public_url: saved.append(
            ("published", source_id, draft_id, post_id, public_url)
        ),
    )

    result = compose.publish_substack_draft(314, source_id="source-1")

    assert calls == [
        ("prepublish", 314),
        ("publish", 314, True, False),
    ]
    assert result == {
        "post_id": "314",
        "public_url": "https://writer.substack.com/p/deep-analysis",
    }
    assert saved[0] == ("intent", "source-1", 314)
    assert saved[1][0] == "published"


def test_ambiguous_publish_intent_only_checks_readback_and_never_resends(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SUBSTACK_COOKIES_STRING", "cookie")
    monkeypatch.setenv("SUBSTACK_PUBLICATION_URL", "https://writer.substack.com")

    class FakeApi:
        def __init__(self, **_kwargs):
            pass

        def prepublish_draft(self, _draft_id):
            raise AssertionError("must not prepublish after an ambiguous publish intent")

        def publish_draft(self, *_args, **_kwargs):
            raise AssertionError("must not resend a possibly delivered newsletter")

        def get_published_posts(self, **_kwargs):
            return [
                {
                    "id": 314,
                    "canonical_url": "https://writer.substack.com/p/deep-analysis",
                }
            ]

    _install_api(monkeypatch, FakeApi)
    monkeypatch.setattr(
        compose,
        "get_remote_receipt",
        lambda _source_id: {
            "draft_id": "314",
            "created_at": "2099-01-01T00:00:00+00:00",
            "publish_attempted_at": "2099-01-01T00:01:00+00:00",
        },
    )
    monkeypatch.setattr(compose, "_public_url_is_live", lambda _url: True)
    saved = []
    monkeypatch.setattr(
        compose,
        "store_publication_receipt",
        lambda *args: saved.append(args),
    )

    assert compose.publish_substack_draft(314, source_id="source-1") == {
        "post_id": "314",
        "public_url": "https://writer.substack.com/p/deep-analysis",
    }
    assert saved


def test_publication_receipt_reconciles_full_evidence(tmp_path) -> None:
    db_path = tmp_path / "news.db"
    receipt_path = tmp_path / "receipts.json"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE news_items(id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO news_items(id) VALUES('source-1')")
    conn.commit()
    conn.close()

    draft_receipts.store_publish_intent("source-1", 314, path=receipt_path)
    draft_receipts.store_publication_receipt(
        "source-1",
        314,
        "314",
        "https://writer.substack.com/p/deep-analysis",
        path=receipt_path,
        published_at="2099-01-01T00:02:00+00:00",
    )
    assert draft_receipts.get_remote_receipt("source-1", path=receipt_path)["post_id"] == "314"

    protected, applied = draft_receipts.reconcile_remote_receipts(db_path, path=receipt_path)
    assert protected == set()
    assert applied == 1
    assert not receipt_path.exists()
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT substack_draft_id,substack_post_id,substack_post_url,"
        "substack_published_at FROM news_items WHERE id='source-1'"
    ).fetchone()
    conn.close()
    assert row == (
        "314",
        "314",
        "https://writer.substack.com/p/deep-analysis",
        "2099-01-01T00:02:00+00:00",
    )


def test_record_publication_evidence_is_distinct_from_draft(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "news.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE news_items(id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO news_items(id) VALUES('source-1')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(compose, "NEWS_DB_PATH", db_path)

    assert compose._record_substack_evidence(
        "source-1",
        draft_id=314,
        publication={
            "post_id": "314",
            "public_url": "https://writer.substack.com/p/deep-analysis",
        },
    )
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT substack_draft_id,substack_post_id,substack_post_url,"
        "substack_published_at FROM news_items"
    ).fetchone()
    conn.close()
    assert row[0:3] == (
        "314",
        "314",
        "https://writer.substack.com/p/deep-analysis",
    )
    assert row[3]


def test_drain_passes_publish_now_and_keeps_unproven_item_retryable(
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
             substack_draft_id TEXT,substack_drafted_at TEXT,
             substack_published_at TEXT
           )"""
    )
    conn.execute(
        """INSERT INTO news_items VALUES(
             'source-1','Owner view',10,'manual://1','Useful source',
             '["publish_now","control_submission:publish-001"]',
             'user_substack','2099-01-01T00:00:00Z','draft-314',
             '2099-01-01T00:01:00Z',NULL
           )"""
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(drain_substack, "DB", db_path)
    monkeypatch.setattr(drain_substack, "DONE_FILE", done_path)
    monkeypatch.setattr(drain_substack, "RECEIPTS_FILE", receipt_path)
    monkeypatch.setattr(sys, "argv", ["drain_substack.py", "--no-enrich"])
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=drain_substack.SUBSTACK_PUBLISH_UNPROVEN)

    monkeypatch.setattr(drain_substack.subprocess, "run", fake_run)
    assert drain_substack.main() == 1
    assert "--publish-now" in commands[0]
    assert not done_path.exists()
