#!/usr/bin/env python3
"""Small authenticated client for the Social Ops control plane."""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def _config() -> tuple[str, str]:
    url = os.environ.get("SOCIAL_OPS_API_URL", "").rstrip("/")
    token = os.environ.get("SOCIAL_OPS_SERVICE_TOKEN", "")
    if not url or not token:
        raise RuntimeError("SOCIAL_OPS_API_URL and SOCIAL_OPS_SERVICE_TOKEN are required")
    return url, token


def _post(path: str, payload: dict) -> dict:
    url, token = _config()
    response = httpx.post(
        f"{url}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("submission_id")
    status.add_argument("value")
    status.add_argument("--workflow-run-url", default="")
    status.add_argument("--error", default="")
    event = sub.add_parser("event")
    event.add_argument("action")
    event.add_argument("status")
    event.add_argument("--subject-id", default="")
    event.add_argument("--metadata-json", default="{}")
    args = parser.parse_args()
    try:
        if args.command == "status":
            result = _post(
                f"/api/service/submissions/{args.submission_id}/status",
                {
                    "status": args.value,
                    "workflow_run_url": args.workflow_run_url,
                    "error": args.error,
                },
            )
        else:
            result = _post(
                "/api/service/events",
                {
                    "action": args.action,
                    "subject_id": args.subject_id,
                    "status": args.status,
                    "metadata": json.loads(args.metadata_json),
                },
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (RuntimeError, httpx.HTTPError, json.JSONDecodeError) as exc:
        print(f"[control-plane] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
