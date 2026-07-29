"""
News Radar · governed three-card Meta carousel renderer
========================================================
Every Meta post uses the same editorial sequence while receiving a native
platform layout:

1. mascot cover + current hook;
2. named source + evidence/number;
3. reader judgement/action + one answerable question.

The contract is deliberately fail-closed.  Missing content, the wrong order,
or a card count other than three is a rendering error rather than permission to
fall back to a single image.

Card content is a list of dicts (built upstream by the composer / a distill step):
    [{"type": "cover",    "title": "...", "subtitle": "..."},
     {"type": "insight",  "statement": "...", "support": "..."},
     {"type": "takeaway", "points": ["...", "..."], "cta": "追蹤 主力爸爸我錯了"}]
Exactly three cards; cover is always first. render_cards() returns the PNG
paths in order.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

from src.cover_renderer import (
    FONT_BRAND_PATH,
    FONT_SUBTITLE_PATH,
    FONT_TITLE_PATH,
    _load_font,
)
from substack_radar.promise_cover import palette_for, _hx, KICKER, SLOGAN

# aspect → (W, H). IG/Threads portrait 4:5, FB square 1:1.
ASPECTS: Dict[str, tuple] = {
    "ig": (1080, 1350),
    "threads": (1080, 1350),
    "fb": (1080, 1080),
}
CAROUSEL_CARD_COUNT = 3
CARD_SEQUENCE = ("cover", "evidence", "action")


def _font(path, pt):
    # Keep clean clones renderable when optional bundled font assets are absent.
    # ``cover_renderer._load_font`` applies the same CJK-capable system fallback
    # used by the mascot cover instead of failing halfway through a carousel.
    return _load_font(path, int(pt))


def _wrap(draw, text: str, font, max_w: int):
    import re
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-%$]*|.", text or "")
    lines, cur = [], ""
    for tok in tokens:
        if not cur or draw.textlength(cur + tok, font=font) <= max_w:
            cur += tok
        else:
            # Prefer break at clause boundary (，。！？、) within cur
            # to avoid mid-phrase line breaks
            _CLAUSE_BOUNDARY = "。！？!?，,、；;"
            break_at = -1
            for i in range(len(cur) - 1, -1, -1):
                if cur[i] in _CLAUSE_BOUNDARY:
                    break_at = i + 1
                    break
            if break_at >= 4 and draw.textlength(cur[:break_at], font=font) <= max_w * 0.9:
                lines.append(cur[:break_at].strip())
                cur = cur[break_at:].strip() + tok
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
    """Shared editorial chrome with a visible sequence and safe-area grid."""
    from PIL import Image, ImageDraw
    bg, ink, acc, sub = (_hx(pal[k]) for k in ("bg", "ink", "acc", "sub"))
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    pad = round(W * 0.063)
    d.rectangle((0, 0, W, max(12, round(H * 0.012))), fill=acc)
    d.text(
        (pad, round(H * 0.048)),
        "HSINTIGER  /  VERIFIED BRIEF",
        font=_font(FONT_BRAND_PATH, W * 0.025),
        fill=sub,
    )
    tag = f"0{idx}  /  0{total}"
    tf = _font(FONT_BRAND_PATH, W * 0.025)
    tag_w = d.textlength(tag, font=tf)
    tag_pad = round(W * 0.018)
    tx = W - pad - tag_w - 2 * tag_pad
    ty = round(H * 0.038)
    d.rounded_rectangle(
        (tx, ty, W - pad, ty + round(H * 0.052)),
        radius=round(W * 0.012),
        outline=acc,
        width=2,
    )
    d.text((tx + tag_pad, ty + round(H * 0.011)), tag, font=tf, fill=ink)
    d.line(
        (pad, H - round(H * 0.075), W - pad, H - round(H * 0.075)),
        fill=sub,
        width=1,
    )
    d.text(
        (pad, H - round(H * 0.055)),
        SLOGAN,
        font=_font(FONT_BRAND_PATH, W * 0.022),
        fill=sub,
    )
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


def _draw_evidence(W, H, pal, card, idx, total):
    """Card 2: one claim, one bounded proof block, one visible source line."""
    from PIL import ImageDraw

    img, d, pad = _frame(W, H, pal, idx, total)
    ink, acc, sub, bg = (_hx(pal[k]) for k in ("ink", "acc", "sub", "bg"))
    mw = W - 2 * pad
    label_font = _font(FONT_BRAND_PATH, W * 0.028)
    d.text((pad, round(H * 0.135)), "EVIDENCE  /  核心證據", font=label_font, fill=acc)

    statement = card.get("statement", "")
    title_font = _font(FONT_TITLE_PATH, W * 0.064)
    statement_lines = _wrap(d, statement, title_font, mw)[:4]
    y = round(H * 0.205)
    for line in statement_lines:
        d.text((pad, y), line, font=title_font, fill=ink)
        y += round(title_font.size * 1.2)

    number = card.get("number", "")
    caption = card.get("caption", "")
    panel_top = max(y + round(H * 0.025), round(H * 0.48))
    panel_bottom = min(H - round(H * 0.235), panel_top + round(H * 0.245))
    d.rounded_rectangle(
        (pad, panel_top, W - pad, panel_bottom),
        radius=round(W * 0.025),
        fill=ink,
    )
    if number:
        number_font = _font(FONT_TITLE_PATH, W * 0.105)
        for pt in range(round(W * 0.13), round(W * 0.07), -4):
            candidate = _font(FONT_TITLE_PATH, pt)
            if d.textlength(number, font=candidate) <= mw * 0.42:
                number_font = candidate
                break
        d.text(
            (pad + round(W * 0.035), panel_top + round(H * 0.035)),
            number,
            font=number_font,
            fill=acc,
        )
        caption_x = pad + round(W * 0.47)
        caption_w = W - pad - caption_x - round(W * 0.025)
    else:
        caption_x = pad + round(W * 0.035)
        caption_w = mw - round(W * 0.07)
    caption_font = _font(FONT_SUBTITLE_PATH, W * 0.032)
    cy = panel_top + round(H * 0.055)
    for line in _wrap(d, caption, caption_font, caption_w)[:3]:
        d.text((caption_x, cy), line, font=caption_font, fill=bg)
        cy += round(caption_font.size * 1.35)

    source = card.get("source", "")
    source_font = _font(FONT_SUBTITLE_PATH, W * 0.034)
    source_y = panel_bottom + round(H * 0.04)
    d.text((pad, source_y), "SOURCE", font=label_font, fill=acc)
    source_y += round(H * 0.045)
    for line in _wrap(d, source, source_font, mw)[:3]:
        d.text((pad, source_y), line, font=source_font, fill=sub)
        source_y += round(source_font.size * 1.35)
    return img


def _draw_action(W, H, pal, card, idx, total):
    """Card 3: scannable actions plus a high-contrast answerable CTA."""
    img, d, pad = _frame(W, H, pal, idx, total)
    ink, acc, sub, bg = (_hx(pal[k]) for k in ("ink", "acc", "sub", "bg"))
    mw = W - 2 * pad
    d.text(
        (pad, round(H * 0.135)),
        "DECIDE  /  你可以怎麼判斷",
        font=_font(FONT_BRAND_PATH, W * 0.028),
        fill=acc,
    )

    points = card.get("points", [])[:3]
    point_font = _font(FONT_SUBTITLE_PATH, W * 0.037)
    number_font = _font(FONT_TITLE_PATH, W * 0.044)
    y = round(H * 0.225)
    for index, point in enumerate(points, 1):
        circle = round(W * 0.027)
        cx, cy = pad + circle, y + circle
        d.ellipse((cx - circle, cy - circle, cx + circle, cy + circle), fill=acc)
        digit = str(index)
        digit_w = d.textlength(digit, font=number_font)
        d.text((cx - digit_w / 2, cy - number_font.size * 0.63), digit, font=number_font, fill=bg)
        py = y - round(H * 0.002)
        for line in _wrap(d, point, point_font, mw - round(W * 0.09))[:3]:
            d.text((pad + round(W * 0.085), py), line, font=point_font, fill=ink)
            py += round(point_font.size * 1.35)
        y = max(py + round(H * 0.025), y + round(H * 0.125))

    question = card.get("question", "")
    panel_top = max(y + round(H * 0.025), round(H * 0.64))
    panel_bottom = H - round(H * 0.14)
    d.rounded_rectangle(
        (pad, panel_top, W - pad, panel_bottom),
        radius=round(W * 0.03),
        fill=acc,
    )
    d.text(
        (pad + round(W * 0.035), panel_top + round(H * 0.03)),
        "YOUR TAKE",
        font=_font(FONT_BRAND_PATH, W * 0.025),
        fill=bg,
    )
    question_font = _font(FONT_TITLE_PATH, W * 0.052)
    qy = panel_top + round(H * 0.082)
    for line in _wrap(d, question, question_font, mw - round(W * 0.07))[:4]:
        d.text((pad + round(W * 0.035), qy), line, font=question_font, fill=ink)
        qy += round(question_font.size * 1.22)
    return img


def _draw_figures(W, H, pal, card, idx, total):
    """關鍵數據卡：3-4 行 label→value，第一行 accent 強調（Bloomberg 風）。"""
    img, d, pad = _frame(W, H, pal, idx, total)
    ink, acc, sub = (_hx(pal[k]) for k in ("ink", "acc", "sub"))
    d.text((pad, round(H * 0.15)), card.get("label", "關鍵數據"),
           font=_font(FONT_TITLE_PATH, W * 0.038), fill=acc)
    rows = card.get("rows", [])[:4]
    n = max(len(rows), 1)
    lf = _font(FONT_SUBTITLE_PATH, W * 0.044)
    top, bot = round(H * 0.26), round(H * 0.84)
    rh = (bot - top) // n
    for i, r in enumerate(rows):
        y = top + i * rh
        d.line((pad, y, W - pad, y), fill=sub, width=1)
        lab, val = r.get("label", ""), r.get("value", "")
        vff = _font(FONT_TITLE_PATH, W * 0.072)
        for ptr in range(72, 36, -4):
            cand = _font(FONT_TITLE_PATH, W * ptr / 1000)
            if d.textlength(val, font=cand) <= W * 0.44:
                vff = cand
                break
        cy = y + rh // 2
        d.text((W - pad - d.textlength(val, font=vff), cy - int(vff.size * 0.62)),
               val, font=vff, fill=(acc if i == 0 else ink))
        d.text((pad, cy - int(lf.size * 0.62)), lab, font=lf, fill=ink)
    d.line((pad, top + n * rh, W - pad, top + n * rh), fill=sub, width=1)
    return img


_RENDERERS = {
    "cover": _draw_cover,
    "evidence": _draw_evidence,
    "action": _draw_action,
    # Retained for old evidence fixtures; governed publishing never emits them.
    "insight": _draw_insight,
    "stat": _draw_stat,
    "takeaway": _draw_takeaway,
    "figures": _draw_figures,
}


# Per-card caps (safety net; the composer prompt is the primary lever). A bit
# roomy so complete sentences fit and the graceful trim rarely fires.
_CAP_COVER_TITLE = 24         # prompt ≤20; extra slack for punctuation etc.
_CAP_INSIGHT_STMT = 32        # prompt ≤30; tiny slack for full-sentence wrapping
_CAP_INSIGHT_SUPPORT = 42     # prompt ≤40
_CAP_STAT_NUMBER = 10         # a hair over the 8-char target ("$1,234" etc.); longer ⇒ not a clean stat ⇒ skip card
_CAP_STAT_CAPTION = 26        # prompt ≤24
_CAP_TAKEAWAY = 20            # prompt ≤18
_SENT_END = "。！？!?"
_CLAUSE = "，,、；;：:"


def _clip(text: str, n: int) -> str:
    """Keep a COMPLETE clause/sentence — the renderers wrap, so a 2-line phrase is
    fine. NEVER a mid-word fragment. Over n: prefer ending on a real boundary; a
    single boundary-less clause is kept WHOLE (let it wrap) unless a true runaway."""
    s = (text or "").strip()
    if len(s) <= n:
        return s
    window = s[: int(n * 1.6)]          # generous window so we can land on a boundary
    best, keep = -1, False
    for i, ch in enumerate(window):
        if ch in _SENT_END:
            best, keep = i, True
        elif ch in _CLAUSE:
            best, keep = i, False
    if best >= max(8, n // 2):          # end on a complete clause/sentence
        return window[: best + 1] if keep else window[:best]
    # No usable boundary → one long clause. Keep it whole (renderer wraps it
    # complete); only hard-trim a genuine runaway, and even then on no ellipsis.
    return s if len(s) <= int(n * 1.6) else window.rstrip("，,、；;：:　 ")


def build_cards(*, title: str, subtitle: str, carousel) -> List[Dict]:
    """Build the governed cover → evidence → action sequence.

    Incomplete structured content returns an empty list.  Callers must treat
    that as a quality hold; they must never infer permission to publish a
    smaller carousel or a single image.
    """
    if carousel is None or not (title or "").strip():
        return []
    g = lambda k: getattr(carousel, k, None) if carousel is not None else None
    stmt = (g("insight_statement") or "").strip()
    support = (g("insight_support") or "").strip()
    num = (g("stat_number") or "").strip()
    caption = (g("stat_caption") or "").strip()
    source = (g("source_attribution") or "").strip()
    points = [_clip(t, _CAP_TAKEAWAY) for t in (g("takeaways") or []) if (t or "").strip()][:3]
    question = (g("reader_question") or "").strip()
    if not stmt or not source or len(points) < 2 or not question:
        return []
    if question.count("？") + question.count("?") > 1:
        return []
    if not question.endswith(("？", "?")):
        question = question.rstrip("。！!；;，,") + "？"
    if num and len(num) > _CAP_STAT_NUMBER:
        num = ""
    rendered_question = _clip(question, 42)
    if not rendered_question.endswith(("？", "?")):
        rendered_question = rendered_question.rstrip("。！!；;，,") + "？"
    cards: List[Dict] = [
        {
            "type": "cover",
            "title": _clip(title, _CAP_COVER_TITLE),
            "subtitle": subtitle or "",
        },
        {
            "type": "evidence",
            "statement": _clip(stmt, _CAP_INSIGHT_STMT),
            "number": num,
            "caption": _clip(caption or support, _CAP_INSIGHT_SUPPORT),
            "source": _clip(source, 54),
        },
        {
            "type": "action",
            "points": points,
            "question": rendered_question,
        },
    ]
    return cards


def render_cards(
    *,
    cards: List[Dict],
    topic_category: str,
    aspect: str,
    output_dir: Path,
) -> List[Path]:
    """Render exactly three ordered PNGs for one platform aspect."""
    if aspect not in ASPECTS:
        raise ValueError(f"aspect must be one of {list(ASPECTS)}")
    W, H = ASPECTS[aspect]
    pal = palette_for(topic_category)
    sequence = tuple(card.get("type") for card in cards)
    if len(cards) != CAROUSEL_CARD_COUNT or sequence != CARD_SEQUENCE:
        raise ValueError(
            "carousel contract requires exactly cover,evidence,action; "
            f"got count={len(cards)} sequence={sequence}"
        )
    total = len(cards)
    output_dir.mkdir(parents=True, exist_ok=True)
    out: List[Path] = []
    for i, card in enumerate(cards, 1):
        img = None
        # The first production card must preserve the existing mascot.  A
        # missing asset is a render failure, not permission to ship a text-only
        # cover.  META_CHARACTER_COVER=0 is retained only for deterministic
        # layout tests that deliberately exercise the typographic renderer.
        mascot_required = os.getenv("META_CHARACTER_COVER", "1") != "0"
        if i == 1 and card.get("type") == "cover" and mascot_required:
            try:
                from src.character_cover_meta import compose_meta_character_cover
                img = compose_meta_character_cover(
                    title=card.get("title", ""), subtitle=card.get("subtitle", ""),
                    topic_category=topic_category, aspect=aspect, brand_name=KICKER,
                )
            except Exception as exc:
                raise RuntimeError("mascot cover render failed") from exc
            if img is None:
                raise RuntimeError("mascot cover render returned no image")
        if img is None:
            draw_fn = _RENDERERS.get(card.get("type", ""), _draw_cover)
            img = draw_fn(W, H, pal, card, i, total)
        p = output_dir / f"card_{aspect}_{i}.png"
        img.save(p, "PNG", optimize=True)
        out.append(p)
    return out
