#!/usr/bin/env python3
"""Prove an exact FB/IG/Threads publication is a readable three-item carousel.

The verifier is intentionally separate from publishing.  It reads the exact
post IDs back from Meta, validates the remote container shape, and only then
may persist ``actual_format=carousel`` for the matching canonical draft.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db as dbmod  # noqa: E402
from src.recovery_mode import record_experiments  # noqa: E402
from src.schema import CarouselCards  # noqa: E402


PLATFORMS = ("facebook", "instagram", "threads")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _children(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("data", [])
    return [item for item in (value or []) if isinstance(item, dict)]


def _proof(
    platform: str, payload: dict[str, Any], expected_id: str
) -> dict[str, Any]:
    actual_id = str(payload.get("id") or "").strip()
    if actual_id != expected_id:
        raise ValueError(f"{platform}: remote id mismatch")

    if platform == "facebook":
        attachments = _children(payload.get("attachments"))
        children: list[dict[str, Any]] = []
        media_type = ""
        for attachment in attachments:
            media_type = media_type or str(attachment.get("media_type") or "")
            children.extend(_children(attachment.get("subattachments")))
        permalink = str(payload.get("permalink_url") or "").strip()
    else:
        children = _children(payload.get("children"))
        media_type = str(payload.get("media_type") or "").upper()
        permalink = str(payload.get("permalink") or "").strip()
        if media_type not in {"CAROUSEL", "CAROUSEL_ALBUM"}:
            raise ValueError(f"{platform}: remote media_type is not carousel")

    if len(children) != 3:
        raise ValueError(f"{platform}: expected 3 remote children, got {len(children)}")
    if not permalink.startswith("https://"):
        raise ValueError(f"{platform}: missing remote permalink")

    child_ids = [
        str(
            child.get("id")
            or (child.get("target") or {}).get("id")
            or ""
        ).strip()
        for child in children
    ]
    if any(not value for value in child_ids):
        raise ValueError(f"{platform}: child id missing")
    return {
        "platform": platform,
        "post_id": expected_id,
        "readable": True,
        "media_type": media_type or "multi_photo",
        "child_count": 3,
        "child_ids": child_ids,
        "permalink": permalink,
    }


async def verify_remote(
    post_ids: dict[str, str], *, client: httpx.AsyncClient | None = None
) -> dict[str, dict[str, Any]]:
    tokens = {
        "facebook": os.environ.get("FB_PAGE_ACCESS_TOKEN", ""),
        "instagram": os.environ.get("IG_ACCESS_TOKEN", ""),
        "threads": os.environ.get("THREADS_ACCESS_TOKEN", ""),
    }
    missing = [platform for platform, token in tokens.items() if not token]
    if missing:
        raise RuntimeError(f"missing Meta token(s): {','.join(missing)}")

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=45.0)
    try:
        proofs: dict[str, dict[str, Any]] = {}
        requests = {
            "facebook": (
                f"https://graph.facebook.com/v20.0/{post_ids['facebook']}",
                "id,permalink_url,attachments{media_type,subattachments{target{id},type,url}}",
            ),
            "instagram": (
                f"https://graph.facebook.com/v20.0/{post_ids['instagram']}",
                "id,media_type,permalink,children{id,media_type}",
            ),
            "threads": (
                f"https://graph.threads.net/v1.0/{post_ids['threads']}",
                "id,media_type,permalink,children{id,media_type}",
            ),
        }
        for platform in PLATFORMS:
            url, fields = requests[platform]
            response = await client.get(
                url,
                params={"fields": fields, "access_token": tokens[platform]},
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"{platform}: Meta readback HTTP {response.status_code}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError(f"{platform}: Meta readback is not JSON") from exc
            proofs[platform] = _proof(platform, payload, post_ids[platform])
        return proofs
    finally:
        if owns_client:
            await client.aclose()


def record_canonical_proof(
    conn,
    *,
    draft_id: str,
    post_ids: dict[str, str],
    proofs: dict[str, dict[str, Any]],
    observed_at: str,
) -> None:
    """Persist format only after remote proof matches the canonical post IDs."""

    draft = conn.execute(
        """
        SELECT d.carousel_json,n.topic_category,n.title
          FROM drafts d
          JOIN news_items n ON n.id=d.news_id
         WHERE d.id=?
        """,
        (draft_id,),
    ).fetchone()
    if draft is None or not draft["carousel_json"]:
        raise ValueError("canonical draft/carousel is missing")
    CarouselCards.model_validate_json(draft["carousel_json"])

    for platform in PLATFORMS:
        proof = proofs.get(platform) or {}
        if not proof.get("readable") or int(proof.get("child_count") or 0) != 3:
            raise ValueError(f"{platform}: incomplete remote carousel proof")
        row = conn.execute(
            """
            SELECT platform_post_id
              FROM publish_log
             WHERE draft_id=? AND platform=? AND success=1
             ORDER BY id DESC LIMIT 1
            """,
            (draft_id, platform),
        ).fetchone()
        if row is None or str(row["platform_post_id"] or "") != post_ids[platform]:
            raise ValueError(f"{platform}: canonical post id mismatch")

    topic = str(draft["topic_category"] or "other")
    record_experiments(
        conn,
        draft_id=draft_id,
        platforms=PLATFORMS,
        topic=topic,
        content_format="carousel",
        created_at=observed_at,
    )
    for platform in PLATFORMS:
        dbmod.mark_recovery_actual_format(
            conn, draft_id, platform, "carousel", observed_at
        )


def _id(value: str) -> str:
    normalized = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_:-]{5,120}", normalized):
        raise argparse.ArgumentTypeError("invalid Meta post id")
    return normalized


async def run(args: argparse.Namespace) -> dict[str, Any]:
    post_ids = {
        "facebook": args.facebook_id,
        "instagram": args.instagram_id,
        "threads": args.threads_id,
    }
    observed_at = _now()
    proofs = await verify_remote(post_ids)
    canonical_updated = False
    if args.record_canonical:
        dbmod.init_db()
        conn = dbmod.get_conn()
        try:
            record_canonical_proof(
                conn,
                draft_id=args.draft_id,
                post_ids=post_ids,
                proofs=proofs,
                observed_at=observed_at,
            )
            canonical_updated = True
        finally:
            conn.close()
    return {
        "status": "passed",
        "observed_at": observed_at,
        "draft_id": args.draft_id,
        "canonical_updated": canonical_updated,
        "proofs": proofs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facebook-id", required=True, type=_id)
    parser.add_argument("--instagram-id", required=True, type=_id)
    parser.add_argument("--threads-id", required=True, type=_id)
    parser.add_argument("--draft-id", required=True, type=_id)
    parser.add_argument("--record-canonical", action="store_true")
    parser.add_argument("--evidence-json", default="logs/meta-carousel-readback.json")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
        exit_code = 0
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        result = {"status": "failed", "reason": str(exc)[:500]}
        exit_code = 1
    target = Path(args.evidence_json)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
