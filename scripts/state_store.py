#!/usr/bin/env python3
"""Durable runtime-state transport backed by versioned GitHub Release assets.

The repository's former ``state`` branch stored one growing SQLite blob and
force-pushed it from several writers.  That design exceeded GitHub's 100 MB
blob limit and allowed last-writer-wins data loss.  This module keeps the
existing local SQLite runtime while changing only its transport:

* a versioned ZIP asset is uploaded first;
* a small manifest pointer is replaced last;
* every DB passes SQLite ``quick_check`` and SHA-256 verification;
* pull restores files only after the complete bundle is verified;
* old versioned assets are retained for rollback.

GitHub Actions must serialize every writer with the same concurrency group.
The release is a transport/backup boundary, not a multi-writer database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote

import httpx


DEFAULT_TAG = "runtime-state-v1"
MANIFEST_ASSET = "news-radar-state-manifest.json"
LOCK_ASSET = "news-radar-state-write-lock.json"
DB_ARCNAME = "data/01_harvest/news_radar.db"
OPTIONAL_EXTRAS = (
    "state/last_harvest.txt",
    "data/05_reflect/proposals",
)
API_VERSION = "2022-11-28"


class StateStoreError(RuntimeError):
    """Raised when a state transition cannot be proven complete."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_database(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise StateStoreError(f"database not found: {path}")
    try:
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            quick = [row[0] for row in conn.execute("PRAGMA quick_check")]
            if quick != ["ok"]:
                raise StateStoreError(f"SQLite quick_check failed: {quick}")
            counts: dict[str, int] = {}
            existing = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table in (
                "news_items",
                "drafts",
                "publish_log",
                "engagement_stats",
                "content_quality_evaluations",
                "reflector_proposal_lineage",
                "social_policy_overrides",
                "social_policy_history",
                "recovery_experiments",
            ):
                if table in existing:
                    counts[table] = int(
                        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise StateStoreError(f"cannot validate SQLite database: {exc}") from exc
    return {
        "sha256": _sha256(path),
        "size": path.stat().st_size,
        "quick_check": "ok",
        "row_counts": counts,
    }


def _iter_extra_files(root: Path) -> Iterable[tuple[Path, str]]:
    for rel in OPTIONAL_EXTRAS:
        source = root / rel
        if source.is_file():
            yield source, PurePosixPath(rel).as_posix()
        elif source.is_dir():
            for path in sorted(source.rglob("*.jsonl")):
                yield path, path.relative_to(root).as_posix()


def _write_deterministic_member(
    bundle: zipfile.ZipFile,
    source: Path,
    arcname: str,
) -> None:
    """Write one ZIP member without host timestamps or platform attributes."""
    info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    with source.open("rb") as input_file, bundle.open(info, "w") as output_file:
        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)


