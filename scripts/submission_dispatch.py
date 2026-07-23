#!/usr/bin/env python3
"""Claim one D1 submission and dispatch only an allowlisted workflow."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx


PLATFORM_SHORT = {"facebook": "fb", "instagram": "ig", "threads": "threads"}


@dataclass(frozen=True)
class Dispatch:
    workflow: str
    inputs: dict[str, str]


def build_dispatch(submission: dict[str, Any]) -> Dispatch:
    common = {
        "source_type": submission["source_type"],
        "content": submission["content"],
        "note": submission.get("note", ""),
        "submission_id": submission["id"],
    }
    if submission["target"] == "substack":
        return Dispatch(
            "substack-submit.yml",
            {
                **common,
                "immediate": "true" if submission.get("mode") == "draft_priority" else "false",
            },
        )
    if submission["target"] != "meta":
        raise ValueError("unsupported submission target")
    platforms = ",".join(PLATFORM_SHORT[value] for value in submission["platforms"])
    if submission["mode"] == "queue":
        return Dispatch("submit-source.yml", {**common, "platforms": platforms})
    if submission["mode"] == "publish_now":
        if submission["source_type"] in {"url", "youtube"}:
            return Dispatch(
                "publish_now.yml",
                {
                    "url": submission["content"],
                    "title": "",
                    "text": "",
                    "platforms": platforms,
                    "note": submission.get("note", ""),
                    "submission_id": submission["id"],
                },
            )
        return Dispatch(
            "publish_now.yml",
            {
                "url": "",
                "title": submission.get("note", ""),
                "text": submission["content"],
                "platforms": platforms,
                "note": submission.get("note", ""),
                "submission_id": submission["id"],
            },
        )
    raise ValueError("unsupported Meta submission mode")


def main() -> int:
    api = os.environ["SOCIAL_OPS_API_URL"].rstrip("/")
    service_token = os.environ["SOCIAL_OPS_SERVICE_TOKEN"]
    github_token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    headers = {"Authorization": f"Bearer {service_token}"}
    with httpx.Client(timeout=30) as client:
        claimed = client.get(f"{api}/api/service/submissions/next", headers=headers)
        claimed.raise_for_status()
        submission = claimed.json().get("submission")
        if not submission:
            print("NO_SUBMISSION")
            return 0
        submission_id = submission["id"]
        try:
            dispatch = build_dispatch(submission)
            response = client.post(
                f"https://api.github.com/repos/{repo}/actions/workflows/"
                f"{dispatch.workflow}/dispatches",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"ref": "main", "inputs": dispatch.inputs},
            )
            if response.status_code != 204:
                raise RuntimeError(
                    f"workflow dispatch failed ({response.status_code}): {response.text[:500]}"
                )
            status = client.post(
                f"{api}/api/service/submissions/{submission_id}/status",
                headers=headers,
                json={"status": "dispatched"},
            )
            status.raise_for_status()
            print(
                json.dumps(
                    {"submission_id": submission_id, "workflow": dispatch.workflow},
                    ensure_ascii=False,
                )
            )
            return 0
        except Exception as exc:
            client.post(
                f"{api}/api/service/submissions/{submission_id}/status",
                headers=headers,
                json={"status": "failed", "error": str(exc)[:1800]},
            )
            print(f"DISPATCH_ERROR: {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
