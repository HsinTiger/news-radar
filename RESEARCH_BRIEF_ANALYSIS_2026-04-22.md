# Gemini Deep Research → News Radar 最佳化總表

**Session:** 2026-04-22 Pivot 2
**Source:** `docs/research_briefs/gemini調研結果.md` (62 KB / 794 行)
**Read status:** Topics 1-5 深讀 + 程式碼側 diff 完成。Topic 6 (cadence/timing) Gemini 尚未回，已跳過。
**Voice benchmark:** Hsin「科技工作講 Tech Job N Talk」Tim Cook → John Ternus 貼文（個人敘事 + 產業內情 + 情緒節點 emoji + 短打）
**紅線提醒（維持）：** `git push` / `rm` data / 個資 / 大量 API → 停下問。本報告不觸發任何紅線——純讀 + 計畫。

---

## 0. 十秒視角：五題一張表

| # | 主題 | 觸碰哪些檔 | 建議動作量級 | 風險 | SSOT 牽動 |
|---|---|---|---|---|---|
| 1 | Composer personae | `config/platforms/{fb,ig,threads}_v2.md` `config/news_radar_soul.md` `docs/BACKLOG.md` | **中** 三份 appendix 重寫 + 腔調校對 | LLM 被過度規則約束；Gemini 的 FB「決策備忘錄」register 偏離用戶實際個人敘事腔調 | 新增 §A1 |
| 2 | Scorer heuristics | `src/scorer.py` `src/topic_taxonomy.py` `config/tw_supply_chain_keywords.yaml` (新) `config/topic_keywords.yaml` | **高** 新增 3 features + 新增 crypto_web3 category + seed_weight 調整 | seed_weight 只在 cold-start 生效，back-prop 可能覆蓋；新類別與 `other` 模糊 | 新增 §A2 |
| 3 | Topic keywords | `config/topic_keywords.yaml` `config/topic_disambiguation.yaml` (新) `config/topic_exclusion_rules.yaml` (新) `src/topic_classifier.py` | **中-高** 30 新詞 + 8 歧義規則 + 6 排除規則 + 分類器改寫 | Priority rules 複雜，單元測試必要；排除規則過激會 over-filter | 新增 §A3 |
| 4 | Content quality 紅旗 | `src/content_quality_guard.py` `config/quality_redflags_v2.yaml` (新) `config/quality_clickbait.yaml` (新) | **中** yaml-loaded 20+10 pattern + 3 action (warn/rewrite/reject) | Pattern 11/14/18 FP High，必須 `warn` 不 `reject`；`rewrite` 行為需要 compose retry loop | 新增 §A4 |
| 5 | Hashtag 2026 | `src/composer.py` (PLATFORM_HASHTAG_RANGE) `config/platforms/*_v2.md` `config/hashtag_pool.yaml` (新) `src/content_quality_guard.py` | **低-中** FB 3-5→1-2, IG 5-10→3-5 (＊急，Meta 1月新政), Threads 1 不動 | IG 5-10 超 Meta 2026/1 的 5 tag cap，有 shadowban 風險；rotation pool 選錯 tag 影響切題度 | 新增 §A5 |

**交叉相依性：** 2 + 3 共用 `topic_taxonomy` / `topic_keywords` → 先 3 後 2；1 + 5 都寫在 `_v2.md` appendix → 同步改；4 + 5 都落在 `content_quality_guard.py` → 一次 refactor 為 yaml-loaded 比較乾淨。

---

## Topic 1 — Composer personae（寫手靈魂）

### Gemini 給了什麼
三個平台人格卡 + 15 條量化觀察：

**FB「Analytical Mentor」** — 蕭上農犀利 + 產業老兵沉穩；承重牆邏輯；決策備忘錄 register；鐵律：粗體專有名詞、數據錨點、**封閉結論不用問號**、2-3-2 行韻律、轉折詞前 100 字（完讀率 +34%）。

**IG「Visual Synthesizer」** — 精煉視覺系；鷹架不是主角；第一行 **≤15 字** 必須提問或極端對比（不然跳出 88%）；符號做視覺錨點（💡 📉 🚀）；7-11 hashtag 觸發長尾（注意：這點和他自己 Section 5 的 3-5 上限衝突，Gemini 自打臉；以 3-5 為準）。