def build_bundle(root: Path, db_path: Path, output: Path) -> dict[str, Any]:
    db_meta = validate_database(db_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as bundle:
        _write_deterministic_member(bundle, db_path, DB_ARCNAME)
        extra_names: list[str] = []
        for source, arcname in _iter_extra_files(root):
            _write_deterministic_member(bundle, source, arcname)
            extra_names.append(arcname)
    return {
        "database": db_meta,
        "bundle_sha256": _sha256(output),
        "bundle_size": output.stat().st_size,
        "extras": extra_names,
    }


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    return name == DB_ARCNAME or name == "state/last_harvest.txt" or (
        name.startswith("data/05_reflect/proposals/") and name.endswith(".jsonl")
    )


def verify_bundle(bundle_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    expected_bundle_sha = manifest.get("bundle_sha256")
    observed_bundle_sha = _sha256(bundle_path)
    if expected_bundle_sha and observed_bundle_sha != expected_bundle_sha:
        raise StateStoreError(
            f"bundle SHA mismatch: expected={expected_bundle_sha} "
            f"observed={observed_bundle_sha}"
        )
    with tempfile.TemporaryDirectory(prefix="news-radar-state-verify-") as tmp:
        temp_root = Path(tmp)
        with zipfile.ZipFile(bundle_path) as bundle:
            names = bundle.namelist()
            if DB_ARCNAME not in names:
                raise StateStoreError(f"bundle is missing {DB_ARCNAME}")
            unsafe = [name for name in names if not _safe_member(name)]
            if unsafe:
                raise StateStoreError(f"bundle contains unexpected paths: {unsafe}")
            bundle.extract(DB_ARCNAME, temp_root)
        db_meta = validate_database(temp_root / DB_ARCNAME)
    expected_db_sha = manifest.get("database", {}).get("sha256")
    if expected_db_sha and db_meta["sha256"] != expected_db_sha:
        raise StateStoreError(
            f"database SHA mismatch: expected={expected_db_sha} "
            f"observed={db_meta['sha256']}"
        )
    return db_meta


def restore_bundle(bundle_path: Path, root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    db_meta = verify_bundle(bundle_path, manifest)
    with tempfile.TemporaryDirectory(prefix="news-radar-state-restore-") as tmp:
        temp_root = Path(tmp)
        with zipfile.ZipFile(bundle_path) as bundle:
            for name in bundle.namelist():
                if not _safe_member(name):
                    raise StateStoreError(f"refusing unsafe bundle path: {name}")
                bundle.extract(name, temp_root)
        for source in sorted(temp_root.rglob("*")):
            if not source.is_file():
                continue
            rel = source.relative_to(temp_root)
            destination = root / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged = destination.with_suffix(destination.suffix + ".incoming")
            shutil.copy2(source, staged)
            os.replace(staged, destination)
    return db_meta


def _resolve_token() -> str:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        value = subprocess.check_output(
            ["gh", "auth", "token"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        value = ""
    if not value:
        raise StateStoreError("missing GITHUB_TOKEN/GH_TOKEN and no gh auth token")
    return value


class GitHubReleaseStore:
    def __init__(self, repo: str, token: str | None = None, tag: str = DEFAULT_TAG):
        if "/" not in repo:
            raise StateStoreError("--repo must be OWNER/REPO")
        self.repo = repo
        self.tag = tag
        self.client = httpx.Client(
            timeout=httpx.Timeout(120.0, connect=20.0),
            follow_redirects=True,
            headers={
                "Authorization": f"Bearer {token or _resolve_token()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "news-radar-state-store/1",
            },
        )

    @property
    def api(self) -> str:
        return f"https://api.github.com/repos/{self.repo}"

    def close(self) -> None:
        self.client.close()

    def release(self, create: bool = False) -> dict[str, Any]:
        response = self.client.get(f"{self.api}/releases/tags/{quote(self.tag)}")
        if response.status_code == 404 and create:
            response = self.client.post(
                f"{self.api}/releases",
                json={
                    "tag_name": self.tag,
                    "name": "News Radar runtime state",
                    "body": (
                        "Machine-managed runtime snapshots. Do not edit assets "
                        "manually; state_store.py verifies every transition."
                    ),
                    "draft": False,
                    "prerelease": False,
                },
            )
        if response.is_error:
            raise StateStoreError(
                f"GitHub release lookup failed ({response.status_code}): "
                f"{response.text[:500]}"
            )
        return response.json()

    @staticmethod
    def _asset_map(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {asset["name"]: asset for asset in release.get("assets", [])}

    def download_asset(self, asset: dict[str, Any], destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.client.stream(
            "GET", asset["url"], headers={"Accept": "application/octet-stream"}
        ) as response:
            if response.is_error:
                raise StateStoreError(
                    f"asset download failed ({response.status_code}): "
                    f"{response.text[:500]}"
                )
            with destination.open("wb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    output.write(chunk)

    def upload_asset(
        self, release: dict[str, Any], source: Path, name: str, content_type: str
    ) -> dict[str, Any]:
        upload_url = release["upload_url"].split("{")[0]
        with source.open("rb") as handle:
            response = self.client.post(
                upload_url,
                params={"name": name},
                headers={"Content-Type": content_type},
                content=handle,
            )
        if response.is_error:
            raise StateStoreError(
                f"asset upload failed ({response.status_code}): {response.text[:500]}"
            )
        return response.json()

    def delete_asset(self, asset: dict[str, Any]) -> None:
        response = self.client.delete(f"{self.api}/releases/assets/{asset['id']}")
        if response.status_code != 204:
            raise StateStoreError(
                f"asset delete failed ({response.status_code}): {response.text[:500]}"
            )

    def load_manifest(self) -> tuple[dict[str, Any], dict[str, Any]]:
        release = self.release(create=False)
        asset = self._asset_map(release).get(MANIFEST_ASSET)
        if not asset:
            raise StateStoreError(f"release {self.tag} has no {MANIFEST_ASSET}")
        with tempfile.TemporaryDirectory(prefix="news-radar-manifest-") as tmp:
            path = Path(tmp) / MANIFEST_ASSET
            self.download_asset(asset, path)
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StateStoreError(f"invalid state manifest: {exc}") from exc
        if manifest.get("schema_version") != 1:
            raise StateStoreError(
                f"unsupported manifest schema: {manifest.get('schema_version')}"
            )
        return release, manifest

    def _download_json_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="news-radar-json-asset-") as tmp:
            path = Path(tmp) / asset["name"]
            self.download_asset(asset, path)
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StateStoreError(f"invalid JSON asset {asset['name']}: {exc}") from exc

    @staticmethod
    def _lease_expired(lease: dict[str, Any], now: dt.datetime | None = None) -> bool:
        current = now or dt.datetime.now(dt.timezone.utc)
        try:
            expires = dt.datetime.fromisoformat(str(lease["expires_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=dt.timezone.utc)
        return expires <= current

    def acquire_lock(
        self,
        owner: str,
        lease_file: Path,
        *,
        lease_seconds: int = 1800,
        wait_seconds: int = 900,
    ) -> dict[str, Any]:
        token = uuid.uuid4().hex
        deadline = time.monotonic() + max(wait_seconds, 0)
        while True:
            release = self.release(create=True)
            existing = self._asset_map(release).get(LOCK_ASSET)
            if existing:
                try:
                    current = self._download_json_asset(existing)
                except StateStoreError:
                    current = {}
                if self._lease_expired(current):
                    try:
                        self.delete_asset(existing)
                    except StateStoreError:
                        pass
                elif time.monotonic() >= deadline:
                    raise StateStoreError(
                        f"state write lock held by {current.get('owner', 'unknown')} "
                        f"until {current.get('expires_at', 'unknown')}"
                    )
                else:
                    time.sleep(5)
                continue

            now = dt.datetime.now(dt.timezone.utc)
            lease = {
                "schema_version": 1,
                "owner": owner,
                "token": token,
                "acquired_at": now.isoformat(),
                "expires_at": (now + dt.timedelta(seconds=lease_seconds)).isoformat(),
            }
            with tempfile.TemporaryDirectory(prefix="news-radar-lock-") as tmp:
                path = Path(tmp) / LOCK_ASSET
                path.write_text(json.dumps(lease, indent=2) + "\n", encoding="utf-8")
                try:
                    self.upload_asset(release, path, LOCK_ASSET, "application/json")
                except StateStoreError as exc:
                    if "(422)" in str(exc) and time.monotonic() < deadline:
                        time.sleep(2)
                        continue
                    raise
            release = self.release(create=False)
            observed_asset = self._asset_map(release).get(LOCK_ASSET)
            if not observed_asset:
                raise StateStoreError("state write lock disappeared after acquisition")
            observed = self._download_json_asset(observed_asset)
            if observed.get("token") != token:
                raise StateStoreError("state write lock token mismatch after acquisition")
            lease_file.parent.mkdir(parents=True, exist_ok=True)
            lease_file.write_text(json.dumps(lease, indent=2) + "\n", encoding="utf-8")
            return lease

    def assert_lock(self, lease_file: Path) -> dict[str, Any]:
        try:
            local = json.loads(lease_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateStoreError(f"missing or invalid local state lease: {exc}") from exc
        release = self.release(create=False)
        asset = self._asset_map(release).get(LOCK_ASSET)
        if not asset:
            raise StateStoreError("durable state write lock is missing")
        remote = self._download_json_asset(asset)
        if remote.get("token") != local.get("token"):
            raise StateStoreError("state write lock is owned by another writer")
        if self._lease_expired(remote):
            raise StateStoreError("state write lock expired before push")
        return remote

    def release_lock(self, lease_file: Path) -> dict[str, Any]:
        if not lease_file.exists():
            return {"released": False, "reason": "local lease not present"}
        try:
            local = json.loads(lease_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateStoreError(f"invalid local state lease: {exc}") from exc
        release = self.release(create=False)
        asset = self._asset_map(release).get(LOCK_ASSET)
        if not asset:
            lease_file.unlink(missing_ok=True)
            return {"released": False, "reason": "durable lease not present"}
        remote = self._download_json_asset(asset)
        if remote.get("token") != local.get("token"):
            raise StateStoreError("refusing to release another writer's state lock")
        self.delete_asset(asset)
        lease_file.unlink(missing_ok=True)
        return {"released": True, "owner": local.get("owner")}

    def pull(self, root: Path) -> dict[str, Any]:
        release, manifest = self.load_manifest()
        bundle_name = manifest.get("bundle_asset")
        asset = self._asset_map(release).get(bundle_name)
        if not asset:
            raise StateStoreError(f"manifest points to missing asset: {bundle_name}")
        with tempfile.TemporaryDirectory(prefix="news-radar-pull-") as tmp:
            bundle_path = Path(tmp) / bundle_name
            self.download_asset(asset, bundle_path)
            db_meta = restore_bundle(bundle_path, root, manifest)
        return {"manifest": manifest, "database": db_meta}

    def push(
        self,
        root: Path,
        db_path: Path,
        producer: str,
        keep: int = 8,
        lease_file: Path | None = None,
    ) -> dict[str, Any]:
        if lease_file is not None:
            self.assert_lock(lease_file)
        release = self.release(create=True)
        assets = self._asset_map(release)
        with tempfile.TemporaryDirectory(prefix="news-radar-push-") as tmp:
            temp = Path(tmp)
            bundle_path = temp / "state.zip"
            meta = build_bundle(root, db_path, bundle_path)
            bundle_name = (
                f"news-radar-state-{meta['database']['sha256'][:12]}-"
                f"{meta['bundle_sha256'][:12]}.zip"
            )
            if bundle_name not in assets:
                self.upload_asset(release, bundle_path, bundle_name, "application/zip")
                release = self.release(create=False)
                assets = self._asset_map(release)

            previous_revision = 0
            if MANIFEST_ASSET in assets:
                try:
                    _, previous = self.load_manifest()
                    previous_revision = int(previous.get("revision", 0))
                except StateStoreError:
                    previous_revision = 0
            manifest = {
                "schema_version": 1,
                "revision": previous_revision + 1,
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "producer": producer,
                "bundle_asset": bundle_name,
                **meta,
            }
            manifest_path = temp / MANIFEST_ASSET
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if lease_file is not None:
                # Re-check after the potentially long bundle upload. Never move
                # the canonical pointer under an expired or stolen lease.
                self.assert_lock(lease_file)
            if MANIFEST_ASSET in assets:
                self.delete_asset(assets[MANIFEST_ASSET])
            release = self.release(create=False)
            self.upload_asset(
                release, manifest_path, MANIFEST_ASSET, "application/json"
            )

            # Post-condition: read the durable pointer and the complete payload back.
            verified_release, verified_manifest = self.load_manifest()
            if verified_manifest != manifest:
                raise StateStoreError("manifest readback differs from uploaded manifest")
            verified_asset = self._asset_map(verified_release).get(bundle_name)
            if not verified_asset:
                raise StateStoreError("bundle disappeared after manifest publication")
            verify_path = temp / "readback.zip"
            self.download_asset(verified_asset, verify_path)
            verify_bundle(verify_path, verified_manifest)

        # Rollback retention is best-effort only after the new pointer verifies.
        release = self.release(create=False)
        versioned = sorted(
            (
                asset
                for asset in release.get("assets", [])
                if asset["name"].startswith("news-radar-state-")
                and asset["name"].endswith(".zip")
                and asset["name"] != bundle_name
            ),
            key=lambda asset: asset.get("created_at", ""),
            reverse=True,
        )
        for old in versioned[max(keep - 1, 0) :]:
            try:
                self.delete_asset(old)
            except StateStoreError:
                pass
        return manifest


def _repo_from_env(value: str | None) -> str:
    repo = (value or os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if not repo:
        raise StateStoreError("missing --repo and GITHUB_REPOSITORY")
    return repo


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("pull", "push", "inspect", "bundle", "lock", "assert", "unlock"),
    )
    parser.add_argument("--repo", help="GitHub OWNER/REPO")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--db", type=Path)
    parser.add_argument("--tag", default=DEFAULT_TAG)
    parser.add_argument("--producer", default=os.environ.get("GITHUB_RUN_ID", "manual"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--keep", type=int, default=8)
    parser.add_argument("--lease-file", type=Path, default=Path(".runtime-state-lease.json"))
    parser.add_argument("--lease-seconds", type=int, default=1800)
    parser.add_argument("--wait-seconds", type=int, default=900)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    db_path = (args.db or root / DB_ARCNAME).resolve()
    try:
        if args.command == "bundle":
            output = (args.output or root / "news-radar-state.zip").resolve()
            print(json.dumps(build_bundle(root, db_path, output), indent=2))
            return 0
        store = GitHubReleaseStore(_repo_from_env(args.repo), tag=args.tag)
        try:
            lease_file = args.lease_file.resolve()
            if args.command == "lock":
                result = store.acquire_lock(
                    args.producer,
                    lease_file,
                    lease_seconds=args.lease_seconds,
                    wait_seconds=args.wait_seconds,
                )
            elif args.command == "assert":
                result = store.assert_lock(lease_file)
            elif args.command == "unlock":
                result = store.release_lock(lease_file)
            elif args.command == "pull":
                result = store.pull(root)
            elif args.command == "push":
                result = store.push(
                    root,
                    db_path,
                    args.producer,
                    keep=args.keep,
                    lease_file=lease_file if args.lease_file else None,
                )
            else:
                _, result = store.load_manifest()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        finally:
            store.close()
    except StateStoreError as exc:
        print(f"[state-store] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
