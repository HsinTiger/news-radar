"""Unit tests for src.image_prep。

測試策略：
- 純函式（parse_dimensions / rewriters / _choose_target_size）直接叫
- 需要網路的 probe_image / prepare_image_for_ig 用 httpx.MockTransport 餵假回應
  （不碰真實網路）
"""
from __future__ import annotations

import struct

import httpx
import pytest

from src.image_prep import (
    IG_FILESIZE_MAX,
    IG_RATIO_MAX,
    IG_RATIO_MIN,
    PrepResult,
    _choose_target_size,
    _rewrite_decrypt,
    _rewrite_wp_content,
    parse_dimensions,
    prepare_image_for_ig,
    probe_image,
)


# ─── 假圖頭製造機 ──────────────────────────────────────────────────────────────

def make_png_head(width: int, height: int) -> bytes:
    """產生合法的 PNG header 前 24 bytes。"""
    return (
        b"\x89PNG\r\n\x1a\n"                      # signature
        + struct.pack(">I", 13)                    # IHDR chunk length
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"                  # rest of IHDR
    )


def make_jpeg_head(width: int, height: int) -> bytes:
    """產生含 SOI + APP0 + SOF0 的 JPEG header。"""
    soi = b"\xff\xd8"
    app0 = (
        b"\xff\xe0"
        + struct.pack(">H", 16)
        + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    )
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    return soi + app0 + sof0


# ─── parse_dimensions ─────────────────────────────────────────────────────────

def test_parse_dimensions_png():
    assert parse_dimensions(make_png_head(1024, 512)) == (1024, 512)


def test_parse_dimensions_jpeg():
    assert parse_dimensions(make_jpeg_head(800, 600)) == (800, 600)


def test_parse_dimensions_unknown_returns_none():
    assert parse_dimensions(b"not an image file") is None


def test_parse_dimensions_truncated_png():
    # 只給前 10 bytes（簽名完整但 IHDR 不完整）
    assert parse_dimensions(b"\x89PNG\r\n\x1a\n\x00\x00") is None


# ─── rewriters ────────────────────────────────────────────────────────────────

def test_rewrite_decrypt_hit():
    url = "https://cdn.decrypt.co/resize/1024/height/512/wp-content/uploads/2026/04/foo.png"
    out = _rewrite_decrypt(url, 800, 500)
    assert out == "https://cdn.decrypt.co/resize/800/height/500/wp-content/uploads/2026/04/foo.png"


def test_rewrite_decrypt_miss_wrong_host():
    assert _rewrite_decrypt("https://example.com/img.png", 800, 500) is None


def test_rewrite_decrypt_miss_unexpected_path():
    assert _rewrite_decrypt("https://cdn.decrypt.co/img.png", 800, 500) is None


def test_rewrite_wp_content_hit_no_existing_query():
    url = "https://techcrunch.com/wp-content/uploads/2026/04/hero.jpg"
    out = _rewrite_wp_content(url, 800, 500)
    assert "w=800" in out
    assert "h=500" in out
    assert "crop=1" in out


def test_rewrite_wp_content_merges_existing_query():
    url = "https://example.com/wp-content/uploads/x.jpg?ssl=1"
    out = _rewrite_wp_content(url, 800, 500)
    assert "ssl=1" in out
    assert "w=800" in out


def test_rewrite_wp_content_miss():
    assert _rewrite_wp_content("https://example.com/static/x.jpg", 800, 500) is None


# ─── _choose_target_size ──────────────────────────────────────────────────────

def test_choose_target_size_normal():
    w, h = _choose_target_size(1024, 512)
    assert w == 1024
    assert 1.5 < w / h < 1.7  # 目標落在安全中心 1.6


def test_choose_target_size_caps_oversized_width():
    w, h = _choose_target_size(4000, 2000)
    assert w == 1600  # width cap
    assert 1.5 < w / h < 1.7


# ─── probe_image（mocked network） ────────────────────────────────────────────

def _make_mock_transport(
    url_to_response: dict,
) -> httpx.MockTransport:
    """url_to_response: {url_str: (status_code, bytes, headers)}"""

    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        if key not in url_to_response:
            return httpx.Response(404)
        status, content, headers = url_to_response[key]
        return httpx.Response(status, content=content, headers=headers or {})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_probe_image_happy_path():
    url = "https://example.com/good.png"
    head = make_png_head(1024, 512)
    transport = _make_mock_transport({
        url: (206, head, {"content-range": "bytes 0-65535/500000"})
    })
    async with httpx.AsyncClient(transport=transport) as c:
        result = await probe_image(url, client=c)
    assert result == (1024, 512, 500000)


@pytest.mark.asyncio
async def test_probe_image_fallback_content_length():
    url = "https://example.com/noc.png"
    head = make_png_head(800, 600)
    transport = _make_mock_transport({
        url: (200, head, {"content-length": "123456"})
    })
    async with httpx.AsyncClient(transport=transport) as c:
        result = await probe_image(url, client=c)
    assert result == (800, 600, 123456)


