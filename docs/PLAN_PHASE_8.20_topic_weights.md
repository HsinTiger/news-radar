# Phase 8.20 · 主題分類 + 權重 back-prop

> 作者：Cowork Claude · 2026-04-21
> 狀態：Draft（待 Hsin 確認後開工）
> 依賴：Phase 8.19c/d（Threads 修復 + 推導聲線）已上線
> 目標：把「主題權重」變成一條可觀察、可調整、會根據實際發文成效自動修正的迴路。

---

## Ⅰ. 使用者需求（原話）

> 增加高效率的演算法，讓資料篩選分不同種類。初始先以**科技圈新發布產品（尤其 AI）/ 產業鏈資訊 / 營收 / 台股 / 美股**的權重最高，之後我們再根據各種文章發布出去的情形 back-propagation 修正我們的資訊主題選擇。

翻成工程語言：
1. 新聞 item 進來時，要先被分到一個「主題類別」。
2. 不同類別乘上不同權重，決定是否進入 composer、或插隊到發文佇列前端。
3. 事後以 `engagement_stats`（likes / comments / shares / reach）回饋，週期性修正各類別權重。
4. 不需要 SGD / 真正的梯度——是「人看得懂的」權重校正（例如 EMA + 上下限）。

---

## Ⅱ. 主題分類 (Topic Taxonomy)

初始 7 個類別，每個有穩定 ID（snake_case）、顯示名、初始權重、白話說明。

| ID | 中文 | 初始權重 | 納入條件（examples） |
|---|---|---|---|
| `ai_product_launch` | AI 新產品發表 | **1.50** | 模型發表、API 發佈、AI 應用第一手官宣、Agent 新版 |
| `tech_product_launch` | 非 AI 科技新產品 | 1.20 | 晶片、消費電子、SaaS 主線更新 |
| `supply_chain` | 產業鏈／供應鏈 | **1.40** | 代工、封測、晶圓、模組廠、能源、材料（GaN / HBM / EUV 等） |
| `earnings` | 營收／財報 | **1.30** | 月營收、法說、毛利、業績指引 |
| `tw_stocks` | 台股個股／大盤 | **1.25** | 上市櫃公司動態、主力籌碼、政策利多／利空 |
| `us_stocks` | 美股個股／大盤 | **1.25** | 科技七雄、指數變動、FOMC 對股市的直接影響 |
| `policy_geopolitics` | 政策／地緣政治 | 1.00 | 法案、制裁、外交、關稅 |
| `other` | 其它 | 0.70 | 以上都不沾邊的雜訊 |

**權重公式**：
```
final_score = llm_confidence_score × topic_weight  [clip 到 0..2.0]
```

`llm_confidence_score` 已是 0–1，乘以權重後可超過 1，但這沒關係——`run_publish_queue` 本來就是取 Top-K 排序，不是閾值篩選。

🛑 **不會**引入硬閾值淘汰某類。權重只是**加權排序**，保留多樣性。

---

## Ⅲ. 技術實作切片

### 3.1 Schema 變更（`data/01_harvest/schema.sql`）

新增兩欄到 `news_items`：
```sql
ALTER TABLE news_items ADD COLUMN topic_category TEXT;      -- 分類 ID
ALTER TABLE news_items ADD COLUMN topic_confidence REAL;    -- 0..1，LLM 自評信心
```

新增 `topic_weights` 表（取代 yaml 寫死的好處：back-prop 會直接 UPDATE）：
```sql
CREATE TABLE IF NOT EXISTS topic_weights (
    category_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    weight REAL NOT NULL,
    last_updated_at TEXT NOT NULL,
    update_reason TEXT,          -- 'initial_seed' / 'back_prop' / 'manual'
    sample_count INTEGER DEFAULT 0  -- 累積用這類別發文數（給 EMA 用）
);
```

初始 seed migration：寫一個 `scripts/seed_topic_weights.py`，把 Ⅱ. 表格內容 INSERT 進去，`update_reason='initial_seed'`。

### 3.2 Classifier（新模組 `src/topic_classifier.py`）

