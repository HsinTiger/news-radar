# T06 · Scene / stage prompt template

**When to use**: 敘事性開場場景（凌晨一點 / 觀眾席 / 廚房 / 劇場 / 書房）

**Specs**: SCENE top 280px / HERO 112px / TIME 5 min

---

## ChatGPT image (recommended)

```
Design a Substack cover at exactly 1456 × 816 pixels for a Chinese
investment newsletter called "主力爸爸我錯了" (English wordmark: NEWS RADAR).

Style: COLD-PRINT EDITORIAL with one minimalist scene illustration as
upper visual anchor. Like a 1960s essay opening with a single
atmospheric vignette.

STRICT BRAND CONSTRAINTS:
- Background: warm off-white #F2EEE5 (NEVER pure white)
- Scene + text: near-black #141414 (NEVER pure #000)
- Single accent: sienna red #C84A32, used ONCE only
- NO gradients, NO 3D, NO glows
- NO cartoon people (silhouettes from behind / partial figures OK if minimal)
- NO faces visible, NO eye contact, NO mascots
- NO emoji, NO decorative borders

LAYOUT (1456 × 816, 64px margins):

TOP BAR:
- Left: "主力爸爸我錯了" Noto Serif TC 700, 28px, #141414
- Right: "NEWS RADAR · Nº {{ISSUE_NUM}} · {{DATE}}" Mono 11px UPPERCASE,
  "RADAR" sienna #C84A32
- 2px #141414 rule below

SCENE REGION (height 280px, just under top bar):
- A single minimalist editorial illustration depicting: {{IMAGERY_HINT}}
- Drawing style: thin black line art (#141414, 2-3px stroke), large
  negative space, vintage etching aesthetic
- Sparse fill — most of the scene is empty paper
- ONE small element in the scene can be sienna red #C84A32 (typically
  a focal anchor like a single light source / a labeled object) — this
  is the single accent placement IF AND ONLY IF the hero accent is not used

HERO REGION (bottom area, left-aligned):
- KICKER: "{{KICKER}}" Mono 13px UPPERCASE, color #8A8378
- HERO: Chinese "{{HERO_TEXT}}" Noto Serif TC 900, 112px, leading 0.95,
  color #141414
- The characters "{{HERO_ACCENT_CHAR}}" colored sienna red #C84A32
  (if scene already has Sienna, hero stays all black; one Sienna total)

BOTTOM BAR:
- 1px #141414 hairline rule above
- Left: "hsin73.substack.com" Mono 11px, #8A8378
- Right: "{{CATEGORY}} / {{ISSUE_NUM}}" Mono 11px, sienna #C84A32

Scene aesthetic: like a vintage scientific illustration plate or a
1960s New Yorker single-line etching. NEVER cartoon, NEVER cute,
NEVER full-color landscape. Sparse, restrained, atmospheric.

Thumbnail test at 60×40 px: hero text readable AND the scene silhouette
recognizable.
```

---

## NanoBanana / Stable Diffusion (fallback)

```
editorial print magazine cover 1456x816, warm off-white paper #F2EEE5,
upper area features a minimalist editorial line illustration of
{{IMAGERY_HINT}}, thin black line art on cream paper, vintage etching
aesthetic, vintage scientific illustration plate, large negative space,
sparse composition, one small sienna red #C84A32 focal element only,
bottom features Chinese serif headline in heavy black weight, masthead
"主力爸爸我錯了" serif top-left, NEWS RADAR mono top-right, 1960s
New Yorker single-line etching aesthetic, no full color, no people
faces, no cartoon --ar 16:9 --no face, portrait, gradient, neon, 3d,
anime, cartoon, full color landscape
```

---

## Midjourney (good fit T06 — vintage etching)

```
vintage scientific illustration plate of {{IMAGERY_HINT}}, thin black
line art on cream paper, sparse composition, 1960s New Yorker single
line etching style, atmospheric editorial vignette, no faces, no people,
no full color, large negative space, --ar 16:9 --style raw --v 6
```

→ 拿到 etching 圖 → Figma 疊 masthead + hero + kicker + bottom bar。

---

## v0.2 Sample · 「主角會死，配角會富」

| 變數 | 值 |
|---|---|
| `{{HERO_TEXT}}` | 主角會死，配角會富。 |
| `{{HERO_ACCENT_CHAR}}` | 會富 |
| `{{KICKER}}` | THESIS · 投資 |
| `{{CATEGORY}}` | Thesis |
| `{{ISSUE_NUM}}` | 042 |
| `{{DATE}}` | 2026·05·15 |
| `{{IMAGERY_HINT}}` | a wide horizontal vignette of an empty theater interior, single distant illuminated stage in faded ochre wash on the right (no actors), foreground left features one open leather notebook on a velvet seat with a fountain pen, handwritten margin notes "Schloss 47y" "Munger 99y" "Caro 50y" visible, no humans, no faces, sparse line art |
