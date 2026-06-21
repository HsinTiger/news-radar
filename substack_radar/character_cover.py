"""
News Radar · Character Cover compositor (Cover System D6 · route 3, 2026-06-21)
==============================================================================
半自動合成封面：把已鎖定的 IP 角色裁切圖（瑞瑞 robot / 達達 owl）貼到 cream 畫布上，
再用 Pillow 在對側標題區排上純 ink 標題。角色是固定素材、不重新生圖 → 零走樣、5 秒出圖、
任意標題都能套。沒有可用素材時回 None → 上層改用 promise_cover 純文字保底封面。

素材庫慣例（可擴充 —— 丟一張 PNG 進去就自動被認得，零改 code）：
    config/cover_ip/assets/{species}_{expression}.png        完整解析度（優先）
    config/cover_ip/assets/{species}_{expression}_sm.png     縮圖（後備）
背景可是 cream #F2EEE5（這裡會 key 成透明）或本來就透明。
要新表情/姿勢：用 route-1 的 D5 prompt 丟 ChatGPT / nanobanana 生圖，存進 assets/ 即可。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from substack_radar.promise_cover import (
    W, H, KICKER, _font, _wrap, _hx, palette_for,
    FONT_TITLE_PATH, FONT_SUBTITLE_PATH,
)
from src.image_brain import pick_character, _anchor_gaze, _DEFAULT_EXPRESSION

ASSETS_DIR = Path(__file__).resolve().parent / "config" / "cover_ip" / "assets"
_CREAM = (242, 238, 229)
_MARGIN = 72


def _find_asset(character: str, expression: Optional[str]) -> Optional[Path]:
    """Asset lookup by convention. Prefer full-res, fall back to _sm, then the
    species' default expression, then any asset for that species. None → no asset
    → caller uses the text-poster cover. Dropping a new PNG extends the library."""
    exprs = []
    if expression:
        exprs.append(expression)
    default = _DEFAULT_EXPRESSION.get(character)
    if default and default not in exprs:
        exprs.append(default)
    for e in exprs:
        for suffix in ("", "_sm"):
            p = ASSETS_DIR / f"{character}_{e}{suffix}.png"
            if p.exists():
                return p
    # Any asset for the species (prefer full-res over _sm).
    cands = sorted(ASSETS_DIR.glob(f"{character}_*.png"),
                   key=lambda p: ("_sm" in p.stem, p.name))
    return cands[0] if cands else None


def _keyed_trim(im):
    """RGBA → key cream to transparent (so the cutout composites cleanly on any
    ground; on a cream cover the keyed edge is invisible anyway) → crop to the
    character's bounding box so placement is exact regardless of source padding."""
    from PIL import Image, ImageChops
    im = im.convert("RGBA")
    if im.getextrema()[3][0] == 255:  # opaque cream bg → colour-key it out
        rgb = im.convert("RGB")
        bg = Image.new("RGB", im.size, _CREAM)
        mask = ImageChops.difference(rgb, bg).convert("L").point(lambda p: 255 if p > 16 else 0)
        im.putalpha(mask)
    bbox = im.split()[3].getbbox()
    return im.crop(bbox) if bbox else im


def render_character_cover(
    *,
    title: str,
    subtitle: str,
    topic_category: str,
    character: Optional[str] = None,
    output_dir: Path,
    expression: Optional[str] = None,
) -> Optional[Path]:
    """Composite a character cover to ``output_dir/cover.png``. Returns the path,
    or None when no character asset is available (→ promise_cover fallback)."""
    from PIL import Image, ImageDraw

    char = character if character in ("robot", "owl") else pick_character(topic_category, None)
    asset = _find_asset(char, expression)
    if asset is None:
        return None  # graceful: no asset yet → text-poster cover handles it

    pal = palette_for(topic_category)
    bg, ink, acc, sub = (_hx(pal[k]) for k in ("bg", "ink", "acc", "sub"))
    img = Image.new("RGB", (W, H), bg)

    # --- character: keyed, trimmed, scaled (taller for short titles = layout A,
    #     smaller for long titles = layout B), grounded near the lower third ---
    cut = _keyed_trim(Image.open(asset))
    tlen = len(title or "")
    frac = 0.82 if tlen <= 14 else (0.72 if tlen <= 24 else 0.62)
    target_h = int(H * frac)
    scale = target_h / cut.height
    cw, ch = max(1, int(cut.width * scale)), target_h
    cut = cut.resize((cw, ch), Image.LANCZOS)

    anchor, _ = _anchor_gaze(title)
    col_w = max(cw, int(W * 0.30))
    if anchor == "left":
        cx = _MARGIN + (col_w - cw) // 2
        title_x0, title_x1 = _MARGIN + col_w + 40, W - _MARGIN
    else:
        cx = W - _MARGIN - col_w + (col_w - cw) // 2
        title_x0, title_x1 = _MARGIN, W - _MARGIN - col_w - 40
    cy = H - 40 - ch
    img.paste(cut, (cx, cy), cut)

    d = ImageDraw.Draw(img)
    tw = title_x1 - title_x0

    # --- kicker + the single accent rule (the one emphasis colour) ---
    d.text((title_x0, 70), KICKER, font=_font(FONT_TITLE_PATH, 30), fill=acc)
    d.line((title_x0, 116, title_x0 + 112, 116), fill=acc, width=6)

    # --- hero title: pure ink, auto-fit the largest size that fits ≤3 lines ---
    title_font = _font(FONT_TITLE_PATH, 96)
    lines = _wrap(d, title, title_font, tw)
    line_h = int(96 * 1.16)
    for pt in range(132, 56, -6):
        f = _font(FONT_TITLE_PATH, pt)
        ls = _wrap(d, title, f, tw)
        lh = int(pt * 1.16)
        if len(ls) <= 3 and len(ls) * lh <= int(H * 0.50):
            title_font, lines, line_h = f, ls, lh
            break
    y = 168
    for ln in lines:
        d.text((title_x0, y), ln, font=title_font, fill=ink)
        y += line_h

    # --- subtitle (muted), right under the title block ---
    if subtitle:
        sf = _font(FONT_SUBTITLE_PATH, 34)
        sy = y + 18
        for ln in _wrap(d, subtitle, sf, tw)[:2]:
            d.text((title_x0, sy), ln, font=sf, fill=sub)
            sy += 46

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "cover.png"
    img.save(out, "PNG", optimize=True)
    return out
