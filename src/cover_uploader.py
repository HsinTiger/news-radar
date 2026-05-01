"""News Radar · Cover uploader (Phase 2 brand-unification).

Pushes a locally rendered cover PNG to the public ``cover-cdn`` branch
on GitHub and returns the raw URL that Meta Graph API will fetch from.

Why a separate branch
---------------------
The dashboard reads from ``state`` branch (mostly the SQLite DB blob).
The ``main`` branch is source code. Cover PNGs need a third location
that is:

  1. **Public-readable** — Meta's image fetcher needs no auth.
  2. **Stable URL** — once a cover is up, the URL must keep working
     for the post's lifetime.
  3. **Disposable** — old covers can be pruned without affecting the
     post (Meta caches the image; we only need the URL alive long
     enough for the initial fetch + any reposts within ~30 days).
  4. **Independent of source-code review** — pushing 100s of binary
     PNGs to ``main`` would make ``git log`` unreadable.

A dedicated ``cover-cdn`` branch satisfies all four. URLs are served by
GitHub's raw CDN at:

    https://raw.githubusercontent.com/{owner}/{repo}/cover-cdn/{name}.png

URL stability + filename
------------------------
Filename = ``{draft_id}_{platform}.png`` where ``platform`` is ``fb``
or ``ig`` (lowercase). draft_id is sha1 hex from
``run_pipeline.py``, so collisions are not a concern.

This pattern means the dashboard can construct cover URLs WITHOUT
touching the DB — it just needs draft_id, which it already has.

Concurrency / retry
-------------------
Two crons can race when both fire near the same minute (Mac compose
+ Cloud publish). The push uses ``git fetch + commit + push`` with
up to 3 retries on rejection. Adding a new PNG is always a non-
conflicting addition to the tree, so retries near-always succeed.

Branch bootstrapping
--------------------
First call ever: ``cover-cdn`` branch doesn't exist remotely. We
push from a fresh orphan workspace, which creates the branch on
the remote.

Public API
----------
    from src.cover_uploader import upload_cover

    raw_url = upload_cover(
        local_png=Path("assets/cover_cache/abc..._fb_1x1.png"),
        draft_id="abc1234567890def...",
        platform_key="fb",        # or "ig"
    )
    # raw_url == "https://raw.githubusercontent.com/HsinTiger/news-radar/cover-cdn/abc1234567890def_fb.png"
    # Returns None on any failure — caller falls back to original URL.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — overridable via env vars for testing / forks
# ---------------------------------------------------------------------------

# Repo coordinates. Defaults match the live news_radar deployment.
DEFAULT_OWNER = "HsinTiger"
DEFAULT_REPO = "news-radar"
COVER_BRANCH = "cover-cdn"

REPO_URL_TEMPLATE = "https://github.com/{owner}/{repo}.git"
RAW_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}"
)

# Retry policy
MAX_PUSH_ATTEMPTS = 3
PUSH_BACKOFF_SECONDS = 1.5

# Authentication: prefer GITHUB_TOKEN env var (works on both Mac and
# GH Actions). Falls back to the user's git credential helper if absent.
ENV_GITHUB_TOKEN = "GITHUB_TOKEN"

# Commit author shown in cover-cdn history. Mirrors push_state.sh's
# author so cover-cdn commits are easy to filter from main.
COMMIT_AUTHOR_NAME = "news-radar-cover-uploader"
COMMIT_AUTHOR_EMAIL = "noreply@local"


# ---------------------------------------------------------------------------
# Filename + URL helpers (pure)
# ---------------------------------------------------------------------------

def cover_filename(draft_id: str, platform_key: str) -> str:
    """Construct the canonical cover filename in cover-cdn.

    Pure function — no IO, no validation beyond shape. Mirrored in the
    dashboard's ``CoverPage.jsx`` so any change here MUST land in both
    repos in the same commit.
    """
    if platform_key not in ("fb", "ig"):
        raise ValueError(f"platform_key must be 'fb' or 'ig', got {platform_key!r}")
    if not draft_id:
        raise ValueError("draft_id must be non-empty")
    return f"{draft_id}_{platform_key}.png"


def construct_raw_url(
    draft_id: str,
    platform_key: str,
    *,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    branch: str = COVER_BRANCH,
) -> str:
    """Build the raw.githubusercontent.com URL.

    Pure function — does NOT verify the URL is reachable. Caller
    decides whether to assume up-to-date readiness.
    """
    fname = cover_filename(draft_id, platform_key)
    return RAW_URL_TEMPLATE.format(
        owner=owner, repo=repo, branch=branch, filename=fname
    )


# ---------------------------------------------------------------------------
# Git operations (subprocess wrappers — testable via patch)
# ---------------------------------------------------------------------------

@dataclass
class _GitContext:
    """Per-call workspace + auth setup."""
    workspace: Path
    repo_url: str
    branch: str
    auth_token: Optional[str]


def _make_authed_url(repo_url: str, token: Optional[str]) -> str:
    """Inject GITHUB_TOKEN into the HTTPS URL for git push auth."""
    if not token:
        return repo_url
    if not repo_url.startswith("https://"):
        return repo_url
    return repo_url.replace("https://", f"https://x-access-token:{token}@")


def _git(args: list, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git subcommand. Captures stdout/stderr for error diagnosis."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


def _setup_workspace(ctx: _GitContext) -> None:
    """Initialize a fresh checkout of the cover-cdn branch.

    Strategy: shallow clone of the cover-cdn branch only. If the branch
    doesn't exist yet (first run ever), fall back to initializing an
    empty orphan workspace — the eventual push creates the branch.
    """
    ws = ctx.workspace
    authed = _make_authed_url(ctx.repo_url, ctx.auth_token)

    # Try shallow clone of the branch
    proc = _git(
        ["clone", "--depth", "1", "--branch", ctx.branch,
         "--single-branch", authed, str(ws)],
        cwd=Path("/"),
        check=False,
    )
    if proc.returncode == 0:
        return

    # Branch missing remotely → bootstrap as orphan
    logger.info(
        "[cover_uploader] cover-cdn branch missing remotely (%s); "
        "bootstrapping as orphan",
        proc.stderr.strip().split("\n")[-1] if proc.stderr else "no stderr",
    )
    ws.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", ctx.branch], cwd=ws)
    _git(["remote", "add", "origin", authed], cwd=ws)
    _git(["config", "user.name", COMMIT_AUTHOR_NAME], cwd=ws)
    _git(["config", "user.email", COMMIT_AUTHOR_EMAIL], cwd=ws)


def _commit_and_push(ctx: _GitContext, message: str) -> None:
    """git add . && git commit && git push origin <branch>."""
    ws = ctx.workspace
    _git(["config", "user.name", COMMIT_AUTHOR_NAME], cwd=ws)
    _git(["config", "user.email", COMMIT_AUTHOR_EMAIL], cwd=ws)
    _git(["add", "-A"], cwd=ws)

    # Skip commit if nothing changed (e.g. PNG already pushed earlier)
    diff = _git(["diff", "--cached", "--quiet"], cwd=ws, check=False)
    if diff.returncode == 0:
        logger.info("[cover_uploader] no diff — skipping commit/push")
        return

    _git(["commit", "-m", message], cwd=ws)
    _git(["push", "origin", ctx.branch], cwd=ws)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def upload_cover(
    local_png: Path,
    draft_id: str,
    platform_key: str,
    *,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    branch: str = COVER_BRANCH,
    auth_token: Optional[str] = None,
) -> Optional[str]:
    """Push the local PNG to ``cover-cdn`` and return its raw URL.

    Returns ``None`` on ANY failure — callers must treat this as a
    soft failure and fall back to the original news image URL.
    Publishing must NEVER fail because cover upload failed.

    Idempotent: if the same filename + content already exists on the
    branch, no push happens and the existing raw URL is returned.
    """
    if not local_png.exists():
        logger.warning("[cover_uploader] local_png not found: %s", local_png)
        return None

    try:
        fname = cover_filename(draft_id, platform_key)
    except ValueError as exc:
        logger.warning("[cover_uploader] bad input: %s", exc)
        return None

    repo_url = REPO_URL_TEMPLATE.format(owner=owner, repo=repo)
    token = auth_token or os.environ.get(ENV_GITHUB_TOKEN)

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_PUSH_ATTEMPTS + 1):
        tmp_root = Path(tempfile.mkdtemp(prefix="cover_cdn_"))
        ws = tmp_root / "ws"
        ctx = _GitContext(
            workspace=ws, repo_url=repo_url, branch=branch, auth_token=token
        )
        try:
            _setup_workspace(ctx)
            target = ws / fname
            shutil.copy(local_png, target)
            _commit_and_push(
                ctx,
                message=f"cover: {draft_id[:12]}… {platform_key} (attempt {attempt})",
            )
            url = RAW_URL_TEMPLATE.format(
                owner=owner, repo=repo, branch=branch, filename=fname
            )
            logger.info("[cover_uploader] uploaded %s → %s", fname, url)
            return url
        except subprocess.CalledProcessError as exc:
            last_err = exc
            logger.warning(
                "[cover_uploader] attempt %d/%d failed: %s\nstderr: %s",
                attempt, MAX_PUSH_ATTEMPTS, exc, exc.stderr,
            )
            if attempt < MAX_PUSH_ATTEMPTS:
                time.sleep(PUSH_BACKOFF_SECONDS)
        except Exception as exc:  # pragma: no cover — defensive
            last_err = exc
            logger.warning("[cover_uploader] unexpected error: %s", exc)
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    logger.error(
        "[cover_uploader] all %d attempts failed; last error: %s",
        MAX_PUSH_ATTEMPTS, last_err,
    )
    return None