**Threads「Street-Smart Observer」** — 游庭皓/IEO 腔；第一人稱「我剛剛發現…」；150-250 字最佳不對稱報酬；超過 300 字斷崖；**零 hashtag +15% reach**（實驗值，和現行 1 tag 衝突）。

另含 3 條 2026 趨勢進 BACKLOG：AI-synthesized meta-analysis、anti-perfectionism（反精緻化）、micro-niche jargon。

### 現況（code 摸清）
`config/news_radar_soul.md` (1-199 行) + 三份 `_v2.md` appendix 由 `src/composer.py:70-74` 載入，在 `_build_system_instruction` (L257) 串成單一 system prompt。FB 900 字上限、IG 900 字目標、Threads 500 字硬限。

### Gap + 聲音基準對齊
- 架構面一致（三平台分流）。
- 細節面 Gemini 更具體（轉折詞、字數、黃金三秒量化）。
- **⚠️ 聲音衝突：** 用戶「科技工作講」的 Tim Cook → John Ternus 貼文是**個人敘事 + 產業內情**（「老實說…」「我昨天在…」），Gemini 的 FB「決策備忘錄 / 承重牆」register 偏**企業分析師**。這是最大分歧。

### 建議
1. **FB persona 收 Gemini 結構規則，拒絕 register**：採用轉折詞前 100 字、2-3-2 行、封閉結論、粗體專有名詞；但 **開場必須是個人視角**（「我看到這件事第一個想法是…」、「圈內人都知道…」），不是「本週科技焦點」。emoji 保留在情緒節點（1-2 顆/篇），拒絕裝飾性撒。
2. **IG persona 加「第一行 ≤15 字」硬規則**：取代現行「前 100 字爆發感」模糊描述。加 emoji anchor 白名單（💡 📉 🚀 ⚡ 🎯），其他裝飾性 emoji 禁用。
3. **Threads persona 加「第一人稱開場 + 150-250 字目標」**：現行沒有硬規則管開頭。零 hashtag 留作 A/B test，不立即砍 primary_topic_tag（變動風險）。
4. **BACKLOG.md 新增 2026 三條趨勢**：作為 R&D 方向，不馬上上線。
5. **news_radar_soul.md 加 Voice Benchmark 區塊**：指向用戶實際 FB 貼文（Tim Cook → John Ternus）作為腔調錨點，給 LLM 一個具體 reference，不然永遠被 Gemini 的「雞湯分析師」預設語料拉走。

### 預期效果
FB 貼文像「是 Hsin 寫的」，不像「是新聞稿」；IG 第一行抓眼可量測（字元數檢核）；Threads 更自然、少「報告味」。

### 風險
- 轉折詞規則如果 LLM 機械套用「但是/然而」會產生不自然文法。
- 零 hashtag Threads 是政策變動，不做直接 rollout。
- 過度加規則會 overfit prompt，創作自由度下降——要留「voice > rules」的逃生門。

### 驗證
- 寫 10 篇 test drafts，人工讀：(a) 是否像用戶真聲音；(b) 結構規則命中率（grep 粗體數、問號結尾、第一人稱「我」在前 20 字、字數）。
- 歷史 20 篇已發 FB 貼文做對照組，新 persona 重跑，盲測哪篇更像用戶。

### SSOT
新增 §A1，≤20 行：三份 appendix 路徑、voice benchmark 來源、字數/hashtag/emoji 硬規則表。

---

## Topic 2 — Scorer heuristics（選題信心）

### Gemini 給了什麼
4 個新 feature + 權重校準表：

- **Feature A 資訊密度**（數值錨點＋實體 / 總字數）——Low 難度，純規則
- **Feature B 在地供應鏈近度**（TSMC/聯發科/瑞昱 等 keyword 命中）——Low 難度
- **Feature C 變動量級**（「裁員 20%」「營收翻倍」「併購」）——Mid，regex 或輕量 LLM
- **Feature D 情緒極端值**——Mid，需情緒模型

權重校準（Gemini 版 vs. 現行）：

| 類別 | Gemini 建議 | 現行 seed |
|---|---|---|
| AI Model | 1.7 → 1.7 | 1.70 ✓ |
| Semiconductors / 供應鏈 | 1.4 → **1.7** | 1.40（對應 `supply_chain`） |
| Consumer Electronics | 1.0 → **0.7** | 1.20（對應 `tech_product_launch`） |
| Crypto / Web3 | 1.0 → **1.3** | **類別不存在** |

