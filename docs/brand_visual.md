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

## Future revisions — when to change this spec

Visual decisions belong to Hsin (per `feedback_reflector_scope.md` — reflector
optimizes engagement, not brand DNA). But "Hsin's intuition" is not a reliable
trigger by itself; we need explicit signals to know WHEN to question the
current spec, otherwise the visuals slowly drift toward staleness.

### Three layers of triggers (any one fires → schedule a visual audit)

#### Layer 1 — quantitative warnings (auto-detectable)

| Signal | Trigger threshold | Why it matters |
|---|---|---|
| **Engagement yield drop (per platform)** | ≥ 25% decline over 30 days **while** `weighted_score` (content quality) holds steady | Content didn't change but response did → most likely visual fatigue |
| **IG saves rate drop** | ≥ 30% decline over 30 days | Saves is IG's gold metric — covers attract clicks but content fails to deliver, OR cover signals "another lazy AI post, skip" |
| **Per-platform CTR imbalance** | One platform drops while others hold | That platform's visual is specifically dated |
| **Cover render fail rate** | > 5% of posts falling back to passthrough | Tech problem masquerading as visual; fix tech first then re-evaluate |

Today these are NOT yet on the dashboard — adding them is open work
(see PM_Radar `roadmap/`).

#### Layer 2 — qualitative warnings (humans notice)

| Signal | Source | Why it matters |
|---|---|---|
| **3+ independent reader mentions** of visual | DMs, comments, in-person | 1 reader is noise, 3 is pattern (today's IG-feedback incident started from 1 reader → triggered the whole cover initiative) |
| **KOL benchmark evolution we didn't follow** | E.g. 游庭皓 changes their cover style and 6 months pass | Visual is also fashion — staying static while peers iterate = relatively dated |
| **Platform UI change** | Meta changes IG feed crop ratio, Threads adds new visual elements | Our spec is calibrated to 2026-05 Meta UI; UI changes → spec changes |
| **Hsin's gut while scrolling own grid** | Weekly habit: scroll, write down "this is feeling fatigued" | **Most important leading signal — never suppress because "no data backs it up"** |

The fourth one is critical. Today's whole cover work started because Hsin
saw that IG's smartmmmoney lacked recognition vs the smartmmmoney brand.
That intuition predated any data signal. **Boss intuition is a load-bearing
input — write it down, don't override it with "but the metric says fine"**.

#### Layer 3 — calendar checkpoint (forces the question)

**Quarterly visual audit, ~30 minutes, regardless of whether layers 1 & 2 fired:**

- Q1 / Q2 / Q3 / Q4 review
- Process:
  1. Pull dashboard signals (when those widgets exist)
  2. Scroll own grid (last 90 posts) on each platform
  3. Scroll 3 KOL benchmark grids same period
  4. Write 1 paragraph: "This quarter the brand feels [better / same / worse] than last because ___"
- Decision: **Hold / Tweak / Pivot**

Reason for calendar trigger: gradual decay (boiling frog) doesn't trip
threshold alarms. The 90-day forced audit catches long-tail drift that
no single metric can flag.

### Hold / Tweak / Pivot — decision tree

| Outcome | Example | Action |
|---|---|---|
| **Hold** | Data stable, no taste fatigue, KOL benchmarks stable | 1 line in changelog: "Q3 maintained spec, no changes". Audit again Q4. |
| **Tweak** | One or two dimensions need refresh (color / layout / font size) but overall direction is right | Change one dimension, ship, A/B compare 14 days, keep the winner |
| **Pivot** | Whole visual feels dated, brand positioning may also be shifting | brand_visual.md major version bump, write new spec, all NEW posts use new spec, **don't backfill old posts** |

**Key discipline**: every Tweak or Pivot writes a changelog entry below.
Without history, future PM agents repeat past mistakes.

### Anti-patterns (do not do these)

| Anti-pattern | Why it's wrong |
|---|---|
| Change visual because of **one bad post** | Noise, not signal. Any < 30-day trend is too short |
| Change visual because **competitor's cover looked cool yesterday** | FOMO-driven rebrands die faster than disciplined evolution |
| Change spec **without changelog** | Lose compounding learning, repeat past mistakes |
| Change **multiple dimensions at once** (color + font + layout simultaneously) | Can't attribute which lever moved engagement → can't validate |
| **Iterate while still validating** previous change | Data contamination → no clean ship decision |

### Changelog

| Date | Change | Reason |
|---|---|---|
| 2026-05-01 | Initial spec (smartmmmoney unified visual) | Hsin's brand-recognition concern after IG reader feedback |
| 2026-05-02 | Threads added to symmetric cover flow (was text-first) | Hsin reversed initial "no Threads cover" call after seeing real production output |
| 2026-05-02 | Topic chip taxonomy aligned to topic_taxonomy.py | Discovered chip was missing real categories (earnings/other etc.) |
| 2026-05-02 | Overlay alpha 0.65 → 0.55 (default), grain noise added to fallback, subtitle 48pt → 58pt, market chips warmer (red/orange) | First Tweak round after seeing production samples — overlay too heavy, fallback too flat, subtitle too small relative to title, palette too cold |
| 2026-05-02 (after preview) | _Hold_ — Hsin reviewed 6-sample gallery (PREVIEW_v2_*.png in PM_Radar/cover_samples), opted to wait for real reader feedback instead of further tweaking. Honors the discipline: collect 14 days of data + qualitative signals before next audit. | Discipline check passed: avoided FOMO/over-iteration. |
| _next_ | _to be filled at next audit (Q3 2026 calendar trigger OR signal trigger fires)_ | _to be filled_ |

### What this section is NOT

- NOT a "brand image score" system. We deliberately don't quantify brand
  visual quality (Goodhart's Law, see `AI_COLLABORATION_PRINCIPLES.md`).
- NOT a reflector proposal source. Reflector NEVER touches brand spec.
- NOT a daily review burden. Quarterly cadence is by design — daily
  obsessing about visual destroys focus on content.
