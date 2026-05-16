# FB Page Cover Banner v1 · 「每天 3 分鐘」editorial

**Spec**: 851 × 315 px (FB cover 標準)
**Brand**: 主力爸爸我錯了 (English: NEWS RADAR)
**Direction**: Cold-print editorial（對齊 visual_brand_system.md v0.2）
**Goal**: 替換現有 AI dark fantasy cover、跟 Substack brand 視覺一致

---

## Hero text 抽取

**「每天 3 分鐘」**5 字（cadence promise 核心句）
- 「3」字單獨著 sienna red #C84A32（single accent placement）
- 副標 「拿走一個被市場藏起來的共識」 一行 sans

---

## Generation Prompt（複製貼 ChatGPT image / NanoBanana）

```
Design a Facebook page cover banner at exactly 851 × 315 pixels for a
Chinese investment newsletter called "主力爸爸我錯了" (English mark: NEWS RADAR).

Style: COLD-PRINT EDITORIAL — like a 1950s serious financial newspaper
masthead, not a startup deck, not a Web3 banner.

STRICT BRAND CONSTRAINTS (binary):
- Background: warm off-white #F2EEE5 (NEVER pure white)
- Text + lines: near-black #141414 (NEVER pure #000)
- Single accent: sienna red #C84A32, used ONLY ONCE on this banner
- NO gradients, NO drop shadows, NO 3D, NO glows
- NO cartoon people, NO faces, NO mascots, NO illustrated characters
- NO emoji, NO decorative borders, NO photo realism
- NO AI-generated Chinese characters (use placeholder boxes if needed)

LAYOUT (851 × 315, 32px margins):

LEFT (~60% of canvas):
- LARGE HERO Chinese text "每天 3 分鐘" in heavy serif (Noto Serif TC 900),
  size approximately 110px, leading 0.92, tracking -2%, color #141414.
  The character "3" colored sienna red #C84A32 (single accent).
- SUBTITLE below in Noto Sans TC 500, 22px, color #2A2724:
  "拿走一個被市場藏起來的共識"

RIGHT (~40% of canvas):
- TOP-RIGHT: small monospace label "主力爸爸我錯了 · NEWS RADAR" in
  JetBrains Mono 13px UPPERCASE, letter-spacing 0.32em, color #141414
- 1px #141414 vertical hairline separating left hero from right meta zone
- BOTTOM-RIGHT: small mono "hsin73.substack.com · daily" color #8A8378

NEGATIVE SPACE: ≥ 25% of canvas is empty paper #F2EEE5.

Render flat 2D editorial print aesthetic — think 1960s Wall Street
Journal masthead or 1980s Business Week banner. No 3D, no gradients,
no AI-style flourishes.
```

---

## 中文渲染失敗備案

ChatGPT image / NanoBanana 對中文 hero 字 rendering 不穩。如果生出來中文字變形：

1. 重 generate 加一句「Leave a placeholder rectangle 600×130px in the hero area, do not generate Chinese text」
2. 拿到無字版 banner → Figma / Canva 後製
3. 疊「每天 3 分鐘」serif 110px、「3」單字 sienna red #C84A32
4. 疊 subtitle「拿走一個被市場藏起來的共識」sans 22px
5. Export 851 × 315 PNG → 上傳 FB Business Suite

---

## 5 條紀律自檢（對齊 §10.2）

- ✅ 字大 dominate：「每天 3 分鐘」110px、占左半 ~60% 面積
- ✅ 一個概念：cadence promise dominate、無其他競爭視覺
- ✅ 2 色限定：Cold Paper + Press Ink + Sienna(僅「3」)
- ✅ 無裝飾人物：純文字 banner
- ✅ 標題-banner 對齊：hero text 跟 about page 同句

---

## Versioning

| v | 日期 | 變更 |
|---|---|---|
| v1 | 2026-05-16 | initial · 替換現有 AI dark fantasy cover |
