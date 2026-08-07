"""Prepare and verify Substack Browser draft handoffs on Windows.

The scheduled Codex task owns the authenticated browser interaction.  This
module deliberately never reads browser cookies or credentials.  It narrows a
run to its exact local artifacts and records the remote editor URL/draft ID so
the task can fail closed instead of confusing local files with remote drafts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DRAFTS_ROOT = REPO_ROOT / "data" / "substack_drafts"
DEFAULT_MANIFEST = REPO_ROOT / ".runtime-state-substack-browser-handoff.json"
PROFILE_CONTRACTS = {
    "podcast-batch": {"prefix": "podcast_", "expected_count": 2},
    "weekly": {"prefix": "company_", "expected_count": 1},
}


class HandoffError(RuntimeError):
    """The browser handoff cannot prove the requested draft contract."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HandoffError(f"cannot read valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HandoffError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _parse_time(value: str, *, fallback_tz: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HandoffError(f"invalid ISO timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=fallback_tz)
    return parsed


def _artifact_row(artifact_dir: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    title = str(metadata.get("title") or "").strip()
    subtitle = str(metadata.get("subtitle") or "").strip()
    if not title or not subtitle:
        raise HandoffError(f"artifact title/subtitle missing: {artifact_dir}")
    article_path = artifact_dir / "Article_Substack.md"
    cover_path = artifact_dir / "cover.png"
    if not article_path.is_file() or article_path.stat().st_size == 0:
        raise HandoffError(f"reader-ready article missing: {article_path}")
    if not cover_path.is_file() or cover_path.stat().st_size == 0:
        raise HandoffError(f"cover missing: {cover_path}")
    source = metadata.get("source")
    source_id = source.get("id") if isinstance(source, dict) else None
    return {
        "title": title,
        "subtitle": subtitle,
        "artifact_dir": str(artifact_dir.resolve()),
        "article_path": str(article_path.resolve()),
        "cover_path": str(cover_path.resolve()),
        "metadata_path": str((artifact_dir / "metadata.json").resolve()),
        "source_id": str(source_id) if source_id else None,
        "generated_by": str(metadata.get("generated_by") or "").strip(),
        "remote_draft_id": None,
        "editor_url": None,
        "remote_drafted_at": None,
    }


def prepare_handoff(
    profile: str,
    *,
    drafts_root: Path = DEFAULT_DRAFTS_ROOT,
    started_at: datetime,
    manifest_path: Path = DEFAULT_MANIFEST,
    now: datetime | None = None,
) -> dict[str, Any]:
    if profile not in PROFILE_CONTRACTS:
        raise HandoffError(f"unsupported profile: {profile}")
    local_tz = started_at.tzinfo or datetime.now().astimezone().tzinfo
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=local_tz)
    finished_at = now or datetime.now().astimezone()
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=local_tz)
    contract = PROFILE_CONTRACTS[profile]
    prefix = str(contract["prefix"])
    candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
    if drafts_root.exists():
        for metadata_path in drafts_root.glob(f"*/{prefix}*/metadata.json"):
            metadata = _read_json(metadata_path)
            created_raw = str(metadata.get("created_at") or "").strip()
            if not created_raw:
                continue
            created_at = _parse_time(created_raw, fallback_tz=local_tz)
            if started_at <= created_at <= finished_at:
                candidates.append((created_at, metadata_path.parent, metadata))
    candidates.sort(key=lambda row: row[0])
    expected_count = int(contract["expected_count"])
    if len(candidates) != expected_count:
        raise HandoffError(
            f"expected {expected_count} current-run artifacts for {profile}, "
            f"found {len(candidates)}"
        )
    artifacts = [_artifact_row(path, metadata) for _, path, metadata in candidates]
    titles = [row["title"] for row in artifacts]
    if len(set(titles)) != len(titles):
        raise HandoffError(f"current-run artifact titles are not unique: {titles}")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "profile": profile,
        "started_at": started_at.isoformat(),
        "prepared_at": finished_at.isoformat(),
        "status": "pending_browser_drafts",
        "expected_count": expected_count,
        "audience": "everyone",
        "remote_action": "draft_only",
        "transport": "substack_browser_ui",
        "artifacts": artifacts,
    }
    _write_json(manifest_path, manifest)
    return manifest


