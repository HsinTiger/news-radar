# T05 · Diagram (對照圖) prompt template

**When to use**: 文章核心是一個 reframing（你以為 vs 真的 / before vs after / 直覺 vs 事實）

**Specs**: DIAGRAM top 280px / HERO 112px / 中央分隔 80px / TIME 4 min

---

## ChatGPT image (recommended)

```
Design a Substack cover at exactly 1456 × 816 pixels for a Chinese
investment newsletter called "主力爸爸我錯了" (English wordmark: NEWS RADAR).

Style: COLD-PRINT EDITORIAL with a binary comparison diagram. Like a
Wittgenstein-style two-column logical contrast in a 1960s academic journal.

STRICT BRAND CONSTRAINTS:
- Background: warm off-white #F2EEE5 (NEVER pure white)
- Left column: Cold Paper #F2EEE5 background, Press Ink #141414 text
- Right column: Press Ink #141414 background, Cold Paper #F2EEE5 text
  (inverted, signals the "truth/contrarian" side)
- Center divider: Bone #E8E3D6 80px wide vertical strip with mono "VS"
- Single accent: sienna red #C84A32 used ONCE (typically the right column
  TRUTH label or the hero accent character)
- NO gradients, NO 3D, NO glows
- NO cartoon people, NO emoji, NO decorative borders

LAYOUT (1456 × 816, 64px margins):

TOP BAR:
- Left: "主力爸爸我錯了" Noto Serif TC 700, 28px, #141414
- Right: "NEWS RADAR · Nº {{ISSUE_NUM}} · {{DATE}}" Mono 11px UPPERCASE,
  "RADAR" sienna #C84A32
- 2px #141414 rule below

DIAGRAM REGION (height 280px, just under top bar, with 1px #141414 border):

  Three-column grid (1fr 80px 1fr):

  LEFT column (Cold Paper bg):
  - Top label: "直覺 · INTUITION" Mono 13px UPPERCASE, color Stone #8A8378
  - Big text below: "{{LEFT_LABEL}}" Noto Serif TC 900, 54px, line-height 1.02
  - Bullet list at bottom: "{{LEFT_BULLETS}}" Sans 18px, color Ink-2 #2A2724

  CENTER divider (Bone #E8E3D6, 80px wide):
  - "VS" in JetBrains Mono 13px, letter-spacing 0.22em, color Stone #8A8378,
    text vertically rotated (writing-mode: vertical-rl), centered

  RIGHT column (Press Ink bg, #141414):
  - Top label: "事實 · TRUTH" Mono 13px UPPERCASE, color sienna #C84A32
    (this is the single accent placement)
  - Big text below: "{{RIGHT_LABEL}}" Noto Serif TC 900, 54px,
    color Cold Paper #F2EEE5

HERO REGION (bottom of diagram, left-aligned):
- KICKER: "{{KICKER}}" Mono 13px UPPERCASE, color #8A8378
- HERO: Chinese "{{HERO_TEXT}}" Noto Serif TC 900, 112px, leading 0.95,
  color #141414
- The characters "{{HERO_ACCENT_CHAR}}" stay #141414 (accent already
  used in TRUTH label)

BOTTOM BAR:
- 1px #141414 hairline rule above
- Left: "hsin73.substack.com" Mono 11px, #8A8378
- Right: "{{CATEGORY}} / {{ISSUE_NUM}}" Mono 11px, sienna #C84A32

Diagram aesthetic: 1960s academic journal binary contrast plate, NEVER
infographic-y, NEVER colorful, just black ink and inverted block.

Thumbnail test at 60×40 px: hero text readable AND the binary contrast
shape (white/black split) recognizable.
```

---

## NanoBanana / Stable Diffusion (fallback)

```
editorial print magazine cover 1456x816, warm off-white paper #F2EEE5,
top section features a binary contrast diagram split into two columns,
left column white background with serif Chinese text "{{LEFT_LABEL}}"
in black, right column inverted with black background and white serif
Chinese text "{{RIGHT_LABEL}}", center divider with "VS" in monospace,
border around the whole diagram in thin black, bottom features Chinese
serif headline in heavy black weight, masthead "主力爸爸我錯了" serif
top-left, NEWS RADAR mono top-right, 1960s academic journal binary
contrast plate aesthetic, flat 2D no gradient no shadow --ar 16:9
```

---

## Midjourney

不建議 — diagram 內 layout 不可控。Midjourney 適合 atmospheric (T04)、不適合 structured (T05)。用 ChatGPT image 或 Figma 純手刻。

替代：Figma 直接做這個 diagram、5 分鐘搞定（最 clean）。

---

## v0.2 Sample · 「學最多，最先出局」

| 變數 | 值 |
|---|---|
| `{{HERO_TEXT}}` | 學最多，最先出局。 |
| `{{HERO_ACCENT_CHAR}}` | 出局 |
| `{{KICKER}}` | CONTRARIAN · 學習 |
| `{{CATEGORY}}` | Learning |
| `{{ISSUE_NUM}}` | 034 |
| `{{DATE}}` | 2026·05·15 |
| `{{LEFT_LABEL}}` | 名詞通膨 |
| `{{LEFT_BULLETS}}` | · RAG / Agent / MCP<br>· 易折舊<br>· 履歷 checklist |
| `{{RIGHT_LABEL}}` | 動詞複利 |
