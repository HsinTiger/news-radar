#!/usr/bin/env python3
"""Read-only preflight for the canonical macOS Substack workers.

The report never prints environment values, cookies, or article content.  It
answers two separate questions: whether the immediate canary lane is ready and,
with ``--require-remote-proof``, whether at least one real remote draft ID exists
before the hourly backlog worker is enabled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Callable


LABEL_FAST = "com.hsin.news-radar.substack-fast"
LABEL_HOURLY = "com.hsin.news-radar.compose"


def _env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _command_ok(
    command: list[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]],
) -> bool:
    try:
        result = runner(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=20,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def _db_evidence(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "exists": path.is_file(),
        "submissions": 0,
        "pending_remote": 0,
        "remote_proven": 0,
        "schema_ready": False,
    }
    if not path.is_file():
        return result
    conn = sqlite3.connect(str(path))
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(news_items)")}
        required = {"substack_draft_id", "substack_drafted_at"}
        result["schema_ready"] = required <= columns
        result["submissions"] = int(
            conn.execute(
                "SELECT COUNT(*) FROM news_items WHERE feed_name='user_substack'"
            ).fetchone()[0]
        )
        if result["schema_ready"]:
            pending, proven = conn.execute(
                """
                SELECT SUM(CASE WHEN substack_draft_id IS NULL OR substack_drafted_at IS NULL
                                THEN 1 ELSE 0 END),
                       SUM(CASE WHEN substack_draft_id IS NOT NULL AND substack_drafted_at IS NOT NULL
                                THEN 1 ELSE 0 END)
                  FROM news_items WHERE feed_name='user_substack'
                """
            ).fetchone()
            result["pending_remote"] = int(pending or 0)
            result["remote_proven"] = int(proven or 0)
        else:
            result["pending_remote"] = result["submissions"]
    except sqlite3.Error as exc:
        result["error"] = type(exc).__name__
    finally:
        conn.close()
    return result


def _receipt_evidence(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"valid": True, "pending": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        receipts = payload.get("receipts") if isinstance(payload, dict) else None
        if payload.get("schema_version") not in {1, 2} or not isinstance(receipts, dict):
            raise ValueError("invalid schema")
        for receipt in receipts.values():
            if not isinstance(receipt, dict) or not receipt.get("draft_id"):
                raise ValueError("incomplete receipt")
            publication = [
                receipt.get("post_id"),
                receipt.get("public_url"),
                receipt.get("published_at"),
            ]
            if any(publication) and not all(publication):
                raise ValueError("incomplete publication receipt")
        return {"valid": True, "pending": len(receipts)}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"valid": False, "pending": None, "error": type(exc).__name__}


def inspect_runtime(
    *,
    repo_root: Path,
    home: Path,
    platform_name: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    require_remote_proof: bool = False,
) -> dict[str, Any]:
    platform_name = platform_name or platform.system()
    runtime_repo = Path(home) / "news_radar"
    env = _env_values(runtime_repo / ".env")
    uid = str(getattr(__import__("os"), "getuid", lambda: -1)())
    is_macos = platform_name == "Darwin"
    fast_loaded = is_macos and _command_ok(
        ["launchctl", "print", f"gui/{uid}/{LABEL_FAST}"], runner=runner
    )
    hourly_loaded = is_macos and _command_ok(
        ["launchctl", "print", f"gui/{uid}/{LABEL_HOURLY}"], runner=runner
    )
    gh_ready = _command_ok(["gh", "auth", "status"], runner=runner)
    publish_api_ready = _command_ok(
        [
            str(runtime_repo / ".venv" / "bin" / "python"),
            "-c",
            (
                "from substack import Api; "
                "assert all(hasattr(Api,n) for n in "
                "('post_draft','prepublish_draft','publish_draft','get_published_posts'))"
            ),
        ],
        runner=runner,
    )
    source_fast = repo_root / "scripts" / "drain_substack_fast.sh"
    installed_fast = home / "bin" / "news_radar_substack_fast.sh"
    fast_script_current = (
        _sha256(source_fast) is not None
        and _sha256(source_fast) == _sha256(installed_fast)
    )
    fast_plist = home / "Library" / "LaunchAgents" / f"{LABEL_FAST}.plist"
    receipts = _receipt_evidence(
        runtime_repo
        / "data"
        / "substack_drafts"
        / ".substack_remote_receipts.json"
    )
    database = _db_evidence(
        runtime_repo / "data" / "01_harvest" / "news_radar.db"
    )
    env_checks = {
        "auto_draft_enabled": env.get("SUBSTACK_AUTO_DRAFT") == "1",
        "cookies_present": bool(env.get("SUBSTACK_COOKIES_STRING")),
        "publication_url_present": env.get("SUBSTACK_PUBLICATION_URL", "").startswith(
            "https://"
        ),
    }
    canary_ready = all(
        [
            is_macos,
            gh_ready,
            publish_api_ready,
            fast_script_current,
            fast_plist.is_file(),
            fast_loaded,
            not hourly_loaded,
            receipts["valid"],
            *env_checks.values(),
        ]
    )
    remote_proof_ready = database.get("remote_proven", 0) > 0
    ok = canary_ready and (remote_proof_ready if require_remote_proof else True)
    return {
        "ok": ok,
        "mode": "hourly_enablement" if require_remote_proof else "immediate_canary",
        "platform": platform_name,
        "checks": {
            "github_auth": gh_ready,
            "python_substack_publish_api": publish_api_ready,
            "fast_script_current": fast_script_current,
            "fast_plist_installed": fast_plist.is_file(),
            "fast_launchagent_loaded": fast_loaded,
            "hourly_launchagent_loaded": hourly_loaded,
            **env_checks,
        },
        "database": database,
        "receipts": receipts,
        "secrets_exposed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Mac worker preflight")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--require-remote-proof", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = inspect_runtime(
        repo_root=args.repo_root.resolve(),
        home=args.home.resolve(),
        require_remote_proof=args.require_remote_proof,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Mac worker doctor:", "READY" if report["ok"] else "BLOCKED")
        for name, value in report["checks"].items():
            print(f"  {'PASS' if value else 'FAIL'} {name}")
        db = report["database"]
        print(
            "  evidence "
            f"submissions={db['submissions']} pending_remote={db['pending_remote']} "
            f"remote_proven={db['remote_proven']} receipts={report['receipts']['pending']}"
        )
        print("  secret values: never printed")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
