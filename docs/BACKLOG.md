# News Radar · Backlog

> 所有被 **Phase 8.11 MVP scope freeze 凍結** 的需求、主題擴充、人格擴充，集中管理於此。
>
> 目的：未來的你（或接手的 agent）看到這份檔案，就能在不重複做設計討論的情況下，判斷哪些題值得解鎖、解鎖條件是什麼。
>
> **凍結原則**：沒有 engagement 基線數據之前的 scope 擴充全是猜測。Phase 8.11 MVP 必須先跑 14–30 天累積數據，再回頭決定 backlog 解鎖順序。

---

## 當前 Phase 8.11 MVP 範圍（什麼**不**在 backlog）

為避免重新爭論，明確寫下 MVP 包含的範圍：

- **Sources**：`config/config.yaml` 內 13 個 feed（Phase 8.10 Kill-the-X 收斂後的狀態）
- **寫手架構**：**三層式 fused soul**（非 KOL 1-to-1 對應平台）
  - **Layer 1 · 核心 Soul**：`config/news_radar_soul.md` —— 由 user 自身風格融合三位 benchmark KOL（IEObserve / Fox Hsiao / 游庭皓）後萃取的**單一** soul。定義「冷靜且具同理心的科技商業戰略評論家」這個唯一的寫作 DNA。
  - **Layer 2 · 平台特化**：`config/platforms/{fb,ig,threads}_v2.md` —— 同一 Soul × 三種平台寫作慣例 = 三位平台特化寫手（FB 產業觀察家 / IG 科技風尚師 / Threads 辛辣評論）。
  - **Layer 3 · 平台慣例參照**：每個平台檔案引用一位 benchmark KOL（IEObserve、Fox Hsiao、游庭皓）**不是**作為身份模板，而是作為「該平台發文慣例」的實證參照（IEObserve 示範 FB 長文結構、Fox Hsiao 示範 IG 降維類比、游庭皓 示範 Threads 金句節奏）。
  - 關鍵區別：三個平台寫手共用同一 soul、共享「誰買單」「價值鏈轉移」「冷靜同理」的敘事 DNA；差異只在平台字數、版面、互動慣例。新增平台 = 寫第 4 個 `config/platforms/*.md` adaptation，不需要新 soul。
- **Pipeline stages**：Module 1–7
  - 已上線：Module 1 (fetch)、Module 2 (clean)
  - Phase 8.11 要做：Module 3 (rank)、Module 4 (select)、Module 5 (compose v2 with N variants)、Module 6 (scorer)、Module 7 (gate)
  - 已部分存在但待重構：Module 5 composer 現在單篇輸出，要改為 N-variant；legacy `src/scorer.py` 其實是 item-level ranker，要改名並清理
- **Engagement 觀察視窗**：MVP 上線後跑 14–30 天，才考慮是否從 backlog 解鎖任何項目

不在 Phase 8.11 scope 的任何新需求，一律進下面的 A/B/C 三類。

---

## A 類：主動擋掉的（不是漏，是策略決策）

這類項目**不是工程題**，是**品牌/策略題**。要解鎖需要先回答「是否改變品牌定位」。

### A-01 · 加密貨幣

- **現況**：`config/config.yaml` 的 `filters.keywords.must_exclude_any` 有 `crypto` 相關關鍵字；Phase 8.7 diagnostic report 顯示 drop 掉 4 筆 crypto 相關 item。
- **為什麼擋**：fused soul 的「冷靜科技商業戰略評論」調性不碰投機 asset；擔心 crypto pro-bull 內容會讓既有產業讀者取消追蹤。
- **解鎖條件**：
  - 明確寫下要承接的 crypto 子議題（基建 / DeFi / 監管 / macro 流動性討論，而非價格預測）
  - 評估 fused soul 能不能無痛吃下（不需要開新 soul，只需在既有三平台寫出來即可）
  - 評估對既有產業讀者的品牌風險
- **若解鎖要做什麼**：移除 `must_exclude_any` 裡的 crypto keywords、新增 1–2 個高品質 crypto 來源（The Block / CoinDesk research 分類頁，不是價格快訊）、可能需要新 scorer rubric 項目（區分「投機」vs「結構性」crypto 內容）

