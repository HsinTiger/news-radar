"""FB 貼文的兩張圖卡（2026-09-19 信哥定的三個條件）。

  1. 每篇兩張圖：第一張是鉤子，第二張是一個重點。
  2. 降低認知負載：每張只放一件事，大字、一眼看完。副標、頁碼、長句都不上圖。
  3. 不要跑版：字放不進框就「退回給寫手縮短」，絕不用「…」截斷、不縮到看不見、
     不壓到框外。畫完再量一次每一行的實際外框，超出安全區或壓到角色就當失敗。

斷行由寫手決定（用「／」分行），程式不自動斷行：自動斷行不懂語意，第一版就斷出
「越拚命，為／何越失控」「控制手／段」。程式只負責確認每一行都放得進一行。

為什麼是 2:1 橫圖（1600×800）：FB 兩圖貼文如果是橫圖，會上下疊、每張滿版寬，
手機上鉤子的字最大；直圖或方圖則是左右並排、每張只剩半寬，而且會被裁成細長條，
兩側的字直接被切掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.cover_renderer import FONT_BRAND_PATH, FONT_TITLE_PATH, _load_font
from src.character_cover_meta import _CREAM, _INK, _SIENNA, _STONE, _fit_char

W, H = 1600, 800
SAFE = 64            # 任何字都不能進入這圈邊界（容忍 FB 小幅裁切）
M = 100              # 版面左右留白
PAGE_NAME = "主力爸爸我錯了"
CTA = "完整版  hsin73.substack.com"

HOOK_MAX_CHARS = 16
POINT_MAX_CHARS = 30
FIGURE_MAX_CHARS = 10
HOOK_MAX_LINES = 2
POINT_MAX_LINES = 3
LINE_SEP = "／"
_NO_LINE_START = "，。！？!?：:；;、）)」』】》%％"


class LayoutError(ValueError):
    """文字放不進版面。訊息會原封不動變成給寫手的工單。"""


@dataclass
class _Fit:
    font: object
    lines: list
    line_h: int


def visible_len(text: str) -> int:
    return len("".join((text or "").split()))


def split_lines(text: str, max_lines: int, label: str) -> list[str]:
    """把寫手用「／」分好的行拆開並檢查。行數、避頭標點不對就退稿。"""
    lines = [ln.strip() for ln in (text or "").split(LINE_SEP) if ln.strip()]
    if not lines:
        raise LayoutError(f"{label}是空的。")
    if len(lines) > max_lines:
        raise LayoutError(f"{label}分成 {len(lines)} 行，最多 {max_lines} 行（用「／」分行）。")
    for ln in lines[1:]:
        if ln[0] in _NO_LINE_START:
            raise LayoutError(f"{label}的「{ln}」以標點開頭，換行位置要移到標點後面。")
    return lines


def _fit(draw, lines, *, max_w, max_h, pt_hi, pt_lo, step=4, path=FONT_TITLE_PATH):
    """由大到小找第一個「每一行都放得進一行、總高度也放得下」的字級。找不到回 None。"""
    for pt in range(pt_hi, pt_lo - 1, -step):
        font = _load_font(path, pt)
        line_h = int(pt * 1.25)
        if len(lines) * line_h <= max_h and all(draw.textlength(ln, font=font) <= max_w for ln in lines):
            return _Fit(font, list(lines), line_h)
    return None


def _draw_lines(draw, fit: _Fit, x, y, fill, boxes):
    for ln in fit.lines:
        draw.text((x, y), ln, font=fit.font, fill=fill)
        boxes.append(draw.textbbox((x, y), ln, font=fit.font))
        y += fit.line_h
    return y


def _check_safe(boxes, label, avoid=None):
    for x0, y0, x1, y1 in boxes:
        if x0 < SAFE or y0 < SAFE or x1 > W - SAFE or y1 > H - SAFE:
            raise LayoutError(f"{label}畫出安全區（{int(x0)},{int(y0)}–{int(x1)},{int(y1)}），請縮短。")
        if avoid and x0 < avoid[2] and x1 > avoid[0] and y0 < avoid[3] and y1 > avoid[1]:
            raise LayoutError(f"{label}的文字壓到角色，請縮短。")


def _base(draw):
    draw.rectangle((0, 0, W, 14), fill=_SIENNA)


def _brand(draw, boxes, text=PAGE_NAME, x=M):
    font = _load_font(FONT_BRAND_PATH, 30)
    draw.line((x, H - 128, x + 88, H - 128), fill=_SIENNA, width=6)
    draw.text((x, H - 116), text, font=font, fill=_STONE)
    boxes.append(draw.textbbox((x, H - 116), text, font=font))


def _chip(draw, x, y, label, boxes):
    font = _load_font(FONT_BRAND_PATH, 34)
    tw = draw.textlength(label, font=font)
    draw.rounded_rectangle((x, y, x + tw + 44, y + 60), radius=14, fill=_STONE)
    draw.text((x + 22, y + 11), label, font=font, fill=_CREAM)
    boxes.append((x, y, x + tw + 44, y + 60))
    return y + 60


def hook_card(hook: str, *, column: str, character_asset: Path, anchor: str, source_options=()):
    """第一張：專欄標籤＋大字鉤子＋（原始節目一行）＋角色。

    source_options 由長到短，例如（「原始節目｜My First Million・1 小時 26 分」,
    「原始節目｜My First Million」）。挑第一個放得進一行的；都放不下就不畫——
    出處在貼文內文一定有，圖卡上這行是加分項，不能為了它跑版。"""
    from PIL import Image, ImageDraw

    if visible_len(hook.replace(LINE_SEP, "")) > HOOK_MAX_CHARS:
        raise LayoutError(f"鉤子 {visible_len(hook.replace(LINE_SEP, ''))} 字，超過 {HOOK_MAX_CHARS} 字上限。")
    lines = split_lines(hook, HOOK_MAX_LINES, "鉤子")
    img = Image.new("RGB", (W, H), _CREAM)
    d = ImageDraw.Draw(img)
    _base(d)
    boxes: list = []

    char_col = 560
    cut = _fit_char(character_asset, char_col - 40, H - 150, anchor)
    if anchor == "left":
        cx = M + (char_col - cut.width) // 2 - 40
        tx0, tx1 = M + char_col, W - M
    else:
        cx = W - M - char_col + (char_col - cut.width) // 2 + 40
        tx0, tx1 = M, W - M - char_col
    cy = H - 40 - cut.height
    img.paste(cut, (cx, cy), cut)
    char_box = (cx, cy, cx + cut.width, cy + cut.height)

    top = _chip(d, tx0, 100, column, boxes) + 44
    src_font = _load_font(FONT_BRAND_PATH, 36)
    source_line = next((opt for opt in source_options
                        if opt and d.textlength(opt, font=src_font) <= tx1 - tx0), "")
    src_h = 70 if source_line else 0
    fit = _fit(d, lines, max_w=tx1 - tx0, max_h=H - top - 150 - src_h, pt_hi=156, pt_lo=96)
    if fit is None:
        raise LayoutError("鉤子有一行在最小字級 96 也放不進一行（每行約 8 字以內），請縮短。")
    block_h = len(fit.lines) * fit.line_h + src_h
    y = top + max(0, (H - top - 150 - block_h) // 2)
    y = _draw_lines(d, fit, tx0, y, _INK, boxes)
    if source_line:
        d.text((tx0, y + 18), source_line, font=src_font, fill=_STONE)
        boxes.append(d.textbbox((tx0, y + 18), source_line, font=src_font))
    _brand(d, boxes, x=tx0)
    _check_safe(boxes, "鉤子卡", avoid=char_box)
    return img


def point_card(point: str, *, figure: str = ""):
    """第二張：（可選）一個大數字＋一句重點＋去哪看全文。"""
    from PIL import Image, ImageDraw

    if visible_len(point.replace(LINE_SEP, "")) > POINT_MAX_CHARS:
        raise LayoutError(f"重點句 {visible_len(point.replace(LINE_SEP, ''))} 字，超過 {POINT_MAX_CHARS} 字上限。")
    lines = split_lines(point, POINT_MAX_LINES if not figure else 2, "重點句")
    if figure and visible_len(figure) > FIGURE_MAX_CHARS:
        raise LayoutError(f"數字欄 {visible_len(figure)} 字，超過 {FIGURE_MAX_CHARS} 字上限。")
    img = Image.new("RGB", (W, H), _CREAM)
    d = ImageDraw.Draw(img)
    _base(d)
    boxes: list = []
    max_w = W - 2 * M
    area_top, area_bottom = 110, H - 150

    fig_fit = None
    if figure:
        fig_fit = _fit(d, [figure.strip()], max_w=max_w, max_h=230, pt_hi=210, pt_lo=120, step=6)
        if fig_fit is None:
            raise LayoutError("數字欄在最小字級也排不進一行，請縮短。")
    room = area_bottom - area_top - (fig_fit.line_h + 30 if fig_fit else 0)
    pt_fit = _fit(d, lines, max_w=max_w, max_h=room, pt_hi=104 if figure else 116, pt_lo=64)
    if pt_fit is None:
        raise LayoutError("重點句有一行在最小字級 64 也放不進一行（每行約 20 字以內），請縮短。")

    block_h = len(pt_fit.lines) * pt_fit.line_h + (fig_fit.line_h + 30 if fig_fit else 0)
    y = area_top + max(0, (area_bottom - area_top - block_h) // 2)
    if fig_fit:
        y = _draw_lines(d, fig_fit, M, y, _SIENNA, boxes) + 30
    _draw_lines(d, pt_fit, M, y, _INK, boxes)
    _brand(d, boxes, f"{PAGE_NAME}    {CTA}")
    _check_safe(boxes, "重點卡")
    return img


def render_pair(*, hook: str, point: str, figure: str, column: str, mode: str,
                topic_category: str, title: str, out_dir: Path, source_options=()) -> list[Path]:
    """產出兩張卡，回傳檔案路徑。任何一張排不下就丟 LayoutError。"""
    from src.image_brain import _anchor_gaze, pick_character, pick_expression
    from substack_radar.character_cover import _find_asset

    char = pick_character(topic_category, mode, title)
    asset = _find_asset(char, pick_expression(topic_category, mode, title, character=char))
    if asset is None:
        raise LayoutError(f"找不到角色素材（{char}）。")
    anchor, _ = _anchor_gaze(title)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, img in (("fb_card1.png", hook_card(hook, column=column, character_asset=asset, anchor=anchor,
                                               source_options=source_options)),
                      ("fb_card2.png", point_card(point, figure=figure))):
        path = out_dir / name
        img.save(path, "PNG", optimize=True)
        paths.append(path)
    return paths