### 現況（code 摸清）
`src/scorer.py` L26-36 `ScoreBreakdown` 4 LLM-judged features：`data_density`、`strategic_signal`、`news_novelty`、`persona_fit`；聚合成 `confidence_score` (L34)。

`src/topic_taxonomy.py` 10 類別 + seed_weight，**但 seed_weight 只在 cold-start DB 生效，runtime 用 back-prop 計算**——這是地雷。

### Gap
- Feature A 和現行 `data_density`（LLM-judged）重疊，但 Gemini 版是**規則預篩**，可當 fast path 在 LLM 之前。
- Feature B C D **完全缺**。B 最便宜最貼合台灣讀者。
- ⚠️ **crypto_web3 類別不存在**——要新增類別才能套用 1.3 權重。
- ⚠️ **seed_weight 調整要檢 back-prop**：若 back-prop 在跑，調 seed 沒用。

### 建議（按 cost/value 排序）
1. **Feature B 先做**：新 `config/tw_supply_chain_keywords.yaml`，列 TSMC / 聯發科 / 瑞昱 / 日月光 / 台達電 等 30 家；scorer 讀命中數加 bonus。最便宜，最直接抬台灣關聯度。
2. **Feature C 次做**：regex 抓「X%」「X 億」「翻倍」「裁員」「併購」等 magnitude pattern；有命中即 bonus。
3. **Feature A 當 fast-path pre-filter**：不進 scoring model，只做跳過——低密度文直接 quality_score < 0.3 skip LLM 省錢。
4. **Feature D 延後**：多一次 model call 成本/延遲。等 B C 數據驗證再考慮。
5. **權重校準需兩步**：
   - (a) `topic_taxonomy.py` 加 `crypto_web3` 類別 + seed 1.3；同步 `topic_keywords.yaml` 加 crypto keywords（BTC/ETH/DeFi/Web3/Solana/穩定幣 等）。
   - (b) `supply_chain` seed 1.40→1.70；`tech_product_launch` seed 1.20→0.70。**先做 back-prop audit**——若 runtime 覆蓋 seed，要改 back-prop 邏輯或暫停 back-prop 一段。

### 預期效果
- ＋台灣供應鏈新聞覆蓋率（MediaTek/TSMC 次級新聞更容易入選）
- −消費性雜訊（Apple 錶帶類 magnitude 低 → 過濾）
- ＋大變動事件佔比（併購、裁員、財報翻倍）
- ＋crypto 深度討論覆蓋

### 風險
- 台灣 supply chain 過度 boost → AI model / AI agent 主題被稀釋。
- Magnitude regex FP on 年份/日期數字。
- 加 crypto_web3 類別要同步改 `src/topic_classifier.py` 和 `other` fallback——如果沒改分類器，crypto 新聞還是塞 `other`。
- Seed 改動若 back-prop 覆蓋 → 改動無效，是**靜默失敗**風險。

### 驗證
- 最近 200 news_items 重跑 scorer，比較新舊分數分佈。
- 抓 diff 最大的 20 篇人工看。
- Gemini 兩個反例（Apple 錶帶、3M SaaS）必須能被過濾——這是 regression test。
- SQL 查 drafts 表 topic 分佈變化：`SELECT topic, COUNT(*), AVG(confidence_score) FROM drafts WHERE generated_at > '2026-04-15' GROUP BY topic;` 前後比。

### SSOT
新增 §A2：feature 清單表、topic_taxonomy + back-prop 契約（文件化「seed 只在 cold-start 生效」的地雷）、crypto_web3 類別定義。§7 預留 case study slot 給「權重調整觸發 back-prop 覆蓋」這個可能出現的 landmine。

---

## Topic 3 — Topic keywords（分類與歧義）

