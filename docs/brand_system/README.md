# News Radar · Brand System Assets

Visual brand system 交付物索引（claude design 設計、Hsin 2026-05-15 收進 repo）。

## 檔案

| 檔案 | 說明 | 用途 |
|---|---|---|
| `visual_brand_system_v0.1.html` | claude design 原 HTML scroll-spec doc | 視覺 reference、跨裝置看（瀏覽器打開） |
| `../../config/visual_brand_system.md` | canonical text spec（從 HTML 萃取要點）| Pipeline / PM agent 寫 image prompt 時讀 |

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

## 已知 placeholder

- T04 photo 是 striped block + Unsplash tag note（之後換真實 B&W photo）
- T02 hourglass 是 basic vector stand-in（之後可用 Noun Project / Streamline icon library）
- 不是 Figma file —— spec doc only、templates 後續可用 Figma 重建
