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
    # Avoid a lone trailing CJK character (orphan): pull the last char of the
    # previous line down so the final line has ≥2 chars. Skip if that char is
    # latin/alphanumeric so words like "API" aren't split.
    if len(lines) >= 2 and len(lines[-1]) == 1 and len(lines[-2]) >= 2:
        tail = lines[-2][-1]
        if not (tail.isascii() and tail.isalnum()):
            lines[-2] = lines[-2][:-1]
            lines[-1] = tail + lines[-1]
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
    # auto-fit hero (largest that fits ≤5 lines within ~half the canvas).
    f, ls, lh = _font(FONT_TITLE_PATH, W * 0.072), _wrap(d, title, _font(FONT_TITLE_PATH, W * 0.072), mw), int(W * 0.072 * 1.18)
    for ptr in range(118, 56, -4):
        pt = W * ptr / 1000
        ff = _font(FONT_TITLE_PATH, pt)
        lls = _wrap(d, title, ff, mw)
        llh = int(pt * 1.18)
        if len(lls) <= 5 and len(lls) * llh <= int(H * 0.52):
            f, ls, lh = ff, lls, llh
            break
    sf = _font(FONT_SUBTITLE_PATH, W * 0.032)
    sub_lines = _wrap(d, card.get("subtitle", ""), sf, mw)[:3] if card.get("subtitle") else []
    sub_lh = round(W * 0.046)
    sub_gap = round(H * 0.045) if sub_lines else 0
    # Vertically center the (hero + subtitle) block between the kicker and slogan
    # so portrait covers don't leave a dead gap in the middle.
    block_h = len(ls) * lh + sub_gap + len(sub_lines) * sub_lh
    region_top, region_bot = round(H * 0.165), H - round(H * 0.11)
    y = region_top + max(0, (region_bot - region_top - block_h) // 2)
    for ln in ls:
        d.text((pad, y), ln, font=f, fill=ink)
        y += lh
    if sub_lines:
        y += sub_gap
        for ln in sub_lines:
            d.text((pad, y), ln, font=sf, fill=sub)
            y += sub_lh
    by = H - round(H * 0.085)
    d.line((pad, by, W - pad, by), fill=ink, width=1)
    d.text((pad, by + round(H * 0.016)), SLOGAN, font=_font(FONT_BRAND_PATH, W * 0.024), fill=sub)
    return img


def _draw_stat(W, H, pal, card, idx, total):
    """A single dominant number/figure card — the article's biggest data point."""
    img, d, pad = _frame(W, H, pal, idx, total)
    ink, acc, sub = (_hx(pal[k]) for k in ("ink", "acc", "sub"))
    mw = W - 2 * pad
    d.text((pad, round(H * 0.16)), card.get("label", "關鍵數字"), font=_font(FONT_TITLE_PATH, W * 0.038), fill=acc)
    number = card.get("number", "")
    nf = _font(FONT_TITLE_PATH, W * 0.10)
    for ptr in range(300, 100, -8):
        ff = _font(FONT_TITLE_PATH, W * ptr / 1000)
        if d.textlength(number, font=ff) <= mw:
            nf = ff
            break
    ny = round(H * 0.30)
    d.text((pad, ny), number, font=nf, fill=acc)
    cap = card.get("caption", "")
    if cap:
        cf = _font(FONT_SUBTITLE_PATH, W * 0.038)
        cy = ny + int(nf.size * 1.05) + round(H * 0.03)
        for ln in _wrap(d, cap, cf, mw):
            d.text((pad, cy), ln, font=cf, fill=ink)
            cy += round(W * 0.054)
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


_RENDERERS = {"cover": _draw_cover, "insight": _draw_insight, "stat": _draw_stat, "takeaway": _draw_takeaway}


# Per-card caps (safety net; the composer prompt is the primary lever). A bit
# roomy so complete sentences fit and the graceful trim rarely fires.
_CAP_COVER_TITLE = 28
_CAP_INSIGHT_STMT = 38
_CAP_INSIGHT_SUPPORT = 46
_CAP_STAT_NUMBER = 10       # a hair over the 8-char target ("$1,234" etc.); longer ⇒ not a clean stat ⇒ skip card
_CAP_STAT_CAPTION = 28
_CAP_TAKEAWAY = 22

_SENT_END = "。！？!?"
_CLAUSE = "，,、；;：:"


def _clip(text: str, n: int) -> str:
    """Graceful cap: if over n, back off to the last sentence/clause boundary so the
    card ends on a clean phrase — NEVER a mid-word cut with a dangling '…'."""
    s = (text or "").strip()
    if len(s) <= n:
        return s
    head = s[:n]
    best, keep = -1, False
    for i, ch in enumerate(head):
        if ch in _SENT_END:
            best, keep = i, True
        elif ch in _CLAUSE:
            best, keep = i, False
    if best >= 8:                       # a boundary that keeps enough meaning
        return head[: best + 1] if keep else head[:best]
    return head.rstrip("，,、；;：:　 ")  # last resort: clean char cut, no ellipsis


def build_cards(*, title: str, subtitle: str, carousel) -> List[Dict]:
    """Assemble 2–4 card dicts from a CarouselCards (schema) + the cover title.

    Per-card editorial spec (each card = one job; hard char caps keep type large):
      1 cover    — hook title only (≤20).               always.
      2 insight  — one statement (≤30) + support (≤40). if statement present.
      3 stat     — ONE number (≤10) + caption (≤24).    only if a clean short number.
      4 takeaway — 2–3 bullets (each ≤18) + CTA.         if any takeaway.
    ``carousel`` may be a pydantic CarouselCards or None; missing parts are skipped.
    """
    cards: List[Dict] = [{"type": "cover", "title": _clip(title, _CAP_COVER_TITLE),
                          "subtitle": subtitle or ""}]
    g = lambda k: getattr(carousel, k, None) if carousel is not None else None

    stmt = (g("insight_statement") or "").strip()
    if stmt:
        cards.append({"type": "insight", "label": "核心洞察",
                      "statement": _clip(stmt, _CAP_INSIGHT_STMT),
                      "support": _clip(g("insight_support") or "", _CAP_INSIGHT_SUPPORT)})

    num = (g("stat_number") or "").strip()
    if num and len(num) <= _CAP_STAT_NUMBER:   # no clean short number ⇒ skip the stat card entirely
        cards.append({"type": "stat", "label": "一個數字看懂",
                      "number": num,
                      "caption": _clip(g("stat_caption") or "", _CAP_STAT_CAPTION)})

    points = [_clip(t, _CAP_TAKEAWAY) for t in (g("takeaways") or []) if (t or "").strip()][:3]
    if points:
        cards.append({"type": "takeaway", "label": "帶走的判斷",
                      "points": points, "cta": "追蹤 主力爸爸我錯了"})
    return cards[:4]


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
