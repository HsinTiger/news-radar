# News Radar · Architect Plan Discussion Log

> 本檔是「架構層級會議紀要」，收錄 agent 與使用者之間的**設計對話、彼此挑戰、最終 trade-off**。
>
> 存在意義：半年後、或換手給下一位 agent 時，能快速回看「我們當初為什麼這樣設計」、「哪些選擇是有意識的權衡、哪些是暫時妥協」、「哪些決策的解鎖條件還沒達成」。
>
> **撰寫原則**：
> - 每輪紀錄格式：**議題 / 使用者立場 / Agent 挑戰 / 結論 / 隱含 trade-off / 後續觀察點**
> - 不是「user 說什麼就是對的」的逢迎紀錄。兩方觀點都保留，連同沒被採用的方案也寫清楚。
> - 日期格式：`YYYY-MM-DD`；若一輪會議跨多議題，依序列出 Round 1.1 / 1.2 …
>
> 維護規則：每次重大架構決策（新增 module、改 pipeline 合約、調整 soul/rubric 層級）後，agent 應主動回寫一輪；日常 bug 修復與 config 微調不寫進本檔（那些進 `AGENT_WORKLOG.md`）。

---

## 🗓️ 2026-04-19 · Phase 8.11 Planning Session

本次會議主題：**Phase 8.10 Kill-the-X 收官後，MVP 的 Module 3–7 設計對齊**。
合議人：使用者（Hsin）× Claude Opus 4.7。

### Round 1 · Pipeline 形狀：從「每篇丟給寫手」到 Module 3–7 分工

**議題**：資料抓完後，該怎麼挑題、怎麼寫、怎麼品管、怎麼守發文節奏？

**使用者立場**：
- 直覺提案是「挑 3 篇資料給寫手寫，寫手一次寫三篇，scorer 評分，最高分 > 0.9 才發」
- 節奏：至少每 2 小時一篇、至多每 1 小時一篇
- 「如果三篇都沒 > 0.9 就再三篇」
- 其中 0.9 閾值的 fallback 時機不確定

**Agent 挑戰**：
1. **選題邏輯跟寫作品質不該用同一把尺**：item-level 「該不該寫」跟 draft-level 「寫得好不好」是兩個不同的 optimization target，合在同一個分數會讓兩邊都失焦。
2. **「一次三篇」隱含假設單一寫手一次生三個 variant**，但實際上是「挑 1 個 item、三個平台寫手各寫 N variant」，否則會混淆「選題」與「多樣性嘗試」。
3. **降閾值的時機**不能只靠時鐘壓力 —— 如果每次 2h 一到就降，系統會學會擺爛；應該要**雙重條件**（已耗盡 N 份 draft + 時鐘壓力）才讓步。

**結論（定案）**：
- **Module 3 Rank**（零 token）：給 cleaned item 打 `rank_score`（7 個 feature）
- **Module 4 Select**（零 token）：top-K 挑選 + diversity 約束
- **Module 5 Compose**（吃 LLM）：對挑中的 item，三個平台寫手各寫 N variant
- **Module 6 Scorer**（吃 LLM）：對每個 draft 用 5 component rubric 評分
- **Module 7 Gate**（零 token）：閾值 + rate limit + graduated fallback
  - Normal 0.9 → Urgent 0.85（>2h + 9 drafts） → Critical 0.8（>3h + 12 drafts） → Emergency publish（>4h 強制）
  - Cooldown 1h 硬鎖、max_silence 4h 觸發 emergency re-select
- **三平台同源 + 各自通關**：同一 item 三平台寫手各自改寫，gate 各平台獨立

**隱含 trade-off**：
- 拆成五個模組複雜度上升，但每個模組的輸入/輸出 contract 更清楚、可獨立 test/回看
- `emergency_publish` 是「品質下限」與「可見度下限」互斥時的讓步機制；代價是 emergency 稿的平均品質必然較低
- 這是「選擇 consistency 而非 peak quality」的 brand commitment

**後續觀察點**：
- MVP 跑 14–30 天後看 `emergency_publish` 佔比 —— **> 10% 代表系統性問題**，應該放寬節奏（例如調 cooldown 或降發文密度），而不是降閾值
- 若長期 cooldown_lock 觸發 > 30%，代表上游題材太密，節奏壓不住，要調 Module 4 select top_K

**寫入檔案**：`docs/PIPELINE.md` § Module 3–7 總覽、狀態機、I/O 契約速查表

---

### Round 2 · Scorer 冷啟動：沒受眾資料怎麼評分？

**議題**：MVP 上線當下我們連平台受眾是誰都不確定，scorer 要用什麼 prompt / soul 引導它做出「符合平台受眾」的評分？

**使用者立場**：
- 直接拋出冷啟動悖論：「我們連平台受眾是誰都不完全確定，如何評分？」

**Agent 挑戰 / 分析**：
- 這是真悖論，但有繞法：**初期不能評「受眾反應」，只能評其代理指標（proxy）**
- 受眾資料累積需要時間（跑 14 天 + 收 Meta Graph Insights），但 scorer 必須 Day 1 就能動

**結論（定案）**：
5 個 component 全是 proxy：

| Component | 意義 | weight | 來源 |
|---|---|---|---|
| `style_fit` | 是否符合 fused soul × 平台 adaptation | 0.30 | soul + platform 文件 |
| `structural` | 硬規則（字數、og:image、禁用詞、開場規則） | 0.25 | rubric YAML |
| `factual_grounded` | 未造假、可追回原 item | 0.20 | source clean_markdown |
| `novelty` | 不與近 30 天 published 內容重複 | 0.15 | 近期 posts 的 embedding |
| `simulation_fit` | 與平台 benchmark KOL 發文慣例相近 | 0.10 | 該平台 benchmark KOL 樣本 |

**Dynamic tuning** 規則：
- **Week ritual human-in-loop**：每週讀 engagement 數據 + 各 component 分數，跑 Spearman correlation，**人工改 rubric_v2.yaml**
- **絕不自動調 weights**：資料稀疏期自動調會漂移、且會失去 narrative 控制權

**隱含 trade-off**：
- 前 4 週的 scorer 是「相信 soul + 競品」而不是「相信數據」，這是 cold-start 的必要妥協
- 5 個 component 中只有 `novelty` 與平台 engagement 弱相關；若早期發現 style/structural 高分但 engagement 低，代表 rubric 與受眾偏好錯配 —— **這是訊號，不是 bug**

**後續觀察點**：
- 第 4 週的 Spearman 報告：哪個 component 與 engagement 正相關、哪個無關？無關的 component 權重要降或整個拿掉
- 若 `style_fit` 與 engagement 長期無相關，意味 soul 過度自我感覺良好、與市場偏好脫節 → 觸發 Layer 1 soul 修訂

**寫入檔案**：`docs/PIPELINE.md` § Module 6 · Scorer

---

### Round 3 · 視覺為主新聞：該不該加權評分？

