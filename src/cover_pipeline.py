"""News Radar · Cover-pipeline orchestrator (Phase 2 — symmetric URL flow).

Sits between composer and publisher. Given the original news image URL
+ draft metadata, returns the publish-API kwargs for each platform.

Phase 2 design change
---------------------
Both **FB and IG** now go through the same flow:

    download original → render cover PNG → upload to cover-cdn branch
    → return public raw URL → publisher passes URL to Graph API.

The earlier asymmetric design (FB used bytes upload, IG used URL) was
revisited 2026-05-02 — symmetric is simpler to maintain, gives a
unified audit trail (one branch = every cover ever shipped), and lets
the dashboard preview both platforms with one URL pattern.

Why this is its own module
--------------------------
``cover_renderer`` does pure pixel work. ``cover_uploader`` does pure
git/network work. This module wires them together and decides what
the publisher should ultimately receive.

Per-platform behavior
---------------------
* **FB**: render 1080×1080 → upload → URL.
* **IG**: render 1080×1350 → upload → URL.
* **Threads**: render 1080×1350 → upload → URL.
  Phase 9.5 / 2026-05-02 update: previously passed through original
  image (text-first strategy assumption); reversed after Hsin's call
  that brand consistency outweighs the "廣告感" risk. Reflector will
  catch any engagement_yield_ratio regression within 14 days.

Failure handling
----------------
If ANY step fails (download / render / upload / missing fonts / network
hiccup), this module returns the original ``image_url`` unchanged.
Publishing never breaks because cover rendering or upload broke.

Public API
----------
    from src.cover_pipeline import prepare_publish_image

    prep = await prepare_publish_image(
        platform_key="fb",                # "fb" | "ig" | "threads"
        original_image_url=row["og_image_url"],
        draft_id=draft_id,
        title=variant.title,
        topic_category=row["topic_category"],
        subtitle=None,
    )
    # prep == {"image_url": Optional[str], "local_file_path": None}
    # local_file_path is now ALWAYS None — kept in shape for backwards
    # compatibility with publisher.publish_to_fb's local upload path.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from PIL import Image

from . import image_manager
from .cover_renderer import ASSETS_DIR, CoverInput, OVERLAY_RGB, render_cover
from .cover_uploader import upload_cover

logger = logging.getLogger(__name__)

# Fallback image for news with no usable og:image. NOT a flat color block —
# we synthesize a film-grain-like texture so the rendered cover doesn't look
# "lazy / template-y" when it lands in feed alongside cover with real news
# images. The texture is generated at low resolution then upscaled bicubic,
# so each "noise pixel" survives the 10px gaussian blur cover_renderer
# applies during render (see brand_visual.md §Background composition).
#
# 2026-05-02 升級：原本是 solid color、視覺極無聊 + 一眼看出「沒有原圖」。
# Noise texture 解這兩個問題、cost 是 ~30k 次 random.randint。Cached 後
# 後續呼叫直接 reuse、不會每次重生成。
_FALLBACK_IMAGE_NAME = "_cover_fallback_textured_navy.jpg"
_FALLBACK_IMAGE_SIZE = (1920, 1080)
_FALLBACK_NOISE_DOWNSCALE = 8       # 8x downscale → noise blocks of 8x8 in final
_FALLBACK_NOISE_AMPLITUDE = 15      # ±15 RGB units around base color
_FALLBACK_NOISE_SEED = 42           # deterministic — same fallback every run


def _ensure_fallback_image() -> Path:
    """Generate-or-reuse the synthetic fallback. Lazy: only generates on
    first call (test + first-run pay the cost, then cached forever).

    Texture method: low-res random luminance jitter around the navy base,
    upscaled bicubic to target size. The downscale + bicubic combination
    yields organic-feeling grain that's coarse enough to survive the
    cover_renderer's gaussian blur (otherwise per-pixel noise would
    average out invisible).

    Determinism: seeded RNG so the fallback is byte-identical across
    machines / CI / re-runs. Important for the sha256 round-trip
    verification path in verification_logs.
    """
    cache_dir = ASSETS_DIR / "image_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / _FALLBACK_IMAGE_NAME
    if out.exists():
        return out

    # Generate at 1/N resolution
    small_w = _FALLBACK_IMAGE_SIZE[0] // _FALLBACK_NOISE_DOWNSCALE
    small_h = _FALLBACK_IMAGE_SIZE[1] // _FALLBACK_NOISE_DOWNSCALE
    small = Image.new("RGB", (small_w, small_h), OVERLAY_RGB)
    pixels = small.load()
    rng = random.Random(_FALLBACK_NOISE_SEED)
    base_r, base_g, base_b = OVERLAY_RGB
    for y in range(small_h):
        for x in range(small_w):
            d = rng.randint(-_FALLBACK_NOISE_AMPLITUDE, _FALLBACK_NOISE_AMPLITUDE)
            pixels[x, y] = (
                max(0, min(255, base_r + d)),
                max(0, min(255, base_g + d)),
                max(0, min(255, base_b + d)),
            )

    # Upscale to target with bicubic — smooths block edges, gives organic feel
    result = small.resize(_FALLBACK_IMAGE_SIZE, Image.Resampling.BICUBIC)
    result.save(out, "JPEG", quality=85)
    return out

# Brand-bar string per platform (per docs/brand_visual.md decision table)
BRAND_NAME_FOR_PLATFORM: Dict[str, str] = {
    "ig":      "smartmmmoney",
    "fb":      "主力爸爸我錯了",
    "threads": "smartmmmoney",
}

# Aspects covered by the renderer (key into cover_renderer.SPECS)
ASPECT_FOR_PLATFORM: Dict[str, str] = {
    "ig":      "ig",
    "fb":      "fb",
    "threads": "threads",
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
    draft_id: Optional[str] = None,
    subtitle: Optional[str] = None,
    date_str: Optional[str] = None,
    now: Optional[datetime] = None,
) -> PrepResult:
    """Decide what image kwargs to pass to ``publisher.publish_to_<platform>``.

    Returns a dict ``{"image_url": str|None, "local_file_path": None}``.

    The ``local_file_path`` slot is preserved (always None in Phase 2) so
    callers in run_pipeline.py / run_publish_queue.py can keep their
    same-shape unpacking. Old FB bytes-upload path is gone.

    ``draft_id`` is required for FB/IG covers — it determines the
    upload filename. If absent, falls back to the original URL.
    """
    # Guard: the string "None" from env/config is not the same as Python None
    if isinstance(original_image_url, str) and original_image_url.strip().lower() in ("none", "null", ""):
        original_image_url = None

    if platform_key not in ASPECT_FOR_PLATFORM:
        logger.warning("[cover_pipeline] unknown platform %r — passthrough", platform_key)
        return _passthrough(original_image_url)

    if not draft_id:
        logger.info(
            "[cover_pipeline] no draft_id given for %s — passthrough (cover URL needs stable filename)",
            platform_key,
        )
        return _passthrough(original_image_url)

    # 1) Try to download the original news image so cover_renderer can use
    #    it as the (heavily blurred + dimmed) backdrop. If that fails OR
    #    the news has no image at all, fall back to a synthetic dark navy
    #    background. Either way the rendered cover is brand-consistent and
    #    publishable — earlier behavior of skipping IG/Threads when the
    #    news source had no og:image is the bug we're undoing here
    #    (Phase 9.5 follow-up, 2026-05-02).
    local_orig: Optional[str] = None
    if original_image_url:
        try:
            local_orig = await image_manager.download_image(original_image_url)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("[cover_pipeline] download_image raised: %s; using fallback", exc)
            local_orig = None
    if not local_orig:
        fallback = _ensure_fallback_image()
        logger.info(
            "[cover_pipeline] no usable source image for %s — using synthetic fallback (%s)",
            platform_key, fallback.name,
        )
        local_orig = str(fallback)

    # 2) Render the cover.
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

    # 3) Upload to cover-cdn → public URL.
    try:
        raw_url = upload_cover(
            local_png=cover_path,
            draft_id=draft_id,
            platform_key=platform_key,
        )
    except Exception as exc:  # pragma: no cover — uploader returns None on failure
        logger.warning("[cover_pipeline] upload raised %s; passthrough", exc)
        return _passthrough(original_image_url)
    if not raw_url:
        logger.warning(
            "[cover_pipeline] upload failed for %s; passthrough to original URL",
            platform_key,
        )
        return _passthrough(original_image_url)

    logger.info("[cover_pipeline] %s cover live at %s", platform_key, raw_url)
    return {"image_url": raw_url, "local_file_path": None}
