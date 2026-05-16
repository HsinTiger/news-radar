# Avatar v0.1 · Design Brief for Claude Design

> Date: 2026-05-16
> Brand: News Radar (publication name varies by platform — see §3)
> Existing brand system: `docs/brand_system/visual_brand_system_v0.2.html`
> Owner: Hsin

---

## §1 Context

**Publication overview**:
- Chinese investment + business analysis newsletter on Substack
- Active across 4 platforms: Substack, FB Page, IG, Threads
- Target audience: 高 agency 知識工作者 25-45 歲、對科技/商業/市場/人生決策有興趣
- Voice: contrarian + cold + analytical, NOT 雞湯, NOT motivational
- Cadence: 每天 1 篇 (~3 分鐘掃讀 / ~10 分鐘深讀)

**Existing visual brand system (v0.2 by claude design)**:
- Direction: cold-print editorial (1950s broadsheet aesthetic)
- Palette: Cold Paper #F2EEE5 + Press Ink #141414 + Sienna #C84A32
- Typography: Noto Serif TC 900 (hero) / JetBrains Mono (mono) / Noto Sans TC (deck)
- 6 cover templates already shipped, real article hero text mapped
- All visual rules in `visual_brand_system_v0.2.html`

---

## §2 Problem

4 platforms currently have **4 completely different profile avatars**:

| Platform | Current Avatar | Style | Brand alignment |
|---|---|---|---|
| FB Page | "Wrong-ing Man" running stick figure with red X | Hand-drawn cartoon, B&W | Medium (mascot equity, but cartoon) |
| IG | AI-generated dark fantasy hooded cosmic scene | AI dramatic | Low (Web3 NFT vibe) |
| Threads | AI-generated cosmic mountain owl scene | AI mystical | Low (different from IG) |
| Substack | Existing logo (URL in API) | Unknown to brief author | Unknown |

**Impact**:
- Reader cannot anchor "this is the same publication" across platforms
- Brand visual identity is diluted
- Recently shipped cold-print editorial brand system (v0.2) creates dissonance with current avatars

---

## §3 Goal

Design a **single unified avatar** that ships across all 4 platforms.

The avatar must:
1. **Anchor brand identity** — readers recognize across all 4 surfaces in 1 glance
2. **Align with cold-print editorial brand DNA** — same family as cover system v0.2
3. **Preserve "我錯了 / wrong" equity** — current FB Wrong-ing Man's wordplay anchor (主力爸爸 **我錯** 了 / I'm wrong-ing) is the brand's emotional core; avatar should keep this pattern even if visual is redesigned
4. **Work for both naming conventions**:
   - FB + Substack labels: 「主力爸爸我錯了」(中文)
   - IG + Threads handles: 「smartmmmoney」(英文)
   - **Avatar therefore CANNOT contain text** (would force language commitment)
5. **Survive 60×60 px thumbnail test** + **30×30 px favicon test**
6. **Be circle-crop tolerant** (IG / Threads / FB / Substack all crop to circle)

---

## §4 Hard Constraints

### Visual

- **Background**: warm off-white #F2EEE5 (Cold Paper, NEVER pure white)
- **Subject + line work**: near-black #141414 (Press Ink, NEVER pure #000)
- **Single accent**: sienna red #C84A32, used ONLY ONCE per design
- **NO**: gradients, drop shadows, 3D, glows, photo realism, AI mystical aesthetics
- **NO**: faces visible, eye contact, exaggerated cartoon expressions
- **NO**: text, letters, Chinese characters, emoji
- **NO**: decorative borders, frames

### Mascot exception

Profile avatar **may include** simplified cartoon-level human figure or mascot symbol. This is an exception to `visual_brand_system.md §10.2 #4` "no cartoon people" rule (which applies to article COVERS, not profile avatars).

Reference: Doomberg's chicken, Drudge Report's mascot, The Browser's logo — serious publications can have visual mascots.

### Format

- **Source format**: 1024 × 1024 PNG (square)
- **Test crops**:
  - Circle (Meta avatars)
  - 60×60 px thumbnail
  - 30×30 px favicon
- **Negative space**: figure / symbol should occupy 50-65% of canvas, leaving room for circle crop bleed

---

## §5 Deliverables (Expected)

### 5.1 Brand DNA mapping for avatar (1 page)