**議題**：體育、軍工、災害、突發事件的爆點在影片/圖片，文字只是旁白。該不該在 ranker/scorer 加視覺加權？還是 MVP 先不做？

**使用者明確要求**：「挑戰我」

**Agent 挑戰**：
- 這**不是一個 scorer feature 能解決的**，是**整條 pipeline 都要動**：
  - Module 1 fetcher 要下載圖片 hash 本地
  - Module 2 cleaner 要產 `visual_markdown`（需要 VLM 做圖片描述）
  - `schema.py` 要加 `visual_summary: str`、`source_type: 'visual_primary'`
  - Module 3 ranker 要加 `visual_signal_strength` feature
  - Module 5 composer 的 prompt 要注入 visual summary
  - Module 6 scorer 要加 `visual_narrative_fit` component
- 這是 **Phase 9.x 的 vertical slice**，預估 20–40 小時
- **半做會劣幣驅逐良幣**：只加 scorer 不加 composer 輸入，LLM 會幻覺 visual content

**結論（定案）**：
- **MVP 不做** visual-primary item 處理
- 唯一的最低保障是 Module 7 gate 的 **`ig_requires_og_image` 硬規則**（IG 沒 og:image 的 draft 直接 rejected_by_gate），確保 IG 至少有封面圖
- 整個 visual vertical slice 進 `docs/BACKLOG.md` C-04

**隱含 trade-off**：
- MVP 的 IG 將在「純文字加封面」模式運作，必然比 reels-based 競品視覺力弱
- 這是有意識的妥協：先跑出 14 天 baseline，再用數據決定視覺 slice 的優先級

**後續觀察點**：
- IG engagement 若長期落後 FB/Threads > 50%，且原因是視覺弱（不是 reach 不足），則解鎖 C-04
- 若 `has_og_image = false` 的 item 在 Module 7 gate 的拒絕率 > 40%，代表 IG 供應面不足，要在 Module 3 ranker 加 `has_og_image` weight

**寫入檔案**：`docs/BACKLOG.md` C-04

---

### Round 4 · 主題擴充清單 → A/B/C 分類 + MVP Scope Freeze

**議題**：使用者一次列出七個「漏掉」的主題領域：科技產品發佈會、關鍵產業財報、氣候景觀、產業經濟分析、台灣房地產、加密貨幣、視覺新聞。

**使用者立場（隱含）**：
- 擔心現有 13 個 feed 撐不起每 2h 一篇的節奏，想補廣度當保險
- 把所有想得到的題目一次攤開來問「要不要做」

**Agent 挑戰**：
這是典型的 **scope spiral 早期訊號**。三個反駁：

1. **「擔心資料不夠」是直覺、不是事實**。Howard Marks + Zeihan + Stratechery 的訊號密度可能遠超直覺預估，要先跑才知道。
2. **沒 engagement 基線的 scope 擴充 30% 會錯方向**：你可能花兩個月補一批根本沒人看的主題。
3. **一次解鎖七個方向 = 七個 rubric + 七個 engagement 追蹤 + 七個品牌稀釋風險**，這是在把 MVP 變成 MVProduct。

**結論（定案）** —— A/B/C 分類法：

| 類別 | 定義 | 解鎖成本 | 本次清單 |
|---|---|---|---|
| **A** | 主動擋掉（品牌/策略決策，不是漏） | 要先決策品牌方向 | A-01 加密貨幣 |
| **B** | 現有 fused soul 能吃，缺的是 source | 低（加 feed + tests） | B-01 產品發佈會、B-02 財報、B-03 產業經濟分析 |
| **C** | fused soul 吃不下，要新 soul + 新 adaptation | 高（新 soul + rubric + 追蹤） | C-01 房地產、C-02 氣候景觀、C-03 軍工、C-04 視覺新聞 |

**凍結原則**：沒有 engagement 基線數據之前的 scope 擴充**全是猜測**。Phase 8.11 MVP 必須先跑 14–30 天累積數據，再回頭決定 backlog 解鎖順序。

**解鎖優先建議**：
1. B 類來源擴充（若 engagement 顯示 structural rubric 過不了 → 補大行數據；若 IG 素材稀 → 補產品發佈會）
2. A-01 crypto（若品牌方向要納）
3. C-01 / C-03 的**單一**新 soul 實驗（一次只加一個，小規模測試兩週）
4. C-04 視覺 vertical slice（規模最大，不輕啟動）

**使用者回應**：「你是對的 這輪挑戰我的建議給得不錯 開始照你的建議做下去吧」

**隱含 trade-off**：
- 短期承受「內容廣度不夠」的焦慮，換取「有數據再擴張」的紀律
- 接受前 2 週可能有部分時段 supply 不足、觸發 emergency_publish

**後續觀察點**：
- 14 天 baseline 結束時，看 emergency_publish 佔比 + 各 feed 通過率直方圖 → 對應解鎖哪一項
- **不要一次解鎖 > 2 項**；每解鎖一項跑 2 週再決定下一項

**寫入檔案**：`docs/BACKLOG.md`（全文）

---

### Round 5 · Fused Soul 架構澄清（使用者糾正 agent）

**議題**：寫手架構到底是「三 KOL 各對應一個平台」還是「三 KOL 融合成一個 soul」？

**Agent 初期錯誤**：在 BACKLOG.md 與 PIPELINE.md 寫下：
```
IEObserve → FB（財經產業寫手）
Fox Hsiao → IG（科技風尚師）
游庭皓 → Threads（辛辣財經評論）
```
這是**錯誤的 1-to-1 對應**。

**使用者糾正**（原話）：
> 「我說了他們三個 KOL 不是一一對應不同平台的關係，我想你應該還記得是要用他們三個的風格融合我自己的風格，最後再特化出三種平台的寫手」

**Agent 重讀的依據**：
- `config/news_radar_soul.md`（9156 bytes，Phase 8.6 建立）開宗明義寫「**融合三個 benchmark 帳號**」：自然體貼的第三人稱同理心、資深科技記者的流暢播報、業界大佬的客觀俯視距 —— 這三種風格是融合而非分派
- `config/platforms/fb_v2.md` 確實寫「對標 KOL: IEObserve」，但角色是「該平台的發文慣例參照」，不是「寫手身份模板」

**結論（三層架構）**：

| Layer | 實體檔案 | 意義 | 版本欄位 |
|---|---|---|---|
| **Layer 1 · Fused Soul** | `config/news_radar_soul.md` | user 自身風格 + 三 KOL 融合後的**唯一** soul，所有平台寫手共用核心 DNA | `soul_version` |
| **Layer 2 · Platform Adaptation** | `config/platforms/{fb,ig,threads}_v2.md` | 同一 soul × 該平台發文慣例 | `platform_guide_version` |
| **Layer 3 · Rubric Shell** | `config/scorers/{platform}_rubric_v{N}.yaml` | LLM judge 的 prompt 與 weights | `rubric_version` |

