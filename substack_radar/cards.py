"""
News Radar · Social carousel CARD renderer (2026-06-02)
=======================================================
Turns one article into a 2–4 card swipeable carousel for IG / FB / Threads, so
the gist is consumable from the cards alone (no click needed). Same flat
typographic-poster look + per-category palette as the Substack cover
(``promise_cover``); this just adds body-card types and multi-aspect sizing.

Card content is a list of dicts (built upstream by the composer / a distill step):
    [{"type": "cover",    "title": "...", "subtitle": "..."},
     {"type": "insight",  "statement": "...", "support": "..."},
     {"type": "takeaway", "points": ["...", "..."], "cta": "追蹤 主力爸爸我錯了"}]
2–4 cards; cover is always first. render_cards() returns the PNG paths in order.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from src.cover_renderer import FONT_TITLE_PATH, FONT_SUBTITLE_PATH, FONT_BRAND_PATH
from substack_radar.promise_cover import palette_for, _hx, KICKER, SLOGAN

# aspect → (W, H). IG/Threads portrait 4:5, FB square 1:1.
ASPECTS: Dict[str, tuple] = {
    "ig": (1080, 1350),
    "threads": (1080, 1350),
    "fb": (1080, 1080),
}


def _font(path, pt):
    from PIL import ImageFont
    return ImageFont.truetype(str(path), int(pt))


def _wrap(draw, text: str, font, max_w: int):
    import re
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-%$]*|.", text or "")
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


def _frame(W, H, pal, idx, total):
    """Shared card chrome: bg, kicker + rule (brand), card index. Returns (img, draw, pad)."""
    from PIL import Image, ImageDraw
    bg, ink, acc, sub = (_hx(pal[k]) for k in ("bg", "ink", "acc", "sub"))
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    pad = round(W * 0.063)
    d.text((pad, round(H * 0.05)), KICKER, font=_font(FONT_TITLE_PATH, W * 0.028), fill=acc)
    ry = round(H * 0.05) + round(W * 0.05)
    d.line((pad, ry, pad + round(W * 0.105), ry), fill=acc, width=6)
    if total > 1:
        tag = f"{idx}/{total}"
        tf = _font(FONT_BRAND_PATH, W * 0.028)
        d.text((W - pad - d.textlength(tag, font=tf), round(H * 0.05)), tag, font=tf, fill=sub)
    return img, d, pad


def _draw_cover(W, H, pal, card, idx, total):
    img, d, pad = _frame(W, H, pal, idx, total)
    ink, acc, sub = (_hx(pal[k]) for k in ("ink", "acc", "sub"))
    mw = W - 2 * pad
    title = card.get("title", "")
    # auto-fit hero
    f = _font(FONT_TITLE_PATH, W * 0.10)
    ls = _wrap(d, title, f, mw)
    lh = int(W * 0.10 * 1.18)
    for ptr in range(118, 56, -4):
        pt = W * ptr / 1000
        ff = _font(FONT_TITLE_PATH, pt)
        lls = _wrap(d, title, ff, mw)
        llh = int(pt * 1.18)
        if len(lls) <= 5 and len(lls) * llh <= int(H * 0.52):
            f, ls, lh = ff, lls, llh
            break
    y = round(H * 0.17)
    for ln in ls:
        d.text((pad, y), ln, font=f, fill=ink)
        y += lh
    sub_txt = card.get("subtitle", "")
    if sub_txt:
        sf = _font(FONT_SUBTITLE_PATH, W * 0.032)
        sy = H - round(H * 0.20)
        for ln in _wrap(d, sub_txt, sf, mw)[:3]:
            d.text((pad, sy), ln, font=sf, fill=sub)
            sy += round(W * 0.046)
    by = H - round(H * 0.085)
    d.line((pad, by, W - pad, by), fill=ink, width=1)
    d.text((pad, by + round(H * 0.016)), SLOGAN, font=_font(FONT_BRAND_PATH, W * 0.024), fill=sub)
    return img


def _draw_insight(W, H, pal, card, idx, total):
    img, d, pad = _frame(W, H, pal, idx, total)
    ink, acc = _hx(pal["ink"]), _hx(pal["acc"])
    mw = W - 2 * pad
    d.text((pad, round(H * 0.15)), card.get("label", "核心洞察"), font=_font(FONT_TITLE_PATH, W * 0.038), fill=acc)
    stmt = card.get("statement", "")
    f = _font(FONT_TITLE_PATH, W * 0.082)
    for ptr in range(90, 50, -4):
        pt = W * ptr / 1000
        ff = _font(FONT_TITLE_PATH, pt)
        lls = _wrap(d, stmt, ff, mw)
        if len(lls) <= 5:
            f = ff
            break
    ls = _wrap(d, stmt, f, mw)
    lh = int(f.size * 1.2)
    y = round(H * 0.24)
    for ln in ls:
        d.text((pad, y), ln, font=f, fill=ink)
        y += lh
    support = card.get("support", "")
    if support:
        sf = _font(FONT_SUBTITLE_PATH, W * 0.034)
        sy = y + round(H * 0.03)
        for ln in _wrap(d, support, sf, mw):
            d.text((pad, sy), ln, font=sf, fill=ink)
            sy += round(W * 0.05)
    return img


def _draw_takeaway(W, H, pal, card, idx, total):
    img, d, pad = _frame(W, H, pal, idx, total)
    ink, acc, sub = (_hx(pal[k]) for k in ("ink", "acc", "sub"))
    d.text((pad, round(H * 0.15)), card.get("label", "帶走的判斷"), font=_font(FONT_TITLE_PATH, W * 0.038), fill=acc)
    pts = card.get("points", [])[:3]
    nf = _font(FONT_TITLE_PATH, W * 0.05)
    bf = _font(FONT_SUBTITLE_PATH, W * 0.034)
    mw = W - 2 * pad - round(W * 0.07)
    y = round(H * 0.24)
    for i, p in enumerate(pts, 1):
        d.text((pad, y), str(i), font=nf, fill=acc)
        yy = y
        for ln in _wrap(d, p, bf, mw):
            d.text((pad + round(W * 0.07), yy), ln, font=bf, fill=ink)
            yy += round(W * 0.048)
        y = yy + round(H * 0.026)
    cta = card.get("cta", "追蹤 主力爸爸我錯了")
    cy = H - round(H * 0.135)
    d.line((pad, cy, W - pad, cy), fill=acc, width=2)
    d.text((pad, cy + round(H * 0.016)), cta, font=_font(FONT_TITLE_PATH, W * 0.038), fill=ink)
    d.text((pad, cy + round(H * 0.062)), SLOGAN, font=_font(FONT_BRAND_PATH, W * 0.026), fill=sub)
    return img


_RENDERERS = {"cover": _draw_cover, "insight": _draw_insight, "takeaway": _draw_takeaway}


def render_cards(
    *,
    cards: List[Dict],
    topic_category: str,
    aspect: str,
    output_dir: Path,
) -> List[Path]:
    """Render a 2–4 card carousel → ordered list of PNG paths. aspect ∈ ASPECTS."""
    if aspect not in ASPECTS:
        raise ValueError(f"aspect must be one of {list(ASPECTS)}")
    W, H = ASPECTS[aspect]
    pal = palette_for(topic_category)
    cards = cards[:4]
    total = len(cards)
    output_dir.mkdir(parents=True, exist_ok=True)
    out: List[Path] = []
    for i, card in enumerate(cards, 1):
        draw_fn = _RENDERERS.get(card.get("type", ""), _draw_cover)
        img = draw_fn(W, H, pal, card, i, total)
        p = output_dir / f"card_{aspect}_{i}.png"
        img.save(p, "PNG", optimize=True)
        out.append(p)
    return out