---

## B 類：現有 soul 能吃，缺的是 source（純工程擴充）

這類項目**不需要新 soul、不需要改 pipeline 邏輯**，只要加 feed + 調 `config.yaml`。fused soul 的既有 DNA 能處理，三個平台寫手都能自然吃下，解鎖成本低。

### B-01 · 科技產品發佈會（Apple Event / NVIDIA GTC / Google I/O）

- **現況**：NVIDIA Blog 有部分覆蓋，但缺「live event coverage」角度。
- **能由哪些平台寫**：FB（產業版圖視角）、IG（產品美學降維講解，尤其 Apple）
- **候選 source**：
  - The Verge RSS（live event 特別強）
  - Apple Newsroom: https://www.apple.com/newsroom/rss-feed.rss
  - CNBC Tech: https://www.cnbc.com/id/19854910/device/rss/rss.html
  - NVIDIA Newsroom 現有 feed 已在 config
- **解鎖條件**：MVP engagement 顯示 IG 缺素材，或 FB 缺產業 hype cycle 題
- **預估工作**：2–4 小時（加 feed、跑 diag、補 tests）

### B-02 · 關鍵產業財報公佈（TSMC 法說 / NVDA / AAPL earnings）

- **現況**：完全無覆蓋。Stratechery 有時 post-hoc 分析但不是即時。
- **能由哪些平台寫**：FB（大行數據、產業邏輯，IEObserve 式的 DNA 題）
- **候選 source**：
  - Seeking Alpha 的 earnings 分類 RSS
  - Reuters 的 earnings 分類頁
  - 各公司 Investor Relations 官方 feed（Apple IR / NVIDIA IR 都有 RSS）
- **解鎖條件**：MVP engagement 顯示 FB 的「大行數據」元素通過率低（structural rubric 過不了 `has_named_citation`）
- **預估工作**：3–5 小時（需要處理 earnings 特殊格式，可能要加 source_type: `earnings`）

### B-03 · 產業經濟分析（半導體 / EV / 能源深度報告）

- **現況**：Stratechery + Zeihan 已部分涵蓋。缺台股視角（台灣產業分析要嘛在券商內部報告不公開、要嘛在內容農場品質低）。
- **能由哪些平台寫**：FB（長文產業版圖），Threads（單點犀利論斷）
- **候選 source**：
  - Semianalysis（付費 tier 的分析不能抓，但 free tier 有）
  - Visual Capitalist RSS
  - Our World in Data 的新 dataset notifications
- **解鎖條件**：MVP engagement 顯示缺「長文 400–600 字」素材密度
- **預估工作**：2–3 小時

---

## C 類：跟現有 fused soul 不搭（需要決定是否養新 soul / 新帳號）

這類才是真正的「缺」。**解鎖成本很高**：每多一個 soul = 多一份 soul guide（全新的 `config/*_soul.md` + 三平台 adaptation） + 多一套 scorer rubric + 多一份 engagement 追蹤 + 多一份風險稀釋既有品牌識別。

### C-01 · 台灣房地產

- **現況**：完全無覆蓋。
- **為什麼 fused soul 吃不下**：當前 soul 的 DNA 是「國際科技商業 × 價值鏈 × 冷靜同理」，不是「本地化實價登錄 / 房市政策 / 區域差異」的在地角度。硬讓三平台寫手吃下會寫出不倫不類的內容。
- **若要做**：需要新 soul（假設命名「**房市 soul**」+ 對應平台 adaptations），負責：
  - 實價登錄解讀
  - 內政部房市政策解析
  - 區域比較（北北桃 / 中台灣 / 南科效應）
- **候選 source**：591 部落格 RSS、內政部不動產資訊平台、信義房屋研究室
- **解鎖條件**：
  - 目標讀者群與既有科技商業讀者重疊度 > 30%（否則是兩個不同的品牌）
  - 決定要不要開獨立帳號處理（會增加平台運營複雜度）

### C-02 · 氣候／能源轉型

