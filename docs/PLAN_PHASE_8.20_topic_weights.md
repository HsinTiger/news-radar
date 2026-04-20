# Phase 8.20 · 主題分類 + 權重 back-prop

> 作者：Cowork Claude · 2026-04-21
> 狀態：**Step 1 已完成**（Hsin 於 2026-04-21 00:55 拍板 5 個決策）
> 依賴：Phase 8.19c/d（Threads 修復 + 推導聲線）已上線
> 目標：把「主題權重」變成一條可觀察、可調整、會根據實際發文成效自動修正的迴路。

**Hsin 的最終決策（2026-04-21）：**
1. AI 新品再高一點 → AI 再拆三類，權重 1.55–1.70（非 AI 壓在 1.20 以下）
2. AI 拆 `ai_model` / `ai_agent` / `ai_application`
3. Back-prop 週一次
4. 保留 `other`（權重 0.70）
5. engagement 公式採用 `likes + 2*comments + 3*shares + 0.01*reach`，且**平台分開算**（Threads / IG / FB 各自用該平台中位數正規化後再加總到類別 delta）

---

## Ⅰ. 使用者需求（原話）

> 增加高效率的演算法，讓資料篩選分不同種類。初始先以**科技圈新發布產品（尤其 AI）/ 產業鏈資訊 / 營收 / 台股 / 美股**的權重最高，之後我們再根據各種文章發布出去的情形 back-propagation 修正我們的資訊主題選擇。

翻成工程語言：
1. 新聞 item 進來時，要先被分到一個「主題類別」。
2. 不同類別乘上不同權重，決定是否進入 composer、或插隊到發文佇列前端。
3. 事後以 `engagement_stats`（likes / comments / shares / reach）回饋，週期性修正各類別權重。
4. 不需要 SGD / 真正的梯度——是「人看得懂的」權重校正（例如 EMA + 上下限）。

---

## Ⅱ. 主題分類 (Topic Taxonomy) — Hsin 拍板版

**10 個類別**，每個有穩定 ID（snake_case）、顯示名、初始權重、白話說明。
單一事實來源：`src/topic_taxonomy.py`。

| ID | 中文 | 初始權重 | 納入條件（examples） |
|---|---|---|---|
| `ai_model` | AI 基礎模型 | **1.70** | GPT / Claude / Gemini 新版；開源 LLM（Llama / Mistral）；多模態模型新代 |
| `ai_agent` | AI Agent／自主系統 | **1.60** | Claude Code / Devin / Manus / Agent SDK；coding agent / research agent |
| `ai_application` | AI 應用層產品 | **1.55** | Perplexity / Cursor / Canva AI / Notion AI；企業導入；垂直領域 AI 方案 |
| `supply_chain` | 產業鏈／供應鏈 | **1.40** | 代工、封測、晶圓、HBM / GaN / EUV；關鍵材料戰略意涵 |
| `earnings` | 營收／財報 | **1.30** | 月營收、法說、毛利、業績指引 |
| `tw_stocks` | 台股個股／大盤 | **1.25** | 主力籌碼、外資動向、台股 ETF、櫃買熱點 |
| `us_stocks` | 美股個股／大盤 | **1.25** | 科技七雄、指數變動、FOMC 對股市的直接影響 |
| `tech_product_launch` | 非 AI 科技新品 | 1.20 | iPhone、特斯拉新車、Vision Pro、遊戲主機、SaaS 主線更新 |
| `policy_geopolitics` | 政策／地緣政治 | 1.00 | CHIPS 法案、對中制裁、AI Act、貿易協定、出口管制 |
| `other` | 其它 | 0.70 | 以上 9 類都不沾邊；不淘汰（保留意外之財） |

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

### 3.4 Back-prop（新模組 `src/reflector_topic.py`，Hsin 要求平台分開算）

**每週一**執行一次（launchd 或 GitHub Actions cron；頻率 Hsin 拍板）：

1. 取過去 30 天所有『已發布、有 engagement_stats』的 posts → JOIN 回
   `news_items.topic_category`。每一則貼文有 3 筆 engagement（FB / IG / Threads）。
2. 每個平台分開算 engagement_score：
   - FB：`likes + 2*comments + 3*shares + 0.01*reach`
   - IG：`likes + 2*comments + 3*shares + 1.5*saves + 0.01*reach`
   - Threads：`likes + 2*replies + 3*reposts + 1.5*quotes + 0.005*views`
   （IG 的 saves、Threads 的 reposts / quotes / replies 在 engagement_stats 已有欄位，公式用得上。）
3. 每個平台分開算**該平台的全站中位數**，作為該平台的基準線。
4. 對每個（類別 × 平台）算 `normalized_delta = category_median / platform_median - 1.0`。
5. **類別 delta = 三平台 normalized_delta 的平均**（平台權重一致；若類別在某平台樣本 < 3 就跳過該平台）。
6. 更新權重：
   - `new_weight = old_weight × (1 + η × category_delta)`，η = 0.1
   - `new_weight = clip(new_weight, 0.3, 2.0)`
   - `abs(new - old)` 超過 0.3 時 clip 到 0.3（單週穩定性護欄）
