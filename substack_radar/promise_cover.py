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
SLOGAN = "每天兩篇對談延伸 · 每週一篇公司深拆"

# 2026-06-21 · Cover System D4: palette is now driven by config/cover_ip/cover_tokens.json
# — paper-cream ground ALWAYS, ink title ALWAYS, exactly ONE accent (single_sienna by
# default). This unifies the text-poster fallback with the character-cover system; the
# old per-bucket colored/dark-AI backgrounds violated the new "cream ground only, one
# sienna" brand rule. See cover_ip/COVER_SYSTEM.md §0/§D4/§D6-route-2.
_TOKENS_PATH = Path(__file__).resolve().parent / "config" / "cover_ip" / "cover_tokens.json"
# Readable warm-grey for subtitle/slogan — stone #8A8378 is meta-only (fails AA as body).
_MUTED_SUB = "#5A5247"
_FALLBACK_TOKENS: Dict = {
    "base": {"cream": {"hex": "#F2EEE5"}, "ink": {"hex": "#141414"}, "sienna": {"hex": "#C84A32"}},
    "category_accent": {"_default_mode": "single_sienna", "tokens": {}},
}
_tokens_cache: Dict | None = None


def _load_cover_tokens() -> Dict:
    global _tokens_cache
    if _tokens_cache is None:
        try:
            import json
            _tokens_cache = json.loads(_TOKENS_PATH.read_text(encoding="utf-8"))
        except Exception:
            _tokens_cache = _FALLBACK_TOKENS
    return _tokens_cache


def _hx(s: str) -> Tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def palette_for(topic_category: str) -> Dict[str, str]:
    """Cover System D4: cream ground + ink title + ONE accent. single_sienna by default;
    per-category accent only if the tokens set _default_mode away from single_sienna."""
    tok = _load_cover_tokens()
    base = tok["base"]
    cat = tok.get("category_accent", {}) or {}
    sienna = base["sienna"]["hex"]
    if (cat.get("_default_mode") or "single_sienna") == "single_sienna":
        acc = sienna
    else:
        acc = ((cat.get("tokens", {}) or {}).get((topic_category or "").strip(), {}) or {}).get("accent", sienna)
    return {"bg": base["cream"]["hex"], "ink": base["ink"]["hex"], "acc": acc, "sub": _MUTED_SUB}


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


# 收尾用的標點邊界：截斷時盡量收在這些字元後，讓縮短後的標題/副標仍語意完整、不殘句。
_BOUNDARY = "。！？!?，,、；;：:）)」』】》"


def _cap_lines(lines, n, draw, font, max_w):
    """把 wrap 後的行收進 ≤ n 行。沒超過 → 原樣；超過 → 只留前 n 行，並把第 n 行收在
    標點/詞界＋補「…」：保證語意完整、不硬切在字中間、也不溢出文字區（封面寧可少
    幾個字、不要殘句或爆框）。"""
    lines = list(lines)
    if len(lines) <= n:
        return lines
    kept = lines[:n]
    last = kept[-1]
    cut = -1
    for i, ch in enumerate(last):
        if ch in _BOUNDARY:
            cut = i
    base = (last[:cut + 1] if cut >= 2 else last).rstrip("，,、；;：:　 ")
    tail = base + "…"
    while len(base) > 1 and draw.textlength(tail, font=font) > max_w:
        base = base[:-1].rstrip("，,、；;：:　 ")
        tail = base + "…"
    return kept[:-1] + [tail]


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
    # 連最小字級都塞不下 3 行時 → 用最小字級＋邊界收尾「…」（不硬切字、不爆框溢到副標）。
    title_font = lines = line_h = None
    for pt in range(148, 68, -6):
        f = _font(FONT_TITLE_PATH, pt)
        ls = _wrap(d, title, f, max_w)
        lh = int(pt * 1.18)
        if len(ls) <= 3 and len(ls) * lh <= int(H * 0.50):
            title_font, lines, line_h = f, ls, lh
            break
    if lines is None:
        title_font, line_h = _font(FONT_TITLE_PATH, 70), int(70 * 1.18)
        lines = _cap_lines(_wrap(d, title, title_font, max_w), 3, d, title_font, max_w)
    y = 180
    for ln in lines:
        d.text((PAD, y), ln, font=title_font, fill=ink)
        y += line_h

    # Subtitle: anchored to a fixed bottom band (≤2 lines) so spacing is consistent.
    if subtitle:
        # 先縮字級盡量塞進 2 行；真的還超過才收在標點邊界＋「…」（不中途硬切）。
        sf = _font(FONT_SUBTITLE_PATH, 38)
        for spt in range(38, 29, -2):
            sf = _font(FONT_SUBTITLE_PATH, spt)
            if len(_wrap(d, subtitle, sf, max_w)) <= 2:
                break
        sy = H - 215
        for ln in _cap_lines(_wrap(d, subtitle, sf, max_w), 2, d, sf, max_w):
            d.text((PAD, sy), ln, font=sf, fill=sub)
            sy += 52

    # Bottom hairline + slogan.
    d.line((PAD, H - 72, W - PAD, H - 72), fill=ink, width=1)
    d.text((PAD, H - 56), SLOGAN, font=_font(FONT_BRAND_PATH, 27), fill=sub)

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "cover.png"
    img.save(out, "PNG", optimize=True)
    return out
