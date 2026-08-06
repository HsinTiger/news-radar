"""Durable receipts bridging remote Substack drafts to canonical SQLite evidence.

The remote API call and the SQLite update cannot be one transaction.  A receipt
written immediately after ``post_draft`` prevents a worker crash from either
losing the remote draft id or retrying the API and creating a duplicate draft.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
DEFAULT_RECEIPTS_PATH = (
    REPO / "data" / "substack_drafts" / ".substack_remote_receipts.json"
)


def _read(path: Path = DEFAULT_RECEIPTS_PATH) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2}:
        raise ValueError("unsupported Substack receipt schema")
    receipts = payload.get("receipts")
    if not isinstance(receipts, dict):
        raise ValueError("invalid Substack receipt payload")
    normalized: dict[str, dict[str, str]] = {}
    for source_id, receipt in receipts.items():
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("invalid Substack receipt source id")
        if not isinstance(receipt, dict):
            raise ValueError(f"invalid Substack receipt for {source_id}")
        draft_id = str(receipt.get("draft_id") or "").strip()
        created_at = str(receipt.get("created_at") or "").strip()
        if not draft_id or not created_at:
            raise ValueError(f"incomplete Substack receipt for {source_id}")
        normalized[source_id] = {
            "draft_id": draft_id,
            "created_at": created_at,
        }
        for key in (
            "publish_attempted_at",
            "post_id",
            "public_url",
            "published_at",
        ):
            value = str(receipt.get(key) or "").strip()
            if value:
                normalized[source_id][key] = value
        publication_fields = (
            normalized[source_id].get("post_id"),
            normalized[source_id].get("public_url"),
            normalized[source_id].get("published_at"),
        )
        if any(publication_fields) and not all(publication_fields):
            raise ValueError(f"incomplete Substack publication receipt for {source_id}")
        if all(publication_fields) and not str(publication_fields[1]).startswith("https://"):
            raise ValueError(f"invalid Substack publication URL for {source_id}")
    return normalized


def _write(
    receipts: dict[str, dict[str, str]],
    path: Path = DEFAULT_RECEIPTS_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not receipts:
        path.unlink(missing_ok=True)
        return
    payload = {
        "schema_version": 2,
        "receipts": dict(sorted(receipts.items())),
    }
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def store_remote_receipt(
    source_id: str,
    draft_id: int | str,
    *,
    path: Path = DEFAULT_RECEIPTS_PATH,
    created_at: str | None = None,
) -> None:
    receipts = _read(path)
    current = receipts.get(str(source_id))
    if current and current["draft_id"] != str(draft_id):
        raise ValueError(
            f"conflicting remote draft receipts for {source_id}: "
            f"{current['draft_id']} != {draft_id}"
        )
    receipts[str(source_id)] = {
        **(current or {}),
        "draft_id": str(draft_id),
        "created_at": (
            (current or {}).get("created_at")
            or created_at
            or datetime.now(timezone.utc).isoformat()
        ),
    }
    _write(receipts, path)


def get_remote_receipt(
    source_id: str,
    *,
    path: Path = DEFAULT_RECEIPTS_PATH,
) -> dict[str, str] | None:
    receipt = _read(path).get(str(source_id))
    return dict(receipt) if receipt else None


def store_publish_intent(
    source_id: str,
    draft_id: int | str,
    *,
    path: Path = DEFAULT_RECEIPTS_PATH,
    attempted_at: str | None = None,
) -> None:
    store_remote_receipt(source_id, draft_id, path=path)
    receipts = _read(path)
    receipt = receipts[str(source_id)]
    receipt["publish_attempted_at"] = (
        receipt.get("publish_attempted_at")
        or attempted_at
        or datetime.now(timezone.utc).isoformat()
    )
    _write(receipts, path)


def store_publication_receipt(
    source_id: str,
    draft_id: int | str,
    post_id: int | str,
    public_url: str,
    *,
    path: Path = DEFAULT_RECEIPTS_PATH,
    published_at: str | None = None,
) -> None:
    if not str(post_id).strip() or not str(public_url).startswith("https://"):
        raise ValueError("public Substack post id and HTTPS URL are required")
    store_publish_intent(source_id, draft_id, path=path)
    receipts = _read(path)
    receipt = receipts[str(source_id)]
    receipt.update(
        {
            "post_id": str(post_id),
            "public_url": str(public_url),
            "published_at": published_at or datetime.now(timezone.utc).isoformat(),
        }
    )
    _write(receipts, path)


def clear_remote_receipt(
    source_id: str,
    draft_id: int | str,
    *,
    path: Path = DEFAULT_RECEIPTS_PATH,
) -> None:
    receipts = _read(path)
    current = receipts.get(str(source_id))
    if current and current["draft_id"] == str(draft_id):
        receipts.pop(str(source_id), None)
        _write(receipts, path)


def reconcile_remote_receipts(
    db_path: Path,
    *,
    path: Path = DEFAULT_RECEIPTS_PATH,
) -> tuple[set[str], int]:
    """Apply receipts to SQLite and return (still-protected ids, applied count).

    A receipt whose source row is not yet present stays protected and is never
    sent through composition again.  Malformed receipt data raises so callers
    can stop fail-closed instead of risking a duplicate remote draft.
    """
    receipts = _read(path)
    if not receipts:
        return set(), 0
    if not db_path.exists():
        return set(receipts), 0

    remaining = dict(receipts)
    protected: set[str] = set()
    applied = 0
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        for column in (
            "substack_written_at TEXT",
            "substack_draft_id TEXT",
            "substack_drafted_at TEXT",
            "substack_post_id TEXT",
            "substack_post_url TEXT",
            "substack_published_at TEXT",
        ):
            try:
                conn.execute(f"ALTER TABLE news_items ADD COLUMN {column}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        for source_id, receipt in receipts.items():
            cursor = conn.execute(
                """
                UPDATE news_items
                   SET substack_written_at=COALESCE(substack_written_at,?),
                       substack_draft_id=?,
                       substack_drafted_at=COALESCE(substack_drafted_at,?)
                 WHERE id=?
                   AND (substack_draft_id IS NULL OR substack_draft_id=?)
                """,
                (
                    receipt["created_at"],
                    receipt["draft_id"],
                    receipt["created_at"],
                    source_id,
                    receipt["draft_id"],
                ),
            )
            if cursor.rowcount != 1:
                protected.add(source_id)
                continue
            publication_complete = all(
                receipt.get(key)
                for key in ("post_id", "public_url", "published_at")
            )
            if publication_complete:
                published = conn.execute(
                    """
                    UPDATE news_items
                       SET substack_post_id=?,
                           substack_post_url=?,
                           substack_published_at=COALESCE(substack_published_at,?)
                     WHERE id=?
                       AND (substack_post_id IS NULL OR substack_post_id=?)
                       AND (substack_post_url IS NULL OR substack_post_url=?)
                    """,
                    (
                        receipt["post_id"],
                        receipt["public_url"],
                        receipt["published_at"],
                        source_id,
                        receipt["post_id"],
                        receipt["public_url"],
                    ),
                )
                if published.rowcount != 1:
                    protected.add(source_id)
                    continue
                remaining.pop(source_id, None)
            elif not receipt.get("publish_attempted_at"):
                remaining.pop(source_id, None)
            applied += 1
        conn.commit()
    finally:
        conn.close()
    _write(remaining, path)
    return protected, applied
