#!/usr/bin/env python3
"""Probe one recent post per platform and persist metric-contract health."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db as dbmod
from src.engagement import PLATFORM_FETCHERS


PLATFORMS = ("facebook", "instagram", "threads")


def _error_components(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        errors = value.get("errors")
        if isinstance(errors, dict):
            found.extend(f"{prefix}{name}" for name, error in errors.items() if error)
        if value.get("error"):
            found.append(f"{prefix}api_error")
        for key, item in value.items():
            if key not in {"error", "errors"}:
                found.extend(_error_components(item, f"{prefix}{key}."))
    elif isinstance(value, list):
        for item in value:
            found.extend(_error_components(item, prefix))
    return sorted(set(found))


def classify_result(result: dict[str, Any]) -> tuple[str, str]:
    errors = _error_components(result.get("raw", {}))
    metric_names = (
        "views", "reach", "likes", "comments", "shares", "saves",
        "replies", "reposts", "quotes", "total_interactions",
    )
    nonzero = [name for name in metric_names if int(result.get(name) or 0) > 0]
    if not result.get("ok"):
        status = "error"
    elif errors:
        status = "degraded"
    else:
        status = "healthy"
    detail = (
        f"latest-post probe; ok={bool(result.get('ok'))}; "
        f"nonzero={','.join(nonzero) or 'none'}; "
        f"degraded_components={','.join(errors) or 'none'}"
    )
    return status, detail


def latest_post_ids() -> dict[str, str]:
    conn = dbmod.get_conn()
    try:
        rows = conn.execute(
            """
            SELECT platform,platform_post_id FROM publish_log
            WHERE success=1 AND platform IN ('facebook','instagram','threads')
              AND platform_post_id IS NOT NULL AND platform_post_id != ''
            ORDER BY COALESCE(posted_at,'') DESC,id DESC
            """
        ).fetchall()
    finally:
        conn.close()
    result: dict[str, str] = {}
    for row in rows:
        result.setdefault(row["platform"], row["platform_post_id"])
    return result


async def run() -> dict[str, Any]:
    api = os.environ.get("SOCIAL_OPS_API_URL", "").rstrip("/")
    service_token = os.environ.get("SOCIAL_OPS_SERVICE_TOKEN", "")
    if not api or not service_token:
        raise RuntimeError("SOCIAL_OPS_API_URL and SOCIAL_OPS_SERVICE_TOKEN are required")
    post_ids = latest_post_ids()
    captured_at = datetime.now(timezone.utc).isoformat()
    health = []
    summaries = []
    async with httpx.AsyncClient() as client:
        for platform in PLATFORMS:
            post_id = post_ids.get(platform)
            if not post_id:
                status, detail = "unknown", "no successful platform post id is available"
            else:
                result = await PLATFORM_FETCHERS[platform](client, post_id)
                status, detail = classify_result(result)
            health.append(
                {
                    "platform": platform,
                    "metric": "latest_post_canary",
                    "status": status,
                    "detail": detail,
                    "captured_at": captured_at,
                }
            )
            summaries.append({"platform": platform, "status": status, "detail": detail})
        response = await client.post(
            f"{api}/api/service/sync",
            headers={"Authorization": f"Bearer {service_token}"},
            json={"health": health},
            timeout=45,
        )
        response.raise_for_status()
    return {"ok": True, "probes": summaries}


def main() -> int:
    try:
        result = asyncio.run(run())
    except (RuntimeError, httpx.HTTPError) as exc:
        print(f"metric health canary failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
