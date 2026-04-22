# News Radar · 起源 vision 與 persona benchmarks

> 2026-04-23 從 OneDrive 三份草稿濃縮保留。原檔
> (`user_concern.md` / `FB粉絲專頁調研報告.md` / `起始研究過程.txt`)
> 已隨 OneDrive clone 刪除。
>
> 絕大部分原始想法（token 精省、deterministic 爬蟲、三平台 persona、analyst
> 迭代）都已落地在 `src/` 與 `config/`。本文只保留**尚未完成**或**值得回頭
> 對照**的內容。

---

## 1. 專案 5 點原始 vision（north star · 出自 `user_concern.md`）

Hsin 在 news_radar 啟動前寫下的產品北極星。至 2026-04-23 狀態：

| # | 原始 vision | 2026-04-23 狀態 |
|---|---|---|
| 1 | 三平台各自每半小時發一篇 | ⏳ 目前 `MIN_PUBLISH_INTERVAL = 1hr` + `MAX_PUBLISH_PER_SLOT = 1`。2 週量取勝實驗後評估 |
| 2 | Token 最省優先，願為此犧牲發文量 | ✅ cleaner 0-token；scorer flash 8B；composer 單次產三平台；Claude CLI 備援 |
| 3 | 三個 KOL benchmark 的第一手快速來源 | ✅ 部分。官方部落格已覆蓋；Bloomberg/Reuters/FT 等頂級收費媒體 RSS 不全，目前靠 Decrypt/CoinDesk/TechCrunch 二手覆蓋 |
| 4 | 文章最終注入個人敘述品味 | ✅ `news_radar_soul.md` + 三平台 persona = 三個中文第三人稱 soul |
| 5 | Meta API 數據驅動發文頻率與主題迭代 | ⏳ 部分。analyst + reflector 每 12 cycles 影響**主題選擇**；未影響**發文頻率**或**feed 權重** |

---

## 2. 三個 KOL persona benchmark（出自 `FB粉絲專頁調研報告.md`）

寫手 soul 的 DNA 出處。每次疊代 persona 時可以回來對照「現在的稿子離這三位的品質還差多少」。

| KOL | 定位 | 關鍵特徵 | 對應到我們的 |
|---|---|---|---|
| **IEO 國際經濟觀察** | 全方位國際財經／科技趨勢解讀 | 高頻長文；Bloomberg/Reuters/FT/Economist 頂級來源；聚焦「結構性改變」；宏觀切入具體事件；品牌化資訊圖 | FB persona 的目標質感 |
| **Fox Hsiao（狐說八道）** | 科技戰略／產品邏輯深度拆解 | 第一手官方資訊（Release Notes / API Doc / 創辦人訪談）；「輸入/處理/輸出/整合」四層系統架構；冷靜一針見血 | **Threads persona 的黃金樣本** |
| **游庭皓的財經皓角** | 市場週期／總體經濟科普 | FED 談話、券商研究、官方指標；週期變換敏感；數據圖表 + 快節奏金句 | IG persona 的「科普口吻」元素 |

### 2.1 Fox Hsiao 四段式結構（Threads persona 驗收 checklist）

原始樣本出自 Fox Hsiao 2026 年初關於 Claude Design + Anthropic Labs 發布的貼文。已內化進 `config/platforms/threads.md`，但四段結構作為**每次 persona 改動的驗收 checklist** 保留：

1. **破題 (Hook & Thesis)**：一句話把幾個新聞疊起來給出高度結論——「我不是報新聞，我是解讀戰略」
2. **框架 (Framework)**：用系統架構邏輯拆解（輸入/處理/輸出/整合），避免條列式功能流水帳
3. **驗證 (Validation)**：引用**具體數字與人名**（「20 次提示縮成 2 次」比「非常快」有力百倍）
4. **宏觀 (Macro Insight)**：把其他產品／對手拉進來一起看，從「一個工具」提升到「產業版圖重塑」

**使用方式**：composer 改了 Threads prompt 後，拿新產出對照這四段，若某一段明顯缺失或變薄，視為退步。

---

## 3. 未實作的 v2 候選（出自 `起始研究過程.txt`）

### 3.1 Delta Logging — AI 原稿 vs 人類最終版的 diff 追蹤

當使用者手動修改 composer 草稿後，系統記錄「AI 寫的 vs Hsin 改的」 diff。累積 10 組後丟給 LLM 分析規律，自動更新 `strategic_directives.md` 或 persona。

**前置條件**：要先導入「review queue」——草稿停在人類審核區，修改後才發文。目前是全自動（2026-04-23 起 0.7/0.65 近乎全開），沒有這個環節。

**啟用時機**：若 2 週量取勝實驗後決定做「品質優先」模式（高門檻 + 人類 review），這套 delta logging 是飛輪的必要基礎設施。

### 3.2 Prompt Caching — composer input token 再削減

Gemini 與 Claude 都支援 Context Caching。把 `news_radar_soul.md` + persona + few-shot 範例打包快取，每次 composer 的 input token 大幅降低（甚至部分平台打折或免費）。

**啟用時機**：若量取勝實驗成功需要再提升發文量，或單日 token 成本超過預算，這是下一個 lever。實作成本低（改 composer 的 API call 方式），但要確認 Gemini flash-lite 是否支援 caching。

---

**Provenance**：三份原檔（總約 250 行、大量 Gemini 生成的通用 pipeline 教學）於 2026-04-23 刪除，上述為濃縮後的核心。本檔案是 news_radar 「為什麼要做成這樣」的歷史備忘，不是 SSOT——SSOT 仍是 `docs/System_Architecture.md` 與 `docs/architecture.md`。
