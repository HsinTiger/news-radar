# Brand Visual Spec — Cover Image Template

**Status**: Hsin-signed 2026-05-01, Threads added 2026-05-02. Authoritative for all auto-generated cover images going forward.
**Scope**: IG + FB + Threads. (Earlier text-first Threads policy reversed — see "Strategy reversal" note below.)
**Owner**: composer pipeline (`src/cover_renderer.py`). NOT a reflector concern.

## Brand identity decision

Two account names, ONE visual line — across ALL three platforms:

| Platform | Account name | Cover visual | Caption tone |
|---|---|---|---|
| IG | `smartmmmoney` | unified dark / silicon-valley / capitalist sage (1080×1350) | analytical, elite framing |
| Threads | `smartmmmoney` | unified dark cover (1080×1350) | analytical, conversational |
| FB | `主力爸爸我錯了` | unified dark cover (1080×1080) | analytical with occasional 韭菜-self-deprecation as caption flavor |

### Strategy reversal note (Threads cover, 2026-05-02)

Initial strategy (2026-05-01) was "Threads stays text-first, no cover" based on the
generalization that Threads users skip image-heavy posts. Hsin reversed this 2026-05-02
after seeing real FB+IG covers in production: the actual aesthetic reads as KOL-grade
brand identity, not 廣告感. Reversal cost was near-zero (one config flag in
`cover_pipeline.py`), and reversibility remains high (one-line revert if data shows
regression).

**Watch signal:** if `engagement_yield_ratio` for Threads drops more than 20% from
baseline within 14 days of this change landing, revert by adding back the
`if platform_key == "threads": return _passthrough(...)` short-circuit. Reflector's
weekly report will surface the trend automatically.

The FB account name keeps the legacy "主力爸爸我錯了" IP but the cover visual unifies to the smartmmmoney aesthetic. This lets cross-platform readers recognize the same author while preserving the brand-recognition equity already built in the FB name.

## Target reader

- 中高產 25-45, 早八通勤
- Reads 國際 / 宏觀 / 政策 / 財經 / 科技 — not deep specialists
- Wants frontier intel + AI-first lens
- Self-image: 矽谷感 / 菁英感 / 資本家感 — wants to feel "I see further than the crowd"

## Cover image template

### Aspect ratios

| Platform | Size | Reason |
|---|---|---|
| IG | 1080×1350 (4:5) | Max vertical real estate in IG feed without crop |
| FB | 1080×1080 (1:1) | Renders cleanly in FB feed AND link-preview cards |
| Threads | 1080×1350 (4:5) | Threads feed handles tall images well; same aspect as IG simplifies caching/CDN |

### Background composition

Original news image is the bottom layer, processed as follows:

1. **Center-crop** to target aspect ratio (no letterbox, no skew).
2. **Gaussian blur** radius `10px` — softens detail so it doesn't compete with title.
3. **Dark overlay** `rgba(10, 14, 29, α)` where α is dynamic by image luminance:
   - `α = 0.55` (140/255) when image is dark (avg luminance < 90)
   - `α = 0.65` (166/255) default (avg luminance 90-160)
   - `α = 0.70` (178/255) when image is bright (avg luminance > 160)

The 0.55-0.70 range is the empirical "balance" point: less than 0.55 leaves chart-screenshot text bleeding through; more than 0.70 makes the image indistinguishable from a flat color block, defeating the point of using the news image at all.

### Topic chip (top-left)

- Position: 60px from top, 60px from left
- Size: 280×80px rounded rectangle, corner radius 12px
- Fill color: per `topic_category` (see table below)
- Label: short Chinese tag in 思源黑體 Bold 38pt white, centered

Keys MUST match `category_id`s in `src/topic_taxonomy.py`. Adding a new category
there REQUIRES adding both label + color here in the same commit, or the chip
falls back to gray + raw English category text (visually obvious failure).

