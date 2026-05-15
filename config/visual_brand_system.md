# News Radar · Visual Brand System (canonical text spec)

> **Source of truth** for cover design + visual brand language.
> 從 `docs/brand_system/visual_brand_system_v0.1.html`（claude design 2026-05-15 交付）萃取。
> 視覺 reference 看 HTML、機器讀 / pipeline / PM agent 寫 image prompt 讀此檔。

---

## §1 Direction · 一句話 brand DNA

**Cold-print editorial** —— 像一份 1950s 黑白嚴肅刊物（FT / Economist / The Diff），不是 2017 startup deck、不是 LinkedIn influencer 封面、不是雞湯 channel。

5 個 style adjectives:
1. Editorial（編輯部出品感、不是個人 vlog 感）
2. Cold（冷感、不熱情、不勵志）
3. Print（紙本印刷感、不數位 UI 感）
4. Contrarian（單一立場、不左右兩面討好）
5. Quiet authority（不喊不吼、用 typography 撐權威）

Voice 對齊：跟 `substack_soul.md` §2 Tone 完全一致（Wise & Warm + Contrarian + Anti-Conclusion + Aporia Turn）。

---

## §2 Color Palette

| Role | Name | Hex | OKLCH | 用途 |
|---|---|---|---|---|
| 01 / Paper | Cold Paper | `#F2EEE5` | oklch .94 .012 84 | 預設背景。**永遠不用純白** |
| 02 / Ink | Press Ink | `#141414` | oklch .19 .001 0 | Hero text、線條、masthead。**near-black、不用 #000** |
| 03 / Accent | Sienna Mark | `#C84A32` | oklch .56 .15 35 | House accent。**一張封面只能 ONE placement** |
| 04 / Reserved | Alert Red | `#B0241D` | oklch .47 .17 28 | Crisis / crash 篇專用。**取代 Sienna、不共存** |
| 05 / Mute | Stone | `#8A8378` | oklch .60 .013 80 | Meta lines、secondary mono、永不 focal |
| 06 / Tone | Bone | `#E8E3D6` | oklch .90 .015 84 | Hairline panels、table rows、optional only |

### 比例目標（across all 52 covers）

```
PAPER · 70%   INK · 24%   SIENNA · 4%   STONE · 2%
```

Sienna > 6% = 視覺上讀成「磚牆」、不是「dispatch」。

### Sienna 唯一允許的 4 種 placement（一張封面選 1）

(a) Brand mark word `RADAR` 著色
(b) Hero phrase 底下單條 underline
(c) 右上角 date stamp
(d) 單個 numeric figure（例：`$3.4T`）

兩處 = breach、必砍。

### Sienna 永不允許的位置

- Body copy / deck text
- Kickers 超過 3 個字
- Drop shadows / gradients
- Photo tinting
- Page borders / decorative shapes / icon fills

不確定 → 留黑。

### Alert Red 觸發 trigger

- Crash / sell-off / bankruptcy / layoffs
- War / sanction / regulatory action

每季 < 10% 篇用、否則 lose signal。

### Light vs Dark covers

- 預設 Paper 底
- **Invert**（Ink 底 + Paper 字）保留給：obituaries / 年終 retrospective / "the case against X"
- 頻率 ≤ 1/8

Dark cover 規則一樣、不解鎖額外色。

---

## §3 Typography

### 三個字族 + 一個 display

| 用途 | 字族 | 規格 |
|---|---|---|
| Hero · 中文 4-8 字 | **Noto Serif TC** | Weight 900、200-280px @ 1456w、tracking -2%、leading .92 |
| Deck · subtitle | **Noto Sans TC** | Weight 500、22px、line-height 1.45 |
| Masthead / Issue / Date | **JetBrains Mono** | Weight 400-700、11-13px、letter-spacing .18-.32em、UPPERCASE |
| Italic accents | **Instrument Serif** | 點綴用、不主導 |

### Fallback 順序

- Serif: `Noto Serif TC → Songti TC → 思源宋體 → PingFang TC Heavy → Georgia Bold（latin only）`
- Sans: `Noto Sans TC → PingFang TC → Helvetica Neue → Arial`
- Mono: `JetBrains Mono → ui-monospace → Menlo`

### 句尾句號規則

Hero 句尾的句號（「越便宜，越貴。」）是 hero 的一部分、**不是 optional**。

---

## §4 Layout Grid · 1456 × 816 canvas

### 安全區 + 邊距

- 全 canvas: 1456 × 816 px
- 邊距: 上下左右各 64 px
- Safe zone: 1328 × 688 px

### 區塊分配