**KOL 在本架構的雙重角色**（釐清重點）：
1. **Layer 1 soul material**：Phase 8.6 時三 KOL 的共同優點（IEO 產業版圖 / Fox 冷靜工匠 / 游 數據架構）已經被**萃取、融合**進 `news_radar_soul.md`
2. **Layer 2 平台慣例 benchmark**：三 KOL 碰巧各自在一個平台經營得好，其**平台操作慣例**（長文結構 / 視覺排版 / 金句節奏）作為各平台 adaptation 的實證樣本 —— 是「平台怎麼發」而非「寫手是誰」

**版本升級的影響範圍**：

| 改動層 | 觸發 | 重跑範圍 |
|---|---|---|
| Layer 3 改 rubric | 調權重、加 component | 只重跑 Module 6 |
| Layer 2 改 platform adaptation | 某平台 engagement 落後、平台政策變 | 重跑該平台的 Module 5+6 |
| Layer 1 改 fused soul | 擴張族群、品牌定位調整 | 全量 re-test（三平台都重跑） |

**DB 三重版本號**：每筆 score 同時記 `soul_version` + `platform_guide_version` + `rubric_version`，任一層改動都能獨立歸因。

**隱含 trade-off**：
- 三層架構比兩層複雜，但**新增平台 = 只寫第 4 個 adaptation**，不用新 soul；換品牌定位 = 只改 Layer 1，底下自動重跑 —— 這個結構支援長期擴張
- 若偷懶把 KOL 寫死成寫手身份，短期看起來省事、長期會在每次調整時牽一髮動全身

**後續觀察點**：
- 若未來想加第 4 個平台（如 LinkedIn、YouTube Shorts），應**只寫新的 `config/platforms/*_v2.md`**，驗證三層架構可擴張
- 若未來想加第 4 個 KOL 作為參照樣本，應融入 Layer 1（觸發 re-test）或作為某平台的新 benchmark（只改該 Layer 2），不應直接變成「第四個寫手」

**寫入檔案**：`docs/PIPELINE.md` §「三層 Soul / Shell 架構」；`docs/BACKLOG.md` §「寫手架構」；`AGENT_WORKLOG.md` Phase 8.11 條目

---

### Round 6 · Executor + Monitor 雙 Agent 結構：被否決的提案

**議題**：是否為每個 Module 配一對「執行者 + 監測優化者」agent，讓 7–14 天後有了 engagement 數據時，每個模組可以依據 (1) 後端數據分析團隊給的大方向、(2) 模組內執行者-監測者長期合作的微調，獨立迭代？

**使用者立場**：
- 類比為「小公司組織架構」，希望在 plan 階段先定義好
- 核心訴求：每個模組能獨立迭代、不是整條 pipeline 一起改

**Agent 挑戰**（四個反駁）：

1. **兩個 agent 觀點會高度相關**：
   - 真實公司 Dev + QA 有價值因為人腦袋不同；兩個吃同一份 soul 的 agent 會給相關的判斷
   - 付兩倍 token、得 1.05 倍資訊量 = 冗員

2. **你想要的「監測者」職能已存在，只是沒掛名牌**：
   - 每個模組已規劃三件事：action output（實際做的事）、telemetry JSONL（決策理由）、diagnose tool（隨時可跑的回看工具）
   - 這三件事就是監測者職能 —— 但它是**可觀測性契約**，不是 always-on agent

3. **後端數據分析團隊已在計劃裡 —— 是人（Hsin）+ reflector.py + 週報 ritual**：
   - 不是 agent 團隊，是一個人每週對所有 telemetry + Meta Insights 做 review、輸出 `weekly_review.md` + config diff
   - 在數據稀疏期把這個角色拆成 agent 團隊，會讓 agent「為了產報表而產報表」

4. **時機反向**：
   - 組織結構設計在**有真實衝突之後**做最準
   - 連一篇 draft 都還沒產出，怎麼知道 Module 5 真正需要什麼樣的監測？
   - MVP 跑完 14 天會知道**哪個模組真的需要被持續監看**，那時候再為那個模組加結構就好

**結論（定案）** —— 被否決，改用替代方案：

**模組三交付物契約**（寫進 `docs/ENGINEERING.md` 作為 MVP 硬規則）：

| 交付物 | 角色 | 例子 |
|---|---|---|
| 主產出 | 做事 | Module 3 的 `rank_score` |
| Telemetry JSONL | 自我揭露決策 | 每筆決策寫 `{item_id, features, weights_version, score}` |
| Diagnose tool | 可回看 | `tools/diagnose_rank.py` 能跑分佈報告 |

**單一跨模組 ritual**：
- **Weekly Review Ritual** = 使用者 + `reflector.py` 讀所有模組 telemetry + engagement → 輸出 `weekly_review.md` + config diff 建議
- 這個週報就是使用者說的「後端數據分析團隊大方向」，但它是**儀式、不是一組 agent**

**解鎖條件**（什麼時候值得再回來做 executor-monitor pair）：
- 當某個模組（最可能是 Module 6 scorer）的 LLM judge 飄得厲害、需要**第二個不同 soul 的 judge** 做交叉驗證時
- 那是真正的「兩個觀點」場景，值得付兩倍 token
- 至少是 Phase 9.x 的事，要先有 baseline 才能判斷

**隱含 trade-off**：
- 短期少一份「看起來很正規」的組織結構，換 MVP 的輕量與可迭代性
- 若未來真的需要某模組有 monitor agent，改動範圍限於該模組、不動整條 pipeline

**使用者想要的屬性 vs agent 提供的結構對照**：

| 使用者想要的「屬性」 | 原方案「結構」 | 替代方案「結構」 |
|---|---|---|
| 每模組獨立可改善 | 每模組 2 agent | telemetry + diagnose tool + versioned config |
| 後端給大方向 | 數據分析 agent 團隊 | weekly human-in-loop ritual |
| 模組內微調 | 內部 executor-monitor 對話 | config diff + rubric 升版（人工） |

**後續觀察點**：
- 若 Module 6 scorer 的 `total_score` 每週波動 > 0.1 且無法解釋 → 考慮加第二個不同 soul 的 judge 做交叉驗證（此時才真正需要 pair）
- 若 weekly review 做不起來（人工負擔太重），才考慮把週報自動化而非拆更多 agent

**寫入檔案**：`docs/architect_plan_disscussion.md`（本檔）；待確認後補寫 `docs/ENGINEERING.md` 的「模組三交付物契約」段

---

### Round 7 · LLM 配額用罄時的大原則：改用 Claude（對話端）作 composer

**議題**：首發三平台當天，Gemini 免費額度在 `gemini-2.0-flash-lite` 與 `gemini-2.0-flash` 雙雙回 429 RESOURCE_EXHAUSTED（`limit: 0`）。`compose_multi_platform()` 直接回 None，發文流程被外部配額卡死。

**使用者立場**：
- 「針對這種 call Gemini API 的步驟都加入大原則 —— 一但 API 不夠，就用自己當大腦，不要 call API 了。正確性最重要。」
- 背後訴求：發文節奏與品質不該被免費額度的日配額剛性綁死，尤其是首發、重要題目這種不能退的場景。

