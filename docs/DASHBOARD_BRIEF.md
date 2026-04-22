# News Radar Editor Dashboard · Brief for Claude Design + Claude Code

> 2026-04-23。為新小編 Alex 設計一個「看得懂、不會怕按錯、不需要懂後端」的 read-only web dashboard。
> 目標是 Claude Design 做設計 → Claude Code 在一個新的 repo（**不是** `news_radar/` 主 repo）實作 → 部署到 GitHub Pages。

---

## 1. Mission (one paragraph)

News Radar 是一個每小時自動產三平台貼文、自動發布到 FB / IG / Threads 的 pipeline。**這個 dashboard 讓小編 Alex 看到系統現在在做什麼、接下來要發什麼、過去發了什麼、哪些被擋掉以及為什麼**。v1 是 **read-only**——Alex 只看不改，一切修改仍由自動系統執行。未來若 Alex 可信且熟悉流程，再開有限的寫入能力。

**Success criteria for v1：** Alex 每天早上打開 dashboard，5 分鐘內知道：
(a) 昨晚到現在發了幾篇、哪幾個平台、哪幾篇有問題
(b) 接下來的 1-3 小時自動會發什麼
(c) 有沒有被擋掉的好料（分數不夠）值得回頭看
完全不需要開終端機或碰 GitHub。

---

## 2. User persona

**Alex，新聘社群小編，28 歲，會用 Canva、IG、Threads 排程。**

- 技術背景：**零**。不會用命令列、不懂 git、不懂 API、看到 JSON 會累。
- 工作時間：每天早上 9:00-18:00。早上到公司第一件事檢查 dashboard。
- 最想知道的：「**昨晚發的那篇為什麼按讚那麼少？**」「**今天下午 3 點會發什麼？**」「**有沒有需要我提醒老闆的狀況？**」
- 最怕的：「**我會不會不小心按壞什麼？**」（回答：v1 沒有任何按鈕可以按壞）

---

## 3. Jobs-to-be-done（依重要性排序）

| # | Alex 想做的事 | 對應的畫面 |
|---|---|---|
| JTBD-1 | 看接下來 24 小時系統會自動發什麼 | Queue 頁 |
| JTBD-2 | 看最近 7 天發了什麼、反應如何 | Archive 頁 |
| JTBD-3 | 看為什麼某篇被擋掉（分數低、主題被降權、圖片不合規） | Dropped 頁 |
| JTBD-4 | 看系統健康度：還在跑嗎？上次發文距現在多久？Token 用量還好嗎？ | Home 頁 |
| JTBD-5 | 看三平台 persona 現在是什麼調性（對照自己的語感） | Persona 頁 |
| JTBD-6 | 看單篇的完整資訊：三平台各自內容、原始新聞、評分邏輯、圖片 | Detail 頁（從任何列表點進來） |
| JTBD-7 | 看主題權重現在偏什麼（例如「AI 模型 1.8 分、meme coin 0.5 分」） | Settings 頁（read-only display） |

---

## 4. 資料來源（read-only，無後端）

**關鍵架構決定**：dashboard 不需要伺服器。所有資料都從 GitHub 公開 API 拉。

