from __future__ import annotations

import builtins

import pytest

import run_harvest


def test_select_feed_config_filters_without_mutating_original() -> None:
    config = {
        "feeds": [
            {"name": "official", "tags": ["primary-record"]},
            {"name": "media", "tags": ["news"]},
        ],
        "filters": {"max_age_hours": 168},
    }

    selected = run_harvest._select_feed_config(config, "primary-record")

    assert [feed["name"] for feed in selected["feeds"]] == ["official"]
    assert len(config["feeds"]) == 2


def test_select_feed_config_fails_closed_for_unknown_tag() -> None:
    with pytest.raises(ValueError, match="no configured feeds carry tag"):
        run_harvest._select_feed_config({"feeds": []}, "primary-record")


@pytest.mark.asyncio
async def test_disposable_harvest_uses_tag_filter_and_writes_no_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {
        "feeds": [
            {"name": "official", "tags": ["primary-record"]},
            {"name": "media", "tags": ["news"]},
        ],
        "filters": {"max_age_hours": 168},
    }
    observed: dict = {}

    async def fake_harvest_all_feeds(
        selected: dict,
        *,
        feed_diagnostics: dict | None = None,
    ) -> list:
        observed["feeds"] = selected["feeds"]
        assert feed_diagnostics is not None
        feed_diagnostics["official"] = {
            "status": "ok",
            "error_type": None,
            "entries_raw": 0,
            "entries_kept": 0,
        }
        return []

    class FakeConnection:
        def close(self) -> None:
            pass

    def forbidden_open(*args, **kwargs):
        raise AssertionError("execution log opened for disposable harvest")

    monkeypatch.setattr(run_harvest, "load_config", lambda: config)
    monkeypatch.setattr(run_harvest, "harvest_all_feeds", fake_harvest_all_feeds)
    monkeypatch.setattr(run_harvest.dbmod, "init_db", lambda: None)
    monkeypatch.setattr(run_harvest.dbmod, "get_conn", lambda: FakeConnection())
    monkeypatch.setattr(builtins, "open", forbidden_open)

    report = await run_harvest.run_harvest_once(
        feed_tag="primary-record",
        write_log=False,
    )

    assert [feed["name"] for feed in observed["feeds"]] == ["official"]
    assert report.feeds_checked == 1
    assert report.feed_results["official"]["status"] == "ok"
