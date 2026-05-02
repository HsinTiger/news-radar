"""Unit tests for src/cover_pipeline.py (Phase 2 — symmetric URL flow).

Both FB and IG go through render → upload → URL. local_file_path
is preserved in the result shape but always None now.
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
    result = await prepare_publish_image(
        platform_key="fb",
        original_image_url=None,
        draft_id="draft_abc",
        title="t",
        topic_category="ai_model",
    )
    assert result == {"image_url": None, "local_file_path": None}


@pytest.mark.asyncio
async def test_threads_now_renders_cover_like_fb_ig(tmp_path: Path):
    """Phase 9.5 / 2026-05-02: Threads added to symmetric cover flow.

    Previously this test asserted Threads passed through with zero IO;
    after Hsin's brand-consistency call, Threads renders a cover like
    FB and IG do. If this test starts passing again with the old
    "no IO" expectation, someone broke the strategy reversion.
    """
    fake_local = tmp_path / "orig.jpg"
    fake_local.write_bytes(b"\xff\xd8\xff")
    fake_cover = tmp_path / "threads.png"
    fake_cover.write_bytes(b"\x89PNG")
    fake_raw_url = "https://raw.githubusercontent.com/HsinTiger/news-radar/cover-cdn/draft_abc_threads.png"

    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local))), \
         patch("src.cover_pipeline.render_cover", return_value=fake_cover) as m_render, \
         patch("src.cover_pipeline.upload_cover", return_value=fake_raw_url) as m_up:
        result = await prepare_publish_image(
            platform_key="threads",
            original_image_url="https://example.com/img.jpg",
            draft_id="draft_abc",
            title="t",
            topic_category="ai_model",
        )

    assert result == {"image_url": fake_raw_url, "local_file_path": None}
    cover_input = m_render.call_args.args[0]
    assert cover_input.brand_name == "smartmmmoney"
    assert m_render.call_args.args[1] == "threads"  # aspect key
    assert m_up.call_args.kwargs["platform_key"] == "threads"


@pytest.mark.asyncio
async def test_unknown_platform_passes_through():
    with patch("src.cover_pipeline.image_manager.download_image", new=AsyncMock()) as m:
        result = await prepare_publish_image(
            platform_key="bluesky",
            original_image_url="https://example.com/img.jpg",
            draft_id="draft_abc",
            title="t",
            topic_category=None,
        )
    assert result["image_url"] == "https://example.com/img.jpg"
    assert result["local_file_path"] is None
    m.assert_not_called()


@pytest.mark.asyncio
async def test_missing_draft_id_passes_through():
    """Without draft_id, uploader can't construct stable filename — fall back."""
    with patch("src.cover_pipeline.image_manager.download_image", new=AsyncMock()) as m:
        result = await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/img.jpg",
            draft_id=None,
            title="t",
            topic_category="ai_model",
        )
    assert result == {"image_url": "https://example.com/img.jpg", "local_file_path": None}
    m.assert_not_called()


# ---------------------------------------------------------------------------
# FB happy path: render → upload → public URL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fb_renders_uploads_and_returns_raw_url(tmp_path: Path):
    fake_local_orig = tmp_path / "orig.jpg"
    fake_local_orig.write_bytes(b"\xff\xd8\xff")
    fake_cover = tmp_path / "rendered_fb.png"
    fake_cover.write_bytes(b"\x89PNG")
    fake_raw_url = "https://raw.githubusercontent.com/HsinTiger/news-radar/cover-cdn/draft_abc_fb.png"

    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local_orig))), \
         patch("src.cover_pipeline.render_cover", return_value=fake_cover) as m_render, \
         patch("src.cover_pipeline.upload_cover", return_value=fake_raw_url) as m_up:
        result = await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/news.jpg",
            draft_id="draft_abc",
            title="OpenAI 把垂直 AI 鎖進企業圍牆",
            topic_category="ai_model",
        )

    assert result == {"image_url": fake_raw_url, "local_file_path": None}
    cover_input = m_render.call_args.args[0]
    assert cover_input.brand_name == BRAND_NAME_FOR_PLATFORM["fb"] == "主力爸爸我錯了"
    m_up.assert_called_once()
    upload_kwargs = m_up.call_args.kwargs
    assert upload_kwargs["draft_id"] == "draft_abc"
    assert upload_kwargs["platform_key"] == "fb"


