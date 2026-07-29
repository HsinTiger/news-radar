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
from substack_radar.promise_cover import _cap_lines

_CREAM = (242, 238, 229)   # paper-cream ground
_INK = (20, 20, 20)        # near-black title
_STONE = (90, 82, 71)      # muted subtitle / meta
_SIENNA = (200, 74, 50)    # the one accent

_SIZES = {"ig": (1080, 1350), "threads": (1080, 1350), "fb": (1080, 1080)}

# Meta 選角（2026-06-23, Hsin 選「依標題角度」）：Meta 的 topic 分類偏科技財經（8 類→robot），
# 純 topic 會讓達達幾乎不出場。改用標題「角度」讓兩隻都依內容出場——
#   達達 owl：反思 / 反共識 / 大局 / 二選一 / 開放質疑的角度（即使是科技財經題）。
#   瑞瑞 robot：硬數據 / 突發 / 具體爆點。
#   都沒命中 → 照 topic 預設（pick_character）。owl 先判（反思框架壓過資料名詞）。
_OWL_ANGLE_CUES = ("也許", "其實是", "其實", "還是", "真的能", "真的會", "真的要", "真的嗎",
                   "誰來", "誰能", "憑什麼", "為何", "是不是", "會不會", "該不該",
                   "不會是", "不只是", "沒有人", "沒人")
_ROBOT_ANGLE_CUES = ("暴跌", "急殺", "閃崩", "崩", "重挫", "新高", "突破", "飆", "財報",
                     "打臉", "賺爆", "用爆", "創紀錄", "燒錢", "%", "兆", "億", "倍")


def _pick_meta_character(topic_category, title) -> str:
    t = title or ""
    if any(c in t for c in _OWL_ANGLE_CUES):
        return "owl"
    if any(c in t for c in _ROBOT_ANGLE_CUES):
        return "robot"
    return pick_character(topic_category, None)


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
    # Never leave closing punctuation at the start of a line.  If it cannot
    # fit on the previous line, move one preceding CJK character down with it.
    opening_forbidden = "，。！？!?：:；;、）)」』】》"
    for index in range(1, len(lines)):
        if lines[index] and lines[index][0] in opening_forbidden and lines[index - 1]:
            moved = lines[index - 1][-1]
            lines[index - 1] = lines[index - 1][:-1]
            lines[index] = moved + lines[index]
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
    # 連最小字級都塞不下 3 行 → 收在標點/詞界＋「…」（不硬切字、不爆框），不再硬丟尾段。
    return f, _cap_lines(_wrap(draw, title, f, max_w), 3, draw, f, max_w), int(pt_lo * 1.22)


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


def compose_meta_character_cover(
    *,
    title: str,
    subtitle: Optional[str],
    topic_category: Optional[str],
    aspect: str,
    brand_name: str = "@smartmmmoney",
    character: Optional[str] = None,
    mode: Optional[str] = None,
):
    """Compose a Meta IP character cover → return a PIL Image (RGB), or None when
    aspect unknown / no character asset (→ caller falls back to the photo cover or
    the typographic cover card). Used by both render_meta_character_cover (saves to
    file, for cover_pipeline) and cards.render_cards (uses the image as carousel slide 0)."""
    from PIL import Image, ImageDraw
    from src.image_brain import _anchor_gaze

    if aspect not in _SIZES:
        return None
    W, H = _SIZES[aspect]
    char = character if character in ("robot", "owl") else _pick_meta_character(topic_category, title)
    expr = pick_expression(topic_category, mode, title, character=char)
    asset = _find_asset(char, expr)
    if asset is None:
        return None

    img = Image.new("RGB", (W, H), _CREAM)
    d = ImageDraw.Draw(img)
    label = TOPIC_CHIP_LABELS.get((topic_category or "other"), "News Radar")
    portrait = (H / W) >= 1.15
    M = 72
    d.rectangle((0, 0, W, max(12, int(H * 0.012))), fill=_SIENNA)
    index_font = _load_font(FONT_BRAND_PATH, 28)
    index_text = "01  /  03"
    index_w = d.textlength(index_text, font=index_font)
    index_x = W - M - index_w - 34
    index_y = 50
    d.rounded_rectangle(
        (index_x, index_y, W - M, index_y + 56),
        radius=13,
        outline=_SIENNA,
        width=2,
    )
    d.text((index_x + 17, index_y + 11), index_text, font=index_font, fill=_INK)

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
            for ln in _cap_lines(_wrap(d, subtitle, sf, W - 2 * M), 2, d, sf, W - 2 * M):
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
            for ln in _cap_lines(_wrap(d, subtitle, sf, tw), 2, d, sf, tw):
                d.text((tx0, sy), ln, font=sf, fill=_STONE)
                sy += 52

    # 品牌（底部、sienna 細線 + handle）
    bf = _load_font(FONT_BRAND_PATH, 30)
    d.line((M, H - 70, M + 96, H - 70), fill=_SIENNA, width=6)
    d.text((M, H - 56), brand_name, font=bf, fill=_STONE)

    return img.convert("RGB")


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
    """Compose + save a Meta IP character cover → output_dir/character_<aspect>.png.
    Returns the path, or None (→ caller falls back to the photo cover). Thin wrapper
    over compose_meta_character_cover (single-image cover path, cover_pipeline)."""
    img = compose_meta_character_cover(
        title=title, subtitle=subtitle, topic_category=topic_category, aspect=aspect,
        brand_name=brand_name, character=character, mode=mode,
    )
    if img is None:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"character_{aspect}.png"
    img.save(out, "PNG", optimize=True)
    return out
