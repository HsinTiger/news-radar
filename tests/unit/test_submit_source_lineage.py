import json

import pytest

from scripts import submit_source


def test_duplicate_source_merges_control_submission_lineage(monkeypatch, tmp_path):
    monkeypatch.setattr(submit_source.dbmod, "DB_PATH", tmp_path / "news_radar.db")
    submit_source.dbmod.init_db()
    text = "同一份 owner 原始內容應該去重，但每次控制面 submission 都必須留下 lineage。"

    first = submit_source.process_text(
        text,
        "owner view",
        ["fb", "threads"],
        "submission-queue-001",
    )
    second = submit_source.process_text(
        text,
        "owner view",
        ["ig"],
        "submission-queue-002",
    )

    assert first["status"] == "created"
    assert second["status"] == "already_exists"
    conn = submit_source.dbmod.get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 1
        tags = json.loads(conn.execute("SELECT tags FROM news_items").fetchone()[0])
    finally:
        conn.close()
    assert set(tags) >= {
        "platform:fb",
        "platform:ig",
        "platform:threads",
        "control_submission:submission-queue-001",
        "control_submission:submission-queue-002",
        "control_route:submission-queue-001:fb,threads",
        "control_route:submission-queue-002:ig",
    }


def test_invalid_platform_fails_closed_without_creating_source(monkeypatch, tmp_path):
    monkeypatch.setattr(submit_source.dbmod, "DB_PATH", tmp_path / "news_radar.db")
    submit_source.dbmod.init_db()
    with pytest.raises(ValueError, match="unknown platforms"):
        submit_source.process_text("owner source", platforms=["youtube"])
    conn = submit_source.dbmod.get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 0
    finally:
        conn.close()


def test_cli_exit_code_never_reports_error_as_queued_success() -> None:
    assert submit_source._result_exit_code({"status": "created"}) == 0
    assert submit_source._result_exit_code({"status": "already_exists"}) == 0
    assert submit_source._result_exit_code({"status": "error"}) == 1
    assert submit_source._result_exit_code({"total": 1, "errors": ["retained"]}) == 1


def test_unreadable_meta_url_fails_before_false_content_queue(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(submit_source.dbmod, "DB_PATH", tmp_path / "news_radar.db")
    submit_source.dbmod.init_db()
    monkeypatch.setattr(submit_source, "_fetch_page_text", lambda _url: None)
    result = submit_source.process_url(
        "https://example.com/paywalled",
        platforms=["threads"],
        submission_id="submission-queue-003",
    )
    assert result["status"] == "error"
    assert submit_source._result_exit_code(result) == 1
    conn = submit_source.dbmod.get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 0
    finally:
        conn.close()