- **現況**：完全無覆蓋。
- **語意澄清**：
  - 若指「氣候變遷的產業影響」（碳交易、綠能政策、能源轉型）→ fused soul **勉強可以**吃（屬於價值鏈/政策影響題），放 B 類即可
  - 若指字面的「氣候景觀」（自然景觀、氣候現象、極端天氣）→ National Geographic 調性，fused soul 吃不下，屬 C 類
- **若屬後者、要做**：需要新 soul（「**地球觀察 soul**」），或接受這條不做
- **解鎖條件**：先由 user 釐清語意

### C-03 · 軍工戰爭／地緣衝突

- **現況**：Zeihan 訊號源有涵蓋地緣政治宏觀，但 **fused soul 是科技商業戰略視角，不是戰地/國安分析** —— 有輸入沒有合適的輸出。
- **為什麼 fused soul 吃不下**：「冷靜科技商業戰略評論」不是軍事/國安分析師調性，寫出來會淪為「新聞搬運工」沒有 angle。
- **若要做**：需要新 soul（「**地緣參謀 soul**」），調性類似 War on the Rocks / Foreign Affairs 的短文評論
- **候選 source**：Institute for the Study of War、Jane's Defence、CSIS RSS
- **解鎖條件**：明確要承接軍工題還是僅限地緣政治宏觀（後者 Zeihan 已夠用）

### C-04 · 視覺為主新聞（體育 / 災害 / 娛樂突發）

- **現況**：上輪討論浮現的題。完全不在當前 pipeline 能力內。
- **為什麼複雜**：這不只是新 soul 問題，是**整條 pipeline 都要改**：
  - Module 1/2：fetcher 要下載圖片 hash 本地、cleaner 要產 `visual_markdown`（VLM 做圖片描述）
  - `schema.py`：`NewsItem` 加 `visual_summary: str`、`source_type = 'visual_primary'`
  - Module 3 ranker：新 feature `visual_signal_strength`
  - Module 5 composer：寫手 prompt 要注入 `visual_summary`
  - Module 6 scorer：新 component `visual_narrative_fit`
  - Module 7 gate 已支援的 `ig_requires_og_image` 硬規則是 MVP 給 IG 最低保障，不處理 visual-primary item
- **解鎖條件**：MVP 上線後，engagement 數據顯示 IG 因視覺素材弱而明顯落後 FB/Threads
- **預估工作**：Phase 9.x 的整條 vertical slice，20–40 小時

### C-05 · 擴張新 soul 的通用檢核表

凡是 C 類要解鎖（養新 soul），都必須先過以下檢核：

1. **目標讀者 Venn diagram**：新 soul 目標讀者跟既有 fused soul 讀者重疊度？
2. **發文平台策略**：跟現有 FB/IG/Threads 同帳號共用，還是開新帳號？
3. **品牌風險**：既有讀者會不會因為新內容取消追蹤？
4. **Scorer rubric**：新 rubric 要多久能到可用狀態（參考 Phase 8.6 花的時間）？新 soul 要不要也配三個平台 adaptation？
5. **Engagement 追蹤**：能和既有數據做對比嗎？新 soul 的 baseline 是 0，無法比較。

---

## D 類 · 部署與運維（Deployment / Infra）

B/C 類是關於「內容與題材」，D 類是關於「執行環境」。本類有獨立優先度：D-01 是 MVP 跑穩後**第一個**要處理的基礎建設題。

### D-01 · 可靠的免費雲端全自動部署

- **現況**：
  - Pipeline 必須在本機（使用者 Mac）手動觸發或透過 `cron` / `launchd` 跑。
  - SQLite DB、cache、logs 全部落在本機；儲存空間會持續長大、跨機器不可攜。
  - 發文節奏綁使用者開機時間；24 小時真正自動化無法達成。
- **為什麼現在不做（MVP 期）**：
  - 部署環境的選擇依賴 engagement 數據（哪些模組最吃 CPU / API quota / 儲存）。未開始跑之前選平台會選錯。
  - 雲端遷移 ≠ 加 feature；對 Phase 8.11 MVP「首發 + 14 天 engagement baseline」沒有貢獻，反而會延後上線。
