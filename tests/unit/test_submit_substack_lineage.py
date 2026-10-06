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


def test_publish_now_is_explicit_source_lineage(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        submit_substack.dbmod,
        "DB_PATH",
        tmp_path / "news_radar.db",
    )
    submit_substack.dbmod.init_db()
    result = submit_substack.process_text(
        "這是一篇要在品質閘門通過後立即公開的長文素材。",
        "owner publish",
        immediate=True,
        publish_now=True,
        submission_id="substack-publish-001",
    )
    assert result["status"] == "created"
    conn = submit_substack.dbmod.get_conn()
    try:
        tags = set(json.loads(conn.execute("SELECT tags FROM news_items").fetchone()[0]))
    finally:
        conn.close()
    assert {
        "immediate",
        "publish_now",
        "control_submission:substack-publish-001",
        "control_substack_route:substack-publish-001:publish_now",
    } <= tags


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


# --- 儀表板「重產新版本」（2026-10-06）：當成獨立新稿，舊的不動 -------------------

def _fresh_db(monkeypatch, tmp_path):
    db_path = tmp_path / "news_radar.db"
    monkeypatch.setattr(submit_substack.dbmod, "DB_PATH", db_path)
    monkeypatch.setattr(drain_substack, "DB", db_path)
    submit_substack.dbmod.init_db()


def test_variant_of_same_source_becomes_a_separate_draft_candidate(monkeypatch, tmp_path) -> None:
    _fresh_db(monkeypatch, tmp_path)
    text = "同一份素材，主編想換一個角度重寫一版，舊版草稿要保留。" * 3
    original = submit_substack.process_text(
        text, "原本的角度", immediate=True, submission_id="substack-submit-101")
    variant = submit_substack.process_text(
        text, "新的提示：更白話", immediate=True, submission_id="substack-submit-102", variant=True)
    assert (original["status"], variant["status"]) == ("created", "created")
    assert original["id"] != variant["id"]
    candidates = {row[0]: row for row in drain_substack._candidates(only_current_control=True)}
    assert set(candidates) == {original["id"], variant["id"]}
    assert candidates[variant["id"]][1] == "新的提示：更白話"        # 新提示就是寫手看到的標題
    assert "regenerated" in candidates[variant["id"]][5]


def test_same_variant_submission_redelivered_is_idempotent(monkeypatch, tmp_path) -> None:
    _fresh_db(monkeypatch, tmp_path)
    text = "雲端重送同一筆重產投稿，不能變成兩篇。" * 4
    first = submit_substack.process_text(text, "x", immediate=True,
                                         submission_id="substack-submit-201", variant=True)
    again = submit_substack.process_text(text, "x", immediate=True,
                                         submission_id="substack-submit-201", variant=True)
    assert (first["status"], again["status"]) == ("created", "already_exists")
    assert first["id"] == again["id"]


def test_variant_of_url_source_does_not_collide_on_unique_url(monkeypatch, tmp_path) -> None:
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setattr(submit_substack, "_fetch_page_text", lambda _url: "Readable evidence " * 20)
    url = "https://example.com/deep-interview"
    first = submit_substack.process_url(url, "角度一", immediate=True, submission_id="substack-submit-301")
    second = submit_substack.process_url(url, "角度二", immediate=True,
                                         submission_id="substack-submit-302", variant=True)
    assert (first["status"], second["status"]) == ("created", "created")
    assert first["id"] != second["id"]


def test_variant_requires_a_submission_id(monkeypatch, tmp_path) -> None:
    import pytest
    _fresh_db(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        submit_substack.process_text("素材" * 50, "x", variant=True)


def test_plain_resubmission_still_merges_without_variant(monkeypatch, tmp_path) -> None:
    """既有規則不變：不是重產的重複投稿照樣併入同一篇。"""
    _fresh_db(monkeypatch, tmp_path)
    text = "不是重產，只是又送了一次。" * 5
    a = submit_substack.process_text(text, "x", submission_id="substack-submit-401")
    b = submit_substack.process_text(text, "x", submission_id="substack-submit-402")
    assert (a["status"], b["status"]) == ("created", "already_exists")