**Agent 挑戰與分析**：
1. **品質向量**：`gemini-2.0-flash-lite` 在「資深分析師語氣」這件事上本就偏弱，Claude（對話端）的基線通常更符合 `news_radar_soul.md` 定義的 voice。fallback 到 Claude 不只是救急，常常**反而是品質升級**。
2. **一致性風險**：若 Claude 與 Gemini 交替作 composer，跨文章風格可能飄移。緩解方式是把 editorial_note + 平台字數規格同樣嚴格套用，Claude 也在同一份 system prompt 規範下寫。
3. **成本 / 人力**：目前 Claude 作 composer 要人把 editorial_note 複製到對話端、把草稿複製回 JSON；之後可以升級成直接 Claude API call。
4. **審計軌跡**：手寫草稿仍要寫進 `drafts / platform_drafts / publish_log`，不能因為「走 fallback」就繞過 telemetry。

**結論（定案）**：
- **原則**：任何呼叫 Gemini 的模組，都必須保留一條「Claude 手稿 JSON」的 fallback 路徑。正確性優先於 API 通路，不允許因配額 429 而停發或降品質截斷。
- **呼叫端契約**：
  - 偵測到 composer 回 None（或偵測到 429 exception）時，應印出明確的 fallback 指引，而不是只說「Composer 失敗」。
  - 接受 `--from-json <path>` 旗標，格式同 `data/first_batch_manual_drafts.json`：`{item_id, image_url, fb: {title, body, hashtags, char_count}, ig: {...}, threads: {...}}`。
  - 載入後仍走 `finalize_variant()` 做字數校驗與 squeeze，不能跳過。
  - 仍寫 `drafts / platform_drafts / publish_log`，只是 `llm_provider = "claude_chat"` 或 `"manual"`，讓之後 reflector 做歸因時能區分。
- **短期實作**：`scripts/first_batch_publish.py` 已實裝 `--from-json`。首發即用此路徑。
- **中期升級（待 Module 5 重構）**：在 `composer.py` 把「choose provider」抽成函式 `pick_composer_provider()`，回傳 `gemini | claude_api | json_manual`；gemini 429 時自動降級到 Claude API。

**隱含 trade-off**：
- **一致性 vs 可靠性**：交替 provider 會帶來風格飄移，用 editorial_note + schema 緊束來緩解。
- **自動化 vs 成本**：最佳作法是自動 fallback 到 Claude API，但會增加 token 成本；暫時以手動 JSON 過渡，等 Claude API cost 在 reflector 迭代成本估完後一併評估。
- **審計負擔**：每次手動 fallback 都要記錄原因（quota exhausted / 重要題目選擇高品質 provider / 其他），否則無法分析何時該常態切換。

**後續觀察點**：
- 過去兩週內若 `llm_provider = manual/claude_chat` 的比例 > 20%，代表應升級到自動 Claude API 路由而非維持手動。
- 若手寫草稿的 engagement 顯著高於 Gemini 寫的，要把這個訊號反饋到 `composer.py` 的 provider 選擇邏輯。
- 這條原則要推廣到 Module 6 Scorer（若 Gemini 評分配額爆）、以及 reflector 的週報生成。

**寫入檔案**：`src/composer.py` 頂部 docstring；`scripts/first_batch_publish.py` 的 `--from-json` flag；`data/first_batch_manual_drafts.json` 作為手稿 schema 的首筆範例。

**2026-04-19 更新（Phase 8.13 Generalisation）**：原條目把 fallback 的大腦綁在 Claude 身上，使用者澄清後放寬——見下方 Round 10。本條目的核心觀點（「不允許因配額而停發／降品質」「走結構化手稿 JSON」「審計軌跡不繞過」）全部保留；只是「誰」接手這件事不再預設是 Claude。

---

### Round 8 · 媒體預覽策略 ＋ 免費雲端部署（「不落地」原則）

**議題**：首發 dry-run 顯示「下載 og_image 到本機」有兩個問題——(1) 未來若來源是影片會吃爆儲存空間；(2) 使用者本機空間已不充裕，長期也不該把 pipeline 綁在本機。

**使用者立場**：
- 「如果是影片呢？感覺下載到本機不見得是好方法，我也沒那麼多空間。給我可以 access 的網址也行。」
- 「接著下一步需要寫進大綱的就是部署上可靠的免費雲端全自動運行。」

**Agent 分析**：
1. **預覽 ≠ 發文所需**：Meta API 發文時 publisher 餵 URL，publisher 層內部會 stream 圖片到 Meta CDN；本機檔案從頭到尾不是必要資產。
2. **下載是 dev-only 便利**：只有使用者想離線看時才下載；在雲端環境（GH Actions runner）絕不能下載，否則會吃 runner disk、而且每次 fresh container 無意義落地。
3. **影片類別必須拒絕本地下載**：大小動輒數十 MB，下載幾次就把 dev 機灌滿。
4. **雲端部署是獨立議題**：D 類是**執行環境**層面的問題，與 B/C 類「內容題材」正交；優先順序應該排在 B/C 前面，一旦本機儲存撐不住就必須解鎖。

**結論（定案）**：
- **媒體預覽大原則：Print URL by default, never download**：
  - `scripts/first_batch_publish.py` 預設只印 cmd+click URL + HEAD probe 結果（content-type / size / is_video）
  - `--download-preview` 是 opt-in flag，dev 離線查看用；即便啟用，若偵測到影片類型仍拒絕下載
  - 雲端跑時絕不使用 `--download-preview`
- **免費雲端部署寫進 BACKLOG D-01**：
  - 首選 GitHub Actions（Python 友善、secrets 管理成熟、社群熟）
  - 資料層搬離本地 SQLite，候選 Turso 或 Supabase
  - 解鎖條件：MVP 穩定跑 14 天後，若本機儲存/可靠性痛點顯現才啟動
  - 這條位列 MVP 上線後**首要**解鎖項，排在所有 B/C 之前

**隱含 trade-off**：
- **預覽品質 vs 體積**：只印 URL 需要使用者有瀏覽器/網路；離線場景體驗較差。緩解：opt-in 的本地下載保留給 dev。
- **雲端 vs 本機**：雲端部署要額外學平台 secrets、DB 搬遷、failure notification。代價換來 24 小時真自動化與跨機可攜。不是所有使用者都需要，但儲存空間壓力讓這條幾乎必做。
- **Claude fallback 在雲端**：雲端沒有「對話端 Claude」——Round 7 的手稿路徑在雲環境必須改為「呼叫 Claude API」。這變成 D-01 的子任務。

**後續觀察點**：
- MVP 第 14 天若本地 DB 已超過 500 MB、或首發三平台發文有漏發事件（因為電腦關機），就立刻啟動 D-01
- 雲端遷移後，追蹤「因 API quota 耗盡而 fallback 到 Claude API 的比例」，決定是否要做 provider 自動切換

**寫入檔案**：`docs/BACKLOG.md` 新增「D 類 · 部署與運維」段 + D-01 ticket；`scripts/first_batch_publish.py` 改 opt-in 下載模型。