### Gemini 給了什麼
- **Section 1：30 個新詞** 分散 ai_model（DeepSeek、Claude 4.7、o1/o3、Reasoning Model 等）、ai_agent（Manus、Claude Code、Operator、Computer Use）、tech_product_launch（Blackwell/B200/GB200/Rubin、AI PC、M5 Mac）、supply_chain（CPO、CoWoS-L、HBM4、液冷、玻璃基板、FOPLP）、tw/us_stocks、policy_geopolitics（Sovereign AI、CHIPS Act、Entity List、Tariffs）。
- **Section 2：8 條歧義規則**（priority / regex）——關鍵如「台積電 + 法說會/毛利率 → earnings（不是 supply_chain）」、「輝達 + 供應商/訂單 → supply_chain；輝達 + 股價/財報 → earnings/us_stocks」、「Apple + WWDC/iPhone → tech_product_launch；蘋果 + 供應鏈/砍單 → supply_chain」、「Meta + Llama → ai_model」、「Server + B200 → supply_chain」、「Agent 單詞 → 只在與 AI/LLM 同現才算 ai_agent」。
- **Section 3：ai_agent vs ai_application 準則**——核心差在 Human-in-the-loop（應用）vs. Agentic Loop + Tool Use（代理）。10 個指標產品分類：Devin/Claude Code/Manus → agent；Cursor/Copilot/Perplexity/Notion AI/Midjourney → application；GPT-5/Gemini Pro → model。
- **Section 4：5 家英文媒體覆蓋缺口** — SemiAnalysis（High-NA EUV、Advanced Packaging、ASIC）、Stratechery（Sovereign AI、Antitrust、Cloud Infrastructure）、The Information（Compute Cluster、GPU shortage、Data Center）、Calculated Risk（Housing Starts、Yield Curve、Federal Reserve）、Marginal Revolution（AGI、Total Factor Productivity）。
- **Section 5：6 條排除規則** — Apple + 水果類、Meta + metadata、Agent + 房仲/保險/特務、晶片 + 洋芋片/悠遊卡、Copilot + 副機師/航空、Model + 玩具/鋼彈/模特兒。

### 現況
`config/topic_keywords.yaml`（19-227 行）10 類別約 250 詞。`src/topic_classifier.py:115` 對前 1500 字做 case-insensitive 純關鍵字 match。`config/config.yaml` L240-399 `keywords.must_include_any` / `must_exclude_any` 是全域名單，不是分類器內部。**沒有 priority 規則，沒有條件邏輯，沒有正則組合**。

### Gap
- 30 新詞直接補 yaml 就行。
- ⚠️ 8 歧義規則要**重寫 classifier**，目前架構不支援。
- ⚠️ 6 排除規則目前也沒有容器——要新 schema。
- ai_agent vs ai_application 現行靠 keyword 二分（Cursor in ai_application 列表、Devin in ai_agent 列表），**但沒有原則文件**——下次新工具上市時沒有判準。

### 建議
1. **先補 30 詞**（zero risk，純新增）：這步可以先行。
2. **新 `config/topic_disambiguation.yaml`**：8 條規則用 priority + condition schema，如：
   ```yaml
   - id: tsmc_earnings_over_supply
     triggers: [台積電, 鴻海, ...]
     require_also: [法說會, 財報, EPS, 毛利率, 營收]
     override_category: earnings
     priority: 10
   ```
3. **新 `config/topic_exclusion_rules.yaml`**：6 條排除，格式：
   ```yaml
   - id: apple_is_fruit
     context_keyword: [Apple, 蘋果]
     exclude_if_also: [農會, 果園, 水果, 蘋果醋, 蘋果派, 蘋果肌]
     action: route_to_other
   ```
4. **重寫 `src/topic_classifier.py`**：新流程：先跑 disambig rules → 若無 hit，跑 keyword match → 跑 exclusion rules → 最後 fallback to other。加 unit tests (≥ 20 cases)。
5. **ai_agent vs ai_application 原則文件**：新 `docs/ai_agent_vs_application.md`，寫 HITL vs. Agentic Loop 判準，列 10 個 reference products。classifier 處理模糊詞時引用該文件邏輯（LLM-assisted classification fallback）。

### 預期效果
- 「台積電法說會」不再錯分 supply_chain（已知 bug 解掉）。
- 英文媒體命中率 +50%（粗估）。
- Apple 水果、保險 Agent、洋芋片 晶片 等 FP 消失。

### 風險
- Priority rules 邏輯錯寫 → silent miscategorization，不報錯難 debug。
- 排除規則太激進可能誤殺有效新聞（e.g., 討論 Apple 進入農業 AI 就被排除）。
- 加新詞不加 disambig 會立刻出現 FP（DeepSeek 在非 AI 語境 FP 低，但 Operator 這類泛用詞一定會）。

