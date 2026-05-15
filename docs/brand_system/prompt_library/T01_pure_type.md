# T01 · Pure type prompt template

**When to use**: headline punchy ≤ 8 字 · 文章是 thesis · 沒有好 imagery anchor · default 80% 篇用此 template

**Specs**: HERO 240px / ACCENT 1-2 字 / IMAGERY none / TIME 3 min

---

## ChatGPT image (recommended)

```
Design a Substack cover at exactly 1456 × 816 pixels for a Chinese
investment newsletter called "主力爸爸我錯了" (English wordmark: NEWS RADAR).

Style: COLD-PRINT EDITORIAL — like a 1950s serious financial broadsheet,
not a startup deck, not a LinkedIn post. Flat 2D, no AI typical aesthetics.

STRICT BRAND CONSTRAINTS (binary, no exceptions):
- Background: warm off-white #F2EEE5 (NEVER pure white)
- Text: near-black #141414 (NEVER pure #000)
- Single accent: sienna red #C84A32, used ONLY on the hero phrase characters specified below
- NO gradients, NO drop shadows, NO 3D, NO glows
- NO cartoon people, NO faces, NO mascots, NO illustrated characters
- NO emoji, NO exclamation marks, NO decorative borders
- NO photo, NO illustration, NO icon — this is a TYPOGRAPHY-ONLY cover

LAYOUT (1456 × 816, 64px margins):

TOP BAR (top 64px):
- Left: "主力爸爸我錯了" in heavy serif (Noto Serif TC weight 700, 28px,
  letter-spacing 0.04em, color #141414)
- Right: "NEWS RADAR · Nº {{ISSUE_NUM}} · {{DATE}}" in JetBrains Mono
  (11px, letter-spacing 0.32em, UPPERCASE, color #141414, the word
  "RADAR" colored sienna red #C84A32)
- 2px solid #141414 horizontal rule directly below

HERO REGION (center-left, dominating ~50% of canvas):
- Chinese text "{{HERO_TEXT}}" in Noto Serif TC weight 900
- Size: 240px, leading 0.92, tracking -2%
- Color: Press Ink #141414
- The character(s) "{{HERO_ACCENT_CHAR}}" colored sienna red #C84A32
  (this is the SINGLE accent on the cover — no other red anywhere)

KICKER (above hero, 28px gap):
- Text "{{KICKER}}" in JetBrains Mono 13px UPPERCASE
- letter-spacing 0.22em, color Stone #8A8378

BOTTOM BAR (bottom 64px):
- 1px #141414 hairline rule above
- Left: "hsin73.substack.com" in JetBrains Mono 11px, color #8A8378
- Right: "{{CATEGORY}} / {{ISSUE_NUM}}" in JetBrains Mono 11px,
  color sienna #C84A32

NEGATIVE SPACE: ≥ 30% of canvas is empty paper #F2EEE5.

Thumbnail test: at 60×40 px, "{{HERO_TEXT}}" must remain readable.

Render flat 2D editorial print aesthetic — think 1960s Wall Street
Journal cover or 1980s Business Week front page. No 3D, no gradients,
no AI-style flourishes.
```

---

## NanoBanana / Stable Diffusion (fallback)

```
editorial print magazine cover, 1456x816, warm off-white paper background
#F2EEE5, single typography focus, large dense Chinese serif headline in
black, one character in sienna red #C84A32, masthead "主力爸爸我錯了" in
serif top-left, "NEWS RADAR" in monospace top-right, 2px black horizontal
rule under masthead, hairline rule above bottom bar with mono URL text,
1950s Wall Street Journal aesthetic, 1960s Business Week front page,
flat 2D, no gradient, no shadow, no 3D, no people, no face, no emoji,
no decorative border, generous negative space, --ar 16:9 --no people,
gradient, neon, 3d, anime, cartoon, illustration
```

---

## Midjourney (atmospheric only — not recommended for T01)

T01 純文字模板不適合 Midjourney（中文 typography 弱）。優先 ChatGPT image。

如果硬要用 Midjourney 出 atmospheric 背景再後製疊字：

```
1950s financial newspaper front page texture, warm off-white paper
#F2EEE5, ink black hairlines, no text, abstract editorial print
texture, flat 2D, vintage broadsheet, --ar 16:9 --style raw --v 6
```

→ 拿到無字背景 → Figma 疊「{{HERO_TEXT}}」hero + masthead + bottom bar 全部組件。

---

## v0.2 Sample · 「精美的廢話」

| 變數 | 值 |
|---|---|
| `{{HERO_TEXT}}` | 精美的廢話。 |
| `{{HERO_ACCENT_CHAR}}` | 廢話 |
| `{{KICKER}}` | CONTRARIAN · 決策 |
| `{{CATEGORY}}` | Decision |
| `{{ISSUE_NUM}}` | 023 |
| `{{DATE}}` | 2026·05·15 |