- **解鎖條件**：
  - MVP 連續穩定跑 14 天，Module 3–7 的 I/O 契約沒再大改
  - 觀察到的痛點：本機儲存 / 使用者電腦不在線導致漏發 / 需要跨裝置同步
- **若解鎖要做什麼（候選評估表）**：

  | 候選平台 | 優點 | 限制 / 風險 | 適合度 |
  |---|---|---|---|
  | **GitHub Actions** + cron | 免費額度穩、secrets 好管、log 可追；社群最熟 | 執行環境每次 fresh，SQLite 要另存（artifact / external） | ★★★★ 首選 |
  | **Cloudflare Workers + D1** | 全免費、低延遲、D1 是 serverless SQLite | Python 支援弱（要改 TS/JS） | ★★ 需改語言 |
  | **Fly.io / Railway / Render free** | Python 友善、有 background worker | 免費層常有限制（冷啟動、跑滿會停） | ★★★ 備援 |
  | **Modal** | Python 原生、serverless、免費額度給預付 | 供應商鎖定較重 | ★★★ 備援 |
  | **自架 VPS**（Oracle Cloud free tier） | 最靈活、無限制 | 運維負擔、非全託管 | ★★ 要有運維能量才選 |

- **關鍵技術決策（解鎖當天要拍板）**：
  1. **DB 位置**：Turso（LibSQL）或 Supabase（Postgres）取代本地 SQLite。若選 GH Actions 也可用 SQLite + commit 回 repo，但不推薦（race condition、repo 體積會爆）
  2. **Secrets 管理**：所有 `.env` 變數（`GEMINI_API_KEY` / `FB_PAGE_ACCESS_TOKEN` / `THREADS_ACCESS_TOKEN` / `IG_ACCESS_TOKEN`）走平台 secrets，**絕不 commit**
  3. **排程**：每 30–60 分鐘跑 `run_pipeline.py`；cadence 邏輯照搬現有（1h cooldown、2h rescue mode）
  4. **通知**：發文失敗 / quota 耗盡 / emergency_publish 觸發時，透過 email 或 webhook 通知
  5. **觀測**：把 `telemetry.jsonl` 送雲端 log（Axiom / Better Stack free tier）而非只落 runner 檔案系統
  6. **媒體策略**：雲端環境**絕不下載圖片／影片**到執行環境 disk；只傳 URL 給 publisher。本機 `--download-preview` 僅作為 dev 工具
  7. **Claude fallback 路徑**：當 Gemini 配額耗盡時，雲端該怎麼跑 Claude composer？選擇：(a) 當場呼叫 Claude API 自動接手；(b) 暫停該次 cycle、寄 email 給使用者手動介入；(c) 壞掉就跳過。這要搭配 quota budget 評估。

- **預估工作**：Phase 9.x 一條 vertical slice，8–16 小時（看選哪個平台）
- **不在本階段做的副作用**：首發跑完後，使用者需要手動觸發或靠本機 cron 維持發文。這可以接受兩週。

---

## Phase 8.11 MVP 上線後的「解鎖優先順序建議」

**假設** MVP 跑 14 天後得到 engagement 基線，回來看 backlog 的順序應該是：

1. **優先：D-01 雲端部署**（使用者電腦空間與可靠性壓力；不做會卡住長期自動化）
2. **次優：B 類的來源擴充**（若 engagement 顯示 structural rubric 過不了門檻 → 補大行數據來源；若 IG 素材稀 → 補產品發佈會來源）
3. **再次：A-01 crypto**（若確認品牌方向要納，則解鎖）
4. **再次：C-01 / C-03 的單一新 soul 實驗**（一次只加一個 soul，小規模測試兩週再決定留不留）
5. **最後：C-04 視覺 vertical slice**（規模最大，不輕啟動）

---

## 維護規則

- 新需求 / 新 scope 第一站就是本檔，分到 A / B / C
- 每條 ticket 應標明：**現況、為什麼現在不做、解鎖條件、若解鎖要做什麼**
- MVP 跑完回來看 backlog 時，根據數據 **只解鎖 1–2 條**，不要一次全攤開
- 解鎖後把該條從本檔移到 `AGENT_WORKLOG.md` 當期 Phase entry