---

### Round 9 · Threads 主題標籤 (topic pill) 首次觀察與 schema 分離

**議題**：首發第一則 Threads 發文後，使用者觀察到「第一個 hashtag 的 `#` 在正文中消失、但被提升為貼文頂部的主題標籤 pill」。最初被當作 hashtag 被 API 吞字元的 bug 回報；細查後確認這是 Threads 的 **discoverability 機制**（topic pill 點進去是一個類目導航頁，等於給貼文額外分發流量）。

**使用者立場**：
- 「thread 有把最重要的 hashtag 放上主題標籤，這很不錯，我們是不是應該……」
- 對 agent 提出的「短期 / 中期 / 長期」三階段方案回應：「這個看起來不難，現在直接把中期任務做進去，長期我們等 7–14 天後數據驗證。」

**Agent 分析**：
1. **非 bug，是 feature**：Threads 主動把 hashtags[0] 升級為 topic pill，正文中同時移除該 hashtag 的 `#`。這是 Instagram/Threads 團隊在 2024–2025 強化 discoverability 的產物，不是 API 隨機吞字元。
2. **既有架構會意外受益也會意外受害**：現況 `composer.py` 沒有保證 hashtags[0] 是「最能代表本貼文分類」的詞——LLM 今天剛好把 `#GPTRosalind` 放第一個，是運氣好。下一則可能首個放 `#AI` 或 `#產業分析`，pill 會被稀釋到幾乎沒流量。
3. **資料層應該分離關注點**：「主題標籤」和「補充標籤」在貼文上扮演完全不同角色——前者決定發現性，後者只是補充搜尋。把兩者合在同一個 `hashtags: List[str]` 裡，未來 reflector 分析「哪些標籤帶來流量」時會混在一起，無法做因果歸因。
4. **FB / IG 不依賴 pill 機制但仍受益**：IG 沒有 pill 機制，但 `primary_topic_tag` 對 IG 也有用——它讓跨平台對比「同一則新聞的不同主題歸類」成為可能。FB 近期也在測試類似 pill 的導航 UI。

**結論（定案）**：
- **短期**（本次實作）：在 `config/platforms/threads_v2.md` 加入「第十段 · 主題標籤硬規則」，明確定義選詞標準（不要太廣義）與反例。
- **中期**（本次實作）：`PlatformVariant` 新增 `primary_topic_tag: Optional[str]` 欄位。`finalize_variant` 會自動把它放到 `hashtags[0]` 並去重。LLM system instruction 要求每個變體都必填 `primary_topic_tag`，Threads 尤其關鍵。
- **長期**（延遲 7–14 天後依數據決定）：reflector / scorer 蒐集每個 `primary_topic_tag` 的 impression / click-through / saved 數據，用於：
  - 判斷是否要把 topic tag 選擇也納入 composer 的結構化提示（例如「本月高轉化 topic tag 前 10 名」當 context）
  - 若某些 topic tag 顯著低效，列黑名單
  - 評估 pill 流量占 Threads 總流量的比例，決定是否值得做更深的優化

**隱含 trade-off**：
- **多一個欄位 vs 簡潔**：schema 從 4 欄變 5 欄，LLM 必須多產一個決定；代價換來 discoverability 的結構化追蹤。
- **強制 LLM 選詞的品質風險**：LLM 可能選到太廣義的詞（`#AI`）。緩解：threads_v2.md 第十段明確反例 + reflector 階段做 top-N 負面清單。
- **Backward compat**：舊的 JSON 手稿若不填 `primary_topic_tag`，hashtags[0] 會繼續是 de facto pill，等於維持原行為；新增欄位為 `Optional`，不 break 任何既存呼叫端。

**後續觀察點**：
- 累積 14 天後，統計：(a) Threads 貼文有多少 impression 來自 topic pill 點擊；(b) primary_topic_tag 命中率高的貼文 vs 低的貼文在總 engagement 上的差距
- 若 pill 流量 < 5% 總 impression，這條路徑可以降優先級；若 > 20%，要把 topic tag 選擇納入 Module 6 scorer 的硬規則
- 觀察 FB / IG 是否也開始把 hashtags[0] 做特殊處理（UI 觀察），若有，把 `primary_topic_tag` 機制擴展到所有平台

**寫入檔案**：`src/schema.py`（PlatformVariant 新增欄位）；`src/composer.py`（`_validate_and_fix_hashtags` 改寫為 primary-first、去重，`_build_system_instruction` 加主題 pill 指引，頂部 docstring 加 Phase 8.12 章節）；`config/platforms/threads_v2.md`（新增第十段）；`data/first_batch_manual_drafts.json`（三平台都填上 `primary_topic_tag: "#GPTRosalind"` 作為格式示範）。

---

### Round 10 · Fallback 大腦的 vendor-neutrality：誰主導，誰代為完成

**議題**：Round 7 把「API 額度用盡時」的 fallback 寫死在「Claude (對話端)」身上。使用者澄清這條原則應該脫鉤——執行者未必是 Claude，可能是 GPT 或 Gemini，由當下主導此任務串的 agent 自己接手。

**使用者立場**：
- 「任何 stage 只要偵測到 API call 的額度不夠或任何原因失效，直接以當下正在執行的主 agent 充當大腦代為完成（有時候執行者未必是 Claude，也可能是 GPT 或 Gemini），就看當下由哪個主 agent 發起主導這整串任務。」

**Agent 分析**：
1. **避免廠商鎖定**：把 fallback 寫死成 Claude，意味著若使用者改用 GPT-based client（例如 ChatGPT Desktop、Cursor 的 GPT）或 Gemini-based client 來主導整個 pipeline，fallback 路徑就無法套用。原則應該以角色（「主導此任務串的 agent」）而非身份（「Claude」）定義。
2. **所有足夠強的主 agent 都能勝任 composer 任務**：只要它能讀 soul / appendix / editorial_note 並輸出符合 PlatformVariant 結構的 JSON。這些都是 text-in text-out 的介面，沒有 Claude-specific 假設。
3. **審計軌跡可辨識即可**：`llm_provider` 欄位此後會出現 `claude_chat / gpt_chat / gemini_chat / manual` 等值；reflector 以此做 per-provider 品質分析。
4. **「誰主導」是 runtime 事實**：不需要程式預先決定。只要主 agent 能讀 `data/*.json` 格式、跑 `--from-json` 即可接手。所以這條原則幾乎純粹是 doc 層改寫，code 端僅需把輸入輸出 schema 保持 vendor-neutral。
5. **這條原則向下游擴散**：不只 composer，Module 6 scorer、reflector 週報、甚至 harvester 的「LLM-輔助去重」都應該遵守同一條原則。