### 驗證
- Unit tests per rule（每條 disambig / exclusion 各 2-3 測例）。
- 最近 500 news_items 重跑分類，比較舊 vs 新的類別分佈。
- 手動抽 30 篇看是否正確（Gemini Section 3 的 10 個 reference products 必須全部分類正確）。

### SSOT
新增 §A3：分類流程（disambig → keyword → exclusion → fallback）、新 yaml 檔路徑、ai_agent vs application 原則連結。

---

## Topic 4 — Content quality 紅旗擴充

### Gemini 給了什麼
- **Section 1**：5 篇 2024-2026 LLM hallucination 文獻（HKU、OpenAI、arXiv、ACL、AAAI）做理論背景。
- **Section 2：20 個新 pattern**（regex / 字串 + 類型 + FP 率 + action）：
  - Hallucination 類：1（空泛來源「根據最新研究」）、2（虛構專家）、6（未解析 [插入...] 佔位）、7（example.com 假網址）、14（超過 90% 企業無引用）、15（2023/2024 時間錨點在 2026 年）、20（資料來源：網路）。
  - AI-tone 類：3（As an AI）、4（抱歉我無法）、5（投資有風險制式尾）、8（毋庸置疑）、9（綜上所述起手）、10（delve into / 深入探索）、11（Innovative / 變革性）、12（To some extent）、13（然而這並不意味著）、16（這不僅僅是…更是）、17（拭目以待）、18（AI 浪潮中）、19（值得深思）。
- **Section 3：10 個 clickbait pattern**（Meta 2025-26 打擊）：「你絕對想不到」「底下告訴我」「XX 背後的真相」「全網瘋傳」「一定要看到最後」「99% 的人都不知道」「趕快收藏」「一文看懂」「竟然…了」。
- **Section 4：AI-detection 避雷**（Grammarly 2026 字彙 + 結構）。
- **Section 5：Meta 2025-26 政策**（原創性打擊、spam 降觸及、冒充移除、AI 訓練透明化、匿名發文）。

### 現況
`src/content_quality_guard.py:110-135` `_RULES` tuple 4 條規則：templated_fallback_marker、generic_hashtag_bundle、untranslated_english_only、empty_or_too_short。**純反套話 + 反空心，完全沒有幻覺 / AI-tone / clickbait 檢測**。

### Gap
現行規則是 hardcoded Python list；20 新 pattern + 10 clickbait = 30 新規則——適合改成 yaml-loaded。要引入 **三種 action**（warn / rewrite / reject），現行只有 reject。

### 建議
1. **Refactor guard 為 yaml-loaded**：新 `config/quality_redflags_v2.yaml` + `config/quality_clickbait.yaml`，schema：
   ```yaml
   - code: hallucination_vague_source
     regex: "(根據|據)?最新(研究|報告|數據)(顯示|表明|指出)"
     action: warn
     category: hallucination
     fp_rate: mid
     note: "若同段無年份/機構名"
   ```
2. **三 action 分派**：
   - `reject`：guard 擋掉 draft，status 改 `quality_rejected`。
   - `warn`：log 進 drafts.quality_warnings，draft 仍流入 pending_review。
   - `rewrite`：觸發 compose_one.py 重跑（**新機制**，要加 retry loop，暫緩）。
3. **高信心 pattern 先上**（FP Low）：3、4、6、7、15、20——這 6 條安全。
4. **高 FP pattern 必用 `warn`**：11、14、18、19——不要 reject。
5. **Clickbait 10 條全上為 `reject`**：Meta 降觸及風險高。
6. **時間錨點 pattern 15**：需動態注入 `current_year = 2026`。
7. **Section 4 AI-detection 不進 guard**：那是 persona prompt 工程，進 Topic 1 的 persona 檔。
8. **Section 5 Meta 政策**：不是 code 檢查，是運營監控 → 進 `docs/OPERATIONS.md` 新 §「平台政策監控」。

### 預期效果
- 抓 AI 拒絕語 / placeholder 未填 / 假網址 / 過時知識錨點。
- Clickbait 降觸及自救。
- 套話 / 排比句提示重寫。