- **SQLite DB**：`https://raw.githubusercontent.com/HsinTiger/news-radar/state/data/01_harvest/news_radar.db`
  - 大小 ~1.6-2.0 MB，每小時更新一次
  - 瀏覽器用 [`sql.js`](https://sql.js.org/) (Emscripten-compiled SQLite) 直接 open
  - 建議快取策略：每 5 分鐘 re-fetch，加 `If-Modified-Since` header
- **Archive markdown 檔案**：`https://raw.githubusercontent.com/HsinTiger/news-radar/state/archive/YYYY/MM/DD/*.md`
  - 只在 publish 成功時寫入，是 human-readable 的發文歷史
- **Config**：`https://raw.githubusercontent.com/HsinTiger/news-radar/main/config/news_radar_soul.md` + `config/platforms/*.md`（給 Persona 頁顯示用）
- **LAST_RUN.txt**：`https://raw.githubusercontent.com/HsinTiger/news-radar/state/LAST_RUN.txt`（系統健康度判斷用）

**零後端、零認證、零 DB server**。部署到 GitHub Pages 就結束。

---

## 5. 資料模型（UI 關心的欄位，非完整 schema）

### 5.1 `news_items`（原始新聞素材）
```
id, url, title, published_at, feed_name, source_type (article/social/video),
og_image_url, og_video_url, topic_category, topic_confidence,
weighted_score, status (fetched/scored/drafted/published/dropped),
drop_reason
```
**小編關心**：title、published_at、feed_name、topic_category、weighted_score、status、drop_reason。

### 5.2 `drafts`（AI 產出的「canonical」草稿 = FB 版本）
```
id, news_id, title, full_text, confidence_score, score_breakdown (JSON),
image_url, generated_at, llm_provider, llm_model, cost_usd,
status (pending_review/auto_approved/published),
queue_status (NULL/queued/published/stale/failed),
publish_at
```
**小編關心**：title、confidence_score、status、queue_status、publish_at、image_url、cost_usd（累計看 token 花費）。

### 5.3 `platform_drafts`（三平台各自的變體）
```
draft_id, platform (facebook/instagram/threads), full_text, char_count
```
**小編關心**：每個 draft 的三平台文字並排顯示，看 Threads 版 vs FB 版語氣的差異。

### 5.4 `publish_log`（發布紀錄）
```
draft_id, platform, platform_post_id, posted_at, success (1/0), error_message
```
**小編關心**：哪篇在哪個平台、哪個時間發、成功還失敗、失敗原因。

### 5.5 `engagement_stats`（發布後的互動數據）
```
draft_id, platform, platform_post_id, fetched_at,
likes, comments, shares, saves, reposts, quotes, replies, views, reach
```
**小編關心**：每篇的讚/留言/分享/觸及，用來回頭檢討 persona 有沒有下對。

### 5.6 `topic_weights`（主題權重）
```
category_id, display_name, weight (0.3-2.0), last_updated_at,
update_reason (initial_seed/back_prop/manual), sample_count
```
**小編關心**：現在系統偏向什麼主題（weight 高 = 想發、weight 低 = 不想發）。

---

## 6. 關鍵狀態機（用在 Queue 頁的色彩語言）

每個 `news_item` + `draft` 組合會走過以下狀態：

```
fetched (灰) ──→ scored (藍) ──→ drafted (紫) ──→ queued (黃) ──→ published (綠)
                     │                  │                              │
                     ↓                  ↓                              ↓
                  dropped (紅)     rejected (紅)                    failed (紅)
                  (低分/主題被降權)                              (發布錯誤 / 圖片不合規)
```

**色系建議**（配 Tailwind 調色盤）：
- 灰 gray-400｜藍 sky-500｜紫 violet-500｜黃 amber-500｜綠 emerald-500｜紅 rose-500

---

## 7. 畫面規格（5 個主畫面 + 1 個 Detail overlay）

### 7.1 Home（首頁）

**頂部**：3 個 big stat cards
- **上次發文**：「2 小時前 · FB/IG/Threads · 標題」（若 > 3 小時用橘色警示）
- **Queue 中**：「目前 N 篇等發、最近一篇預計 HH:MM」
- **今日 token 成本**：「$0.14 USD · 48 次 LLM call」（綠燈 < $1 / 黃燈 $1-3 / 紅燈 > $3）

**中段**：最近 24 小時時間軸（timeline）
- 橫軸：過去 24 小時（0-24）
- 標記每次發文、每次 scoring、每次 harvest cycle
- 點進去 → 對應 detail overlay

**底部**：3 個迷你列表
- 左：**下一批即將發布**（Top 3 queued，依 publish_at 排序）
- 中：**最近 7 天平均互動**（大概 KPI，例如「FB 平均 12 讚 / IG 平均 8 讚 / Threads 平均 5 讚」）
- 右：**系統警示**（若有：Gemini 429、Meta API error、image_prep failed，過去 24 小時）

### 7.2 Queue（佇列）

列表，欄位：
| Column | 範例 | 備註 |
|---|---|---|
| 狀態 icon | 🟡 queued | 用 §6 色彩 |
| Title | 「Anthropic 推出 Claude Design，所有應用層都在射程內」 | 可點進 Detail |
| Score | 0.78 | 顏色：≥0.8 綠、0.65-0.79 黃、<0.65 紅 |
| Topic | 🤖 ai_model | emoji + snake_case |
| 圖片狀況 | ✅ 合規 / ⚠️ rewrote / ❌ failed | IG aspect-ratio check |
| 預計發布 | 15:30（40 分鐘後） | |
| 平台 | FB IG TH | 三個小 badge |

**預設排序**：publish_at 最近的在上。

**Filter bar**：status (all/queued/drafted/published/failed) + topic + score range + 日期

### 7.3 Archive（歷史）

Card grid 格式（視覺重於密度，小編要能「一眼看到最近發了什麼」）：
- 每張卡：標題 + 縮圖 + 發布時間 + 三平台 badge（含各自 likes/comments 迷你數字）
- 預設顯示最近 7 天，無限捲動往前
- 點卡 → Detail overlay

### 7.4 Dropped（被擋掉）

Alex 要回頭挖「可惜沒發」的好料。
- 列表：title + drop_reason + weighted_score + published_at
- Filter：drop_reason（太短 / 分數不夠 / 主題降權 / 重複 / 其他）
- 每列旁邊一個「🔗 打開原始 URL」的 outbound link，讓 Alex 自己讀原文決定要不要截圖手動發

### 7.5 Persona（語氣）

純展示用。把 `config/news_radar_soul.md` + `config/platforms/{fb,ig,threads}.md` 渲染出來，Alex 可以看：
- 三平台各自的寫作原則
- Hook 範例
- 禁止用語
- 最近一次 reflector update 的摘要

### 7.6 Settings（設定顯示）

純 read-only。顯示：
- 當前 threshold（AUTO_PUBLISH / RESCUE / MIN_SCORE）
- topic_weights 表格（依 weight 排序，可 hover 看 sample_count / last_delta）
- 最近 5 次 reflection_events（時間 + samples_used + 加了什麼規則）

---

## 8. Detail overlay（點任何項目都跳這個）

大型 modal / side panel（不換頁），內容：

**Tab 1 · 三平台內容並排**
- 三欄（FB / IG / Threads）顯示 `platform_drafts.full_text`
- 每欄上方：字數 + 是否超限 + hashtag 數
- 若已發布：底下秀 engagement_stats（👍 💬 🔄 💾）

**Tab 2 · 原始新聞**
- `news_items.title` + `url`（outbound link）
- `clean_markdown` preview（前 500 字）
- `og_image_url` 縮圖
- 來源 feed + published_at

**Tab 3 · 評分邏輯**
- `confidence_score` + `score_breakdown`（視覺化：雷達圖或 bar chart）
- `topic_category` + `topic_confidence` + `topic_rationale`
- `weighted_score = score × topic_weight` 的計算過程

**Tab 4 · 發布歷史**
- publish_log 時間軸
- 每個平台的 success/fail + platform_post_id（點可跳到 FB/IG/Threads 實際貼文）
- 若 fail：error_message

---

## 9. 視覺方向

**參考品牌語言**：
- Buffer（社群排程）的「看起來乾淨、不像工程後台」
- Linear 的色彩語言與留白節奏
- Vercel Dashboard 的時間軸 + stat card 組合
- **避免**：Datadog / Grafana 的密集資料科學風（Alex 會逃跑）

**字型**：sans-serif（Inter / Noto Sans TC 混用，中英文混排場景需特別處理）

**行為語言**：
- 任何「數字」都要有**單位 + 顏色語意**（例：Token `$0.14` 綠色 = 正常）
- 任何「時間」都要**相對時間 + 絕對時間並陳**（例：「2 小時前 · 13:42」）
- 任何「狀態」都要**icon + 文字**（不要只 icon，Alex 會猜錯）

**Dark mode**：必要（Alex 可能早上 8:00 還沒全亮起床就滑手機看）。

**Responsive**：mobile-first。Alex 有時用手機看（通勤、午休）。Home 頁在手機上要能一個捲動看完 stat + timeline + 下一批即將發布。

---

## 10. 技術 stack（給 Claude Code 實作用）

**強烈建議**：
- **Framework**：Next.js 14 static export（`output: 'export'`）或 Vite + React
- **UI**：Tailwind + shadcn/ui（已在 news_radar `.claude/` 有熟悉紀錄）
- **SQLite in browser**：`sql.js` v1.10+ （`@jlongster/sql.js` 更小但一般版就夠）
- **Chart**：Recharts（lightweight）或 visx
- **資料存取層**：寫一個 `useNewsRadarDB()` custom hook，負責 fetch + sql.js open + 快取，其他 component 都從這裡 select
- **部署**：GitHub Pages，repo 名 `news-radar-dashboard`（**不要建在 news_radar 主 repo 內**，避免 launchd 的 `--ff-only` 鎖死本地 clone）

**不要做**：
- 伺服器 / 後端 API（無必要）
- 使用者登入（read-only 公開資料）
- 任何寫入操作（v1 不做）
- 即時 websocket（每 5 分鐘 re-fetch 就夠）

---

## 11. Non-goals（明確排除）

- ❌ 編輯功能（改分數、改文字、改主題權重、改發文時間）
- ❌ 手動觸發 compose / publish 按鈕
- ❌ 直接顯示 API key、.env、LAST_RUN 的 git sha（Alex 看不懂也沒必要）
- ❌ 展示 Python 程式碼 / log 原始檔（同上）
- ❌ 任何需要寫入 GitHub 或 Meta API 的功能
- ❌ 真實 push notifications（Alex 自己看瀏覽器 tab title 的「N 篇待發」就夠）

---

## 12. 附件清單（Claude Design 一併讀這些，Claude Code 實作時也會用到）

- **`docs/architecture.md`**（Mermaid 完整資料流圖，理解系統全貌用）
- **`docs/System_Architecture.md`** §1-§5（run-time 層面的 ground truth）
- **`data/01_harvest/schema.sql`**（完整 DB schema）
- **`config/news_radar_soul.md`**（總 soul）
- **`config/platforms/fb.md` / `ig.md` / `threads.md`**（三平台 persona）
- **最近 3 份已發布 archive**：`data/04_publish/archive/2026/04/*/` 的 markdown 檔（做 mockup 用的真實資料）
- **最近 3 份 pending_drafts**：`data/03_compose/pending_drafts/*.md`（queue mockup 用）

**不要丟進去的**：
- `src/` Python 原始碼（UI 不需要）
- `tests/`（同上）
- `.venv/` / `logs/` / `__pycache__/`（runtime 垃圾）
- 歷史 MORNING_CHECKLIST / OVERNIGHT_REPORT（Alex 不用看）

---

## 13. Handoff 建議順序

1. **階段 1 · Claude Design**：讀這份 brief + 附件，輸出 5 個主畫面 + Detail overlay 的設計稿（Figma 或 HTML prototype）。重點在**資訊密度分配** + **視覺語言**。
2. **階段 2 · Claude Code**（新 repo `news-radar-dashboard`）：
   - scaffold Next.js static export
   - 實作 `useNewsRadarDB()` hook
   - 按設計稿組 5 個頁面
   - 接 GitHub Pages deployment
3. **階段 3 · 你驗收 + 指給 Alex**：跑 local dev 先看，確認數字對得起來；部署到 `https://hsintiger.github.io/news-radar-dashboard`（或 custom domain）；開一個 GitHub Issue 記錄 Alex 的第一輪 feedback。

---

## 附錄 · 真實 DB snapshot 大概長什麼樣（給 Claude Design 腦補用）

- `news_items`：幾千筆
- `drafts`：幾百筆（每篇新聞 composer 會產一份 canonical draft）
- `platform_drafts`：drafts × 3（三平台各一）
- `publish_log`：幾十到幾百筆（只有成功進 queue 且被 publisher 處理過的才有）
- `engagement_stats`：每小時 analyst 執行一次，每次 append 當下數字；同一 post 會有多筆時間序列
- `topic_weights`：**13 筆固定類別**（ai_model / open_source_ai / agent_framework / ... 等）

Dashboard 不需要處理超大資料集——整個 DB 也才 ~2 MB，全部讀進瀏覽器記憶體不是問題。
