"""Unit tests for src/cover_pipeline.py.

The pipeline coordinates two side-effecting collaborators
(``image_manager.download_image`` for network IO and
``cover_renderer.render_cover`` for file IO). We mock both and verify
the orchestration logic — which platform gets a cover, when we fall
back to the original URL, and how the publish-API kwargs are shaped.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.cover_pipeline import (
    BRAND_NAME_FOR_PLATFORM,
    prepare_publish_image,
)


# ---------------------------------------------------------------------------
# Pass-through cases (no rendering should happen)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_image_url_returns_both_none():
    """No source URL → publisher gets nothing to upload."""
    result = await prepare_publish_image(
        platform_key="fb",
        original_image_url=None,
        title="t",
        topic_category="ai_model",
    )
    assert result == {"image_url": None, "local_file_path": None}


@pytest.mark.asyncio
async def test_threads_passes_through():
    """Threads strategy is text-first — never rendered, never downloaded."""
    with patch("src.cover_pipeline.image_manager.download_image", new=AsyncMock()) as m:
        result = await prepare_publish_image(
            platform_key="threads",
            original_image_url="https://example.com/img.jpg",
            title="t",
            topic_category="ai_model",
        )
    assert result == {"image_url": "https://example.com/img.jpg", "local_file_path": None}
    m.assert_not_called()  # zero IO for Threads — important


@pytest.mark.asyncio
async def test_unknown_platform_passes_through():
    """A typo in platform_key shouldn't crash the publisher."""
    with patch("src.cover_pipeline.image_manager.download_image", new=AsyncMock()) as m:
        result = await prepare_publish_image(
            platform_key="bluesky",  # not yet supported
            original_image_url="https://example.com/img.jpg",
            title="t",
            topic_category=None,
        )
    assert result["image_url"] == "https://example.com/img.jpg"
    assert result["local_file_path"] is None
    m.assert_not_called()


# ---------------------------------------------------------------------------
# FB happy path: cover rendered → bytes upload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fb_renders_cover_and_returns_local_path(tmp_path: Path):
    """FB should download the original, render, and return local_file_path."""
    fake_local_orig = tmp_path / "orig.jpg"
    fake_local_orig.write_bytes(b"\xff\xd8\xff")  # JPEG magic
    fake_cover = tmp_path / "rendered_fb_1x1.png"
    fake_cover.write_bytes(b"\x89PNG")

    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local_orig))) as m_dl, \
         patch("src.cover_pipeline.render_cover",
               return_value=fake_cover) as m_render:
        result = await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/img.jpg",
            title="OpenAI 把垂直 AI 鎖進企業圍牆",
            topic_category="ai_model",
        )

    assert result == {"image_url": None, "local_file_path": str(fake_cover)}
    m_dl.assert_awaited_once()
    m_render.assert_called_once()
    # Sanity: the brand_name passed to render_cover must be FB's brand.
    cover_input = m_render.call_args.args[0]
    assert cover_input.brand_name == BRAND_NAME_FOR_PLATFORM["fb"] == "主力爸爸我錯了"


@pytest.mark.asyncio
async def test_fb_falls_back_when_download_fails(tmp_path: Path):
    """If download_image returns None, FB should pass the original URL."""
    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=None)), \
         patch("src.cover_pipeline.render_cover") as m_render:
        result = await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/img.jpg",
            title="t",
            topic_category="ai_model",
        )
    assert result == {"image_url": "https://example.com/img.jpg", "local_file_path": None}
    m_render.assert_not_called()  # no point rendering with no source


@pytest.mark.asyncio
async def test_fb_falls_back_when_render_raises(tmp_path: Path):
    """If render_cover raises, publish must still succeed via Plan A."""
    fake_local = tmp_path / "orig.jpg"
    fake_local.write_bytes(b"\xff\xd8\xff")

    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local))), \
         patch("src.cover_pipeline.render_cover",
               side_effect=RuntimeError("font not found")):
        result = await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/img.jpg",
            title="t",
            topic_category="ai_model",
        )
    assert result == {"image_url": "https://example.com/img.jpg", "local_file_path": None}


# ---------------------------------------------------------------------------
# IG: cover rendered locally, but Phase 1 publishes original URL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ig_renders_but_passes_original_url(tmp_path: Path):
    """IG cover is saved (Phase-2 hosting prep) but image_url stays original."""
    fake_local_orig = tmp_path / "orig.jpg"
    fake_local_orig.write_bytes(b"\xff\xd8\xff")
    fake_cover = tmp_path / "rendered_ig_4x5.png"
    fake_cover.write_bytes(b"\x89PNG")

    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local_orig))), \
         patch("src.cover_pipeline.render_cover",
               return_value=fake_cover) as m_render:
        result = await prepare_publish_image(
            platform_key="ig",
            original_image_url="https://example.com/img.jpg",
            title="t",
            topic_category="ai_model",
        )
    # Cover rendered (Phase 2 will use this) but publisher still uses original URL.
    m_render.assert_called_once()
    assert result == {"image_url": "https://example.com/img.jpg", "local_file_path": None}
    cover_input = m_render.call_args.args[0]
    assert cover_input.brand_name == BRAND_NAME_FOR_PLATFORM["ig"] == "smartmmmoney"


# ---------------------------------------------------------------------------
# Topic category fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_topic_category_falls_back_to_macro(tmp_path: Path):
    """Legacy rows lacking topic_category render with gray ('macro') chip."""
    fake_local = tmp_path / "orig.jpg"
    fake_local.write_bytes(b"\xff\xd8\xff")
    fake_cover = tmp_path / "out.png"
    fake_cover.write_bytes(b"\x89PNG")

    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local))), \
         patch("src.cover_pipeline.render_cover",
               return_value=fake_cover) as m_render:
        await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/img.jpg",
            title="t",
            topic_category=None,  # legacy
        )

    cover_input = m_render.call_args.args[0]
    assert cover_input.topic_category == "macro"


@pytest.mark.asyncio
async def test_missing_title_uses_placeholder(tmp_path: Path):
    """Empty title shouldn't crash the renderer — fall back to placeholder."""
    fake_local = tmp_path / "orig.jpg"
    fake_local.write_bytes(b"\xff\xd8\xff")
    fake_cover = tmp_path / "out.png"
    fake_cover.write_bytes(b"\x89PNG")

    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local))), \
         patch("src.cover_pipeline.render_cover",
               return_value=fake_cover) as m_render:
        await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/img.jpg",
            title="",
            topic_category="ai_model",
        )

    cover_input = m_render.call_args.args[0]
    assert cover_input.title  # non-empty
