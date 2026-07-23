import json

from scripts import drain_substack, submit_substack


def test_duplicate_substack_source_merges_priority_and_submission_tags(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        submit_substack.dbmod,
        "DB_PATH",
        tmp_path / "news_radar.db",
    )
    submit_substack.dbmod.init_db()
    text = "同一份 Substack 觀點再次以 priority 投稿時，不應丟失新的控制面 lineage。"

    first = submit_substack.process_text(
        text,
        "owner essay",
        immediate=False,
        submission_id="substack-submit-001",
    )
    second = submit_substack.process_text(
        text,
        "owner essay",
        immediate=True,
        submission_id="substack-submit-002",
    )
    assert first["status"] == "created"
    assert second["status"] == "already_exists"

    conn = submit_substack.dbmod.get_conn()
    try:
        tags = json.loads(conn.execute("SELECT tags FROM news_items").fetchone()[0])
    finally:
        conn.close()
    assert set(tags) >= {
        "substack_source",
        "immediate",
        "control_submission:substack-submit-001",
        "control_submission:substack-submit-002",
    }


def test_unreadable_url_fails_before_false_source_queue(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        submit_substack.dbmod,
        "DB_PATH",
        tmp_path / "news_radar.db",
    )
    submit_substack.dbmod.init_db()
    monkeypatch.setattr(submit_substack, "_fetch_page_text", lambda _url: "")
    result = submit_substack.process_url(
        "https://example.com/paywalled",
        submission_id="substack-submit-003",
    )
    assert result["status"] == "error"
    conn = submit_substack.dbmod.get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 0
    finally:
        conn.close()


def test_short_owner_view_is_still_a_substack_compose_candidate(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "news_radar.db"
    monkeypatch.setattr(submit_substack.dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(drain_substack, "DB", db_path)
    submit_substack.dbmod.init_db()
    result = submit_substack.process_text(
        "短觀點也應由長文 composer 擴寫。",
        "owner seed",
        submission_id="substack-submit-004",
    )
    assert result["status"] == "created"
    candidates = drain_substack._candidates()
    assert [row[0] for row in candidates] == [result["id"]]

    conn = submit_substack.dbmod.get_conn()
    try:
        conn.execute(
            "UPDATE news_items SET substack_drafted_at='2099-01-01T00:00:00Z'"
        )
        conn.commit()
    finally:
        conn.close()
    assert drain_substack._candidates() == []