**結論（定案）**：
- **原則（generalised）**：任何 stage 只要偵測到 API 失效，**當下主導此任務串的主 agent** 直接充當大腦接手那一步。不重試同一條 API、不 fallback 到低品質模型、不因額度停發。
- **身份中立**：文件與 error message 都不再假設 fallback 大腦是 Claude。改用「主 agent」一詞。
- **介面契約不變**：仍以 `PlatformVariant` JSON schema + `--from-json` 接入，主 agent 手寫的 JSON 仍走 `finalize_variant` 校驗。
- **推廣範圍**：適用 composer / scorer / reflector / harvester 的 LLM 輔助步驟——凡呼叫外部 LLM 的 stage 都要預留手稿 JSON 入口。
- **llm_provider 欄位擴展**：之後寫入時使用 `"{agent}_chat"` 格式（claude_chat / gpt_chat / gemini_chat），或 `"manual"` 表示非 chat 客戶端的人工介入。

**隱含 trade-off**：
- **可移植 vs 最佳化**：不同主 agent 的 voice 會有差異，跨主 agent 的長期數據可能有風格噪音。用 soul + editorial_note 緊束來緩解，並把 `llm_provider` 做 per-provider 歸因分析。
- **介面 vs 深度**：vendor-neutral 介面意味著不能做 Claude-specific 的 tool-use / system_instruction 優化。接受這個取捨——此專案主要智慧在 soul / appendix / scorer 的規則層，而非 vendor-specific 技巧。
- **主 agent 對沒見過的 pipeline 的學習成本**：GPT 或 Gemini 第一次接手時需要讀 soul + appendix + editorial_note + JSON schema。不是零成本，但屬於一次性。緩解：保留清楚的「手稿 fallback README」段落指南（之後補）。

**後續觀察點**：
- 累積兩週的 `llm_provider` 分佈。若某個主 agent 的 fallback 次數主導、且 engagement 顯著差於 Gemini 主路徑，要警覺是否該主 agent 的 voice 不適合本專案。
- 若跨主 agent 的 voice drift 在 reflector 階段用肉眼可辨，要考慮把 soul 從「共用 markdown」升級為「per-provider prompt variant」。
- 若這條原則開始被用於非 fallback 場景（例如「主 agent 認為自己寫得比 API 好就主動接手」），要把原則明確區分「被動 fallback」vs「主動取代」，避免濫用而稀釋審計軌跡。

**寫入檔案**：`src/composer.py` 頂部 docstring 大幅改寫（Phase 8.13 generalisation）；Round 7 加 2026-04-19 更新補註指向本條；未來 Module 5/6/reflector 實作時都需 honour 此原則，文件留在本檔作為契約。

---

### Round 11 · 幣圈擴充：頻道定位從「科技 / 商業」延伸到「科技 / 商業 / 加密貨幣產業」

**議題**：使用者原本想發一則短影片測試影片通道。挑中的測試素材（X 平台上一則 STRC + MSTR 年化 23% 教學影片）暴露三個問題：(1) Meta Graph API 需要 .mp4 直鏈，X 播放頁面 URL 不能直接吃；(2) 搬運他人 X 影片有著作權風險；(3) 內容本質是加密貨幣投資策略（alpha），與頻道原有「產業分析」定位不符。使用者隨即轉向：「那我們針對現在的資料素材進行擴充試看看，幣圈有很多類似 Cointelegraph / CoinMarketCap / CoinGecko 一堆。」此舉的真正含義是**頻道 scope 擴張**，必須先把編輯方針與過濾器一次定義清楚再動 config。

**使用者立場**：
- 希望 harvester 覆蓋幣圈「一堆」新聞源，具體列舉 Cointelegraph / CoinMarketCap / CoinGecko。
- 同意編輯方針邊界由 agent 挑戰後設定：產業事件 / 基建 / 監管進來，alpha / 年化收益 / 買賣信號不進來。
- 選源決策：放行 crypto 與 web3 關鍵字；第一批加入 CoinDesk / The Block / Decrypt / Cointelegraph 4 家新聞 + a16z crypto / Paradigm 2 家研究 + Bankless 1 家長文。

**Agent 分析**：
1. **挑戰：CoinMarketCap / CoinGecko 不適合加**：這兩個是價格聚合器，不是新聞媒體。其 news feed 多為爬別家標題或 pay-to-post 軟文，訊噪比極差。agent 主動排除，由使用者確認。
2. **挑戰：當前 config 明文封殺 crypto / NFT / web3**：若不先解除，新增任何幣圈源都會被 cleaner 的 `must_exclude_any` 整批 Drop，等於白加。這是第一個硬阻塞，需要由使用者決策是否解鎖。
3. **挑戰：新增的源能不能通過 must_include_any？** 現有白名單沒有任何幣圈詞，即使文章通過 exclude 也會卡在 `no_keyword_match`。必須同步擴充白名單。
4. **子字串匹配陷阱系統性識別**：cleaner 用 Python `in` substring match，case-insensitive。以下組合會誤擊正類文章——全部**不准加入任何關鍵字清單**：
   - `ETF` → 子字串匹配 **Netf**lix、target 3-letter
   - `SEC` → 子字串匹配 **sec**ond、**sec**tion、**sec**retary
   - `DeFi` → 子字串匹配 **defi**ne、**defi**nition、**defi**es
   - `Circle` → 子字串匹配 virtuous **circle**、inner **circle**、**Circle** K
   - `airdrop` → 子字串匹配 Apple **AirDrop**（真正 iOS 功能）
   - `Fed` → 已存在於白名單；承接既有容忍度
   因此白名單只用多字片語（`"spot ETF"`, `"Bitcoin ETF"`, `"ETF flows"`, `"crypto regulation"`, `"digital asset"`, `"proof of stake"`, `"DeFi protocol"`, `"layer 2"`, `"Ethereum upgrade"`）或罕見長獨特詞（`Chainalysis`, `OFAC`, `CFTC`, `Grayscale`, `tokenization`）。
5. **Alpha-filter 排除詞策略**：第二道防線過濾 alpha 類滲透。選字原則同前——只用多字片語或無歧義長詞，避免誤殺。加入 `"price prediction"`, `"price target"`, `"yield farm"`, `"yield farming"`, `shitcoin`, `"pump and dump"`, `"rug pull"`, `degen`, `APY`, `shill`, `FUD`, `"to the moon"`, `"get rich"`, `"100x"`, `"1000x"`。
6. **12-case simulation 驗證**：5 則產業新聞 PASS、3 則 alpha 類 DROP、4 則子字串陷阱控制組（Netflix / Apple AirDrop / Second quarter GDP / DeepMind defines）全部正確 PASS 不誤擊。filter 鏈行為符合預期。

**結論（定案）**：
- **頻道 scope**：正式擴張為「科技 / 商業 / 加密貨幣產業」三軸，加密貨幣軸只收產業事件 / 協議升級 / 監管 / 機構動向。
- **config 改動（`config/config.yaml`）**：
  - `feeds`：新增 7 條（CoinDesk / The Block / Decrypt / Cointelegraph / a16z crypto / Paradigm Research / Bankless）
  - `must_exclude_any`：移除 `crypto` / `web3`（NFT 保留）；新增 15 個 alpha / 投機排除詞
  - `must_include_any`：新增 28 個幣圈產業關鍵字
