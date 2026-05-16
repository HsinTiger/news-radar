# News Radar · Brand System Assets

Visual brand system 交付物索引（claude design 設計、Hsin 2026-05-15 收進 repo）。

## 檔案

| 檔案 | 說明 | 用途 |
|---|---|---|
| `visual_brand_system_v0.2.html` | claude design v0.2（**current**、real articles + Chinese masthead）| 視覺 reference、PM agent 寫 prompt 時看 |
| `visual_brand_system_v0.1.html` | claude design v0.1（historic、representative samples）| 版本對照、不再使用 |
| `prompt_library/` | 6 個 template prompt 模板、calibrated for ChatGPT image | 每篇 dispatch 時 PM agent 抽 hero text 後填空、給你 copy 給生圖工具 |
| `../../config/visual_brand_system.md` | canonical text spec（從 v0.2 HTML 萃取）| Pipeline / cron / PM agent 機器讀 |

## 兩處設計

- **canonical text spec** 在 `config/visual_brand_system.md`：機器可讀、git 版控、cron + agent 直讀
- **視覺 reference HTML** 在 `docs/brand_system/`：人可讀、瀏覽器渲染、看到實際 6 個 cover 範例

兩者同步更新、互相引用。

## 怎麼看 HTML

```bash
open docs/brand_system/visual_brand_system_v0.1.html
```

或拖進 Chrome / Safari 直接看。HTML 自帶 Google Fonts CDN、需要網路第一次 load。

## v0.1 → v 後續

claude design 後續迭代版（v0.2、v0.3...）放同一資料夾、檔名遞增。canonical text spec `config/visual_brand_system.md` 同步更新版本號。

舊版保留作 reference、不刪除。

## v0.2 變更摘要（2026-05-15 晚）

- **Masthead pivot**: 主力爸爸我錯了 (Noto Serif TC 700 28px) 左上 為主、NEWS RADAR · Nº · date (mono) 右上 為副
- **6 covers swap 進 real article hero text**（對應今天 6 篇 real ad-hoc）
- **§4.5 Do #4 重寫**：「Chinese masthead + EN watermark」取代原「NEWS RADAR top-left」規則
- **cv5/cv6 visual zone 從 380 → 280px**、hero font 從 124 → 112px（修正 visual 與 hero text overlap）
- **cv3 duplicate OPENAI label 移除**

## 已知 placeholder（v0.2 仍存在）

- T04 photo 仍 striped block + Unsplash tag note（之後給真實 B&W photo source）
- T02 coffee cup 是 vector stand-in（Cerebras 篇可考慮 swap 成 wafer chip 視覺、v0.3 議題）
- 不是 Figma file —— spec doc only、templates 後續可用 Figma 重建

## v0.2.1 變更摘要（2026-05-15 深夜）

- **§13 Inline Image Workflow 加入** —— body 內嵌圖走 markdown marker blockquote（不 upload 實際圖）、含 Path B 搜尋 + Path C 生圖 prompt 雙路徑、Hsin 自己找圖或生圖
- **Visual editor 5 動作 codify**（substack_soul.md §10.1 連動）
- 第一個 working case: Substack draft 197913816（配角會富 圖文 markers v2）
- 補充說明：Path D 全自動 upload 在 5/15 prototype 過 test draft 197911590 + 197912645、技術通但 token cost / 找圖摩擦 / 版權判斷三條 trade-off 後改走 §13 markers 路線