7. UPDATE `topic_weights`，INSERT 一筆 `topic_weight_history`，同時寫一筆 `reflection_events`。
8. 產出 Markdown 報告到 `docs/topic_weight_log/YYYY-MM-DD.md`，內容：
   - 當週各類別權重（new vs old）
   - 每個類別在三平台的表現分解（讓 Hsin 能看出『這類在 Threads 特別好、但在 IG 弱』這種訊號）
   - 下週建議的人工覆核項目

🛑 **護欄**：
- 樣本數（跨平台合計）`< 5` 的類別不調。
- 單週變動 `abs(new - old) > 0.3` 時 clip。
- 連續 3 週 delta 同方向才視為趨勢；否則視為抖動。
- `other` 類別永遠不自動降到 0.3 以下（保留意外之財的渠道）。

### 3.5 觀察性（Hsin 看得到的界面）

在 `docs/MORNING_CHECKLIST.md` 裡加一段週日產出：
- 目前各類別權重表
- 上週 back-prop 的調整幅度
- 下週建議的人工覆核項目（例如『ai_product_launch 連三週權重下降，是不是選題太窄？』）

---

## Ⅳ. Rollout 順序 + 進度

- ✅ **Step 1（done 2026-04-21 01:00）**：`src/topic_taxonomy.py` 建立、`schema.sql` 新增 4 個 news_items 欄位 + `topic_weights` / `topic_weight_history` 兩張表、`src/db.py::init_db` 加 migration + seed（冪等）、`tests/unit/test_topic_taxonomy.py` 8 個測試全綠。
- ✅ **Step 2（done 2026-04-21 overnight）**：
  - `config/topic_keywords.yaml` 建立（10 類的代表性關鍵字清單）
  - `src/topic_classifier.py`：keyword fast-path（免 LLM、conf=0.6）+ LLM fallback（走 llm_brain.call_for_json）+ orchestrator（保證永遠回 `other` 落地）
  - `src/topic_classifier.compute_weighted_score(base, weight)`：clip 到 [0.0, 2.0]
  - 整合點改在 `run_pipeline.py::process_item`（不動 scorer.py，保留 scorer 的純評分責任）：scoring 通過門檻後 → classify → get_topic_weight → compute_weighted_score → `dbmod.set_news_topic` + `bump_topic_sample_count`
  - `src/db.py`：新增 `set_news_topic` / `get_topic_weight` / `bump_topic_sample_count` helpers
  - `scripts/backfill_topic_classifier.py`（冪等；預設只跑 miss，可 --force 或 --llm）
  - `tests/unit/test_topic_classifier.py`：keyword path + compute_weighted_score 共 12 條全綠
- ✅ **Step 3（done 2026-04-21 overnight）**：
  - `get_pending_items` 改排序：`COALESCE(weighted_score, 0) DESC, published_at DESC`（第一輪 NULL 時行為與舊版同）
  - `pick_fallback_any_approved` 改排序：同上，讓 2h lower-bound fallback 優先挑高權重類別
  - `pick_freshest_queued` **刻意不動**：Phase 8.18 freshness-first 契約不因 weight 而動搖；weight 只在『queue 空或處理 pending』時發聲
  - `tests/unit/test_pick_fallback_weighted.py` 3 條全綠
- ⏳ **Step 4（延後 2 週）**：等累積 ≥ 每類 5 篇真實發文，再上 `src/reflector_topic.py` 做週一 back-prop。在那之前權重保持 seed 值。

**Phase 8.20 附帶守門員（2026-04-21 overnight）**：
- `src/content_quality_guard.py` + `src/local_notify.py`：攔下『【系統代班速報】』系列 emergency_template
- 雙整合點共用同一個純函式 checker（compose-time 防線 + publish-time 防線）
- Mac 本機通知由 publisher 在攔下時自行觸發 `osascript display notification`
- 9 條測試全綠（7 unit + 2 integration shape）

---

## Ⅴ. 已決策事項（封存）

1. 初始權重：**AI 三類 1.55–1.70，非 AI 供應鏈／財報 1.25–1.40，其它 ≤ 1.20**。
2. 分類粒度：**AI 拆三類**（`ai_model` / `ai_agent` / `ai_application`），非 AI 保持單一 `tech_product_launch`。
3. Back-prop：**週一次**，每週一早上。
4. `other`：**保留**，權重 0.70（不自動降到 0.3 以下）。
5. engagement：**平台分開算**，FB / IG / Threads 各自用該平台特色指標加權並用該平台中位數正規化後再加總。

（這 5 條在 Ⅱ / 3.4 節已具體化；此處只做封存。）
