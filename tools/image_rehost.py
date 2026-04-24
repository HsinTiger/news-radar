#!/usr/bin/env python3
"""
News Radar · Image Rehost Utility
==================================

Download a news article's hero image and re-host it via GitHub raw content
so Meta's fetcher (FB / IG / Threads) can actually read it.

Why this exists
---------------
Many news CDNs block hot-linking for external publishers:

  - Reuters (resizer.v2): requires `Referer: www.reuters.com` — returns 403 otherwise.
  - Bloomberg / WSJ / FT / NYT: similar hot-link defenses or auth-tokened URLs
    that expire in hours.

When these URLs are passed to Meta Graph API as `image_url`, Meta's server
tries to GET the URL (without our Referer / UA) and fails with codes like:

  - FB 324 "Missing or invalid image file"
  - IG/Threads 2207052 "影音素材下載失敗"

We learned this the hard way on 2026-04-24 with the Reuters "Meta captures
employee mouse/keystrokes" article — publish failed all three platforms
until we re-hosted the image to GitHub raw.

This module automates that workaround.

Contract
--------
    rehost_to_github_raw(image_url, news_id, *, force=False) -> str

Steps:
    1. Check if assets/{news_id_prefix}.{ext} already exists in the repo.
       If yes and `force=False`, return the existing raw URL. No net IO.
    2. Download image_url using a browser-like User-Agent (and Referer, if
       we can infer one from the URL's host).
    3. Validate the download is a real image (PIL Image.open succeeds;
       dimensions are non-zero).
    4. Save to news_radar/assets/{prefix}.{ext}.
    5. git add, commit, push (only the new asset — uses `git commit -- path`
       so a dirty working tree elsewhere doesn't get bundled in).
    6. Return raw.githubusercontent.com URL.

Raises RuntimeError on any failure step with an actionable message.

Filename convention
-------------------
    assets/{news_id[:16]}.{ext}

  - news_id is sha1(url) so it's stable per article → idempotent rehost.
  - 16-char prefix is plenty to avoid collision in a single-operator repo
    (2^64 name space) while staying short.
  - Extension: inferred from Content-Type, with jpg as safe fallback.

Standalone CLI
--------------
For debugging / one-off use:

    python tools/image_rehost.py \\
        --url "https://reuters.com/resizer/v2/..." \\
        --news-id 8ab18d3f7ea6ec3c963a3dbbc61138d6425117ff

Prints the resulting raw URL on success, or error on stderr.
"""
from __future__ import annotations

import argparse
import hashlib
import mimetypes
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"

# Match src/fetcher.py's UA so Reuters et al. treat us like a normal browser.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.2 Safari/605.1.15"
)

# Map Content-Type to file extension. Keep the list short — if we encounter
# something else, we fall back to .jpg (Meta accepts JPEG widely).
_CT_TO_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


# ---- helpers ---------------------------------------------------------------
def _infer_referer(image_url: str) -> Optional[str]:
    """Infer a plausible Referer from the image host.

    Reuters CDN images are under `reuters.com` subdomains and expect
    `Referer: https://www.reuters.com/` (not the exact article URL). Similar
    pattern for most publishers — the site's own homepage works.
    """
    try:
        parsed = urllib.parse.urlparse(image_url)
        host = parsed.netloc.lower()
    except Exception:
        return None
    # Strip `resizer.` / `www.` / `cdn.` prefixes to find the base domain.
    for prefix in ("resizer.", "cdn.", "www.", "images.", "img."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    # Reuters resizer URLs have `reuters.com` at root; similar for most.
    # Just reassemble with `www.` and https.
    return f"https://www.{host}/" if host else None


def _derive_raw_url(news_id_prefix: str, ext: str) -> str:
    """Build the github raw URL. Parses git remote to get owner/repo so this
    works on anyone's fork without hardcoding HsinTiger/news-radar.
    """
    try:
        remote = subprocess.check_output(
            ["git", "-C", str(PROJECT_ROOT), "config", "--get", "remote.origin.url"],
            text=True,
        ).strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"無法讀 git remote.origin.url：{e}")

    # Normalize both SSH and HTTPS remotes:
    #   https://github.com/Owner/Repo.git
    #   git@github.com:Owner/Repo.git
    slug = None
    if remote.startswith("https://github.com/"):
        slug = remote[len("https://github.com/"):]
    elif remote.startswith("git@github.com:"):
        slug = remote[len("git@github.com:"):]
    if slug is None:
        raise RuntimeError(f"git remote 看起來不是 GitHub：{remote!r}")
    if slug.endswith(".git"):
        slug = slug[:-4]

    # Default branch: assume `main`. We could query `git symbolic-ref refs/remotes/origin/HEAD`
    # but that requires having fetched origin, which isn't guaranteed in all envs.
    # For news_radar / dashboards the convention is main.
    branch = "main"
    return f"https://raw.githubusercontent.com/{slug}/{branch}/assets/{news_id_prefix}.{ext}"


def _run(cmd: list[str], *, check: bool = True, capture: bool = False) -> str:
    """Run a subprocess. Returns stdout if capture=True, else empty string."""
    try:
        if capture:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
            return out
        else:
            subprocess.run(cmd, check=check)
            return ""
    except subprocess.CalledProcessError as e:
        tail = (e.output or "")[-500:] if hasattr(e, "output") and e.output else ""
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{tail}")


