#!/usr/bin/env python3
"""Persist proof that a real GitHub schedule event reached the scheduler."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Mapping

import httpx


def build_payload(
    env: Mapping[str, str],
    *,
    captured_at: str | None = None,
) -> dict[str, list[dict[str, str]]]:
    if env.get("GITHUB_EVENT_NAME") != "schedule":
        raise ValueError("scheduler heartbeat requires a real schedule event")
    captured_at = captured_at or datetime.now(timezone.utc).isoformat()
    run_id = env.get("GITHUB_RUN_ID", "unknown")[:40]
    cron = env.get("GITHUB_EVENT_SCHEDULE", "unknown")[:80]
    head_sha = env.get("GITHUB_SHA", "unknown")[:64]
    return {
        "health": [
            {
                "platform": "system",
                "metric": "scheduler_delivery",
                "status": "healthy",
                "detail": (
                    "event=schedule; "
                    f"run_id={run_id}; cron={cron}; head_sha={head_sha}"
                ),
                "captured_at": captured_at,
            }
        ]
    }


def main() -> int:
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        print("SKIP_NON_SCHEDULE")
        return 0
    api = os.environ.get("SOCIAL_OPS_API_URL", "").rstrip("/")
    service_token = os.environ.get("SOCIAL_OPS_SERVICE_TOKEN", "")
    if not api or not service_token:
        print("scheduler heartbeat credentials are unavailable", file=sys.stderr)
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
        print(f"scheduler heartbeat sync failed: {type(exc).__name__}", file=sys.stderr)
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
