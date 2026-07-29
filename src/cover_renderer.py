"""News Radar · Cover image renderer (Phase 9.5 brand-unification).

Takes a downloaded news image + draft metadata and produces a unified-brand
cover: dark overlay → big bold title → topic chip → brand bar.

Visual spec source of truth: ``docs/brand_visual.md`` (Hsin-signed
2026-05-01). When you change a number in this module, update the spec doc
in the same commit.

Why this lives at top-level (not inside ``src/composer/``)
----------------------------------------------------------
``src/composer.py`` is the legacy MultiPlatformDraft producer; making a
package directory of the same name shadows it and breaks every caller.
Cover rendering is image-pipeline work anyway, so this module sits
alongside ``image_prep.py`` / ``image_manager.py`` which already own the
image lane.

Public API
----------
    inp = CoverInput(
        image_path=Path("..."),
        title="OpenAI 把垂直 AI 鎖進企業圍牆",
        subtitle="Rosalind 的二度被剝奪",   # optional
        topic_category="ai_model",
        brand_name="smartmmmoney",         # or "主力爸爸我錯了" for FB
        date_str="2026/05/01",
    )
    ig_path = render_cover(inp, aspect="ig")
    fb_path = render_cover(inp, aspect="fb")

Inputs
------
- ``image_path``: a local file (use ``image_manager.download_image`` to
  cache the original news image first). Cover renderer never reaches the
  network.
- ``title``: main hook, expected ≤ 24 Chinese chars for best layout.
  Renderer will auto-shrink + line-wrap up to 3 lines, beyond that the
  output starts to look cramped — composer should pre-trim.
- ``subtitle``: optional secondary line. Pass ``None`` or empty to skip.
- ``topic_category``: one of the keys in ``TOPIC_CHIP_COLORS``. Unknown
  categories fall back to gray + raw category string as label.
- ``brand_name``: literal string rendered in the brand bar.
- ``date_str``: pre-formatted, no parsing here.

Outputs
-------
PNG written to ``assets/cover_cache/{stem}_{ig_4x5|fb_1x1}.png``. Returns
the absolute Path. Idempotent: same input yields same output bytes.

Fonts
-----
Adobe Source Han Sans/Serif TC at ``assets/fonts/`` — see
``docs/brand_visual.md`` for download links. If the font files are
missing, renderer falls back to PIL's default font (latin-only, mostly
useful for testing the layout structure without rendering CJK glyphs
correctly).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PROJECT_ROOT / "assets"
FONT_DIR = ASSETS_DIR / "fonts"
COVER_CACHE_DIR = ASSETS_DIR / "cover_cache"

FONT_TITLE_PATH = FONT_DIR / "SourceHanSansTC-Bold.otf"
FONT_SUBTITLE_PATH = FONT_DIR / "SourceHanSerifTC-Light.otf"
FONT_BRAND_PATH = FONT_DIR / "SourceHanSansTC-Regular.otf"

# ---------------------------------------------------------------------------
# Visual constants — all values mirror docs/brand_visual.md
# ---------------------------------------------------------------------------

# Per-aspect output specs
# Phase 9.5 / 2026-05-02 update: Threads added per Hsin's brand-consistency
# call. Same 4:5 aspect as IG (Threads feed handles tall images well).
SPECS: Dict[str, Dict] = {
    "ig":       {"size": (1080, 1350), "suffix": "ig_4x5"},
    "fb":       {"size": (1080, 1080), "suffix": "fb_1x1"},
    "threads":  {"size": (1080, 1350), "suffix": "threads_4x5"},
    # 2026-05-12 — Substack hero (preview/list/email). 1456×816 is the
    # exact dimension Substack renders post previews at; smaller images
    # get upscaled (blurry) and taller images get center-cropped (head
    # cut off). 16:9-ish so it works as Substack email-to-draft hero too.
    "substack": {"size": (1456, 816),  "suffix": "substack_hero"},
}

# Background processing
BLUR_RADIUS_PX = 10
OVERLAY_RGB = (10, 14, 29)  # deep navy

# Topic-chip color map (RGB). Keys MUST match category_ids in
# src/topic_taxonomy.py. Adding a new category there requires adding a
# row here AND in TOPIC_CHIP_LABELS below; a missing key falls back to
# gray + raw English category text (visually obvious failure mode).
#
# Color assignment groups by family:
#   AI cluster    → purple shades
#   Industrial    → blue / teal
#   Money         → green / amber / pink
#   Other         → gray
TOPIC_CHIP_COLORS: Dict[str, Tuple[int, int, int]] = {
    "ai_model":            (127, 119, 221),  # purple — AI base model
    "ai_agent":            (107, 93, 213),   # deeper purple — autonomous AI
    "ai_application":      (157, 148, 232),  # lighter purple — AI app layer
    "supply_chain":        (55, 138, 221),   # blue — industrial chain
    "earnings":            (99, 153, 34),    # green — financials
    "tw_stocks":           (226, 75, 74),    # warm red — market-alert punch (2026-05-02 升級)
    "us_stocks":           (239, 159, 39),   # vivid orange — market-alert punch
    "tech_product_launch": (43, 179, 155),   # teal — non-AI tech launch
    "policy_geopolitics":  (136, 135, 128),  # gray — policy
    "current_affairs":     (200, 74, 50),    # sienna — public impact
    "tw_politics":         (200, 74, 50),    # sienna — accountability
    "food_safety":         (173, 92, 45),    # rust — consumer safety
    "military_defense":    (73, 92, 75),     # olive — national security
    "other":               (136, 135, 128),  # gray — fallback
}

# Topic-chip Chinese labels — short form for the 86×24px chip.
# Full display_name is in topic_taxonomy.py for week-report / DocsUI.
TOPIC_CHIP_LABELS: Dict[str, str] = {
    "ai_model":            "AI 模型",
    "ai_agent":            "AI Agent",
    "ai_application":      "AI 應用",
    "supply_chain":        "產業鏈",
    "earnings":            "財報",
    "tw_stocks":           "台股",
    "us_stocks":           "美股",
    "tech_product_launch": "科技新品",
    "policy_geopolitics":  "政策",
    "current_affairs":     "時事",
    "tw_politics":         "政府監督",
    "food_safety":         "食安",
    "military_defense":    "國防",
    "other":               "其它",
}

# Topic-chip layout
CHIP_PAD_LEFT = 60
CHIP_PAD_TOP = 60
CHIP_W = 280
CHIP_H = 80
CHIP_RADIUS = 12
CHIP_FONT_PT = 38

# Title typography
TITLE_LARGE_PT = 95   # used when title ≤ 16 chars
TITLE_MEDIUM_PT = 80  # used when 17–24 chars
TITLE_SMALL_PT = 65   # used when ≥ 25 chars
TITLE_CHAR_BUDGET_LARGE = 16
TITLE_CHAR_BUDGET_MEDIUM = 24
TITLE_LINE_HEIGHT_RATIO = 1.25
TITLE_MAX_LINES = 3
TITLE_HORIZONTAL_PAD = 100  # px on each side
TITLE_VERTICAL_OFFSET = -50  # negative = above true vertical center

# Subtitle typography
# 2026-05-02 升級：從 48pt → 58pt。原本 48 跟 title 95pt 差 ~2x 太懸殊、
# 副標看起來像可有可無。58pt 跟 title 1.6x 比例符合 typographic best practice。
SUBTITLE_PT = 58
SUBTITLE_MIN_PT = 34          # 2026-05-30: adaptive floor before ellipsizing
SUBTITLE_MAX_LINES = 2        # subtitle wraps to at most 2 lines (no more clipping)
SUBTITLE_GAP_FROM_TITLE = 30  # px below title block

# Brand-bar layout
BRAND_BAR_BOTTOM_OFFSET = 80  # distance of hairline from bottom edge
BRAND_BAR_HORIZONTAL_PAD = 60
BRAND_BAR_FONT_PT = 28
BRAND_BAR_TEXT_GAP = 22  # px between hairline and text

# Drop-shadow for title (hairline letter outline; not "shadow effect")
TITLE_SHADOW_OFFSET = 2
TITLE_SHADOW_ALPHA = 160  # ~0.63

# Dynamic-overlay alpha thresholds
# 2026-05-02 升級：整體下調約 10 個百分點。原本 65% default 太重、原圖細節
# 被吃掉 90%，犧牲了「真的有事在發生」的紀實感。新值讓原圖透出來更多。
# 字體可讀性靠 title shadow（已有）+ blur 10px 維持。
LUMINANCE_BRIGHT = 160
LUMINANCE_DARK = 90
ALPHA_BRIGHT = 153   # 0.60 (was 0.70)
ALPHA_DEFAULT = 140  # 0.55 (was 0.65) — 預設值
ALPHA_DARK = 115     # 0.45 (was 0.55)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CoverInput:
    """Pure input for ``render_cover``. No I/O happens here."""
    image_path: Path
    title: str
    subtitle: Optional[str]
    topic_category: str
    brand_name: str
    date_str: str


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def compute_overlay_alpha(image: Image.Image) -> int:
    """Tune overlay alpha to image luminance.

    Brighter image → heavier overlay (title needs to fight more contrast).
    Darker image → lighter overlay (don't flatten an already-dark image).

    Returns an int in [140, 178] — the 0.55-0.70 band documented in
    ``docs/brand_visual.md``.
    """
    stat = ImageStat.Stat(image.convert("L"))
    luminance = stat.mean[0]
    if luminance > LUMINANCE_BRIGHT:
        return ALPHA_BRIGHT
    if luminance < LUMINANCE_DARK:
        return ALPHA_DARK
    return ALPHA_DEFAULT


def _crop_to_aspect(img: Image.Image, target_size: Tuple[int, int]) -> Image.Image:
    """Center-crop and resize to ``target_size`` (object-fit: cover semantics)."""
    tw, th = target_size
    sw, sh = img.size
    target_ratio = tw / th
    src_ratio = sw / sh
    if src_ratio > target_ratio:
        # source too wide — crop sides
        new_w = int(sh * target_ratio)
        x = (sw - new_w) // 2
        img = img.crop((x, 0, x + new_w, sh))
    elif src_ratio < target_ratio:
        # source too tall — crop top/bottom
        new_h = int(sw / target_ratio)
        y = (sh - new_h) // 2
        img = img.crop((0, y, sw, y + new_h))
    return img.resize(target_size, getattr(Image, "Resampling", Image).LANCZOS)


def _pick_title_font_pt(title: str) -> int:
    """Choose title font size from char count."""
    n = len(title)
    if n <= TITLE_CHAR_BUDGET_LARGE:
        return TITLE_LARGE_PT
    if n <= TITLE_CHAR_BUDGET_MEDIUM:
        return TITLE_MEDIUM_PT
    return TITLE_SMALL_PT


def _wrap_chinese_title(
    draw: ImageDraw.ImageDraw,
    title: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int = TITLE_MAX_LINES,
) -> List[str]:
    """Greedy line-wrap by character (no word boundaries in CJK).

    Returns at most ``max_lines`` lines. If the input doesn't fit, the
    final line gets the remainder + ellipsis so it's visually obvious
    something was cut. Composer should ideally pre-trim long titles —
    this is graceful-degradation, not the primary path.
    """
    if not title:
        return []

    lines: List[str] = []
    current = ""
    for i, ch in enumerate(title):
        candidate = current + ch
        bbox = draw.textbbox((0, 0), candidate, font=font)
        w = bbox[2] - bbox[0]
        if w > max_width and current:
            lines.append(current)
            current = ch
            if len(lines) == max_lines - 1:
                # Last line gets the rest; ellipsize if it overflows again.
                rest = title[i:]
                bbox_rest = draw.textbbox((0, 0), rest, font=font)
                if bbox_rest[2] - bbox_rest[0] > max_width:
                    # Trim from the right and append ellipsis.
                    while rest and (
                        draw.textbbox((0, 0), rest + "…", font=font)[2] > max_width
                    ):
                        rest = rest[:-1]
                    rest = rest + "…" if rest else "…"
                lines.append(rest)
                return lines
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:max_lines]


def _load_font(path: Path, pt: int) -> ImageFont.ImageFont:
    """Load the branded font or a CJK-capable host font.

    Clean workflow clones may not contain the optional font bundle.  Rendering
    tofu boxes is not an acceptable production fallback, so use a known
    Traditional-Chinese system face before falling back to PIL's bitmap font.
    """
    if path.exists():
        return ImageFont.truetype(str(path), pt)
    bold = "bold" in path.name.casefold()
    candidates = [
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/mingliu.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), pt)
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Drawing primitives
# ---------------------------------------------------------------------------

def _draw_topic_chip(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    topic_category: str,
) -> None:
    """Top-left rounded-rect chip with topic label."""
    color = TOPIC_CHIP_COLORS.get(topic_category, (136, 135, 128))
    label = TOPIC_CHIP_LABELS.get(topic_category, topic_category)

    box = (
        CHIP_PAD_LEFT,
        CHIP_PAD_TOP,
        CHIP_PAD_LEFT + CHIP_W,
        CHIP_PAD_TOP + CHIP_H,
    )
    draw.rounded_rectangle(box, radius=CHIP_RADIUS, fill=color + (255,))

    chip_font = _load_font(FONT_TITLE_PATH, CHIP_FONT_PT)
    bbox = draw.textbbox((0, 0), label, font=chip_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    # PIL's textbbox top is the ascender baseline; nudge up a few px so
    # the label sits visually centered.
    cx = CHIP_PAD_LEFT + (CHIP_W - tw) // 2
    cy = CHIP_PAD_TOP + (CHIP_H - th) // 2 - 8
    draw.text((cx, cy), label, fill=(255, 255, 255, 255), font=chip_font)


def _draw_title_block(
    draw: ImageDraw.ImageDraw,
    title: str,
    canvas_size: Tuple[int, int],
) -> Tuple[int, int]:
    """Render the multi-line title centered. Returns (top_y, total_h)."""
    pt = _pick_title_font_pt(title)
    font = _load_font(FONT_TITLE_PATH, pt)
    max_text_width = canvas_size[0] - 2 * TITLE_HORIZONTAL_PAD
    lines = _wrap_chinese_title(draw, title, font, max_text_width)
    line_h = int(pt * TITLE_LINE_HEIGHT_RATIO)
    total_h = len(lines) * line_h
    top_y = (canvas_size[1] - total_h) // 2 + TITLE_VERTICAL_OFFSET

    cx = canvas_size[0] // 2
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        x = cx - line_w // 2
        y = top_y + i * line_h
        # Hairline letter shadow for legibility against blurred backgrounds.
        draw.text(
            (x + TITLE_SHADOW_OFFSET, y + TITLE_SHADOW_OFFSET),
            line,
            fill=(0, 0, 0, TITLE_SHADOW_ALPHA),
            font=font,
        )
        draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)

    return top_y, total_h


def _draw_subtitle(
    draw: ImageDraw.ImageDraw,
    subtitle: str,
    canvas_size: Tuple[int, int],
    title_top: int,
    title_h: int,
) -> None:
    """Render subtitle below the title, WRAPPED + adaptively shrunk so it never
    clips.

    2026-05-30 fix: the old version drew the whole subtitle as ONE centered line
    with no width check — a long subtitle made ``x = (W - tw)//2`` negative, so the
    text started off the left edge and ran off the right (both sides clipped). Now
    we shrink the font (58→34pt) until it fits ``SUBTITLE_MAX_LINES`` within the
    safe horizontal margins, wrap by character, and only ellipsize as a last
    resort. ``x`` is clamped so it can never go negative."""
    if not subtitle:
        return
    max_width = canvas_size[0] - 2 * TITLE_HORIZONTAL_PAD

    # Fallback = smallest size (ellipsized if even that overflows).
    chosen_pt = SUBTITLE_MIN_PT
    font = _load_font(FONT_SUBTITLE_PATH, chosen_pt)
    lines = _wrap_chinese_title(draw, subtitle, font, max_width, max_lines=SUBTITLE_MAX_LINES)
    # Prefer the largest size that fits WITHOUT needing an ellipsis.
    for pt in range(SUBTITLE_PT, SUBTITLE_MIN_PT - 1, -6):
        f = _load_font(FONT_SUBTITLE_PATH, pt)
        wrapped = _wrap_chinese_title(draw, subtitle, f, max_width, max_lines=SUBTITLE_MAX_LINES)
        if wrapped and not wrapped[-1].endswith("…"):
            chosen_pt, font, lines = pt, f, wrapped
            break

    line_h = int(chosen_pt * 1.35)
    y = title_top + title_h + SUBTITLE_GAP_FROM_TITLE
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font)
        w = bbox[2] - bbox[0]
        x = max((canvas_size[0] - w) // 2, TITLE_HORIZONTAL_PAD)  # never negative
        draw.text((x, y), ln, fill=(255, 255, 255, 191), font=font)  # 0.75 alpha
        y += line_h


def _draw_brand_bar(
    draw: ImageDraw.ImageDraw,
    canvas_size: Tuple[int, int],
    brand_name: str,
    date_str: str,
) -> None:
    """Bottom hairline + brand text."""
    bar_y = canvas_size[1] - BRAND_BAR_BOTTOM_OFFSET
    draw.line(
        (
            (BRAND_BAR_HORIZONTAL_PAD, bar_y),
            (canvas_size[0] - BRAND_BAR_HORIZONTAL_PAD, bar_y),
        ),
        fill=(255, 255, 255, 51),  # 0.20 alpha
        width=1,
    )
    font = _load_font(FONT_BRAND_PATH, BRAND_BAR_FONT_PT)
    text = f"{brand_name} · {date_str}"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (canvas_size[0] - tw) // 2
    y = bar_y + BRAND_BAR_TEXT_GAP
    draw.text((x, y), text, fill=(255, 255, 255, 166), font=font)  # 0.65 alpha


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_cover(
    inp: CoverInput,
    aspect: Literal["ig", "fb", "threads", "substack"],
    *,
    output_dir: Optional[Path] = None,
) -> Path:
    """Render one cover image. Returns saved PNG path.

    Process order (mirrors brand_visual.md §Background composition):
      1. Open + center-crop + resize to aspect target.
      2. Gaussian blur.
      3. Compute dynamic overlay alpha from blurred-image luminance.
      4. Composite navy overlay on top.
      5. Draw topic chip → title → subtitle → brand bar.
      6. Write PNG to ``assets/cover_cache/`` (or ``output_dir`` if given).
    """
    if aspect not in SPECS:
        raise ValueError(f"aspect must be one of {list(SPECS)}, got {aspect!r}")
    spec = SPECS[aspect]
    target_size = spec["size"]

    # 1) Background image: crop + resize
    src = Image.open(inp.image_path).convert("RGB")
    base = _crop_to_aspect(src, target_size)

    # 2) Blur
    base = base.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS_PX))

    # 3 + 4) Overlay
    alpha = compute_overlay_alpha(base)
    overlay = Image.new("RGBA", target_size, OVERLAY_RGB + (alpha,))
    canvas = Image.alpha_composite(base.convert("RGBA"), overlay)

    # 5) Text + chip
    draw = ImageDraw.Draw(canvas, "RGBA")
    _draw_topic_chip(canvas, draw, inp.topic_category)
    title_top, title_h = _draw_title_block(draw, inp.title, target_size)
    _draw_subtitle(draw, inp.subtitle or "", target_size, title_top, title_h)
    _draw_brand_bar(draw, target_size, inp.brand_name, inp.date_str)

    # 6) Save
    out_dir = output_dir or COVER_CACHE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{inp.image_path.stem}_{spec['suffix']}.png"
    out_path = out_dir / fname
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def render_cover_pair(inp: CoverInput, *, output_dir: Optional[Path] = None) -> Dict[str, Path]:
    """Convenience: render both IG (4:5) and FB (1:1) versions in one call.

    Returns ``{"ig": Path, "fb": Path}``. Threads is intentionally NOT
    rendered — Threads strategy is text-first per ``docs/brand_visual.md``.
    """
    return {
        "ig": render_cover(inp, "ig", output_dir=output_dir),
        "fb": render_cover(inp, "fb", output_dir=output_dir),
    }
