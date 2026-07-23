#!/usr/bin/env python3
"""Collect governed follower snapshots without publishing content."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import httpx


def _metric_value(payload: Any, name: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get(name)
    if isinstance(direct, (int, float)):
        return max(0, int(direct))
    for item in payload.get("data", []) if isinstance(payload.get("data"), list) else []:
        if not isinstance(item, dict) or item.get("name") != name:
            continue
        total = item.get("total_value")
        if isinstance(total, dict) and isinstance(total.get("value"), (int, float)):
            return max(0, int(total["value"]))
        values = item.get("values") or []
        if values and isinstance(values[0], dict) and isinstance(values[0].get("value"), (int, float)):
            return max(0, int(values[0]["value"]))
    return None


def _error_detail(payload: Any, status_code: int) -> str:
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    message = str(error.get("message") or "platform response did not contain a follower count")
    code = error.get("code")
    return f"http={status_code}; code={code}; message={message[:300]}"


def collect(client: httpx.Client, env: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    captured_at = datetime.now(timezone.utc).isoformat()
    graph_version = env.get("META_GRAPH_VERSION", "v20.0")
    requests = {
        "facebook": {
            "url": f"https://graph.facebook.com/{graph_version}/{env.get('FB_PAGE_ID', '')}",
            "params": {"fields": "followers_count,fan_count", "access_token": env.get("FB_PAGE_ACCESS_TOKEN", "")},
            "required": ("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN"),
        },
        "instagram": {
            "url": f"https://graph.facebook.com/{graph_version}/{env.get('IG_BUSINESS_ACCOUNT_ID', '')}",
            "params": {"fields": "followers_count,media_count", "access_token": env.get("IG_ACCESS_TOKEN", "")},
            "required": ("IG_BUSINESS_ACCOUNT_ID", "IG_ACCESS_TOKEN"),
        },
        "threads": {
            "url": f"https://graph.threads.net/v1.0/{env.get('THREADS_USER_ID', '')}/threads_insights",
            "params": {"metric": "followers_count", "access_token": env.get("THREADS_ACCESS_TOKEN", "")},
            "required": ("THREADS_USER_ID", "THREADS_ACCESS_TOKEN"),
        },
    }
    audience: list[dict[str, Any]] = []
    health: list[dict[str, Any]] = []
    for platform, spec in requests.items():
        missing = [name for name in spec["required"] if not env.get(name)]
        if missing:
            health.append(
                {
                    "platform": platform,
                    "metric": "audience",
                    "status": "unknown",
                    "detail": f"missing runtime credentials: {','.join(missing)}",
                    "captured_at": captured_at,
                }
            )
            continue
        try:
            response = client.get(spec["url"], params=spec["params"], timeout=30)
            payload = response.json()
        except Exception as exc:
            health.append(
                {
                    "platform": platform,
                    "metric": "audience",
                    "status": "error",
                    "detail": f"request exception: {str(exc)[:300]}",
                    "captured_at": captured_at,
                }
            )
            continue
        followers = _metric_value(payload, "followers_count")
        if platform == "facebook" and followers is None:
            followers = _metric_value(payload, "fan_count")
        status = "healthy" if response.status_code == 200 and followers is not None else "degraded"
        detail = (
            f"followers_count={followers}"
            if status == "healthy"
            else _error_detail(payload, response.status_code)
        )
        health.append(
            {
                "platform": platform,
                "metric": "audience",
                "status": status,
                "detail": detail,
                "captured_at": captured_at,
            }
        )
        if followers is not None:
            audience.append(
                {
                    "platform": platform,
                    "captured_at": captured_at,
                    "followers": followers,
                    "followers_delta_7d": None,
                    "source": "platform_api",
                    "metric_status": "ok" if status == "healthy" else "degraded",
                    "raw_summary": {"collector": "audience_snapshot_v1"},
                }
            )
    return {"audience": audience, "health": health}


def main() -> int:
    api = os.environ.get("SOCIAL_OPS_API_URL", "").rstrip("/")
    service_token = os.environ.get("SOCIAL_OPS_SERVICE_TOKEN", "")
    if not api or not service_token:
        print("SOCIAL_OPS_API_URL and SOCIAL_OPS_SERVICE_TOKEN are required", file=sys.stderr)
        return 2
    with httpx.Client() as client:
        payload = collect(client, dict(os.environ))
        response = client.post(
            f"{api}/api/service/sync",
            headers={"Authorization": f"Bearer {service_token}"},
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
    print(
        json.dumps(
            {
                "ok": True,
                "audience_snapshots": len(payload["audience"]),
                "health_snapshots": len(payload["health"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
