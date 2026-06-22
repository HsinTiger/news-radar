"""
News Radar · Meta IP Character Cover (2026-06-23, Hsin directive)
=================================================================
把 Substack 的雙 IP 角色封面「依樣畫葫蘆」搬到 Meta 三平台的第一張圖：
cream 底 + 角色（瑞瑞 robot / 達達 owl，依新聞 topic_category 自動選）+ 標題 + 品牌。

  - fb 1080×1080（方）→ 左右並排：角色一側、標題另一側（依標題交替錨點、角色面向標題）。
  - ig / threads 1080×1350（直）→ 上下堆疊：標題在上、角色在下置中。

選角 (pick_character)、選表情 (pick_expression)、角色裁切/去背/鏡像全部重用
image_brain + substack_radar.character_cover；字體用 cover_renderer 的 SourceHanSansTC
（Meta 品牌字）。沒角色素材或出錯 → 回 None，cover_pipeline 退回原本的新聞照封面。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

from src.image_brain import pick_character, pick_expression
from src.cover_renderer import (
    FONT_TITLE_PATH, FONT_SUBTITLE_PATH, FONT_BRAND_PATH,
    TOPIC_CHIP_LABELS, _load_font,
)
from substack_radar.character_cover import _find_asset, _keyed_trim, _ASSET_FACES

_CREAM = (242, 238, 229)   # paper-cream ground
_INK = (20, 20, 20)        # near-black title
_STONE = (90, 82, 71)      # muted subtitle / meta
_SIENNA = (200, 74, 50)    # the one accent

_SIZES = {"ig": (1080, 1350), "threads": (1080, 1350), "fb": (1080, 1080)}


def _wrap(draw, text: str, font, max_w: int) -> list:
    """Greedy CJK wrap; keep ASCII/number tokens unbroken."""
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-%]*|.", text or "")
    lines, cur = [], ""
    for t in toks:
        if not cur or draw.textlength(cur + t, font=font) <= max_w:
            cur += t
        else:
            lines.append(cur)
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _fit_title(draw, title, max_w, max_total_h, pt_hi, pt_lo):
    """Auto-fit the largest title size that fits ≤3 lines within max_total_h."""
    for pt in range(pt_hi, pt_lo - 1, -6):
        f = _load_font(FONT_TITLE_PATH, pt)
        ls = _wrap(draw, title, f, max_w)
        lh = int(pt * 1.22)
        if len(ls) <= 3 and len(ls) * lh <= max_total_h:
            return f, ls, lh
    f = _load_font(FONT_TITLE_PATH, pt_lo)
    return f, _wrap(draw, title, f, max_w)[:3], int(pt_lo * 1.22)


def _chip(draw, x, y, label):
    """Small stone-grey category pill with ink text (on-brand, not the bright
    feed-cover colours — keeps the cream/ink/sienna character aesthetic)."""
    f = _load_font(FONT_BRAND_PATH, 30)
    tw = draw.textlength(label, font=f)
    w, h = int(tw + 44), 56
    draw.rounded_rectangle((x, y, x + w, y + h), radius=12, fill=_STONE)
    draw.text((x + 22, y + 11), label, font=f, fill=_CREAM)
    return y + h


def _fit_char(asset_path, max_w, max_h, anchor=None):
    """Keyed+trimmed character, flipped to face the title (when an anchor is given),
    scaled to fit WITHIN (max_w, max_h) preserving aspect — so wide poses don't
    overflow their column."""
    from PIL import Image
    cut = _keyed_trim(Image.open(asset_path))
    if anchor is not None:
        face_title = "right" if anchor == "left" else "left"
        if face_title != _ASSET_FACES:
            cut = cut.transpose(Image.FLIP_LEFT_RIGHT)
    scale = min(max_w / cut.width, max_h / cut.height)
    return cut.resize((max(1, int(cut.width * scale)), max(1, int(cut.height * scale))), Image.LANCZOS)


def render_meta_character_cover(
    *,
    title: str,
    subtitle: Optional[str],
    topic_category: Optional[str],
    aspect: str,
    output_dir: Path,
    brand_name: str = "@smartmmmoney",
    character: Optional[str] = None,
    mode: Optional[str] = None,
) -> Optional[Path]:
    """Composite a Meta IP character cover → output_dir/character_<aspect>.png.
    Returns the path, or None when aspect unknown / no character asset (→ caller
    falls back to the photo cover)."""
    from PIL import Image, ImageDraw
    from src.image_brain import _anchor_gaze

    if aspect not in _SIZES:
        return None
    W, H = _SIZES[aspect]
    char = character if character in ("robot", "owl") else pick_character(topic_category, mode)
    expr = pick_expression(topic_category, mode, title)
    asset = _find_asset(char, expr)
    if asset is None:
        return None

    img = Image.new("RGB", (W, H), _CREAM)
    d = ImageDraw.Draw(img)
    label = TOPIC_CHIP_LABELS.get((topic_category or "other"), "News Radar")
    portrait = (H / W) >= 1.15
    M = 72

    if portrait:
        # ── 直圖：標題在上、角色在下置中 ──
        chip_bottom = _chip(d, M, 70, label)
        char_h = int(H * 0.46)
        cut = _fit_char(asset, W - 2 * M, char_h)  # 置中、不鏡像、限寬高
        title_zone_h = H - char_h - 120            # 標題可用高度（上半）
        tf, lines, lh = _fit_title(d, title, W - 2 * M, int(title_zone_h * 0.62), 104, 60)
        y = chip_bottom + 46
        for ln in lines:
            d.text((M, y), ln, font=tf, fill=_INK)
            y += lh
        if subtitle:
            sf = _load_font(FONT_SUBTITLE_PATH, 44)
            sy = y + 16
            for ln in _wrap(d, subtitle, sf, W - 2 * M)[:2]:
                d.text((M, sy), ln, font=sf, fill=_STONE)
                sy += 58
        img.paste(cut, ((W - cut.width) // 2, H - 24 - char_h), cut)
    else:
        # ── 方圖：角色一側、標題另一側 ──
        anchor, _ = _anchor_gaze(title)
        cut = _fit_char(asset, int(W * 0.44), int(H * 0.62), anchor)  # 限在一側方框內
        col_w = max(cut.width, int(W * 0.40))
        if anchor == "left":
            cx = M + (col_w - cut.width) // 2
            tx0, tx1 = M + col_w + 30, W - M
        else:
            cx = W - M - col_w + (col_w - cut.width) // 2
            tx0, tx1 = M, W - M - col_w - 30
        img.paste(cut, (cx, H - 24 - cut.height), cut)
        tw = tx1 - tx0
        chip_bottom = _chip(d, tx0, 78, label)
        tf, lines, lh = _fit_title(d, title, tw, int(H * 0.46), 92, 54)
        y = chip_bottom + 40
        for ln in lines:
            d.text((tx0, y), ln, font=tf, fill=_INK)
            y += lh
        if subtitle:
            sf = _load_font(FONT_SUBTITLE_PATH, 40)
            sy = y + 14
            for ln in _wrap(d, subtitle, sf, tw)[:2]:
                d.text((tx0, sy), ln, font=sf, fill=_STONE)
                sy += 52

    # 品牌（底部、sienna 細線 + handle）
    bf = _load_font(FONT_BRAND_PATH, 30)
    d.line((M, H - 70, M + 96, H - 70), fill=_SIENNA, width=6)
    d.text((M, H - 56), brand_name, font=bf, fill=_STONE)

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"character_{aspect}.png"
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out