@pytest.mark.asyncio
async def test_probe_image_404_returns_none():
    transport = _make_mock_transport({})
    async with httpx.AsyncClient(transport=transport) as c:
        result = await probe_image("https://example.com/missing.png", client=c)
    assert result is None


@pytest.mark.asyncio
async def test_probe_image_garbage_bytes_returns_none():
    url = "https://example.com/bad.png"
    transport = _make_mock_transport({url: (200, b"not an image", {})})
    async with httpx.AsyncClient(transport=transport) as c:
        result = await probe_image(url, client=c)
    assert result is None


# ─── prepare_image_for_ig（end-to-end with mock） ─────────────────────────────

@pytest.mark.asyncio
async def test_prepare_ok_when_ratio_already_in_range():
    """1.5:1 圖直接過——不需要 rewrite。"""
    url = "https://example.com/already_ok.png"
    head = make_png_head(1500, 1000)  # ratio 1.5
    transport = _make_mock_transport({
        url: (206, head, {"content-range": "bytes 0-65535/2000000"})
    })
    async with httpx.AsyncClient(transport=transport) as c:
        result = await prepare_image_for_ig(url, client=c)
    assert result.action == "ok"
    assert result.url == url
    assert result.is_usable is True


@pytest.mark.asyncio
async def test_prepare_rewrites_decrypt_2to1_banner():
    """Decrypt 1024×512 = 2.0 出界 → rewrite 成 1024×{target}。"""
    orig = "https://cdn.decrypt.co/resize/1024/height/512/wp-content/uploads/2026/04/foo.png"
    orig_head = make_png_head(1024, 512)
    # _choose_target_size(1024, 512) → (1024, 640)
    rewritten_url = "https://cdn.decrypt.co/resize/1024/height/640/wp-content/uploads/2026/04/foo.png"
    rewritten_head = make_png_head(1024, 640)  # ratio 1.6
    transport = _make_mock_transport({
        orig: (206, orig_head, {"content-range": "bytes 0-65535/300000"}),
        rewritten_url: (206, rewritten_head, {"content-range": "bytes 0-65535/280000"}),
    })
    async with httpx.AsyncClient(transport=transport) as c:
        result = await prepare_image_for_ig(orig, client=c)
    assert result.action == "rewrote"
    assert result.url == rewritten_url
    assert result.ratio_before == pytest.approx(2.0)
    assert IG_RATIO_MIN <= result.ratio_after <= IG_RATIO_MAX
    assert result.is_usable is True


@pytest.mark.asyncio
async def test_prepare_fails_when_no_rewriter_matches():
    """Host 不認得 + ratio 出界 → failed_no_rewriter，is_usable=False。"""
    url = "https://someobscurehost.com/static/banner.png"
    head = make_png_head(1024, 512)  # ratio 2.0
    transport = _make_mock_transport({
        url: (206, head, {"content-range": "bytes 0-65535/200000"})
    })
    async with httpx.AsyncClient(transport=transport) as c:
        result = await prepare_image_for_ig(url, client=c)
    assert result.action == "failed_no_rewriter"
    assert result.url is None
    assert result.is_usable is False
    assert "ratio" in result.reason


@pytest.mark.asyncio
async def test_prepare_skipped_when_probe_fails_returns_usable():
    """探頭失敗（404） → skipped_probe_failed，但原 URL 照樣回傳讓 caller 試
    （保留 pre-module 行為，不因為探頭失敗就擋下發文）。"""
    url = "https://example.com/unreachable.png"
    transport = _make_mock_transport({})  # 全都回 404
    async with httpx.AsyncClient(transport=transport) as c:
        result = await prepare_image_for_ig(url, client=c)
    assert result.action == "skipped_probe_failed"
    assert result.url == url
    assert result.is_usable is True  # 讓 Meta 自己判斷


@pytest.mark.asyncio
async def test_prepare_fails_when_filesize_exceeds_cap():
    """ratio 合規但 filesize 超標 → 嘗試 rewrite；找不到 rewriter 則 failed。"""
    url = "https://example.com/huge.png"
    head = make_png_head(1500, 1000)  # ratio 1.5 OK
    transport = _make_mock_transport({
        url: (206, head, {"content-range": f"bytes 0-65535/{IG_FILESIZE_MAX + 1}"})
    })
    async with httpx.AsyncClient(transport=transport) as c:
        result = await prepare_image_for_ig(url, client=c)
    assert result.action == "failed_no_rewriter"
    assert "size" in result.reason


# ─── PrepResult.log_line smoke test ───────────────────────────────────────────

def test_log_line_ok():
    r = PrepResult(url="https://x", action="ok", ratio_before=1.5, filesize_before=100_000)
    line = r.log_line()
    assert "action=ok" in line
    assert "ratio=1.500" in line
    assert "KB" in line


def test_log_line_rewrote():
    r = PrepResult(
        url="https://x",
        action="rewrote",
        ratio_before=2.0,
        ratio_after=1.6,
        filesize_before=100_000,
    )
    line = r.log_line()
    assert "ratio=2.000" in line
    assert "→1.600" in line