| topic_category | RGB hex | Label | Cluster |
|---|---|---|---|
| `ai_model` | `#7F77DD` (purple) | AI 模型 | AI |
| `ai_agent` | `#6B5DD5` (deep purple) | AI Agent | AI |
| `ai_application` | `#9D94E8` (light purple) | AI 應用 | AI |
| `supply_chain` | `#378ADD` (blue) | 產業鏈 | Industrial |
| `earnings` | `#639922` (green) | 財報 | Money |
| `tw_stocks` | `#D4537E` (pink) | 台股 | Money |
| `us_stocks` | `#BA7517` (amber) | 美股 | Money |
| `tech_product_launch` | `#2BB39B` (teal) | 科技新品 | Industrial |
| `policy_geopolitics` | `#888780` (gray) | 政策 | Other |
| `other` | `#888780` (gray) | 其它 | Other |
| _unknown_ | `#888780` (gray) | _topic_category as-is_ | — |

### Title (main hook)

- Font: 思源黑體 Bold (`SourceHanSansTC-Bold.otf`)
- Size: dynamic by length
  - ≤16 chars → 95pt
  - 17-24 chars → 80pt
  - ≥25 chars → 65pt (3-line max, ellipsis after that)
- Color: pure white `#FFFFFF`
- Shadow: 2px offset, `rgba(0,0,0,0.63)` — pulls letter edges off the blurred background
- Position: vertical center, slightly above midline (offset -50px from true center) so subtitle has room
- Line break: greedy by character (no word boundaries in Chinese), max 3 lines
- Horizontal alignment: center

### Subtitle (optional, secondary line)

- Font: 思源宋體 Light (`SourceHanSerifTC-Light.otf`)
- Size: 48pt
- Color: white at 75% opacity (`rgba(255,255,255,0.75)`)
- Position: 30px below title block, centered
- Skipped silently if `subtitle` is None or empty — design must not break

The black-bold + serif-light combination is intentional: black for "stop and read this hook", serif for "this is analysis, not a slogan". Avoid all-black-bold (feels shouty) or all-serif (feels academic).

### Brand bar (bottom)

- Position: 80px from bottom
- Above bar: 0.5px hairline, white at 20% opacity, full width minus 60px padding each side
- Text: `{brand_name} · {YYYY/MM/DD}`
  - IG: `smartmmmoney · 2026/05/01`
  - FB: `主力爸爸我錯了 · 2026/05/01`
- Font: 思源黑體 Regular 28pt, white at 65% opacity
- Center-aligned

## Fonts

Adobe Source Han Sans/Serif TC. Open-source, OFL license, commercially permissive.

Drop these files at `assets/fonts/`:
- `SourceHanSansTC-Bold.otf` — title + topic chip
- `SourceHanSansTC-Regular.otf` — brand bar
- `SourceHanSerifTC-Light.otf` — subtitle

Download from <https://github.com/adobe-fonts/source-han-sans/tree/release/OTF/TraditionalChinese> and <https://github.com/adobe-fonts/source-han-serif/tree/release/OTF/TraditionalChinese>.

## Implementation pointer

- Module: `src/composer/cover_renderer.py`
- Pure function entry: `render_cover(CoverInput, aspect: "ig"|"fb") -> Path`
- Output: `assets/cover_cache/{news_image_stem}_{ig_4x5|fb_1x1}.png`
- Composer wiring: hook AFTER LLM produces MultiPlatformDraft, BEFORE publisher upload. For IG and FB only — Threads keeps original image.

## What this spec is NOT

- **Not the FB page banner** (1640×859). That's a one-off Canva job, not auto-generated.
- **Not a reflector concern.** Reflector tunes engagement parameters, not visual style. Brand DNA decisions land here, not in `proposals.jsonl`.
- **Not a soul.md addition.** Soul is voice/editorial; this is purely visual layout. Keeping them separate makes future visual revisions cheaper.

## Future revisions

If reader feedback or `engagement_yield_ratio` data forces visual changes (e.g. 4 weeks of declining IG saves after launch), update THIS file and bump cover_renderer version. Do NOT route visual changes through reflector proposals.