def _validate_editor_url(draft_id: str, editor_url: str) -> None:
    if not re.fullmatch(r"[0-9]+", draft_id):
        raise HandoffError(f"draft id must be numeric: {draft_id}")
    parsed = urlparse(editor_url)
    expected_path = f"/publish/post/{draft_id}"
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".substack.com")
        or parsed.path != expected_path
    ):
        raise HandoffError(f"editor URL does not match draft id {draft_id}: {editor_url}")


def record_remote_draft(
    manifest_path: Path,
    *,
    title: str,
    draft_id: str,
    editor_url: str,
    drafted_at: str | None = None,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    if manifest.get("remote_action") != "draft_only" or manifest.get("audience") != "everyone":
        raise HandoffError("manifest is not an Everyone/draft-only handoff")
    draft_id = str(draft_id).strip()
    editor_url = str(editor_url).strip()
    _validate_editor_url(draft_id, editor_url)
    matches = [row for row in manifest.get("artifacts", []) if row.get("title") == title]
    if len(matches) != 1:
        raise HandoffError(f"manifest title must match exactly once: {title}")
    row = matches[0]
    existing_id = row.get("remote_draft_id")
    if existing_id and str(existing_id) != draft_id:
        raise HandoffError(
            f"conflicting remote draft id for {title}: {existing_id} != {draft_id}"
        )
    for other in manifest.get("artifacts", []):
        if other is not row and str(other.get("remote_draft_id") or "") == draft_id:
            raise HandoffError(f"remote draft id reused by two artifacts: {draft_id}")
    timestamp = drafted_at or datetime.now().astimezone().isoformat()
    _parse_time(timestamp, fallback_tz=datetime.now().astimezone().tzinfo)
    row.update(
        {
            "remote_draft_id": draft_id,
            "editor_url": editor_url,
            "remote_drafted_at": timestamp,
        }
    )
    metadata_path = Path(str(row["metadata_path"]))
    metadata = _read_json(metadata_path)
    metadata["remote_draft"] = {
        "id": draft_id,
        "editor_url": editor_url,
        "drafted_at": timestamp,
        "transport": "substack_browser_ui",
        "audience": "everyone",
    }
    _write_json(metadata_path, metadata)
    if all(item.get("remote_draft_id") for item in manifest.get("artifacts", [])):
        manifest["status"] = "complete"
        manifest["completed_at"] = timestamp
    _write_json(manifest_path, manifest)
    return manifest


def verify_handoff(manifest_path: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    artifacts = manifest.get("artifacts")
    expected_count = manifest.get("expected_count")
    if not isinstance(artifacts, list) or len(artifacts) != expected_count:
        raise HandoffError("manifest artifact count does not match its contract")
    if manifest.get("status") != "complete":
        raise HandoffError("browser handoff is incomplete")
    for row in artifacts:
        _validate_editor_url(str(row.get("remote_draft_id") or ""), str(row.get("editor_url") or ""))
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("profile", choices=tuple(PROFILE_CONTRACTS))
    prepare.add_argument("--started-at", required=True)
    prepare.add_argument("--drafts-root", type=Path, default=DEFAULT_DRAFTS_ROOT)
    prepare.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    record = subparsers.add_parser("record")
    record.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    record.add_argument("--title", required=True)
    record.add_argument("--draft-id", required=True)
    record.add_argument("--editor-url", required=True)
    record.add_argument("--drafted-at")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "prepare":
            result = prepare_handoff(
                args.profile,
                drafts_root=args.drafts_root,
                started_at=_parse_time(
                    args.started_at,
                    fallback_tz=datetime.now().astimezone().tzinfo,
                ),
                manifest_path=args.manifest,
            )
        elif args.command == "record":
            result = record_remote_draft(
                args.manifest,
                title=args.title,
                draft_id=args.draft_id,
                editor_url=args.editor_url,
                drafted_at=args.drafted_at,
            )
        else:
            result = verify_handoff(args.manifest)
    except HandoffError as exc:
        print(f"[substack-browser-handoff] ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