- **不加的源（明確拒絕）**：CoinMarketCap、CoinGecko（訊噪比過低）
- **首次 harvest 後驗證清單**：Paradigm RSS 路徑 (`https://www.paradigm.xyz/feed.xml`) 未經線上驗證，若回 404 改用 `https://research.paradigm.xyz/feed` 或移除。The Block 的 `rss.xml` 端點是否仍有效需確認。

**隱含 trade-off**：
- **覆蓋廣 vs 頻道一致性**：擴到三軸後，排名系統 (scorer) 看到的訊號更雜。短期可觀察 engagement 分佈做 per-axis 歸因；若幣圈軸長期拉低整體品質，可設 axis-level quota 限制每日幣圈篇數。
- **產業 vs alpha 的灰色地帶**：某些新聞會同時帶產業事件 + 價格評論（例如「Bitcoin ETF 上市後破 $70k」）。目前 exclude 選字保守，這類會通過進 scorer。依賴 scorer 的 `has_specific_numbers` + `official_source` 做第二層篩選；若實測發現價格類滲透率高再加強 exclude。
- **RSS 源壽命**：幣圈媒體換系統頻繁，feed 路徑可能隨時 404。DEAD_FEED 監控已經存在於 harvester，靠 logs 捕捉後再改。

**後續觀察點**：
- 首次 harvest 跑起來後：7 個新 feed 各自的 HTTP 狀態、parse 結果、通過 cleaner 的文章數。任何一條連續兩次 DEAD 就從 config 移除。
- 前 3 天：人眼審視發布候選清單，看有沒有 alpha 類滲透。若率 > 10%，回頭加強 `must_exclude_any`。
- 第一週：per-axis 的 engagement 分佈（科技 / 商業 / 加密貨幣）。若幣圈軸的 engagement 遠低於其他兩軸，重新檢討是否該收斂回兩軸。
- `must_include_any` 現在有 114 條，已經很長。繼續擴張前先評估 per-keyword 實際命中率，剪除長期零命中的廢詞。

**寫入檔案**：`config/config.yaml`（feeds / must_include_any / must_exclude_any 三處全面擴充，Phase 8.15 標記）；本輪決策完整保留於本檔作為契約；首次 harvest 結果出來後在本條目底下追加 `2026-XX-XX 更新` 記錄實測發現。

**2026-04-19 更新（Phase 8.15b post-harvest）**：
- 首次 harvest 285 entries → 84 新入庫、37 Drop、22 errors；**alpha filter 精準命中**（`shitcoin`/`price target`/`price prediction` 各 1 筆，`meme` 7 筆，`NFT` 4 筆，`celebrity` 1 筆）——子字串陷阱零誤擊（Netflix / AirDrop / Second quarter / DeepMind 全部通過）。
- **失效源（確認撤下）**：Paradigm Research (`/feed.xml` 404)、a16z crypto (`/feed/` 404)、Bankless (試 `substack.com/feed` 400、`bankless.com/rss` 404 後放棄)——三者共通原因：網站改平台 / 本身不公開 RSS。
- **The Block 特例處理（新增 source_type）**：RSS 本身 200 但 25 篇文章頁全 Cloudflare 403。新增 `source_type: rss_summary` 型別：
  - `fetcher.py` 加一條平行分支，強制從 RSS `<description>` 預填 `clean_markdown`，多層 fallback（`_reddit_rss_to_markdown` → `BeautifulSoup.get_text` → title-only），最終以 `Article Summary (RSS only):` 為前綴區分來源性質。
  - `config.yaml` 在 `filters.min_word_count` 新增 `rss_summary: 60`（比 social 40 稍嚴、比 article 200 寬鬆）。
  - 二次 harvest 驗證通過：`raw=25 kept=25（其中 25 篇已從 RSS 預填內容）`、25 行 `跳過 HTML 抓取` 取代原本 25 行 403 Forbidden、僅 1 篇落在 `too_short[rss_summary]:58<60`。
- **最終幣圈軸 4 個源穩定運作**：CoinDesk (25) + The Block (25, RSS summary 模式) + Decrypt (77) + Cointelegraph (60) = 187 entries。
- **新觀察點**：`rss_summary` source_type 是一條可重用的 design primitive——**任何未來 RSS 活 / 文章 403 的源都能套用**（Bloomberg / WSJ 等以前被 phase 8.10 撤下的 paywall / Cloudflare 擋源，理論上也可以改用這條路徑降級接回，只要拿到 RSS summary 夠過 60 字門檻）。未來若要大規模回補被 Phase 8.10 撤掉的源，先考慮這條輕量路徑。

---

## 🗓️ 2026-04-19 · Phase 8.16 Round 12：harvester 補上影片 URL 提取能力

**議題**：短影片 publish 測試（Phase 8.14 `publisher.py` 已支援）卡在「有 publisher 卻沒素材」——目前 harvester 鏈只抽 `og:image`，沒抽任何影片 URL；DB 也沒欄位存。Phase 8.15 的幣圈源擴充初衷之一就是希望撈到有影片的新聞，但發現少了最底層的「看見影片」的能力，擴再多源也沒用。

**使用者立場**（原話）：「補上影片 URL 提取能力」（在「補影片提取 vs 擴美股 / 傳統金融源」的取捨中選了前者）。意思是：**先把 pipeline 有「視覺上能識別影片」的能力，再決定要不要繼續擴源**。

**Agent 分析**：
1. **Meta Graph API 的硬性要求**：FB / IG 上傳影片需要公網可取的直鏈媒體檔（`.mp4` / `.mov` / `.webm`）——不是 YouTube 頁面 URL、不是 embed iframe URL。因此 harvester 抽出 URL 後要能**分類**，不是抓到就放 publisher 會撞牆。
2. **兩條主要來源通路**：
   - **HTML `og:video`**：主流發布站（官方部落格、新聞網）會在 `<head>` 帶 `og:video:secure_url`（推薦優先級最高、HTTPS 直鏈）、`og:video:url`、`og:video` 三個欄位；Twitter Cards 另有 `twitter:player:stream`。
   - **RSS `<enclosure>`**：podcast 與少數原生影片 feed 會直接把媒體檔掛在 enclosure 上。這條比 HTML 抽取更穩——文章頁可能 Cloudflare 擋（參考 Phase 8.15b The Block 問題），RSS enclosure 已在 feed 文件裡，不用再打文章頁。
3. **`<video>` 與 `<source>` 標籤**：某些 site 不使用 OGP，直接內嵌 `<video src>` 或 `<video><source>`。補上最後一層 fallback。
4. **不在本輪處理的**：YouTube embed URL 解成直 `.mp4` 是違反 TOS、私權影片需要認證——這些留給 publisher 端決定怎麼 degrade。本輪只負責「誠實地標記 is_direct」。
5. **DB 需不需要兩個欄位？** 討論過只存 URL 一欄、推論時再看副檔名。結論是加 `og_video_is_direct` 第二欄，理由是：(a) 下游 SQL 查詢可直接 `WHERE og_video_is_direct = 1` 過濾「可直接上傳」的素材，不用在應用層重新 parse 每條 URL；(b) 未來 publisher 端如果會主動做「YouTube → MP4 resolve」並把結果寫回，可以自然把 `og_video_is_direct` 翻成 1；(c) 遷移成本可忽略（SQLite ALTER TABLE ADD COLUMN 是 O(1)）。

