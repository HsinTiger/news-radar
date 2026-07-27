import json

from scripts import drain_substack, submit_substack
from src.schema import NewsItem


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


def test_harvested_url_can_be_submitted_without_stealing_the_source_row(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "news_radar.db"
    monkeypatch.setattr(submit_substack.dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(drain_substack, "DB", db_path)
    submit_substack.dbmod.init_db()
    source_url = "https://example.com/official-release"
    conn = submit_substack.dbmod.get_conn()
    try:
        submit_substack.dbmod.upsert_news(
            conn,
            NewsItem(
                id="harvested-source",
                feed_name="official_feed",
                feed_tier="primary",
                source_type="article",
                url=source_url,
                title="Official release",
                published_at="2099-01-01T00:00:00+00:00",
                fetched_at="2099-01-01T00:00:00+00:00",
                clean_markdown="Original harvest row",
                word_count=3,
                tags=[],
                status="fetched",
            ),
        )
    finally:
        conn.close()
    monkeypatch.setattr(
        submit_substack,
        "_fetch_page_text",
        lambda _url: "Official evidence " * 20,
    )

    first = submit_substack.process_url(
        source_url,
        note="Owner long-form angle",
        immediate=True,
        submission_id="substack-submit-005",
    )
    second = submit_substack.process_url(
        source_url,
        note="Owner long-form angle",
        immediate=True,
        submission_id="substack-submit-006",
    )

    assert first["status"] == "created"
    assert second["status"] == "already_exists"
    conn = submit_substack.dbmod.get_conn()
    try:
        rows = conn.execute(
            "SELECT id,feed_name,url,tags FROM news_items ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    harvested = next(row for row in rows if row["id"] == "harvested-source")
    submitted = next(row for row in rows if row["id"] == first["id"])
    assert harvested["feed_name"] == "official_feed"
    assert harvested["url"] == source_url
    assert submitted["feed_name"] == "user_substack"
    assert submitted["url"].startswith(source_url + "#news-radar-substack=")
    assert set(json.loads(submitted["tags"])) >= {
        "substack_source",
        "immediate",
        "control_submission:substack-submit-005",
        "control_submission:substack-submit-006",
    }
    assert [row[0] for row in drain_substack._candidates(only_immediate=True)] == [
        first["id"]
    ]


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
    assert [
        row[0]
        for row in drain_substack._candidates(only_current_control=True)
    ] == [result["id"]]

    conn = submit_substack.dbmod.get_conn()
    try:
        conn.execute(
            "UPDATE news_items SET substack_drafted_at='2099-01-01T00:00:00Z'"
        )
        conn.commit()
    finally:
        conn.close()
    assert drain_substack._candidates() == []


def test_current_control_lane_prioritizes_immediate_and_excludes_legacy(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "news_radar.db"
    monkeypatch.setattr(submit_substack.dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(drain_substack, "DB", db_path)
    submit_substack.dbmod.init_db()

    normal = submit_substack.process_text(
        "一般控制面投稿也必須被已載入的 fast worker 服務。",
        "normal current",
        immediate=False,
        submission_id="substack-current-normal",
    )
    priority = submit_substack.process_text(
        "優先投稿應排在一般控制面投稿之前。",
        "priority current",
        immediate=True,
        submission_id="substack-current-priority",
    )
    conn = submit_substack.dbmod.get_conn()
    try:
        legacy = NewsItem(
            id="legacy-unverified",
            feed_name="user_substack",
            feed_tier="primary",
            source_type="text",
            url="manual-text://legacy-unverified",
            title="legacy",
            published_at="2020-01-01T00:00:00+00:00",
            fetched_at="2020-01-01T00:00:00+00:00",
            clean_markdown="Historical row without control-plane lineage.",
            word_count=6,
            tags=["substack_source"],
            status="fetched",
        )
        submit_substack.dbmod.upsert_news(conn, legacy)
    finally:
        conn.close()

    selected = drain_substack._candidates(only_current_control=True)
    assert [row[0] for row in selected] == [priority["id"], normal["id"]]
    assert "legacy-unverified" not in {row[0] for row in selected}
