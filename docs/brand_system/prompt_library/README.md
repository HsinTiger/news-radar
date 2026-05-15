# Prompt Library · 6 templates × 3 工具

PM agent 寫文章後、抽 hero text、選 template、從本資料夾拿對應 `.md` 模板、填變數、output ready-to-paste prompt 給 Hsin 丟生圖工具。

## 用法

每個 template 模板含 3 段 prompt（針對不同生圖工具 calibrated）：

1. **ChatGPT image** —— 自然語言 narrative、prompt-following 強、適合 layout-heavy 設計
2. **NanoBanana** —— Stable Diffusion 系、token-level visual descriptor、quality 高但 layout 弱
3. **Midjourney** —— 美學最強、但 brand 約束力弱、適合 atmospheric T04

預設用 ChatGPT image（layout 跟 brand 約束力最強），其他兩個是 fallback。

## 變數位

| 變數 | 範例 | 來源 |
|---|---|---|
| `{{HERO_TEXT}}` | 精美的廢話 / 單品咖啡 | 從文章標題抽 4-8 字最 punch 詞組 |
| `{{HERO_ACCENT_CHAR}}` | 廢話 / 單品 / 34.4% | hero 內哪 1-3 字著 Sienna red |
| `{{KICKER}}` | CONTRARIAN · 決策 | 文章 category + 子主題 |
| `{{CATEGORY}}` | Decision / Capital / AI / Essay / Learning / Thesis | 6 種固定 |
| `{{ISSUE_NUM}}` | 053 | Substack 累計 issue 號（從 publish 順序算）|
| `{{DATE}}` | 2026·05·15 | 中點 separator、`YYYY·MM·DD` |
| `{{IMAGERY_HINT}}` | (template-specific) | T02 物件 / T03 chart 形狀 / T04 photo tag / T05 對照詞 / T06 scene |

## 中文 hero text 渲染失敗備案

ChatGPT image / NanoBanana / Midjourney 對中文字渲染都不穩。Workflow：

1. 第一次 prompt 含中文 hero —— 看出來如果中文成功 → ship
2. 中文失敗 → 改 prompt 加 `"Leave a placeholder rectangle 800×280px in the hero area, do not generate Chinese text"` 重 generate
3. 拿到無字版 PNG → Figma / Canva / Keynote 後製疊「{{HERO_TEXT}}」5 分鐘 ship

Hsin 已驗證：後製疊字比硬讓 AI 生中文字穩。

## v0.2 對應 6 templates

依 `config/visual_brand_system.md §12` 的 6 covers real article 對應表：

| Template | 對應 article（v0.2 example） |
|---|---|
| [T01 Pure type](T01_pure_type.md) | 精美的廢話（網-樹-線投資寫作） |
| [T02 Single object](T02_single_object.md) | 單品咖啡（Cerebras IPO） |
| [T03 Data / chart](T03_data_chart.md) | 9% → 34.4%（Anthropic 企業 AI 採用）|
| [T04 B&W photo](T04_bw_photo.md) | 學會閉嘴（Peaky Blinders 講師）|
| [T05 Diagram](T05_diagram.md) | 學最多最先出局（名詞通膨）|
| [T06 Scene / stage](T06_scene_stage.md) | 主角會死配角會富（Schloss/Munger/Caro）|