How does the avatar concept connect to:
- "我錯了 / wrong" wordplay anchor
- Cold-print editorial aesthetic
- Newsletter cadence promise (每天 3 分鐘 / 365 天複利)

### 5.2 5-7 design variants (each at 1024×1024 + 60×60 thumbnail)

Each variant should explore a different conceptual angle:
- Refined Wrong-ing Man (modernized mascot)
- Abstract correction symbol (X mark only / red strike-through)
- Editorial monogram (single mark resembling a print press symbol)
- Vintage scientific illustration figure
- Geometric / minimal symbol
- Hybrid (figure + symbol)
- Wildcard direction

For each variant:
- Render at full size (1024×1024)
- Show 60×60 thumbnail crop
- Show 30×30 favicon crop
- Show circle-cropped version (Meta avatar simulation)
- 1-paragraph rationale (what does this anchor on)

### 5.3 4-platform mockup of recommended winner (1 page)

For the strongest variant:
- FB Page profile + cover combined view
- IG profile (with bio)
- Threads profile (with bio)
- Substack publication logo (header + email signature)

Show how the avatar renders in each platform's actual UI context.

### 5.4 Thumbnail legibility test

Side-by-side row showing all variants at 60×60 + 30×30 sizes.
Annotate which variants survive thumbnail-readability test.

### 5.5 Construction spec for winning variant

Final canonical spec for whichever variant Hsin selects:
- Color hex (already locked: #F2EEE5 / #141414 / #C84A32)
- Stroke widths
- Composition coordinates (figure position, X mark size + angle)
- File export specs (1024 PNG / 512 PNG / 256 PNG / 32 favicon)
- Build instructions if recreated in Figma / Illustrator

### 5.6 Workflow handoff doc

How does Hsin (or PM agent) ship a new version if avatar evolves to v0.2?
- Editing source file location
- Brand consistency check before upload
- Per-platform upload procedures (manual web UI for all 4)

---

## §6 Inputs (we will provide)

### 6.1 Existing brand system

- Full HTML doc: `docs/brand_system/visual_brand_system_v0.2.html`
- Color palette + typography + 6 cover templates already locked
- Cover hero extraction examples (real articles)

### 6.2 Current avatars (screenshots Hsin will provide)

- FB Wrong-ing Man (current)
- IG dark fantasy
- Threads cosmic
- Substack logo (URL: see python-substack `api.get_user_primary_publication()['logo_url']`)

### 6.3 Brand voice / soul

- `config/substack_soul.md` (newsletter writing voice + brand declaration)
- `feedback_documentary_narrator.md` (5/15 voice update — for-画面騰空間, scene over comment)

### 6.4 Aspirational visual references (we want to feel like)

- 1960s WSJ op-ed sketches
- Vintage scientific journal illustrations
- Doomberg's chicken (mascot done right for serious publication)
- Stratechery (no avatar but typography-as-brand)

### 6.5 Anti-pattern references (avoid)

- AI dark fantasy / NFT-style cosmic
- Generic startup mascot illustrations
- LinkedIn influencer headshots
- Anime / kawaii style
- Any motivational / 雞湯 aesthetic

---

## §7 Success Criteria

- Strongest variant survives 30×30 favicon test (still reads as "the publication")
- 4-platform mockup shows brand cohesion across surfaces
- Construction spec is precise enough for Hsin to recreate / iterate without claude design re-engagement
- Avatar pairs visually with v0.2 cover templates (T01-T06) without dissonance
- Wrong-ing Man wordplay equity preserved in some form (literal or abstract)

---

## §8 Out of Scope (don't deliver)

- Brand system v0.3 (we already have v0.2, only avatar in this brief)
- Cover template iterations (v0.2 still active)
- Any text-based wordmark (would break dual-naming constraint)
- Any AI-generated avatar (brand rule + we tried IG/Threads current = failed)

---

## §9 Timeline & Format

- Expected turnaround: 3-5 days
- Output format: HTML scroll-doc (same format as `visual_brand_system_v0.1.html`) preferred. Or PDF + asset folder.
- Variant assets: PNG 1024 / PNG 512 / PNG 256 / SVG (if vector-friendly)

---

## §10 Versioning

| v | Date | Change |
|---|---|---|
| v0.1 | 2026-05-16 | Initial brief |

After claude design v0.1 delivery → Hsin selects winner → v1 of avatar (canonical) + may iterate to v2 later.
