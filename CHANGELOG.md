# News Radar · 更新日誌

## [2026-06-03] Frontend-Backend 全面審計 + Dashboard 功能補完

### 修復
- **手機版選單無法打開**：CSS `display:none!important` 覆蓋手機版，導致左側抽屜被隱藏、點「☰」和「更多」畫面變暗但不出選單。在 `.mobile-drawer` 加上 `display:flex;`。
- **Dashboard 所有 onclick 按鈕審計**：確認每個 HTML onclick handler 都有對應 JS 函式、每個 JS 函式都有對應後端或流程。

### 新增
- **提交來源 → 立即發布選項**：提交時可選「🚀 立即發布」（需設 GitHub PAT）或「⏳ 下一輪 pipeline」。設定頁可輸入 GitHub PAT。
- **「我的提交」專區**：設定頁顯示本人提交統計（總篇數/已發布數）、歷史存檔頁可切換「全部 / 📝 我的提交」過濾。
- **Carousel 卡片文字品質**：`_wrap()` 改為在句號/逗號邊界斷行，`_clip()` 確保不回傳殘缺句子。composer prompt 新增卡2-4 自測規則（主詞動詞受詞、禁止 AI 套話、禁止抽象方向）。
- **排程發布 vs 即時發布**：兩種模式，使用者自由切換。

### 變更
- `publish_now.yml` + `publish_now.py` — 獨立 workflow，不經 2h 排程，直接 fetch→compose→publish。
- `submit_source.py` — 新增 `--image-base64` 支援手機上傳圖片。
- `full_pipeline.yml` — Stage 0 處理使用者提交來源。

## [2026-06-02] Agent Ecosystem 建立

## [2026-06-02] Agent Ecosystem 建立

### 新增
- **Analytics Engine** (`src/analytics_engine.py`)
  - 權重互動率計算 (FB/IG/Threads 三平台差異公式)
  - Engagement Velocity 時間序列分析
  - Topic Performance Z-Score 標準分評估
  - Post Lifespan Index 貼文生命週期判定
  - WeightLearner 自我迭代權重調整器
- **Dashboard 分析頁** — Chart.js 圖表: 互動趨勢線圖、主題雷達圖
- **Dashboard 提交來源頁** — 貼上URL、選平台、提交、儲存至 localStorage
- **Dashboard 更新日誌頁** — 從 GitHub raw 讀取本檔案
- **Dashboard Toast 通知系統** — 操作回饋通知

### 修改
- `db.py`: 新增 engagement_per_post view
- `views.sql`: 新增 v_engagement_growth, v_engagement_summary, v_topic_performance_30d, v_account_daily, v_publish_cadence views
- `composer.py`: 新增 Hook 開場多樣化強制規則，7種開場類型輪流用

### 修復
- FB 圖片上傳失敗降級為純文字發布
- LiteLLM 後端自動偵測 Cloud/Mac 環境
- Cover upload token 清理
- Pipeline timeout 從 30min 提高到 45min

## [2026-06-01] Cloud Pipeline 遷移

### 新增
- `full_pipeline.yml` — GitHub Actions 完整雲端流程 (取代10個舊workflow)
- `run_full_pipeline.py` — 一鍵端到端: harvest→score→compose→publish→engagement
- `dashboard/` — sql.js + Chart.js 前端 SPA Dashboard
- `dashboard-deploy.yml` — GitHub Pages 自動部署
- `scripts/verify_harvest.py`, `verify_compose.py`, `verify_publish.py` — 驗證Agent

### 修改
- `llm_brain.py`: 新增 LiteLLM 後端，Cloud/Mac 動態切換
- `composer.py`: Threads 專用情緒化規則 (Rule 6)
- `config.yaml`: 38 feeds 補上 feed_added_at，修復 harvest
- Docker 架構: 舊10個workflow改名.bak停用

### 安全
- 所有 GitHub Secrets 已設定 (15個)
- `llm-router-report/INSTALL.md`: 移除真實 API keys