**結論（定案）**：
- **`schema.py`**：`NewsItem` 新增 `og_video_url: Optional[str]` 與 `og_video_is_direct: bool = False`；`source_type` 註解補上 `rss_summary`（Phase 8.15b 遺漏的文件對齊）。
- **`cleaner.py`**：
  - 新函式 `_classify_video_url(url) -> (url, is_direct)`：以副檔名判斷 direct。`.mp4 / .m4v / .mov / .webm` → True。`.m3u8`（HLS playlist）**故意標 True** 讓 publisher 明確 reject 並 log，避免靜默 fallthrough。
  - 新函式 `extract_og_video(html) -> (url, is_direct)`:嚴格優先序 og:video:secure_url → og:video:url → og:video → twitter:player:stream → `<video src>` / `<video><source>`。
  - `clean_and_filter` 在抽 `og:image` 後併抽 `og:video`——**但若 fetcher 已從 RSS enclosure 預填 `og_video_url`,不覆蓋**（enclosure URL 比頁面 og:video 更穩）。
- **`fetcher.py`**：`fetch_feed` 迴圈裡每 entry 掃一次 `entry.enclosures`,靠 MIME (`video/*` / `audio/*`) 或副檔名（`.mp4 / .mp3 / .m4a / ...`）判定,寫入 `og_video_url` + `og_video_is_direct`。
- **`db.py`**：`init_db()` 加兩條 `_migrate_add_column_if_missing`（idempotent、舊 DB 自動補欄）；`upsert_news` INSERT 欄位清單延伸；新增 `list_items_with_direct_video()` 與 `count_video_coverage()` 兩個查詢 helper 給 publisher 測試 / diagnose 用。
- **`schema.sql`**：同步加 `og_video_url TEXT` 與 `og_video_is_direct INTEGER DEFAULT 0`（新裝 DB 用）。
- **測試**：`tests/unit/test_cleaner.py` 新增 13 個 case 覆蓋 `_classify_video_url` 與 `extract_og_video` 的優先序 / fallback / embed / empty html / prefill 不覆蓋；另用 `/tmp/validate_video_extract.py` 與 `/tmp/validate_fetcher_enclosure.py` 在沙箱直接跑純邏輯驗證（沙箱無 pytest）——24 個 assertion 全綠。

**不在本輪處理（deferred）**：
- **YouTube / Twitter 影片 URL resolve 成 `.mp4`**:需要第三方解析服務,TOS 風險。留給 publisher 端決定是否接觸這條路徑。
- **scorer 對「帶影片素材」加權**:等真正有影片素材在庫、看實測發布效果再調。不猜。
- **美股 / 傳統金融源擴張**：使用者在 8.16 前的抉擇點已明確延後；下一輪若決定擴源再處理。

**隱含 trade-off**：
- **`og_video_is_direct` 的 `.m3u8 = True` 設計有違反直覺**:我們知道 Meta 收不了 HLS,但還是標 direct,為了 publisher 能明確噴錯而不是靜默跳過。未來若 publisher 真的支援 HLS（例如透過 transcode pipeline）語意剛好對上;若不支援,publisher 端的 reject log 反而是診斷信號。
- **不做 HEAD 驗證**:抓到 URL 不打 HEAD 探測這個 URL 現在是不是活著——理由是 harvest 已經夠慢、publisher 端本來就會在上傳時撞到 404。把驗證延後到使用點,保持 harvester 的 deterministic + fast 特質。
- **`og_video_url` 不進 `word_count` 門檻判斷**:影片素材可能字數極少（YouTube description 短、podcast show notes 兩句帶過),但素材價值來自影片本身。這裡**刻意**不給影片素材放寬 min_word_count——因為現在 composer 還沒有「拿影片當主軸寫稿」的能力,字數不夠還是走不通。等 Module 3 真的接入影片素材生成 path 時再放寬。

**後續觀察點**：
- 下次 harvest 後跑 `list_items_with_direct_video(conn)` 看能撈到幾筆。現有 4 個幣圈源裡 Cointelegraph / Decrypt 有機會帶影片。
- 若連續兩次 harvest 撈到 0 筆 direct video：需要重新檢視要不要引入明確的影片 feed（例如 podcast `feed.megaphone.fm/...`、YouTube channel RSS `https://www.youtube.com/feeds/videos.xml?channel_id=...`）。
- 若撈到但都是 embed URL（is_direct=False）：是正向訊號——代表 harvester 看見了、只是 Meta 不收。這種情況可先把該 item 導入「圖文 degrade」流程（以 og:image 發文、body 帶「附原影片連結」）。

**寫入檔案**：`src/schema.py`、`src/cleaner.py`、`src/fetcher.py`、`src/db.py`、`data/01_harvest/schema.sql`、`tests/unit/test_cleaner.py`；本檔 Round 12。

---

## 🔁 本次 session 的 meta 觀察

1. **Agent 被期待挑戰，不是逢迎**：使用者在 Round 3 明確要求「挑戰我」；Round 6 使用者主動拋出組織結構提案、期待被 agent 質疑。這是健康的合議模式 —— 本檔保留挑戰軌跡，避免未來 agent 誤以為「使用者說什麼就是對的」。

2. **「現在 plan 階段先定義好」的直覺經常反向**：Round 4（scope）、Round 6（組織結構）都出現這種訴求，兩次的答案都是「**等數據**」。這不是偷懶，是避免 premature optimization。

3. **使用者也會主動糾正 agent 的錯誤**（Round 5）。下次 agent 處理架構描述前，先 `Read` 對應 config / schema 檔案，而非依賴 summary 或記憶。

4. **Scope freeze + BACKLOG + weekly ritual 是三位一體**：
   - Scope freeze 守住 MVP 期的注意力
   - BACKLOG 接住被凍結的需求，保證它們不會被遺忘
   - Weekly ritual 是把「凍結期」轉為「數據驅動解鎖」的機制

---

## 📋 維護規則

- 下一次重大架構決策後（新 module / 改 pipeline 合約 / soul 層級重整），追加 `## 🗓️ YYYY-MM-DD · <session 名>` 區塊
- 每個決策至少要有：**議題 / 挑戰 / 結論 / trade-off / 觀察點**五件事，缺一不可
- 若後續發現某個「已定案」的決策被數據打臉，不要刪舊條目，而是在該條目底下追加 `**2026-XX-XX 更新**：...` 說明為何改，原決策作為歷史保留
- 本檔不是規格文件（規格在 PIPELINE.md）、不是工作日誌（日誌在 AGENT_WORKLOG.md）；本檔是「為什麼這樣設計」的思辨紀錄
