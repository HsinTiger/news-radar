from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
import zipfile
from pathlib import Path

import pytest

from scripts.state_store import (
    DB_ARCNAME,
    StateStoreError,
    build_bundle,
    restore_bundle,
    validate_database,
    verify_bundle,
    GitHubReleaseStore,
)


def _database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE news_items(id TEXT PRIMARY KEY);
        CREATE TABLE drafts(id TEXT PRIMARY KEY);
        CREATE TABLE publish_log(id INTEGER PRIMARY KEY);
        CREATE TABLE engagement_stats(id INTEGER PRIMARY KEY);
        INSERT INTO news_items VALUES ('n1');
        INSERT INTO drafts VALUES ('d1');
        INSERT INTO publish_log DEFAULT VALUES;
        INSERT INTO engagement_stats DEFAULT VALUES;
        """
    )
    conn.commit()
    conn.close()


def test_bundle_roundtrip_is_hash_and_sqlite_verified(tmp_path: Path) -> None:
    root = tmp_path / "source"
    db = root / DB_ARCNAME
    _database(db)
    proposal = root / "data/05_reflect/proposals/2026-W30.jsonl"
    proposal.parent.mkdir(parents=True)
    proposal.write_text('{"proposal":"keep"}\n', encoding="utf-8")

    bundle = tmp_path / "state.zip"
    meta = build_bundle(root, db, bundle)
    manifest = {"schema_version": 1, **meta}

    assert verify_bundle(bundle, manifest)["quick_check"] == "ok"
    destination = tmp_path / "destination"
    restored = restore_bundle(bundle, destination, manifest)
    assert restored["sha256"] == meta["database"]["sha256"]
    assert validate_database(destination / DB_ARCNAME)["row_counts"] == {
        "news_items": 1,
        "drafts": 1,
        "publish_log": 1,
        "engagement_stats": 1,
    }
    assert (destination / "data/05_reflect/proposals/2026-W30.jsonl").is_file()


def test_bundle_rejects_unexpected_paths(tmp_path: Path) -> None:
    bundle = tmp_path / "bad.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(DB_ARCNAME, b"not sqlite")
        archive.writestr("../escape", b"bad")
    with pytest.raises(StateStoreError, match="unexpected paths"):
        verify_bundle(bundle, {"schema_version": 1})


def test_manifest_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "source"
    db = root / DB_ARCNAME
    _database(db)
    bundle = tmp_path / "state.zip"
    meta = build_bundle(root, db, bundle)
    manifest = json.loads(json.dumps({"schema_version": 1, **meta}))
    manifest["bundle_sha256"] = "0" * 64
    with pytest.raises(StateStoreError, match="bundle SHA mismatch"):
        verify_bundle(bundle, manifest)


def test_lease_expiry_is_fail_closed() -> None:
    now = datetime.now(timezone.utc)
    assert GitHubReleaseStore._lease_expired(
        {"expires_at": (now - timedelta(seconds=1)).isoformat()}, now
    )
    assert not GitHubReleaseStore._lease_expired(
        {"expires_at": (now + timedelta(seconds=1)).isoformat()}, now
    )
    assert GitHubReleaseStore._lease_expired({"expires_at": "not-a-date"}, now)
