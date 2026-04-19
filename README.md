# 📡 News Radar｜科技商業新聞 → FB / Threads 自動化管線

> 目的：讓「優質短文 + 官方數據 + 一致視覺」可以**每天穩定輸出**到 FB 粉專與 Threads，初期培養粉絲、後期接流量分潤。
> 設計哲學：**Token 一滴都不浪費**，把 LLM 的算力只花在「認知與邏輯推演」，其他全部交給確定性程式碼。

---

## 🏗️ 架構摸底報告 (Executive Summary)

基於既有 `alpha_pipeline.py` 的實證基礎，`news_radar` 採三段式 **async pipeline** 架構：
1.  **Module 1: Harvest** - SQLite cache + 外部 RSS/API 抓取（0 Token）。
2.  **Module 2: Structured Logic** - Pydantic 強制 JSON 輸出，區分 Fact / Opinion / SeparatorOutput。
3.  **Module 3: Personality Engine** - 三方 Persona 紅藍對抗 (Bull / Red Team / Arbitrator)，確保觀點不偏頗。

**核心模式與約定**：
- **Logging**：採 `[Module N] / ↳` 前綴風格，不依賴外部 Logging 函式庫。
- **錯誤處理**：極簡保護色，「Cache-first → API → Cache-write」。
- **SQLite 基底**：沿用 `id + content + timestamp` 三欄式極簡風格。
- **Soul 分工**：
    - `intepreter_soul.md`：願景式轉譯，硬性規定 **Anti-Conclusion Protocol**（禁總結式收尾）。
    - `empirical_soul.md`：實證考據，強烈要求官方數據出處。
    - `visual_soul.md`：視覺美學控管，莫蘭迪低飽和 + Nano Banana 模型。
    - `news_radar_soul.md`：**[NEW]** 速報專用，融合 Fox Hsiao + IEObserve 風格。

---

## 1. 與既有系統的關係

`news_radar/` 是 **獨立的兄弟管線**，與 Substack 長文流程（`master_brain.md`）保持平行。

| 設計約定 | 既有 Alpha Pipeline | News Radar |
|---|---|---|
| **主控檔** | `master_brain.md` (Phase 0~4) | `news_radar_brain.md` (開發中) |
| **儲存** | SQLite (`Alpha_Pipeline.db`) | SQLite (`news_radar.db`) |
| **結構化** | Pydantic (`Fact` / `Opinion`) | Pydantic (`NewsItem` / `Draft`) |
| **人格封裝** | 3 Souls | 4 Souls (含 `news_radar_soul.md`) |
| **目錄風格** | `KnowledgeBase/` | `news_radar/state/` & `drafts/` |

---

## 2. 資料流（5 階段）

```
[1. Harvest 採集]                ← Deterministic, 0 token
   feedparser + httpx 抓 RSS
        │
        ▼
[2. Clean 清洗]                  ← Deterministic, 0 token
   trafilatura 純化 HTML → Markdown
   抓 og:image
   過濾字數 / 關鍵字白名單
        │
        ▼
[3. Score 信心評分]              ← 約 200 token / 篇 (Gemini Flash)
   是否含官方數據？是否有具體數字？
   產業重要性評估 → 0~1 分數
        │
        ▼
[4. Compose 撰寫]                ← 約 1500 token / 篇 (含 cache hit)
   引用 news_radar_soul.md
   Pydantic 強制 JSON 輸出
   產出 hook / framework / validation / macro_insight
        │
        ▼
[5. Review & Publish 發布]
   信心 ≥ 0.85 → 自動排程
   信心 < 0.85 → 進審核佇列 (本地 Web UI)
   發布到 FB Page + Threads
        │
        ▼
[6. Reflect 反思 (週批次)]       ← 約 5000 token / 週
   AI 原始版 vs Hsin 修改版
   自動更新 news_radar_soul.md
```

**Token 預算估算**（每天 3 篇短文）：
- Score：3 × 200 = 600 token
- Compose：3 × 1500 = 4500 token
- Reflect：5000 token / 週 ≈ 700 token / 天
- **每日總計 ≈ 5800 token，約 0.005~0.02 美元（Gemini Flash 免費額度內可全免）**

---

## 3. 目錄結構

```
news_radar/
├── README.md                    ← 你正在讀這份
├── config/
│   ├── config.yaml              ← RSS 來源、關鍵字、閾值、發文時段
│   └── news_radar_soul.md       ← 短文 persona（Fox Hsiao 風格）
├── docs/
│   ├── META_API_SETUP.md        ← FB + Threads API 從零設置（必讀）
│   └── RUNBOOK.md               ← 出事故時怎麼救
├── src/
│   ├── schema.py                ← Pydantic 模型
│   ├── db.py                    ← SQLite 初始化、CRUD
│   ├── fetcher.py               ← RSS / HTTP 抓取
│   ├── cleaner.py               ← trafilatura 清洗
│   ├── scorer.py                ← AI 信心評分（Milestone 2）
│   ├── composer.py              ← AI 撰寫（Milestone 2）
│   ├── publisher.py             ← FB + Threads 發布（Milestone 2）
│   ├── reviewer_ui.py           ← FastAPI 審核面板（Milestone 2）
│   └── reflector.py             ← 週迭代（Milestone 3）
├── db/
│   ├── schema.sql               ← 建表腳本
│   └── news_radar.db            ← 執行後自動產生
├── state/
│   └── workflow_state.json      ← 當前處理進度
├── logs/
│   └── execution_log.jsonl      ← 每次 LLM call 的 token 紀錄
├── drafts/
│   └── YYYY-MM-DD/              ← 每日草稿快照
├── .env.example
├── requirements.txt
└── run_harvest.py               ← Milestone 1 入口（端到端跑一次採集）
```

