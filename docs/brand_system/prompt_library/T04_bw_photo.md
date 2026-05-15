# T04 · B&W photograph + hero overlay prompt template

**When to use**: topic 有 atmospheric anchor · ≥ 1500 字 · Thesis / Essay category

**Specs**: PHOTO full bleed / HERO 236px overlay (paper) / TIME 4 min

**Rule**: 沒有 portrait、沒有人臉看鏡頭、沒有 face。

---

## ChatGPT image (recommended)

```
Design a Substack cover at exactly 1456 × 816 pixels for a Chinese
investment newsletter called "主力爸爸我錯了" (English wordmark: NEWS RADAR).

Style: COLD-PRINT EDITORIAL with ONE desaturated B&W photograph as
full-bleed background. Think 1960s magazine essay opening spread.

STRICT BRAND CONSTRAINTS:
- Background: full-bleed B&W desaturated photo of: {{IMAGERY_HINT}}
- Text overlay: warm off-white Cold Paper #F2EEE5 (against dark photo)
- Single accent: sienna red #C84A32, used ONCE only
- NO gradients ON the type (gradient on photo IS the desaturation)
- NO cartoon people, NO faces, NO portraits, NO eyes meeting camera
- NO emoji, NO decorative borders
- Photo aesthetic: high-contrast B&W, low-key, atmospheric, NOT cinematic
  CGI, NOT AI-generated faces

LAYOUT (1456 × 816, 64px margins):

PHOTO LAYER (full bleed under everything):
- Subject: {{IMAGERY_HINT}}
- Treatment: desaturated B&W, slight grain texture, dramatic light/shadow
- Optional: 80% opacity Press Ink #141414 wash on bottom half if photo
  is too busy where hero text sits

TOP BAR (over photo):
- Left: "主力爸爸我錯了" Noto Serif TC 700, 28px, color Cold Paper #F2EEE5
- Right: "NEWS RADAR · Nº {{ISSUE_NUM}} · {{DATE}}" JetBrains Mono 11px,
  UPPERCASE, color rgba(242,238,229,0.7), "RADAR" sienna #C84A32
- 2px solid Cold Paper #F2EEE5 horizontal rule below

HERO REGION (bottom-left, vertically centered in lower half):
- KICKER above: "{{KICKER}}" JetBrains Mono 13px UPPERCASE,
  color sienna #C84A32 (this is the single accent placement)
- HERO: Chinese "{{HERO_TEXT}}" Noto Serif TC weight 900, 236px,
  leading 0.95, color Cold Paper #F2EEE5
- The character "{{HERO_ACCENT_CHAR}}" stays Cold Paper color too
  (Sienna already used in kicker; one accent total)

BOTTOM BAR:
- 1px Cold Paper #F2EEE5 hairline rule above
- Left: "hsin73.substack.com" Mono 11px, rgba(242,238,229,0.7)
- Right: "{{CATEGORY}} / {{ISSUE_NUM}}" Mono 11px, sienna #C84A32

Photo source guideline: real B&W photography (Unsplash desaturated,
in-house, or stock). NEVER AI-generated faces or people. Acceptable
subjects: architecture, machinery, infrastructure, hands without faces,
shadows, smoke, mood landscapes.

Thumbnail test at 60×40 px: hero text "{{HERO_TEXT}}" must remain
readable against the photo (use ink wash if needed).
```

---

## NanoBanana / Stable Diffusion (fallback)

```
editorial magazine cover 1456x816, full bleed desaturated B&W photograph
of {{IMAGERY_HINT}}, low-key dramatic shadows, high contrast monochrome,
overlaid with cold paper white Chinese serif headline in heavy weight at
bottom left, sienna red #C84A32 kicker text in monospace above hero,
masthead "主力爸爸我錯了" serif top-left in white, NEWS RADAR mono top-right,
2px white rule under masthead, 1960s magazine essay opening spread aesthetic,
no faces no portraits no people looking at camera, atmospheric mood,
no gradient on type --ar 16:9 --no face, portrait, person looking at camera,
gradient overlay on type, neon, 3d, anime
```

---

## Midjourney (best fit T04 — atmospheric photos)

```
desaturated B&W cinematography still of {{IMAGERY_HINT}}, low-key lighting,
dramatic shadows, atmospheric mood, 1960s magazine essay aesthetic,
high contrast monochrome, no faces, no eye contact, architectural detail,
emotional weight, --ar 16:9 --style raw --v 6
```

→ 拿到 atmospheric photo → Figma 疊 masthead + hero + kicker + bottom bar。

---

## v0.2 Sample · 「學會閉嘴」（Peaky Blinders 講師篇）

| 變數 | 值 |
|---|---|
| `{{HERO_TEXT}}` | 學會閉嘴。 |
| `{{HERO_ACCENT_CHAR}}` | 閉嘴 |
| `{{KICKER}}` | ESSAY · 個人 |
| `{{CATEGORY}}` | Essay |
| `{{ISSUE_NUM}}` | 011 |
| `{{DATE}}` | 2026·05·15 |
| `{{IMAGERY_HINT}}` | a single B&W still of empty pub interior with smoke, 1920s English bar atmosphere, low-key lighting, no people, just texture and shadow (Peaky Blinders mood) |
