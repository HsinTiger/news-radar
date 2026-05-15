# T02 · Single object + hero prompt template

**When to use**: topic 有自然單一物件 anchor（沙漏 / 晶片 / 咖啡杯 / 樹）

**Specs**: HERO 160px / OBJECT 380×480px / TIME 4 min

---

## ChatGPT image (recommended)

```
Design a Substack cover at exactly 1456 × 816 pixels for a Chinese
investment newsletter called "主力爸爸我錯了" (English wordmark: NEWS RADAR).

Style: COLD-PRINT EDITORIAL — like a 1950s serious financial broadsheet.
Flat 2D vector illustration, no AI typical aesthetics.

STRICT BRAND CONSTRAINTS:
- Background: warm off-white #F2EEE5 (NEVER pure white)
- Text + line art: near-black #141414 (NEVER pure #000)
- Single accent: sienna red #C84A32, used ONCE only
- NO gradients, NO drop shadows, NO 3D, NO glows, NO photo realism
- NO cartoon people, NO faces, NO mascots
- NO emoji, NO exclamation marks, NO decorative borders

LAYOUT (1456 × 816, 64px margins):

TOP BAR:
- Left: "主力爸爸我錯了" Noto Serif TC 700, 28px, color #141414
- Right: "NEWS RADAR · Nº {{ISSUE_NUM}} · {{DATE}}" JetBrains Mono 11px,
  letter-spacing 0.32em UPPERCASE, "RADAR" colored sienna #C84A32
- 2px solid #141414 horizontal rule below

LEFT HERO REGION (occupies left 60% of canvas, vertically centered):
- KICKER above: "{{KICKER}}" in JetBrains Mono 13px UPPERCASE,
  letter-spacing 0.22em, color #8A8378
- HERO: Chinese "{{HERO_TEXT}}" in Noto Serif TC weight 900, 160px,
  leading 0.95, color #141414
- The characters "{{HERO_ACCENT_CHAR}}" colored sienna red #C84A32
  (this is the SINGLE accent placement)

RIGHT OBJECT REGION (right 380×480px area, vertically centered):
- A SINGLE flat 2D vector illustration of: {{IMAGERY_HINT}}
- Drawing style: thin black line art (#141414, 3px stroke), minimal fill
- One small fill area inside the object can be sienna #C84A32 IF AND ONLY IF
  the hero text accent is NOT used (one Sienna placement per cover total)
- No background behind the object — it sits on Cold Paper

BOTTOM BAR:
- 1px #141414 hairline rule above
- Left: "hsin73.substack.com" JetBrains Mono 11px, color #8A8378
- Right: "{{CATEGORY}} / {{ISSUE_NUM}}" JetBrains Mono 11px, sienna #C84A32

Object source guideline: clean editorial vector style, like a botanical
plate illustration or 1960s scientific journal diagram. NEVER photo-real,
NEVER 3D rendered, NEVER cute/playful.

Thumbnail test: at 60×40 px, "{{HERO_TEXT}}" must remain readable AND
the object silhouette must be recognizable.
```

---

## NanoBanana / Stable Diffusion (fallback)

```
editorial print magazine cover, 1456x816, warm off-white paper #F2EEE5,
left side dense Chinese serif headline in black with one accent character
in sienna red #C84A32, right side single flat vector illustration of
{{IMAGERY_HINT}}, thin black line art style, botanical plate illustration
aesthetic, 1960s scientific journal diagram, masthead "主力爸爸我錯了"
serif top-left, NEWS RADAR mono top-right, 2px black rule under masthead,
hairline rule above bottom bar, flat 2D, no gradient, no shadow, no 3D,
no people, no face --ar 16:9 --no people, gradient, neon, 3d, anime,
cartoon, photorealistic
```

---

## Midjourney (best fit T02 if vector quality matters)

```
single isolated flat vector illustration of {{IMAGERY_HINT}}, editorial
botanical plate style, fine black line work, minimal fill, vintage 1960s
scientific journal diagram, sienna red accent #C84A32 sparingly used,
warm off-white background #F2EEE5, no text, no people, no shadow,
no gradient --ar 16:9 --style raw --v 6
```

→ 拿到 object 圖 → Figma 疊 masthead + hero + kicker + bottom bar。

---

## v0.2 Sample · 「200億的單品咖啡」

| 變數 | 值 |
|---|---|
| `{{HERO_TEXT}}` | 200億的單品咖啡。 |
| `{{HERO_ACCENT_CHAR}}` | 單品 |
| `{{KICKER}}` | MARKET · AI 資本 |
| `{{CATEGORY}}` | Capital |
| `{{ISSUE_NUM}}` | 019 |
| `{{DATE}}` | 2026·05·15 |
| `{{IMAGERY_HINT}}` | a single ceramic pour-over coffee dripper with one coffee bean below it, side profile, minimalist line art |

**v0.3 候選 swap**：替換成「one circular silicon wafer disc with etched circuit lines, top-down view」對應 Cerebras 文章的 wafer-chip 視覺。Hsin 決定。