---

## 4. Milestone 拆分

### ✅ Milestone 1：採集層 + 設定檔（這次完成）
產出：能跑 `python run_harvest.py`，從 RSS 抓回乾淨的 Markdown 並存進 SQLite，**不呼叫任何 LLM**。
驗收：跑完後 `news_radar.db` 裡有 N 筆 `news_items`，每筆都有 `clean_markdown` 與 `og_image_url`。

### ⏭️ Milestone 2：AI 處理 + 審核 UI + 發文器
- `scorer.py` + `composer.py`（Gemini Flash 免費額度為主）
- `reviewer_ui.py`（本地 FastAPI，`localhost:8000`，列出待審清單，按一鍵發布）
- `publisher.py`（Meta Graph API：FB Page Feed + Threads Container/Publish）
- launchd 排程（每 4 小時跑一次採集 + score）

### ⏭️ Milestone 3：自我迭代 + 視覺處理 + 雲端遷移
- `reflector.py`：每週吃 10 筆 (AI 原始版 vs 你修改版)，更新 `news_radar_soul.md`
- 圖片處理：抓 og:image → crop_image.py 統一裁切 → 加品牌浮水印
- 評估免費雲端：GitHub Actions（cron）+ Cloudflare Pages（review UI）

---

## 5. 「混合審核」信心分機制

每篇 AI 產出的草稿會帶一個 `confidence_score`（0–1），由四個訊號加權：

| 訊號 | 權重 | 衡量方式 |
|---|---|---|
| 是否含具體數字 | 0.25 | 正則抓 `%`、`$`、`億`、`倍`、年份 |
| 是否引用官方來源 | 0.30 | `source_domain` 是否在白名單 |
| 結構完整度 | 0.25 | hook / framework / validation / macro_insight 四欄是否都填滿 |
| 與既發內容相似度 | 0.20 | 與最近 30 篇 cosine similarity，太高扣分（避免重複） |

**閾值（可在 `config.yaml` 調）**：
- `≥ 0.85`：自動排程發布
- `0.65 ~ 0.85`：進審核佇列，等你勾 OK
- `< 0.65`：直接 Drop，不浪費你的眼睛

---

## 6. 「省 Token」三道防波堤

對應 Gemini 給的「Harness / Context / Prompt」三層：

1. **Harness 層**：所有爬蟲、清洗、字數過濾、relevance filter 全用 Python 完成。低於 300 字、不在關鍵字白名單的，直接 Drop，**從來不送進 LLM**。
2. **Context 層**：`news_radar_soul.md` + 過往 5 篇示範文章 → 用 Anthropic Prompt Caching / Gemini Context Caching 包成快取（首次寫入後，後續每次只付增量 token 費用，最高省 90%）。
3. **Prompt 層**：強制 Pydantic JSON 輸出，禁止「好的，這是一篇為您...」這類前後綴廢話。

---

## 7. 你需要做的人工決策

只剩兩件事是程式碼無法替你完成的：

1. **Meta API 申請**：詳見 `docs/META_API_SETUP.md`，預估 30–60 分鐘人工操作（要登入你自己的 Meta 帳號、建立開發者 App、綁定粉專、取得長效 Token）。
2. **首次審核校準**：頭兩週的草稿全部進審核佇列、不自動發。等你修改紀錄累積到 10 筆，`reflector.py` 會自動把你的口味學進 `news_radar_soul.md`，之後信心分才會逐步開放自動發。

---

## 8. 後續延伸（暫不做但保留設計空間）

- **影音**：未來可接 yt-dlp 抓官方 Keynote 影片片段，剪 30 秒短片（利用既有 `yt-dlp` 二進位）
- **跨平台**：X (Twitter) / LinkedIn / 小紅書，只要在 `publisher.py` 加 adapter
- **變現**：FB Reels Bonus、Threads 創作者基金、Substack 連結回流（Notion 已有 alpha pipeline 的長文，可以做「短文鉤子 → 長文付費牆」漏斗）

---

## 9. 一行使用法

```bash
# 環境準備（首次）
cd news_radar
pip install -r requirements.txt
cp .env.example .env       # 編輯 .env 填入 Meta API tokens
python src/db.py           # 初始化資料庫

# 跑一次採集（Milestone 1 已可用）
python run_harvest.py
```