def _download_image(url: str, dest: Path) -> str:
    """Download `url` to `dest`. Returns the inferred extension (no leading dot).

    Uses stdlib urllib so we don't add an httpx/requests dependency here.
    """
    import urllib.request

    headers = {"User-Agent": _BROWSER_UA}
    referer = _infer_referer(url)
    if referer:
        headers["Referer"] = referer

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    except Exception as e:
        raise RuntimeError(f"下載失敗：{url}\n{type(e).__name__}: {e}")

    if not data or len(data) < 512:
        raise RuntimeError(f"下載的內容太小，非圖片可能：{len(data)} bytes (url={url})")

    ext = _CT_TO_EXT.get(ct)
    if ext is None:
        # Fall back to inferring from URL path
        guessed_ext = mimetypes.guess_extension(ct) if ct else None
        if guessed_ext:
            ext = guessed_ext.lstrip(".")
        else:
            # Last resort: JPG
            ext = "jpg"

    # Retro-fit the dest suffix so it matches the real content type
    dest = dest.with_suffix(f".{ext}")
    dest.write_bytes(data)

    # Sanity-check: open with PIL to verify it's actually an image
    try:
        from PIL import Image

        with Image.open(dest) as im:
            w, h = im.size
            if w < 32 or h < 32:
                raise RuntimeError(f"圖片尺寸過小 ({w}x{h}) — 可能是防盜鏈 placeholder")
    except Exception as e:
        # Clean up the bad file so retry is sane
        try:
            dest.unlink()
        except Exception:
            pass
        raise RuntimeError(f"下載的檔案不是合法圖片：{dest.name} — {type(e).__name__}: {e}")

    return ext


def _git_add_commit_push(asset_path: Path, title_hint: str, news_id: str) -> None:
    """Add just this one asset, commit, push. Leaves any other uncommitted
    changes in the working tree alone (we pass the path explicitly)."""
    rel = asset_path.relative_to(PROJECT_ROOT)

    # 0. Safety: verify we're on main. Pushing an asset-commit to `state`
    # branch would corrupt the state-branch-as-DB-snapshot convention.
    branch = _run(
        ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
        capture=True,
    ).strip()
    if branch != "main":
        raise RuntimeError(
            f"目前在 branch '{branch}'，不是 main。auto-rehost 只能在 main 上跑"
            f"（避免 asset commit 跑進 state branch / feature branch）。"
            f"請先 `git checkout main` 再重試。"
        )

    # 1. Is the file already tracked + clean? If so, nothing to commit.
    status = _run(
        ["git", "-C", str(PROJECT_ROOT), "status", "--porcelain", "--", str(rel)],
        capture=True,
    ).strip()
    if not status:
        print(f"[rehost] {rel} 已 tracked + 無改動，跳過 git push")
        return

    # 2. Stage
    _run(["git", "-C", str(PROJECT_ROOT), "add", "--", str(rel)])

    # 3. Commit (only this path — don't sweep in other dirty files)
    msg = f"chore(assets): add hero image for news_id={news_id[:16]} [{title_hint[:60]}]"
    _run(
        ["git", "-C", str(PROJECT_ROOT), "commit", "-m", msg, "--", str(rel)],
        check=True,
    )

    # 4. Push
    _run(["git", "-C", str(PROJECT_ROOT), "push", "origin", "main"])


# ---- public API ------------------------------------------------------------
def rehost_to_github_raw(
    image_url: str,
    news_id: str,
    *,
    title_hint: str = "",
    force: bool = False,
) -> str:
    """Download + re-host + push. Returns the raw.githubusercontent.com URL.

    Idempotent: if assets/{news_id[:16]}.* already exists, returns the existing
    raw URL without re-downloading or pushing (unless `force=True`).

    Raises RuntimeError on any step failure.
    """
    if not image_url:
        raise RuntimeError("image_url is empty — 無 og_image 可 rehost")
    if not news_id:
        raise RuntimeError("news_id is empty")

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    prefix = news_id[:16]
    # Check if any file with this prefix already exists (any ext)
    existing = sorted(ASSETS_DIR.glob(f"{prefix}.*"))
    if existing and not force:
        asset = existing[0]
        ext = asset.suffix.lstrip(".")
        raw_url = _derive_raw_url(prefix, ext)
        print(f"[rehost] 已存在：{asset.name} → 直接用 {raw_url}")
        return raw_url

    # Download (provisional dest, extension gets set by Content-Type)
    dest = ASSETS_DIR / f"{prefix}.jpg"
    print(f"[rehost] 下載中：{image_url[:80]}... → assets/{dest.name}")
    ext = _download_image(image_url, dest)
    # _download_image may have moved dest to `dest.with_suffix(.{ext})`
    final = ASSETS_DIR / f"{prefix}.{ext}"
    size = final.stat().st_size
    print(f"[rehost] 下載完成：{final.name} ({size:,} bytes)")

    # Clean up other same-prefix files (different extension from a previous try)
    for other in ASSETS_DIR.glob(f"{prefix}.*"):
        if other != final:
            print(f"[rehost] 移除舊檔：{other.name}")
            try:
                other.unlink()
            except Exception:
                pass

    # git
    _git_add_commit_push(final, title_hint or prefix, news_id)

    raw_url = _derive_raw_url(prefix, ext)
    print(f"[rehost] raw URL：{raw_url}")
    return raw_url


# ---- CLI -------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Download + re-host a hero image via GitHub raw")
    ap.add_argument("--url", required=True, help="image URL to download")
    ap.add_argument("--news-id", required=True, help="news_id (sha1 of article URL)")
    ap.add_argument("--title-hint", default="", help="short title for commit message")
    ap.add_argument("--force", action="store_true", help="force re-download + re-commit")
    args = ap.parse_args()

    try:
        url = rehost_to_github_raw(
            args.url, args.news_id, title_hint=args.title_hint, force=args.force
        )
        print(url)
        return 0
    except Exception as e:
        print(f"[rehost] ❌ {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
