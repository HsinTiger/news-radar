import json
import sqlite3
import subprocess
from pathlib import Path

from scripts.mac_worker_doctor import inspect_runtime


def _write_runtime(root: Path, home: Path, *, remote_proven: bool) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    source = scripts / "drain_substack_fast.sh"
    source.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "news_radar_substack_fast.sh").write_bytes(source.read_bytes())
    agent_dir = home / "Library" / "LaunchAgents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "com.hsin.news-radar.substack-fast.plist").write_text(
        "plist", encoding="utf-8"
    )
    runtime = home / "news_radar"
    runtime.mkdir()
    (runtime / ".env").write_text(
        "SUBSTACK_AUTO_DRAFT=1\n"
        "SUBSTACK_COOKIES_STRING=secret-not-reported\n"
        "SUBSTACK_PUBLICATION_URL=https://example.substack.com\n",
        encoding="utf-8",
    )
    db_path = runtime / "data" / "01_harvest" / "news_radar.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE news_items(
             id TEXT,feed_name TEXT,substack_draft_id TEXT,substack_drafted_at TEXT
           )"""
    )
    conn.execute(
        "INSERT INTO news_items VALUES('s1','user_substack',?,?)",
        ("draft-1", "2099-01-01T00:00:00Z") if remote_proven else (None, None),
    )
    conn.commit()
    conn.close()


def _canary_runner(command, **_kwargs):
    return subprocess.CompletedProcess(
        command,
        1 if "com.hsin.news-radar.compose" in " ".join(command) else 0,
    )


def test_canary_doctor_reports_ready_without_exposing_secret(tmp_path) -> None:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    _write_runtime(root, home, remote_proven=False)
    report = inspect_runtime(
        repo_root=root,
        home=home,
        platform_name="Darwin",
        runner=_canary_runner,
    )
    assert report["ok"] is True
    assert report["database"]["pending_remote"] == 1
    assert report["secrets_exposed"] is False
    assert "secret-not-reported" not in json.dumps(report)


def test_hourly_enablement_requires_real_remote_draft_evidence(tmp_path) -> None:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    _write_runtime(root, home, remote_proven=False)
    blocked = inspect_runtime(
        repo_root=root,
        home=home,
        platform_name="Darwin",
        runner=_canary_runner,
        require_remote_proof=True,
    )
    assert blocked["ok"] is False

    proven_home = tmp_path / "proven-home"
    _write_runtime(root, proven_home, remote_proven=True)
    ready = inspect_runtime(
        repo_root=root,
        home=proven_home,
        platform_name="Darwin",
        runner=_canary_runner,
        require_remote_proof=True,
    )
    assert ready["ok"] is True


def test_non_macos_host_is_blocked(tmp_path) -> None:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    _write_runtime(root, home, remote_proven=True)
    report = inspect_runtime(
        repo_root=root,
        home=home,
        platform_name="Windows",
        runner=_canary_runner,
    )
    assert report["ok"] is False
    assert report["checks"]["fast_launchagent_loaded"] is False


def test_immediate_canary_is_blocked_when_hourly_worker_is_already_loaded(
    tmp_path,
) -> None:
    root = tmp_path / "repo"
    home = tmp_path / "home"
    _write_runtime(root, home, remote_proven=False)

    def all_loaded(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0)

    report = inspect_runtime(
        repo_root=root,
        home=home,
        platform_name="Darwin",
        runner=all_loaded,
    )
    assert report["checks"]["hourly_launchagent_loaded"] is True
    assert report["ok"] is False
