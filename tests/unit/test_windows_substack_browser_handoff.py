import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import windows_substack_browser_handoff as handoff


@pytest.fixture(autouse=True)
def _writer_mode_live(monkeypatch):
    """讓測試不受線上停機閘門影響。

    config/windows_writer_mode 是營運開關；ops 把它設成 paused 時，
    這些測試不該跟著失敗——測試驗的是 handoff 契約，不是當下的營運狀態。
    """
    monkeypatch.setenv("WINDOWS_WRITER_MODE", "live")




TAIPEI = timezone(timedelta(hours=8))


def _artifact(
    drafts_root: Path,
    *,
    day: str,
    folder: str,
    title: str,
    created_at: str,
    source_id: str | None,
) -> Path:
    artifact_dir = drafts_root / day / folder
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "Article_Substack.md").write_text(
        f"# {title}\n\n*subtitle*\n\nreader-ready body",
        encoding="utf-8",
    )
    (artifact_dir / "cover.png").write_bytes(b"png")
    (artifact_dir / "metadata.json").write_text(
        json.dumps(
            {
                "title": title,
                "subtitle": "subtitle",
                "mode": "podcast" if folder.startswith("podcast_") else "company",
                "generated_by": "Codex CLI · 模型 gpt-latest",
                "source": {"id": source_id},
                "created_at": created_at,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return artifact_dir


def test_prepare_podcast_handoff_selects_only_two_current_run_artifacts(tmp_path: Path) -> None:
    drafts_root = tmp_path / "drafts"
    started_at = datetime(2026, 8, 8, 12, 0, tzinfo=TAIPEI)
    _artifact(
        drafts_root,
        day="2026-08-08",
        folder="podcast_stale",
        title="stale",
        created_at="2026-08-08T11:59:59+08:00",
        source_id="stale-source",
    )
    first = _artifact(
        drafts_root,
        day="2026-08-08",
        folder="podcast_first",
        title="first",
        created_at="2026-08-08T12:10:00+08:00",
        source_id="source-1",
    )
    second = _artifact(
        drafts_root,
        day="2026-08-08",
        folder="podcast_second",
        title="second",
        created_at="2026-08-08T12:20:00+08:00",
        source_id="source-2",
    )
    manifest_path = tmp_path / "handoff.json"

    manifest = handoff.prepare_handoff(
        "podcast-batch",
        drafts_root=drafts_root,
        started_at=started_at,
        manifest_path=manifest_path,
        now=datetime(2026, 8, 8, 12, 21, tzinfo=TAIPEI),
    )

    assert manifest["status"] == "pending_browser_drafts"
    assert manifest["expected_count"] == 2
    assert manifest["audience"] == "everyone"
    assert manifest["remote_action"] == "draft_only"
    assert [row["title"] for row in manifest["artifacts"]] == ["first", "second"]
    assert [row["artifact_dir"] for row in manifest["artifacts"]] == [
        str(first.resolve()),
        str(second.resolve()),
    ]
    assert all(row["remote_draft_id"] is None for row in manifest["artifacts"])
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


def test_prepare_handoff_fails_closed_when_expected_count_is_missing(tmp_path: Path) -> None:
    drafts_root = tmp_path / "drafts"
    started_at = datetime(2026, 8, 8, 12, 0, tzinfo=TAIPEI)
    _artifact(
        drafts_root,
        day="2026-08-08",
        folder="podcast_only_one",
        title="only one",
        created_at="2026-08-08T12:10:00+08:00",
        source_id="source-1",
    )

    with pytest.raises(handoff.HandoffError, match="expected 2 current-run artifacts"):
        handoff.prepare_handoff(
            "podcast-batch",
            drafts_root=drafts_root,
            started_at=started_at,
            manifest_path=tmp_path / "handoff.json",
        )


def test_record_remote_drafts_requires_matching_editor_urls_and_completes_manifest(
    tmp_path: Path,
) -> None:
    drafts_root = tmp_path / "drafts"
    started_at = datetime(2026, 8, 8, 12, 0, tzinfo=TAIPEI)
    first = _artifact(
        drafts_root,
        day="2026-08-08",
        folder="podcast_first",
        title="first",
        created_at="2026-08-08T12:10:00+08:00",
        source_id="source-1",
    )
    _artifact(
        drafts_root,
        day="2026-08-08",
        folder="podcast_second",
        title="second",
        created_at="2026-08-08T12:20:00+08:00",
        source_id="source-2",
    )
    manifest_path = tmp_path / "handoff.json"
    handoff.prepare_handoff(
        "podcast-batch",
        drafts_root=drafts_root,
        started_at=started_at,
        manifest_path=manifest_path,
        now=datetime(2026, 8, 8, 12, 21, tzinfo=TAIPEI),
    )

    partial = handoff.record_remote_draft(
        manifest_path,
        title="first",
        draft_id="210300001",
        editor_url="https://hsin73.substack.com/publish/post/210300001",
        drafted_at="2026-08-08T12:30:00+08:00",
    )
    assert partial["status"] == "pending_browser_drafts"
    first_metadata = json.loads((first / "metadata.json").read_text(encoding="utf-8"))
    assert first_metadata["remote_draft"] == {
        "id": "210300001",
        "editor_url": "https://hsin73.substack.com/publish/post/210300001",
        "drafted_at": "2026-08-08T12:30:00+08:00",
        "transport": "substack_browser_ui",
        "audience": "everyone",
    }

    with pytest.raises(handoff.HandoffError, match="does not match draft id"):
        handoff.record_remote_draft(
            manifest_path,
            title="second",
            draft_id="210300002",
            editor_url="https://hsin73.substack.com/publish/post/999999999",
        )

    complete = handoff.record_remote_draft(
        manifest_path,
        title="second",
        draft_id="210300002",
        editor_url="https://hsin73.substack.com/publish/post/210300002",
        drafted_at="2026-08-08T12:31:00+08:00",
    )
    assert complete["status"] == "complete"
    assert handoff.verify_handoff(manifest_path)["status"] == "complete"
