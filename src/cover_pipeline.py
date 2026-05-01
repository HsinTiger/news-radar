"""News Radar · Cover-pipeline orchestrator.

Sits between composer and publisher. Given the original news image URL +
draft metadata, returns the publish-API kwargs for each platform.

Why this is its own module
--------------------------
``cover_renderer`` is pure pixel work — given an input dataclass, it
writes a PNG and returns its path. This module wraps that with the
runtime concerns: download the original image to local cache, decide
which platform gets a cover, decide whether the publish call should use
``image_url`` (URL-fetch) or ``local_file_path`` (bytes upload), and
fall back gracefully when any step fails.

Per-platform behavior
---------------------
* **FB**: render 1080×1080 cover, upload via bytes path
  (``local_file_path=...``). FB Graph API accepts multipart bytes via the
  ``source`` form field; this is the cleanest way to ship a freshly
  rendered file without round-tripping through a public URL.
* **IG**: render 1080×1350 cover and SAVE it locally for future use, but
  Phase 1 still publishes the original news image URL — IG Graph API
  rejects bytes uploads and requires a public URL, which means we need a
  CDN / branch-hosting step that's deferred to Phase 2 (see
  ``docs/brand_visual.md`` and ``image_prep.py`` design notes).
* **Threads**: pass through the original image URL. Threads strategy is
  text-first per ``brand_visual.md``; covers would read as 廣告感.

If anything in the chain fails (download, render, missing fonts), this
module falls back to the original ``image_url`` and logs the reason.
Publishing must never fail because cover rendering failed.

Public API
----------
    from src.cover_pipeline import prepare_publish_image

    prep = await prepare_publish_image(
        platform_key="fb",                # "fb" | "ig" | "threads"
        original_image_url=row["og_image_url"],
        title=variant.title,
        topic_category=row["topic_category"],   # may be None
        subtitle=None,                          # composer doesn't emit yet
    )
    # prep == {"image_url": Optional[str], "local_file_path": Optional[str]}
    # — exactly one will be populated for FB-with-cover; one or both
    # may be None when the original URL is missing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from . import image_manager
from .cover_renderer import CoverInput, render_cover

logger = logging.getLogger(__name__)

# Brand-bar string per platform (per docs/brand_visual.md decision table)
BRAND_NAME_FOR_PLATFORM: Dict[str, str] = {
    "ig":      "smartmmmoney",
    "fb":      "主力爸爸我錯了",
    "threads": "smartmmmoney",  # never used — Threads gets no cover
}

# Aspects covered by the renderer
ASPECT_FOR_PLATFORM: Dict[str, str] = {
    "ig": "ig",
    "fb": "fb",
    # threads intentionally absent — guard against accidental call
}


PrepResult = Dict[str, Optional[str]]


def _passthrough(image_url: Optional[str]) -> PrepResult:
    """Return the {image_url:..., local_file_path: None} default."""
    return {"image_url": image_url, "local_file_path": None}


async def prepare_publish_image(
    *,
    platform_key: str,
    original_image_url: Optional[str],
    title: str,
    topic_category: Optional[str],
    subtitle: Optional[str] = None,
    date_str: Optional[str] = None,
    now: Optional[datetime] = None,
) -> PrepResult:
    """Decide what image kwargs to pass to ``publisher.publish_to_<platform>``.

    Returns a dict with two keys: ``image_url`` and ``local_file_path``.
    Exactly one is populated when a rendered cover is ready for bytes
    upload (FB path); ``image_url`` is populated for URL-based publish
    (IG, Threads, FB-fallback). Both may be ``None`` if the input had
    no image at all.
    """
    if not original_image_url:
        return _passthrough(None)

    # Threads: never gets a cover, pass through.
    if platform_key == "threads":
        return _passthrough(original_image_url)

    if platform_key not in ASPECT_FOR_PLATFORM:
        logger.warning("[cover_pipeline] unknown platform %r — passthrough", platform_key)
        return _passthrough(original_image_url)

    # Download the original image so cover_renderer can read it as bytes.
    try:
        local_orig = await image_manager.download_image(original_image_url)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[cover_pipeline] download_image raised: %s; passthrough", exc)
        return _passthrough(original_image_url)
    if not local_orig:
        logger.info(
            "[cover_pipeline] download failed for %s; passthrough",
            platform_key,
        )
        return _passthrough(original_image_url)

    # Build CoverInput. Topic_category may be None on legacy rows — fall
    # back to "macro" (gray chip) so render still produces something
    # useful instead of crashing on KeyError.
    when = now or datetime.now(timezone.utc)
    inp = CoverInput(
        image_path=Path(local_orig),
        title=title or "(無標題)",
        subtitle=subtitle,
        topic_category=topic_category or "macro",
        brand_name=BRAND_NAME_FOR_PLATFORM[platform_key],
        date_str=date_str or when.strftime("%Y/%m/%d"),
    )

    aspect = ASPECT_FOR_PLATFORM[platform_key]
    try:
        cover_path = render_cover(inp, aspect)
    except Exception as exc:
        logger.warning(
            "[cover_pipeline] render failed for %s (%s); passthrough",
            platform_key, exc,
        )
        return _passthrough(original_image_url)

    if platform_key == "fb":
        # FB takes bytes via local_file_path → use the rendered cover.
        logger.info("[cover_pipeline] FB cover rendered: %s", cover_path)
        return {"image_url": None, "local_file_path": str(cover_path)}

    # IG: cover is rendered (kept on disk for later URL hosting) but we
    # still publish the original URL because IG Graph API only accepts
    # public URLs. URL hosting wire-up is Phase 2.
    logger.info(
        "[cover_pipeline] IG cover rendered (%s) — Phase 2 hosting NYI; "
        "publishing with original URL",
        cover_path,
    )
    return _passthrough(original_image_url)