```
┌──────────────────────────────────────────┐
│ TOP BAR  (64px from top, 14px tall)      │
│ • masthead L  • issue/date R · 雙欄 grid │
│ ───── 2px ink rule ─────                  │
│                                          │
│  HERO REGION                             │
│  • 中文 4-8 字、Noto Serif TC 900        │
│  • size 120-280 px depending on template │
│  • 占 canvas 30-50% 高度                 │
│                                          │
│  ───── 1px ink hairline rule ─────       │
│ BOT BAR  (64px from bot, 14px tall)      │
│ • URL L  • category/issue №R · 雙欄 grid │
└──────────────────────────────────────────┘
```

### Imagery region（template 依賴）

- T01 純文字：無 imagery、純粹用 negative space
- T02 single object：右側 380 × 480 px，物件居中
- T03 chart：上 55%（含 caption + source）
- T04 photo：full bleed 在 hero 下層、overlay 在 hero
- T05 diagram：中段 380px 高，三欄對照（左/分隔/右）
- T06 scene/stage：背景 wash + foreground hero

---

## §5 Discipline · 5 Do / 5 Don't

### 5 Do（5/5 必過、缺一即重做）

1. **Hero text dominates** —— 整張封面最大物件、永遠是 hero。如果 imagery 比 type 響、砍 imagery。
   - Test: 瞇眼、headline 仍贏。

2. **One subject, one claim** —— 一個 cover 一個立場、不要「也」。
   - 如果 hero 需要逗號、你有兩張 cover、不是一張。

3. **Two colours, period** —— Paper + Ink + 一處 Sienna。Reserved Red 取代 Sienna、絕不共存。
   - 多色 = 讀成「磚牆」、不是「dispatch」。

4. **Masthead + issue stamp visible** —— `NEWS RADAR` 左上、`ISSUE №` 右上、每張封面都要有。
   - 52 期放一起的 feed 必須讀成同一刊物。

5. **Read at 60 × 40 px** —— 打開 thumbnail 列表、讀不到 hero = 整張封面 reject。
   - 沒例外、沒「但 layout 很美」。

### 5 Don't（觸碰任一條 = 編輯需明確 override 才出）

1. **No cartoon people** —— 沒有吉祥物、沒有 LinkedIn 大頭照、沒有插畫主角。
   - 走錯刊物、走錯讀者。

2. **No gradients, glows, or 3D** —— 純 flat fill。Type 不加 drop shadow。Photo 不加 gradient overlay。
   - 全部讀起來像 2017 startup deck。

3. **No emoji, no exclamation marks** —— 沒有 💡 🚀 🔥、沒有「！」、沒有 quoted hot-take headline。
   - 我們不是雞湯 channel。

4. **No decorative borders or frames** —— 沒有雙線、沒有 ornament、沒有 rounded card with shadow。
   - Masthead 那條規線是封面唯一的 frame。

5. **No AI-generated imagery** —— 特別是 AI 生成的中文字。Stock photo、icon、純 typography only。
   - 質感不穩 + brand risk + workflow 慢。

---

## §6 Six Templates · 1456 × 816

### T01 · Pure type（DEFAULT）

- **Formula**: HERO + MARK · NO IMAGERY
- **When**: headline punchy ≤ 8 字 · 文章是 thesis · 沒有好 imagery
- **Specs**: HERO 236px / ACCENT 1 char / IMAGERY none / TIME 3 min
- **Why**: 60×40 px 比任何照片都活、把 headline 當整個產品

### T02 · Single object

- **Formula**: ICON/object 右側 + HERO 左側
- **When**: topic 有自然單一物件 anchor（沙漏 / 晶片 / 咖啡杯 / 樹）
- **Specs**: HERO 168px / OBJECT 380×480 / TIME 4 min
- **Source**: Noun Project / Streamline / 自己 SVG。**禁 AI 生成**

### T03 · Data / chart

- **Formula**: CHART top 55% + HERO bot 35%
- **When**: 文章 centres on chart · ≤ 2 series · time series or comparison
- **Specs**: HERO 120px / CHART 2 series / SRC required / TIME 5 min
- **Rule**: chart must argue。可被任何其他 chart 取代 = 砍掉走 T01

### T04 · B&W photograph

- **Formula**: PHOTO full bleed + HERO OVERLAY (paper)
- **When**: topic 有 atmospheric anchor · ≥ 1500 字 · Thesis category
- **Specs**: HERO 200px / PHOTO B&W / WASH optional / TIME 4 min
- **Source**: Unsplash desaturated 或 in-house photography
- **Rule**: 沒有 portrait、沒有人臉看鏡頭、沒有 face

### T05 · Diagram

