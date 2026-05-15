# T03 · Data / chart prompt template

**When to use**: 文章核心是一張 chart · ≤ 2 series · time series or comparison

**Specs**: CHART top 55% / HERO bot 35% / 120-138px / TIME 5 min

**Rule**: chart must argue. 可被任何其他 chart 取代 = 砍掉走 T01。

---

## ChatGPT image (recommended)

```
Design a Substack cover at exactly 1456 × 816 pixels for a Chinese
investment newsletter called "主力爸爸我錯了" (English wordmark: NEWS RADAR).

Style: COLD-PRINT EDITORIAL — 1950s financial broadsheet, like a Bloomberg
chart printed in a Wall Street Journal front page.

STRICT BRAND CONSTRAINTS:
- Background: warm off-white #F2EEE5 (NEVER pure white)
- Text + chart lines: near-black #141414 (NEVER pure #000)
- Chart accent + hero accent: sienna red #C84A32 (used ONCE total)
- NO gradients, NO 3D, NO glows
- NO cartoon people, NO emoji, NO decorative borders

LAYOUT (1456 × 816, 64px margins):

TOP BAR:
- Left: "主力爸爸我錯了" Noto Serif TC 700, 28px, #141414
- Right: "NEWS RADAR · Nº {{ISSUE_NUM}} · {{DATE}}" JetBrains Mono 11px,
  UPPERCASE, "RADAR" sienna #C84A32
- 2px #141414 rule below

CHART REGION (top 55% of canvas, in a Bone-toned panel):
- Background: Bone #E8E3D6 panel with 1px #141414 border
- A clean editorial chart showing: {{IMAGERY_HINT}}
- Chart type: bar chart OR line chart, 1-2 series ONLY
- Data labels and axis labels in JetBrains Mono 12-14px, color #141414
- Source attribution top-right: "Source: [name]" in Mono 11px, #8A8378
- Caption above chart: "{{KICKER}}" in Mono 13px UPPERCASE, #8A8378
- The most important data point colored sienna red #C84A32
  (this is the SINGLE accent — hero text stays all black)

HERO REGION (bottom 35%, right-aligned):
- Chinese "{{HERO_TEXT}}" Noto Serif TC weight 900, 138px, leading 0.95
- All text color: Press Ink #141414 (no Sienna here, accent is in chart)
- KICKER above hero already covered by chart caption — skip duplicate

BOTTOM BAR:
- 1px #141414 hairline rule above
- Left: "hsin73.substack.com" Mono 11px, #8A8378
- Right: "{{CATEGORY}} / {{ISSUE_NUM}}" Mono 11px, sienna #C84A32

Chart aesthetic: clean editorial like FT or Economist, NOT 3D, NOT
gradient-filled. Flat fills, hairline strokes, mono labels.

Thumbnail test at 60×40 px: hero text readable AND chart shape recognizable
(at minimum the bar/line silhouette).
```

---

## NanoBanana / Stable Diffusion (fallback)

```
editorial print magazine cover 1456x816, warm off-white paper #F2EEE5,
top half features a clean black bar chart with {{IMAGERY_HINT}}, one bar
highlighted in sienna red #C84A32, mono labels in JetBrains Mono style,
chart in a bone-toned panel #E8E3D6 with thin black border, bottom right
features Chinese serif headline in heavy black weight, masthead "主力爸爸我錯了"
serif top-left, NEWS RADAR mono top-right, FT or Economist editorial
chart aesthetic, flat 2D no gradient no shadow no 3D no people --ar 16:9
--no gradient, neon, 3d, anime, cartoon, photo
```

---

## Midjourney

不建議 — Midjourney chart rendering 不可控。用 ChatGPT image 或拿 chart screenshot 後製。

替代：在 Excel / Datawrapper / Observable 做 chart → screenshot → Figma 套 masthead + hero。

---

## v0.2 Sample · 「9% → 34.4% Anthropic」

| 變數 | 值 |
|---|---|
| `{{HERO_TEXT}}` | 9% → 34.4% |
| `{{HERO_ACCENT_CHAR}}` | 34.4% |
| `{{KICKER}}` | MARKET · AI 採用 |
| `{{CATEGORY}}` | AI |
| `{{ISSUE_NUM}}` | 047 |
| `{{DATE}}` | 2026·05·15 |
| `{{IMAGERY_HINT}}` | bar chart comparing enterprise AI adoption rate "Q1 2024: 9%" vs "Q1 2026: 34.4%", two vertical bars, the 34.4% bar in sienna red #C84A32, x-axis labels "2024 Q1" and "2026 Q1", y-axis 0-40% |
