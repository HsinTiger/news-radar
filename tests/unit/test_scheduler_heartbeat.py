import json

import pytest

from scripts import scheduler_heartbeat
from scripts.scheduler_heartbeat import build_payload


def test_scheduler_heartbeat_requires_a_real_schedule_event() -> None:
    with pytest.raises(ValueError, match="real schedule event"):
        build_payload({"GITHUB_EVENT_NAME": "workflow_dispatch"})


def test_scheduler_heartbeat_payload_is_minimal_and_secret_free() -> None:
    env = {
        "GITHUB_EVENT_NAME": "schedule",
        "GITHUB_RUN_ID": "123456",
        "GITHUB_EVENT_SCHEDULE": "17 10,11 * * *",
        "GITHUB_SHA": "a" * 40,
        "SOCIAL_OPS_SERVICE_TOKEN": "must-not-leak",
    }
    payload = build_payload(env, captured_at="2026-07-27T10:17:00+00:00")
    row = payload["health"][0]

    assert row == {
        "platform": "system",
        "metric": "scheduler_delivery",
        "status": "healthy",
        "detail": (
            "event=schedule; run_id=123456; cron=17 10,11 * * *; "
            f"head_sha={'a' * 40}"
        ),
        "captured_at": "2026-07-27T10:17:00+00:00",
    }
    assert "must-not-leak" not in json.dumps(payload)


def test_main_skips_manual_events_without_network(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setattr(
        scheduler_heartbeat.httpx,
        "post",
        lambda *args, **kwargs: pytest.fail("manual event must not call network"),
    )

    assert scheduler_heartbeat.main() == 0
    assert capsys.readouterr().out.strip() == "SKIP_NON_SCHEDULE"


def test_main_posts_only_minimal_health_payload(monkeypatch, capsys) -> None:
    calls = []

    class Response:
        def raise_for_status(self) -> None:
            return None

    def fake_post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        return Response()

    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("GITHUB_RUN_ID", "987654")
    monkeypatch.setenv("GITHUB_EVENT_SCHEDULE", "17 10,11 * * *")
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setenv("SOCIAL_OPS_API_URL", "https://ops.example.test")
    monkeypatch.setenv("SOCIAL_OPS_SERVICE_TOKEN", "secret-service-token")
    monkeypatch.setattr(scheduler_heartbeat.httpx, "post", fake_post)

    assert scheduler_heartbeat.main() == 0
    assert len(calls) == 1
    url, headers, payload, timeout = calls[0]
    assert url == "https://ops.example.test/api/service/sync"
    assert headers == {"Authorization": "Bearer secret-service-token"}
    assert payload["health"][0]["metric"] == "scheduler_delivery"
    assert timeout == 30
    assert "secret-service-token" not in capsys.readouterr().out