### 風險
- Pattern 11/14/18 FP High → 若設 reject 會誤殺正常稿。
- `rewrite` 機制沒有現成 retry loop——要新開發，暫緩。
- 30 條規則命中率高會導致大量 warn 訊號雜訊，要 dashboard 觀察 top 5 觸發 pattern。

### 驗證
- 對最近 200 篇 drafts（含 published、pending_review、failed）跑全部 pattern，統計每條觸發率 + 誤殺率。
- 已發佈過的 past good drafts 必須**不被 reject**（regression）。
- 單元測試每條 regex（正例 + 負例）。
- Dashboard：最近 7 天 top 10 觸發 pattern（發現調校機會）。

### SSOT
新增 §A4：三 action 契約（reject/warn/rewrite）、yaml 檔路徑、FP 率表、動態時間錨點注入機制。`docs/OPERATIONS.md` 新增 Meta 政策監控 runbook。

---

## Topic 5 — Hashtag 策略 2026

### Gemini 給了什麼
- FB 1-2、IG 3-5、Threads 嚴格 1（Meta 2026/1 IG 官方上限改 5）。
- 80% 中 / 20% 英（英文限 AI/SaaS/Web3/ESG 台灣人也懂的縮寫）。
- 三平台 Top 30/15 熱門 tag 清單供輪替池。
- Brand hashtag #NewsRadar：IG 保留 1 slot；FB 略；Threads 絕對不放。
- content_quality_guard 新增 `check_ig_hashtag_count`、`check_threads_tag_count`、`check_hashtag_repetition`（三篇重疊 < 50%）、`check_banned_words`。
- ⚠️ **docs 檔 L685-794 同一份報告重複出現兩次**——ingest 異常。

### 現況
`src/composer.py:88-92` `PLATFORM_HASHTAG_RANGE`：FB 3-5、IG **5-10**、Threads 1。IG 上限超過 Meta 2026/1 新政的 **5 tag 硬上限**——有 shadowban 風險。

`threads_v2.md:105-130` 定義 `primary_topic_tag` 邏輯，將 `hashtags[0]` 升為 topic pill。

沒有 rotation pool、沒有 repetition guard、沒有 banned list、沒有 brand hashtag。

### Gap
- 🚨 **IG 5-10 超 Meta 2026/1 新政，是現役最大風險**。
- FB 3-5 vs. 建議 1-2 是 50-66% 砍。
- 無 repetition 檢查 → LLM 每篇可能 #AI #科技 疊加，累積重複使用降權風險。

### 建議（按緊急度）
1. **🚨 IMMEDIATE：IG 5-10 → 3-5**。一個 dict 修改：`PLATFORM_HASHTAG_RANGE["ig"] = (3, 5)`。同步改 `config/platforms/ig_v2.md` appendix 文本（現行寫 5-10）。mitigates shadowban 風險。1 小時內可做，風險最低效益最高。
2. **FB 3-5 → 1-2**：同上 dict + fb_v2.md 同步。
3. **Rotation pool**：新 `config/hashtag_pool.yaml`，按 Gemini Section 2 Top 30 分三類（Tech / Business / Stock）。LLM 從對應類別挑 1-2 個，避免「同一組 tag 累積使用」紅旗。
4. **Brand hashtag**：IG 5 tag 中保留 1 個給 #NewsRadar；FB 不放；Threads 絕對不放。composer.py prompt 加硬規則。
5. **Guard 檢查**（和 Topic 4 refactor 同場做）：
   - `check_hashtag_count(platform, tags)` — FB ≤ 2, IG ≤ 5, Threads ≤ 1，超過 reject。
   - `check_hashtag_repetition(last_3_draft_tags, current_tags)` — 重疊 > 50% warn。
   - `check_banned_hashtags(tags)` — 新 `config/banned_hashtags.yaml`，色情/暴力/極端政治字眼。
6. **Threads 0-tag 實驗**：Gemini 說 **零 hashtag Threads +15% reach**。這和現行 primary_topic_tag 衝突。建議**分離成 A/B test**：加 `TRE ADS_USE_PRIMARY_TAG` env flag 控制，跑 2 週比較 reach。不直接砍。

### 預期效果
- IG 立即避開 Meta 2026/1 shadowban。
- FB 1-2 tag 據 Gemini 比 3-5 reach 高 30%+。
- Rotation pool 分散 tag 使用 → repetition 降權風險下降。
- Brand 識別 +（IG）。