- **Formula**: 雙欄對照（直覺 vs 事實 / before vs after / 你以為 vs 真的）
- **When**: 文章核心是一個 reframing
- **Specs**: HERO（左+右各一）54px / 中央分隔欄 80px wide / TIME 4 min
- 左欄: Paper 底 + Ink hero
- 右欄: Ink 底 + Paper hero（反白突顯 contrarian）
- 中央 `VS` 用 Mono vertical-rl

### T06 · Scene / stage

- **Formula**: 場景 wash + foreground hero
- **When**: 敘事性開場（凌晨一點 / 觀眾席 / 廚房）
- **Specs**: HERO 168px / scene flat-fill 或 stage spotlight / TIME 5 min

---

## §7 Hero Text Extraction · 從標題抽 4-8 字

`PM agent 寫文章後、視覺編輯角色（substack_soul.md §10.1）做這個動作`

### 規則

1. 從標題挑「**最 punch 的 4-8 字詞組**」當 hero text
2. 該詞組必須**獨立讀得通**（不需要剩下的標題上下文）
3. 該詞組必須**讀者第一秒能感受到 emotion / contrarian / curiosity**

### 範例

| 標題 | Hero text 抽取 |
|---|---|
| 9% → 34.4%：Anthropic 第一次贏了 OpenAI，但贏的不是你以為的那場仗 | **34.4%** |
| 我發現自己是個討厭的講師，然後我學會閉嘴 | **學會閉嘴** |
| 為什麼學最多 AI 框架的人最先出局？ | **最先出局** |
| OpenAI 要喝 200 億美元的單品咖啡 | **單品咖啡** |
| 精美的廢話：為什麼讀完十篇報告，你還是不會做決策 | **精美的廢話** |
| 主角會死、配角會富 | **配角會富** |

抽不出 4-8 字 = 標題本身寫得不夠 punch、回去重寫。

---

## §8 PM Agent Decision Tree · 拿到新文章標題 → 5 分鐘出 cover

```
標題 + insight 一句話
  ↓
Step 1 · 抽 hero text（§7 規則）
  ↓
Step 2 · 決定 template（依下表）
  ↓
Step 3 · 決定 Sienna 落點（4 選 1、§2 規則）
  ↓
Step 4 · 確認 6/6 Do/Don't pass
  ↓
Step 5 · Render 1456 × 816
```

### Template 選擇 decision tree

| 條件 | Template |
|---|---|
| 沒有 imagery anchor / hero ≤ 8 字 / thesis 篇 | T01 Pure type（**default、80% 篇用這個**）|
| 有自然單一物件 anchor（晶片 / 咖啡杯 / 樹）| T02 Single object |
| 文章核心是一張 chart | T03 Data / chart |
| Atmospheric anchor / 長文 ≥ 1500 字 | T04 B&W photo |
| 文章核心是一個 reframing（你以為 vs 真的）| T05 Diagram |
| 敘事性開場場景 | T06 Scene / stage |

衝突時優先 T01（最安全、最 scroll-stop）。

---

## §9 Wordmark · Masthead 決策

### claude design 預設

`NEWS RADAR` 英文 monospace、左上、Sienna 強調 `RADAR`。

### Hsin 決策（待定 · 2026-05-15）

選項：
- **A**: 「主力爸爸我錯了」中文 masthead 為主、`NEWS RADAR` 為 secondary watermark
- **B**: `NEWS RADAR` 英文 only（claude design 預設）
- **C**: 中英並列

PM agent 推薦 A：訂閱者 thumbnail 一秒識別「中文 newsletter」、英文太通用易混淆。

---

## §10 已知 placeholder（v0.1 → 後續迭代）

- T04 photo 是 striped block + Unsplash tag note（之後換真實 B&W photo source）
- T02 hourglass 是 basic vector stand-in（之後用 Noun Project / Streamline icon library）
- 不是 Figma file —— spec doc only、templates 後續可用 Figma 重建

---

## §11 與 substack_soul.md 的關係

- **`substack_soul.md` §10** 是 voice / 紀律 source of truth（5 條封面紀律 + 5 條標題紀律 + 視覺編輯角色）
- **本檔 (`visual_brand_system.md`)** 是 visual implementation source of truth（顏色 / 字體 / 模板 / hex code）
- 兩者互補、不衝突
- 衝突時優先 substack_soul.md（voice 規則）、本檔細化執行

`substack_soul.md §10` 應加 cross-ref 指向本檔。

---

## §12 Versioning

| 版本 | 日期 | 來源 | 變更 |
|---|---|---|---|
| v0.1 | 2026-05-15 | claude design 首次交付 | initial |

下次迭代 → `visual_brand_system_v0.2.md`、舊版保留 reference。
