"""Unit tests for src/cover_renderer.py.

Covers:
  * compute_overlay_alpha thresholds (bright / dark / mid)
  * _crop_to_aspect cover-mode behavior on wide and tall sources
  * _wrap_chinese_title line-break + ellipsis fallback
  * render_cover smoke test: writes a PNG of the right size, no crashes,
    even with the default-font fallback (no CJK glyphs in repo's CI env).
  * render_cover_pair returns both ig and fb outputs.

No live network. No font files required (uses PIL default fallback).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from src.cover_renderer import (
    ALPHA_BRIGHT,
    ALPHA_DARK,
    ALPHA_DEFAULT,
    CoverInput,
    SPECS,
    _crop_to_aspect,
    _wrap_chinese_title,
    compute_overlay_alpha,
    render_cover,
    render_cover_pair,
)


# ---------------------------------------------------------------------------
# compute_overlay_alpha
# ---------------------------------------------------------------------------

def test_overlay_alpha_bright_image():
    """Mostly white → alpha = bright threshold (heavier overlay)."""
    img = Image.new("RGB", (200, 200), (240, 240, 240))
    assert compute_overlay_alpha(img) == ALPHA_BRIGHT


def test_overlay_alpha_dark_image():
    """Mostly black → alpha = dark threshold (lighter overlay)."""
    img = Image.new("RGB", (200, 200), (10, 10, 10))
    assert compute_overlay_alpha(img) == ALPHA_DARK


def test_overlay_alpha_mid_image():
    """Mid-luminance → default alpha 0.65."""
    img = Image.new("RGB", (200, 200), (128, 128, 128))
    assert compute_overlay_alpha(img) == ALPHA_DEFAULT


# ---------------------------------------------------------------------------
# _crop_to_aspect
# ---------------------------------------------------------------------------

def test_crop_wide_source_to_4x5():
    """1920×1080 (16:9) cropped to 1080×1350 (4:5) — sides cut, height keeps."""
    src = Image.new("RGB", (1920, 1080), "white")
    out = _crop_to_aspect(src, (1080, 1350))
    assert out.size == (1080, 1350)


def test_crop_tall_source_to_1x1():
    """1080×1920 cropped to 1080×1080 — top/bottom cut."""
    src = Image.new("RGB", (1080, 1920), "white")
    out = _crop_to_aspect(src, (1080, 1080))
    assert out.size == (1080, 1080)


def test_crop_already_correct_aspect():
    """Same aspect → just resize, no crop info lost (output size matches)."""
    src = Image.new("RGB", (2160, 2700), "white")  # 4:5 already
    out = _crop_to_aspect(src, (1080, 1350))
    assert out.size == (1080, 1350)


# ---------------------------------------------------------------------------
# _wrap_chinese_title
# ---------------------------------------------------------------------------

def _draw_for_wrap_test():
    """Produce a draw context bound to a throwaway image."""
    img = Image.new("RGBA", (1080, 1350), (0, 0, 0, 0))
    return ImageDraw.Draw(img)


def test_wrap_short_title_single_line():
    draw = _draw_for_wrap_test()
    font = ImageFont.load_default()
    lines = _wrap_chinese_title(draw, "AI 來了", font, max_width=1000)
    assert lines == ["AI 來了"]


def test_wrap_empty_title_returns_empty():
    draw = _draw_for_wrap_test()
    font = ImageFont.load_default()
    assert _wrap_chinese_title(draw, "", font, max_width=1000) == []


def test_wrap_respects_max_lines():
    """Very long title with very narrow width → cap at max_lines, last line ellipsized."""
    draw = _draw_for_wrap_test()
    font = ImageFont.load_default()
    long = "abcdefghijklmnopqrstuvwxyz" * 4
    lines = _wrap_chinese_title(draw, long, font, max_width=80, max_lines=3)
    assert len(lines) <= 3
    # Last line should end with ellipsis when truncation happened
    assert lines[-1].endswith("…") or sum(len(line) for line in lines) >= len(long)


# ---------------------------------------------------------------------------
# render_cover smoke tests
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_news_image(tmp_path: Path) -> Path:
    """Write a 1600×900 placeholder image to tmp_path."""
    p = tmp_path / "fake_news_image.jpg"
    img = Image.new("RGB", (1600, 900), (40, 80, 120))
    img.save(p, "JPEG", quality=85)
    return p


@pytest.fixture
def cover_input(fake_news_image: Path) -> CoverInput:
    return CoverInput(
        image_path=fake_news_image,
        title="OpenAI 把垂直 AI 鎖進企業圍牆",
        subtitle="Rosalind 的二度被剝奪",
        topic_category="ai_model",
        brand_name="smartmmmoney",
        date_str="2026/05/01",
    )


def test_render_ig_writes_correct_size(cover_input: CoverInput, tmp_path: Path):
    out = render_cover(cover_input, "ig", output_dir=tmp_path)
    assert out.exists()
    with Image.open(out) as im:
        assert im.size == SPECS["ig"]["size"] == (1080, 1350)


def test_render_fb_writes_correct_size(cover_input: CoverInput, tmp_path: Path):
    out = render_cover(cover_input, "fb", output_dir=tmp_path)
    assert out.exists()
    with Image.open(out) as im:
        assert im.size == SPECS["fb"]["size"] == (1080, 1080)


def test_render_pair_produces_both(cover_input: CoverInput, tmp_path: Path):
    paths = render_cover_pair(cover_input, output_dir=tmp_path)
    assert set(paths) == {"ig", "fb"}
    assert all(p.exists() for p in paths.values())
    # No Threads — explicit non-output, per brand_visual.md
    assert "threads" not in paths


def test_render_handles_missing_subtitle(fake_news_image: Path, tmp_path: Path):
    """subtitle=None must NOT crash — drawing skips silently."""
    inp = CoverInput(
        image_path=fake_news_image,
        title="DeepMind 30 人突擊小組",
        subtitle=None,
        topic_category="ai_model",
        brand_name="smartmmmoney",
        date_str="2026/05/01",
    )
    out = render_cover(inp, "ig", output_dir=tmp_path)
    assert out.exists()


def test_render_handles_unknown_topic_category(fake_news_image: Path, tmp_path: Path):
    """Unknown topic → falls back to gray chip + raw category as label, no crash."""
    inp = CoverInput(
        image_path=fake_news_image,
        title="未知主題的測試標題",
        subtitle=None,
        topic_category="totally_made_up_category",
        brand_name="smartmmmoney",
        date_str="2026/05/01",
    )
    out = render_cover(inp, "ig", output_dir=tmp_path)
    assert out.exists()


def test_render_invalid_aspect_raises(cover_input: CoverInput, tmp_path: Path):
    with pytest.raises(ValueError):
        render_cover(cover_input, "story", output_dir=tmp_path)  # type: ignore[arg-type]


def test_render_filename_includes_suffix(cover_input: CoverInput, tmp_path: Path):
    """Output filename encodes which aspect it is — useful for debugging."""
    ig = render_cover(cover_input, "ig", output_dir=tmp_path)
    fb = render_cover(cover_input, "fb", output_dir=tmp_path)
    assert "ig_4x5" in ig.name
    assert "fb_1x1" in fb.name


def test_render_idempotent_byte_for_byte(cover_input: CoverInput, tmp_path: Path):
    """Same input → same output bytes. Regression guard against
    nondeterministic font kerning or stray timestamp metadata."""
    out1 = render_cover(cover_input, "ig", output_dir=tmp_path / "a")
    out2 = render_cover(cover_input, "ig", output_dir=tmp_path / "b")
    assert out1.read_bytes() == out2.read_bytes()
