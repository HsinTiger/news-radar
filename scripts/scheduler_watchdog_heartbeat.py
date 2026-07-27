#!/usr/bin/env python3
"""Persist proof that the independent Cloudflare watchdog reached Actions."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Mapping

import httpx


WATCHDOG_SOURCE = "cloudflare_watchdog"
DISPATCH_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def is_watchdog_event(env: Mapping[str, str]) -> bool:
    return (
        env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        and env.get("SCHEDULER_WATCHDOG_SOURCE") == WATCHDOG_SOURCE
        and bool(DISPATCH_ID_RE.fullmatch(env.get("SCHEDULER_WATCHDOG_DISPATCH_ID", "")))
    )


def build_payload(
    env: Mapping[str, str],
    *,
    captured_at: str | None = None,
) -> dict[str, list[dict[str, str]]]:
    if not is_watchdog_event(env):
        raise ValueError("watchdog heartbeat requires the allowlisted dispatch source")
    captured_at = captured_at or datetime.now(timezone.utc).isoformat()
    dispatch_id = env["SCHEDULER_WATCHDOG_DISPATCH_ID"].lower()
    run_id = env.get("GITHUB_RUN_ID", "unknown")[:40]
    head_sha = env.get("GITHUB_SHA", "unknown")[:64]
    return {
        "health": [
            {
                "platform": "system",
                "metric": "scheduler_watchdog_delivery",
                "status": "healthy",
                "detail": (
                    "event=workflow_dispatch; source=cloudflare_watchdog; "
                    f"dispatch_id={dispatch_id}; run_id={run_id}; head_sha={head_sha}"
                ),
                "captured_at": captured_at,
            }
        ]
    }


def main() -> int:
    if not is_watchdog_event(os.environ):
        print("SKIP_NON_WATCHDOG")
        return 0
    api = os.environ.get("SOCIAL_OPS_API_URL", "").rstrip("/")
    service_token = os.environ.get("SOCIAL_OPS_SERVICE_TOKEN", "")
    if not api or not service_token:
        print("watchdog heartbeat credentials are unavailable", file=sys.stderr)
        return 2
    payload = build_payload(os.environ)
    try:
        response = httpx.post(
            f"{api}/api/service/sync",
            headers={"Authorization": f"Bearer {service_token}"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"watchdog heartbeat sync failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    row = payload["health"][0]
    print(
        json.dumps(
            {
                "ok": True,
                "metric": row["metric"],
                "captured_at": row["captured_at"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