兩層策略：
1. **關鍵字快速通道**（免 LLM 成本）：維護 `config/topic_keywords.yaml`，每個類別一組正則／關鍵詞。命中就直接回傳，`topic_confidence=0.6`（保守）。
2. **LLM fallback**：命中不到或模棱兩可就打 `llm_brain.call_for_json`，用 `TopicClassification` pydantic schema。

```python
class TopicClassification(BaseModel):
    category_id: str       # 必須是 taxonomy 裡的 ID
    confidence: float      # 0..1
    rationale: str         # 一句話解釋，存 DB 方便人工檢查
```

### 3.3 Scorer 整合

`scorer.py::score_news` 尾端加一層：
```python
classification = await classify_topic(title, content)
news_items_repo.set_topic(news_item_id, classification.category_id, classification.confidence)
weight = topic_weights_repo.get(classification.category_id)
final_score = clip(base_score.confidence_score * weight, 0.0, 2.0)
```

`run_pipeline.py` 那邊排序改讀 `final_score` 欄位（在 DB 再加一欄 `weighted_score` 或動態 JOIN 都可；先選加欄方案，simpler）。

### 3.4 Back-prop（新模組 `src/reflector_topic.py`）

每週執行一次（launchd 或 GitHub Actions cron）：
1. 取過去 30 天所有『已發布、有 engagement_stats』的 drafts → JOIN 回 news_items.topic_category。
2. 每個類別算 `median_engagement_score`（簡化公式：`likes + 2*comments + 3*shares + 0.01*reach`）。
3. 算全站 median 作為基準線。
4. 對每個類別：
   - `category_delta = category_median / site_median - 1.0`  （大於 0 表示這類比平均好）
   - `new_weight = old_weight × (1 + η × category_delta)`，`η = 0.1`（學習率）
   - `new_weight = clip(new_weight, 0.3, 2.0)`（避免飛走）
5. UPDATE `topic_weights`，`update_reason='back_prop'`, 寫 `reflection_events` 記一筆 log。
6. 產出 Markdown 報告到 `docs/topic_weight_log/YYYY-MM-DD.md`，給 Hsin 週報。

🛑 **護欄**：
- 樣本數 `< 5` 的類別不調（樣本太少，噪音 > 信號）。
- 一次調整幅度 `abs(new - old) > 0.3` 時必須 clip 到 0.3，避免單週巨變。
- 連續 3 週 delta 同方向才算穩定趨勢，否則視為抖動。

### 3.5 觀察性（Hsin 看得到的界面）

在 `docs/MORNING_CHECKLIST.md` 裡加一段週日產出：
- 目前各類別權重表
- 上週 back-prop 的調整幅度
- 下週建議的人工覆核項目（例如『ai_product_launch 連三週權重下降，是不是選題太窄？』）

---

## Ⅳ. Rollout 順序

1. **Step 1（1–2 小時）**：schema migration + seed_topic_weights.py + 手動 UPDATE 現有 news_items（最近 50 筆直接打 LLM classifier）。
2. **Step 2（1 小時）**：topic_classifier.py + 整合進 scorer。下一輪 compose 開始帶著分類跑。
3. **Step 3（30 分鐘）**：weighted_score 加欄位 + run_pipeline 排序改讀新欄。
4. **Step 4（延後 2 週）**：等累積至少 2 週、每類別 ≥ 5 篇真實發文後，再上 back-prop job。在那之前權重保持 seed 值。

---

## Ⅴ. 待 Hsin 決策的問題

1. **初始權重數值**：Ⅱ. 表格的 1.50 / 1.40 / 1.30 是我抓的建議，要不要調？（比方想讓 AI 新品再高一點？）
2. **分類粒度**：`ai_product_launch` vs `tech_product_launch` 要合併嗎？還是再分拆（`ai_model` / `ai_agent` / `ai_application`）？
3. **Back-prop 觸發頻率**：週一次 vs 雙週一次？樣本越多越穩，但越慢見到反應。
4. **`other` 類別**：要不要直接淘汰（權重 0）？還是保留 0.7 作為意外之財的渠道？
5. **engagement 公式**：`likes + 2*comments + 3*shares + 0.01*reach` 是合理加權嗎？Threads / IG / FB 的每個指標含意不同，要不要平台分開算？

回答這 5 題我就可以動 Step 1–3 的 code。Step 4 不急，先讓 Step 1–3 跑 2 週收資料。
