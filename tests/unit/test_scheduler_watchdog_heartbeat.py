import json

import pytest

from scripts import scheduler_watchdog_heartbeat
from scripts.scheduler_watchdog_heartbeat import build_payload


DISPATCH_ID = "6a8d6354-a0d8-4b3f-8dc9-43d2b3219ca2"


def test_watchdog_heartbeat_requires_allowlisted_dispatch_source() -> None:
    with pytest.raises(ValueError, match="allowlisted dispatch source"):
        build_payload({"GITHUB_EVENT_NAME": "workflow_dispatch"})
    with pytest.raises(ValueError, match="allowlisted dispatch source"):
        build_payload(
            {
                "GITHUB_EVENT_NAME": "schedule",
                "SCHEDULER_WATCHDOG_SOURCE": "cloudflare_watchdog",
                "SCHEDULER_WATCHDOG_DISPATCH_ID": DISPATCH_ID,
            }
        )
    with pytest.raises(ValueError, match="allowlisted dispatch source"):
        build_payload(
            {
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "SCHEDULER_WATCHDOG_SOURCE": "cloudflare_watchdog",
                "SCHEDULER_WATCHDOG_DISPATCH_ID": "not-a-uuid",
            }
        )


def test_watchdog_payload_is_minimal_and_secret_free() -> None:
    env = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "SCHEDULER_WATCHDOG_SOURCE": "cloudflare_watchdog",
        "SCHEDULER_WATCHDOG_DISPATCH_ID": DISPATCH_ID,
        "GITHUB_RUN_ID": "123456",
        "GITHUB_SHA": "a" * 40,
        "SOCIAL_OPS_SERVICE_TOKEN": "must-not-leak",
    }
    payload = build_payload(env, captured_at="2026-07-27T11:27:00+00:00")
    row = payload["health"][0]

    assert row == {
        "platform": "system",
        "metric": "scheduler_watchdog_delivery",
        "status": "healthy",
        "detail": (
            "event=workflow_dispatch; source=cloudflare_watchdog; "
            f"dispatch_id={DISPATCH_ID}; run_id=123456; head_sha={'a' * 40}"
        ),
        "captured_at": "2026-07-27T11:27:00+00:00",
    }
    assert "must-not-leak" not in json.dumps(payload)


def test_main_skips_manual_dispatch_without_network(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("SCHEDULER_WATCHDOG_SOURCE", "manual")
    monkeypatch.setattr(
        scheduler_watchdog_heartbeat.httpx,
        "post",
        lambda *args, **kwargs: pytest.fail("manual event must not call network"),
    )

    assert scheduler_watchdog_heartbeat.main() == 0
    assert capsys.readouterr().out.strip() == "SKIP_NON_WATCHDOG"


def test_main_posts_watchdog_health(monkeypatch, capsys) -> None:
    calls = []

    class Response:
        def raise_for_status(self) -> None:
            return None

    def fake_post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        return Response()

    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("SCHEDULER_WATCHDOG_SOURCE", "cloudflare_watchdog")
    monkeypatch.setenv("SCHEDULER_WATCHDOG_DISPATCH_ID", DISPATCH_ID)
    monkeypatch.setenv("GITHUB_RUN_ID", "987654")
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setenv("SOCIAL_OPS_API_URL", "https://ops.example.test")
    monkeypatch.setenv("SOCIAL_OPS_SERVICE_TOKEN", "secret-service-token")
    monkeypatch.setattr(scheduler_watchdog_heartbeat.httpx, "post", fake_post)

    assert scheduler_watchdog_heartbeat.main() == 0
    assert len(calls) == 1
    url, headers, payload, timeout = calls[0]
    assert url == "https://ops.example.test/api/service/sync"
    assert headers == {"Authorization": "Bearer secret-service-token"}
    assert payload["health"][0]["metric"] == "scheduler_watchdog_delivery"
    assert timeout == 30
    assert "secret-service-token" not in capsys.readouterr().out
