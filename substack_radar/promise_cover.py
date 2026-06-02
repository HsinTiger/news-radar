"""
News Radar · Substack "promise thumbnail" cover (2026-06-02)
===========================================================
A flat typographic POSTER cover (no photo): big bold curiosity-hook title that
stays readable as a feed/email thumbnail, with a per-category color palette.

Replaces the old blurred-photo + navy-overlay Substack cover. Design + palettes
came from a 3-agent research pass (design-systems / color-psychology / a11y):
- CONSTANT (= the brand): layout, near-black ink, kicker + thin accent rule.
- VARIES by category: background tint (one light family + one dark AI card) and
  the accent hue (kicker/rule). Accent is the primary category signal.
- Every hero/bg pair is ≥15:1 contrast (thumbnail-safe). Flat PNG so email
  dark-mode can't repaint it. TW markets never get green (紅漲綠跌 trap).
"""
from __future__ import annotations

from pathlib import Path
from datetime import date
from typing import Dict, Tuple

# Reuse the brand fonts already vendored for the social cover renderer.
from src.cover_renderer import FONT_TITLE_PATH, FONT_SUBTITLE_PATH, FONT_BRAND_PATH

W, H = 1456, 816
PAD = 92
KICKER = "主力爸爸我錯了"
SLOGAN = "每天 3 分鐘 · 拿走一個被市場藏起來的共識"

# Per-category palettes (bg / hero ink / accent / subtitle). See module docstring.
_PALETTES: Dict[str, Dict[str, str]] = {
    "market":  {"bg": "#F2EEE5", "ink": "#141414", "acc": "#C84A32", "sub": "#5A5247"},
    "ai":      {"bg": "#14171C", "ink": "#F2EEE5", "acc": "#E0653F", "sub": "#A8AEB8"},
    "product": {"bg": "#E8EEF0", "ink": "#141414", "acc": "#1C6378", "sub": "#566069"},
    "policy":  {"bg": "#EBE3CF", "ink": "#141414", "acc": "#7A4A12", "sub": "#6B5E45"},
    "opinion": {"bg": "#EAE7E0", "ink": "#141414", "acc": "#3F4A7A", "sub": "#5A5A52"},
}

# Pipeline topic_category → palette bucket. Unknown → "opinion" (the neutral default).
_TOPIC_TO_BUCKET: Dict[str, str] = {
    "us_stocks": "market", "tw_stocks": "market", "earnings": "market",
    "ai_model": "ai", "ai_agent": "ai", "ai_application": "ai",
    "tech_product_launch": "product",
    "policy_geopolitics": "policy", "supply_chain": "policy",
    "other": "opinion",
}


def _hx(s: str) -> Tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def palette_for(topic_category: str) -> Dict[str, str]:
    return _PALETTES[_TOPIC_TO_BUCKET.get((topic_category or "other").strip(), "opinion")]


def _font(path: Path, pt: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(path), pt)


def _wrap(draw, text: str, font, max_w: int):
    """Greedy wrap for mixed CJK + Latin: CJK chars break per-char (no spaces),
    but a run of ASCII letters/digits (e.g. "Opendoor", "ChatGPT", "AM5", "18%")
    stays together as one unbreakable token so brand/number words don't split."""
    import re

    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-%]*|.", text)
    lines, cur = [], ""
    for tok in tokens:
        if not cur or draw.textlength(cur + tok, font=font) <= max_w:
            cur += tok
        else:
            lines.append(cur)
            cur = tok
    if cur:
        lines.append(cur)
    return lines


def render_promise_cover(
    *,
    title: str,
    subtitle: str,
    topic_category: str,
    output_dir: Path,
) -> Path:
    """Render the typographic poster cover to ``output_dir/cover.png``; return it."""
    from PIL import Image, ImageDraw

    pal = palette_for(topic_category)
    bg, ink, acc, sub = (_hx(pal[k]) for k in ("bg", "ink", "acc", "sub"))
    max_w = W - 2 * PAD

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    # Kicker (publication name) + thin accent rule — the brand thread.
    d.text((PAD, 68), KICKER, font=_font(FONT_TITLE_PATH, 32), fill=acc)
    d.line((PAD, 120, PAD + 118, 120), fill=acc, width=6)

    # Hero title: auto-fit the largest size that fits ≤3 lines within the top half.
    title_font = _font(FONT_TITLE_PATH, 80)
    lines = _wrap(d, title, title_font, max_w)
    line_h = int(80 * 1.18)
    for pt in range(148, 68, -6):
        f = _font(FONT_TITLE_PATH, pt)
        ls = _wrap(d, title, f, max_w)
        lh = int(pt * 1.18)
        if len(ls) <= 3 and len(ls) * lh <= int(H * 0.50):
            title_font, lines, line_h = f, ls, lh
            break
    y = 180
    for ln in lines:
        d.text((PAD, y), ln, font=title_font, fill=ink)
        y += line_h

    # Subtitle: anchored to a fixed bottom band (≤2 lines) so spacing is consistent.
    if subtitle:
        sf = _font(FONT_SUBTITLE_PATH, 38)
        sy = H - 215
        for ln in _wrap(d, subtitle, sf, max_w)[:2]:
            d.text((PAD, sy), ln, font=sf, fill=sub)
            sy += 52

    # Bottom hairline + slogan.
    d.line((PAD, H - 72, W - PAD, H - 72), fill=ink, width=1)
    d.text((PAD, H - 56), SLOGAN, font=_font(FONT_BRAND_PATH, 27), fill=sub)

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "cover.png"
    img.save(out, "PNG", optimize=True)
    return out