@pytest.mark.asyncio
async def test_fb_falls_back_when_download_fails():
    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=None)), \
         patch("src.cover_pipeline.render_cover") as m_render, \
         patch("src.cover_pipeline.upload_cover") as m_up:
        result = await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/img.jpg",
            draft_id="draft_x",
            title="t",
            topic_category="ai_model",
        )
    assert result == {"image_url": "https://example.com/img.jpg", "local_file_path": None}
    m_render.assert_not_called()
    m_up.assert_not_called()


@pytest.mark.asyncio
async def test_fb_falls_back_when_render_raises(tmp_path: Path):
    fake_local = tmp_path / "orig.jpg"
    fake_local.write_bytes(b"\xff\xd8\xff")
    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local))), \
         patch("src.cover_pipeline.render_cover", side_effect=RuntimeError("font not found")), \
         patch("src.cover_pipeline.upload_cover") as m_up:
        result = await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/img.jpg",
            draft_id="draft_y",
            title="t",
            topic_category="ai_model",
        )
    assert result == {"image_url": "https://example.com/img.jpg", "local_file_path": None}
    m_up.assert_not_called()


@pytest.mark.asyncio
async def test_fb_falls_back_when_upload_returns_none(tmp_path: Path):
    """Upload soft-failed (auth, network) → publisher uses original URL."""
    fake_local = tmp_path / "orig.jpg"
    fake_local.write_bytes(b"\xff\xd8\xff")
    fake_cover = tmp_path / "rendered.png"
    fake_cover.write_bytes(b"\x89PNG")

    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local))), \
         patch("src.cover_pipeline.render_cover", return_value=fake_cover), \
         patch("src.cover_pipeline.upload_cover", return_value=None):
        result = await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/img.jpg",
            draft_id="draft_z",
            title="t",
            topic_category="ai_model",
        )
    assert result == {"image_url": "https://example.com/img.jpg", "local_file_path": None}


# ---------------------------------------------------------------------------
# IG: same shape as FB now, just different brand_name + aspect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ig_uses_smartmmmoney_brand_and_returns_url(tmp_path: Path):
    fake_local = tmp_path / "orig.jpg"
    fake_local.write_bytes(b"\xff\xd8\xff")
    fake_cover = tmp_path / "ig.png"
    fake_cover.write_bytes(b"\x89PNG")
    fake_raw_url = "https://raw.githubusercontent.com/HsinTiger/news-radar/cover-cdn/draft_abc_ig.png"

    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local))), \
         patch("src.cover_pipeline.render_cover", return_value=fake_cover) as m_render, \
         patch("src.cover_pipeline.upload_cover", return_value=fake_raw_url) as m_up:
        result = await prepare_publish_image(
            platform_key="ig",
            original_image_url="https://example.com/img.jpg",
            draft_id="draft_abc",
            title="t",
            topic_category="ai_model",
        )

    assert result == {"image_url": fake_raw_url, "local_file_path": None}
    cover_input = m_render.call_args.args[0]
    assert cover_input.brand_name == "smartmmmoney"
    assert m_up.call_args.kwargs["platform_key"] == "ig"


# ---------------------------------------------------------------------------
# Topic / title fallbacks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_topic_category_falls_back_to_macro(tmp_path: Path):
    fake_local = tmp_path / "orig.jpg"
    fake_local.write_bytes(b"\xff\xd8\xff")
    fake_cover = tmp_path / "out.png"
    fake_cover.write_bytes(b"\x89PNG")
    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local))), \
         patch("src.cover_pipeline.render_cover", return_value=fake_cover) as m_render, \
         patch("src.cover_pipeline.upload_cover", return_value="http://x"):
        await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/img.jpg",
            draft_id="draft_a",
            title="t",
            topic_category=None,
        )
    cover_input = m_render.call_args.args[0]
    assert cover_input.topic_category == "macro"


@pytest.mark.asyncio
async def test_missing_title_uses_placeholder(tmp_path: Path):
    fake_local = tmp_path / "orig.jpg"
    fake_local.write_bytes(b"\xff\xd8\xff")
    fake_cover = tmp_path / "out.png"
    fake_cover.write_bytes(b"\x89PNG")
    with patch("src.cover_pipeline.image_manager.download_image",
               new=AsyncMock(return_value=str(fake_local))), \
         patch("src.cover_pipeline.render_cover", return_value=fake_cover) as m_render, \
         patch("src.cover_pipeline.upload_cover", return_value="http://x"):
        await prepare_publish_image(
            platform_key="fb",
            original_image_url="https://example.com/img.jpg",
            draft_id="draft_a",
            title="",
            topic_category="ai_model",
        )
    cover_input = m_render.call_args.args[0]
    assert cover_input.title  # non-empty placeholder
