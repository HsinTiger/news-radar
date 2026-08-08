"""Windows-owned Substack editorial writer.

This machine owns source selection and LLM composition.  The runner reuses the
repository's editorial profiles, deterministic reader-ready boundary, cover,
and footer.  It writes local/OneDrive artifacts and canonical state but does
not create or publish a remote Substack draft until this host is explicitly
given its own Substack credentials.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_REPO = "HsinTiger/news-radar"
LOCK_DIR = REPO_ROOT / ".runtime-state-windows-editorial.lock.d"
LEASE_FILE = REPO_ROOT / ".runtime-state-editorial-lease.json"


def writer_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("SUBSTACK_COMPOSER_BACKEND", "codex_cli,claude_cli")
    env.setdefault("CODEX_MODEL", "gpt-latest")
    env.setdefault("CLAUDE_MODEL", "claude-latest")
    # Windows Store Python inherits a CP950 console by default. The editorial
    # pipeline logs Unicode markers and Chinese paths, so force UTF-8 for every
    # child process before harvest or composition starts.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # This host currently owns writing only.  Never infer remote-write authority.
    env["SUBSTACK_AUTO_DRAFT"] = "0"
    return env


def compose_commands(
    profile: str,
    *,
    python_executable: str | None = None,
) -> list[list[str]]:
    py = python_executable or sys.executable
    if profile == "podcast-batch":
        return [
            [
                py,
                "-u",
                "substack_radar/compose.py",
                "podcast",
                "--harvest",
                "--editorial-profile",
                "weekly",
                "--no-draft",
            ],
            [
                py,
                "-u",
                "substack_radar/compose.py",
                "podcast",
                "--editorial-profile",
                "weekly",
                "--no-draft",
            ],
        ]
    if profile == "weekly":
        return [
            [py, "scripts/pick_company_candidate.py"],
            [
                py,
                "-u",
                "substack_radar/compose.py",
                "company",
                "--editorial-profile",
                "weekly",
                "--no-draft",
            ],
        ]
    raise ValueError(f"unsupported profile: {profile}")


def _run(command: list[str], *, env: dict[str, str], allow_one: bool = False) -> int:
    print("[windows-editorial] run:", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=REPO_ROOT, env=env, check=False)
    if completed.returncode == 0 or (allow_one and completed.returncode == 1):
        return completed.returncode
    raise RuntimeError(
        f"command failed exit={completed.returncode}: {' '.join(command)}"
    )


def _state_command(action: str, producer: str) -> list[str]:
    return [
        sys.executable,
        "scripts/state_store.py",
        action,
        "--repo",
        STATE_REPO,
        "--root",
        ".",
        "--producer",
        producer,
        "--lease-file",
        str(LEASE_FILE),
    ]


def run(profile: str) -> int:
    env = writer_environment()
    producer = f"windows_substack_writer_{profile}:{socket.gethostname()}"

    try:
        LOCK_DIR.mkdir()
    except FileExistsError as exc:
        raise RuntimeError(f"local writer lock already exists: {LOCK_DIR}") from exc

    leased = False
    try:
        if subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip():
            raise RuntimeError("worktree is dirty; refusing automated merge/write")

        _run(["git", "fetch", "--quiet", "origin", "main"], env=env)
        _run(["git", "merge", "--ff-only", "origin/main"], env=env)

        # 遠端停機閘門。刻意放在 merge 之後、取 lease 之前：
        # 之後才讀得到剛拉下來的新值，之前才不會在暫停時佔住 runtime-state 租約。
        #
        # 為什麼需要它：Windows Task Scheduler 的任務只能在該機器上停用，
        # 而這台機器會自動對外建立 Substack 草稿。owner 不在機器旁時，
        # 原本沒有任何辦法叫停——只能等人回到電腦前。
        # 這條讓一個 commit 就能遠端停機，與 Meta 線既有的
        # SUBMISSION_PROCESSOR_MODE 閘門同一個思路。
        #
        # 之所以放在 repo 檔案而不是環境變數：worker 每次執行都會
        # fetch + merge --ff-only origin/main（上面兩行），所以 push 之後
        # 下一次排程就會讀到新值，不需要任何人登入那台機器。
        mode_path = REPO_ROOT / "config" / "windows_writer_mode"
        mode = "live"
        if mode_path.exists():
            mode = (mode_path.read_text(encoding="utf-8").strip().split("\n")[0] or "live").lower()
        if mode != "live":
            print(
                f"[windows-writer] mode={mode} → 本次不寫稿、不取租約、不建立草稿。"
                f" 要恢復請把 {mode_path.relative_to(REPO_ROOT)} 改回 live 並 push。"
            )
            return 0

        lock = _state_command("lock", producer) + [
            "--lease-seconds",
            "7200",
            "--wait-seconds",
            "1800",
        ]
        _run(lock, env=env)
        leased = True
        _run(_state_command("pull", producer), env=env)

        failures = 0
        for index, command in enumerate(compose_commands(profile)):
            try:
                _run(
                    command,
                    env=env,
                    allow_one=(profile == "weekly" and index == 0),
                )
            except RuntimeError as exc:
                print(f"[windows-editorial] compose failure: {exc}", flush=True)
                failures += 1

        _run(_state_command("push", producer), env=env)
        if failures:
            return 1
        print(
            f"[windows-editorial] {profile} writing complete; remote draft not attempted",
            flush=True,
        )
        return 0
    finally:
        if leased:
            subprocess.run(
                _state_command("unlock", producer),
                cwd=REPO_ROOT,
                env=env,
                check=False,
            )
        LOCK_DIR.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=("podcast-batch", "weekly"))
    args = parser.parse_args()
    return run(args.profile)


if __name__ == "__main__":
    raise SystemExit(main())
