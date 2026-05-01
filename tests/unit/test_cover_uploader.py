"""Unit tests for src/cover_uploader.py.

Strategy: subprocess.run is patched per-test so no real git commands
fire. URL construction and filename pattern are pure functions and
get straight-line tests; the upload orchestration is tested via the
mocked subprocess output.

Independent verification (curl + sha256 round-trip against a real
deployed cover-cdn branch) lives in
``verification_logs/2026-05-02_phase2_cover_cdn.md`` — these unit
tests are the "I built what I designed" half, not the full picture.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from src.cover_uploader import (
    COVER_BRANCH,
    DEFAULT_OWNER,
    DEFAULT_REPO,
    MAX_PUSH_ATTEMPTS,
    construct_raw_url,
    cover_filename,
    upload_cover,
)


# ---------------------------------------------------------------------------
# Pure helpers — straight-line tests
# ---------------------------------------------------------------------------

def test_filename_format_for_fb():
    assert cover_filename("abc123def456", "fb") == "abc123def456_fb.png"


def test_filename_format_for_ig():
    assert cover_filename("abc123def456", "ig") == "abc123def456_ig.png"


def test_filename_rejects_unknown_platform():
    with pytest.raises(ValueError):
        cover_filename("abc123", "threads")


def test_filename_rejects_empty_draft_id():
    with pytest.raises(ValueError):
        cover_filename("", "fb")


def test_raw_url_uses_default_repo():
    url = construct_raw_url("draft_xyz", "fb")
    assert url == (
        f"https://raw.githubusercontent.com/{DEFAULT_OWNER}/"
        f"{DEFAULT_REPO}/{COVER_BRANCH}/draft_xyz_fb.png"
    )


def test_raw_url_overrideable_for_forks():
    url = construct_raw_url(
        "draft_xyz", "ig",
        owner="someone", repo="myrepo", branch="my-cdn",
    )
    assert url == (
        "https://raw.githubusercontent.com/someone/myrepo/my-cdn/draft_xyz_ig.png"
    )


# ---------------------------------------------------------------------------
# upload_cover orchestration — subprocess mocked
# ---------------------------------------------------------------------------

def _ok_proc(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=0, stdout=stdout, stderr=stderr)


def _fail_proc(stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=1, stdout="", stderr=stderr)


@pytest.fixture
def fake_png(tmp_path: Path) -> Path:
    p = tmp_path / "input.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return p


def test_upload_cover_returns_none_when_local_missing(tmp_path: Path):
    """Caller must handle a missing local PNG without crashing."""
    fake_path = tmp_path / "does_not_exist.png"
    assert upload_cover(fake_path, draft_id="abc", platform_key="fb") is None


def _make_subprocess_mock(diff_has_changes: bool):
    """Build a side_effect callable that simulates git subprocess success.

    The clone call needs to create the workspace dir so subsequent
    shutil.copy succeeds. The diff --cached --quiet call returns 0 (no
    diff) or 1 (diff present) depending on `diff_has_changes`.
    """
    def _side_effect(args, cwd=None, capture_output=None, text=None, check=None):
        # First positional arg is ['git', subcommand, ...]
        sub = args[1] if len(args) > 1 else ""
        if sub == "clone":
            # Last arg is the destination dir — create it so shutil.copy works
            dest = Path(args[-1])
            dest.mkdir(parents=True, exist_ok=True)
            return _ok_proc()
        if sub == "diff":
            return _fail_proc("") if diff_has_changes else _ok_proc()
        return _ok_proc()
    return _side_effect


def test_upload_cover_happy_path_returns_raw_url(fake_png: Path):
    """Successful clone + commit + push → raw URL returned."""
    with patch(
        "src.cover_uploader.subprocess.run",
        side_effect=_make_subprocess_mock(diff_has_changes=True),
    ) as m:
        url = upload_cover(fake_png, draft_id="draft_abc", platform_key="fb")
    assert url == (
        f"https://raw.githubusercontent.com/{DEFAULT_OWNER}/"
        f"{DEFAULT_REPO}/{COVER_BRANCH}/draft_abc_fb.png"
    )
    # Sanity: at least clone, add, diff, commit, push
    assert m.call_count >= 5


def test_upload_cover_skips_push_when_no_diff(fake_png: Path):
    """Idempotency: same content already on branch → no commit, but URL still returned."""
    with patch(
        "src.cover_uploader.subprocess.run",
        side_effect=_make_subprocess_mock(diff_has_changes=False),
    ):
        url = upload_cover(fake_png, draft_id="draft_xyz", platform_key="ig")
    assert url is not None
    assert "draft_xyz_ig.png" in url


def test_upload_cover_retries_then_fails(fake_png: Path):
    """3 attempts of clone+commit+push all fail → return None."""
    # Each attempt: clone fails (orphan fallback init→add→diff→commit→push fails)
    fail = subprocess.CalledProcessError(
        returncode=128, cmd=["git", "push"], output="", stderr="auth failed",
    )
    with patch("src.cover_uploader.subprocess.run", side_effect=fail):
        url = upload_cover(fake_png, draft_id="draft_x", platform_key="fb")
    assert url is None


def test_upload_cover_swallows_unexpected_error(fake_png: Path):
    """Any unexpected error → soft failure (None), publishing must continue."""
    with patch("src.cover_uploader.subprocess.run", side_effect=RuntimeError("disk full")):
        url = upload_cover(fake_png, draft_id="draft_y", platform_key="ig")
    assert url is None


def test_upload_cover_max_retries_constant():
    """Sanity check on the constant — if changed, push_state-style retry tests need updating."""
    assert MAX_PUSH_ATTEMPTS == 3