### 風險
- IG 3-5 後初期可能感覺「變難發現」，但其實對齊 Meta 政策。
- Rotation pool 選錯類別（e.g., crypto 新聞選到 Business tag）→ 切題度下降；需要 per-topic subpool。
- Brand hashtag 佔 1 slot 的代價：若新聞本身超適合用熱門 tag，brand tag 擠掉——這點要允許 opt-out。

### 驗證
- Unit test count enforcement per platform。
- 20 篇新 draft inspect tag 分佈。
- SQL：`SELECT platform, COUNT(*), AVG(json_array_length(hashtags)) FROM platform_drafts GROUP BY platform;` 前後對比。
- 2 週後查 IG published 帳號 reach 變化。

### SSOT
新增 §A5：三平台 hashtag count 表、rotation pool 設計、Meta 2026/1 政策引用、primary_topic_tag vs. zero-tag A/B 契約。

---

## 附帶：docs 檔 normalize（維運建議）

- `gemini調研結果.md` L685-794 Topic 5 內容**重複**（兩份 identical）。
- Topic 2（L136-355）Gemini 原始輸出是 HTML-in-Python 格式，不是乾淨 markdown。
- Topic 6 缺，未來補。

**建議**：保留原始檔，另寫一份 normalize 版本 `docs/research_briefs/gemini_report_normalized.md`（去重 + HTML→markdown 清洗 + 小目錄）。這只是文件整理，不動 code。

---

## SSOT 新增區段草稿（System_Architecture.md）

建議在現行 §7（案例庫）之後新增 §A「Content generation subsystems」：

- **§A1 Composer personae** — 三 appendix 路徑、voice benchmark、硬規則表。
- **§A2 Scorer features + topic weight strategy** — feature 清單、back-prop vs. seed 契約（⚠️ 地雷文件化）、crypto_web3 類別。
- **§A3 Topic classification & disambiguation** — 分類流程（disambig → keyword → exclusion → fallback）、yaml 路徑、ai_agent vs. application 原則。
- **§A4 Content quality guard taxonomy** — 三 action 契約、yaml 路徑、FP 率表。
- **§A5 Hashtag strategy & 2026 platform policies** — count 表、rotation pool、zero-tag 實驗。

每個子節 ≤ 20 行、附 file:line citation、last-verified 日期。

---

## 建議實施優先順序（等你拍板）

### Tier 1 — IMMEDIATE，低風險，1-2 小時
1. **Topic 5.1** — `PLATFORM_HASHTAG_RANGE` IG 5-10→3-5、FB 3-5→1-2 + 同步 appendix 文字。**優先做這個**，Meta 2026/1 shadowban 風險最大。
2. **Topic 4 高信心 pattern**（3/4/6/7/15/20 共 6 條）加到 guard，FP 極低。

### Tier 2 — 半天級，中風險
3. **Topic 3** — 30 新詞 + 8 歧義規則 + 6 排除規則 + classifier 重寫 + unit tests。
4. **Topic 2 Feature B** — supply_chain_proximity 純規則 feature。
5. **Topic 1** — 三份 persona appendix 更新 + voice benchmark 落地。

### Tier 3 — 全天級，高風險
6. **Topic 2 weight recalibration** — 加 crypto_web3 + seed 調整 + back-prop audit。
7. **Topic 4 rewrite action loop** — 新 compose retry 機制。
8. **Topic 1 BACKLOG 2026 趨勢**（R&D，非立即上線）。

---

## 紅線與待決事項

**不跨紅線**：本計畫不含 `git push`、`rm` data、個資、大量 API burst。所有 config 改動是本地 yaml/py 檔編輯 + local commit。

**待決（你的 call）**：
1. Tier 1 要不要立刻動？（IG shadowban 風險是時間軸壓力）
2. Topic 2 seed_weight 改動前要不要先做 back-prop audit（我去查 `src/` 找有沒有 weight-recompute 腳本）？
3. Topic 6（cadence/timing）你想何時補 Gemini research？
4. Voice benchmark 檔——你有沒有希望我把 Tim Cook / John Ternus 那則貼文全文放進 `news_radar_soul.md` 當直接 reference？還是只放連結 + 風格 bullet？

準備好收你的 prioritize 決定再繼續。
