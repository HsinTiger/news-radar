# News Radar · Pipeline Contract

> 每個階段的輸入 / 輸出 / 失敗模式合約。修改任何一層時，
> 先對照本檔確認**自己打算改變的 contract**，再動程式碼。

> ⚠️ **2026-04-21 更新 · 部分章節已被 Phase 8.11/8.17/8.18/8.19/8.20 覆蓋**
>
> 本檔最原始的 Stage 02-04 contract 反映 **Milestone 2-3** 時期的設計，與目前 production 行為有以下差異。在重讀前請先掌握差異清單，必要時以 `docs/architect_plan_disscussion.md` 為 ground truth：
>
> | 條目 | 本檔原文 | 目前實況（Phase 8.20） |
> |------|----------|-----------------------|
> | scorer.py 是否呼叫 LLM | Stage 02 合約要求「**不得**呼叫 LLM」 | 呼叫 LLM（Gemini → Claude CLI 雙路徑，經 `src/llm_brain.py`），該要求已作廢；scorer 產 `NewsScore`（含 confidence / breakdown / editorial_note） |
> | 排序依據 | Stage 02 confidence_score 單鍵排序 | `news_items.weighted_score = scorer_confidence × topic_weights.weight`（Phase 8.20 Step 3），排序以 weighted_score 為主、confidence_score 為 tie-breaker |
> | Compose 後如何送至 publisher | Stage 03 → Stage 04 直接手動審核 | Mac 端 `run_pipeline.py --compose-only` 寫入 `drafts.queue_status='queued'`；Cloud 端 `run_publish_queue.py` 每小時從 queue 挑最新一筆發文（見 Phase 8.18） |
> | Publish 的觸發來源 | `platform_drafts` where `status='approved'`（手動審核） | `drafts.queue_status='queued'`（auto-approve via confidence_score ≥ AUTO_PUBLISH_THRESHOLD）；approved 流程僅做 legacy fallback |
> | schedule.publishing_slots + jitter | 合約要求先查 slots | 已不存在；改為 `cadence` 規則：min 1hr、max 2hr (rescue) |
> | LLM 雙路徑全失敗時的行為 | 未定義 | 回傳 `"skipped_no_llm"` / 不寫 draft / news 狀態保持 fetched；**絕不塞 fallback 範本或偽分數**（Phase 8.19 emergency template + scorer-fail fabricated score 均已移除） |
> | 主題分類是否存在 | 未定義，所有 news_items 無分類欄位 | Phase 8.20 Step 1-2：`news_items.topic_category/topic_confidence/topic_rationale`；classifier 走 keyword fast-path → LLM fallback |
> | 每篇貼文互動如何回饋到選題 | `reflector.py` 只改 soul.md，不調權重 | Phase 8.20 Step 4：`reflector_topic.py` 每週一跑，把三平台 engagement 依照固定公式聚合→正規化→用 EMA-style 溫和更新 `topic_weights` |
>
> 其餘章節（Stage 01 Harvest / Stage 05 Feedback / Phase 8.11 Module 3-7 延伸設計）仍有效。

---

## 總覽

```
┌─────────┐  01_harvest  ┌─────────┐  02_score  ┌─────────┐
│  RSS    │ ───────────► │ cleaner │ ─────────► │ scorer  │
│ Feeds   │              │ + filter│            │ 信心分  │
└─────────┘              └─────────┘            └────┬────┘
                                                     │
                                           03_compose▼
                                              ┌──────────┐
                                              │composer  │
                                              │ (LLM)    │
                                              └────┬─────┘
                                       04_publish ▼
                                              ┌──────────┐
                                              │publisher │
                                              │Meta API  │
                                              └────┬─────┘
                                       05_feedback▼
                                              ┌──────────┐
                                              │reflector │
                                              │ (LLM-W)  │
                                              └──────────┘
```

- **白框** = deterministic，零 token。
- **灰框**（composer / reflector）= 呼叫 LLM。

---

## Stage 01 · Harvest

### 輸入

- `config/config.yaml` → `feeds[]`（RSS URL）、`filters`（關鍵字、字數）

### 輸出

- SQLite `news_items` 資料列（status ∈ {`fetched`, `dropped`}）
- `logs/execution_log.jsonl` 追加一筆 `HarvestReport`

### 資料保證

- 每個 `news_items.id` = `sha1(url)`，**跨日穩定**
- `status = 'fetched'` 的 item 保證有非空 `clean_markdown` 與 `word_count > 0`
- `status = 'dropped'` 的 item 保證有 `drop_reason`（格式：`<reason>:<meta>`）

### 已知失敗模式

| 模式 | 表徵 | 排查 |
|---|---|---|
| Feed 404 | HarvestReport.errors 有對應 URL | `diagnose_feeds.py` |
| Bot 牆（Bloomberg/OpenAI） | RSS 可抓，文章頁 403 | 從 config 下架 |
| YouTube 短路 | `clean_markdown` 起頭 `YouTube Interview Description`，`word_count` < 100 | 見 DEBUGGING §4 |
| trafilatura 失敗 | `drop_reason = extract_failed` | `replay_item.py --refetch` |

---

## Stage 02 · Score

### 輸入

- SQLite `news_items` where `status = 'fetched'`
- `config.review.score_weights`

### 輸出

- `news_items.status` ← `scored`
- （擴充欄位預留）`score_density / score_structural / score_novelty / score_persona`
- 若 `score_total < manual_review_threshold` → status ← `dropped`（reason = `low_score`）

### 合約要求

- `scorer.py` **不得**呼叫 LLM
- 每個 sub-score ∈ [0, 1]
- 權重和 = 1.0（由 config 保證）

---

## Stage 03 · Compose

### 輸入

- SQLite `news_items` where `status = 'scored'`
- `config/news_radar_soul.md` + `config/platforms/{fb,ig,threads}.md`
- LLM（primary model 由 `config.llm.primary` 決定）

### 輸出

- `drafts` 表：每個 item 一筆 `Draft`
- `platform_drafts` 表：每個 item × 每個啟用平台 一筆 `PlatformVariant`
- `news_items.status` ← `drafted`

### 合約要求

- 輸出必須是 `MultiPlatformDraft` Pydantic 驗證通過的 JSON
- 每個平台的 `char_count` 誤差 < 5%（由程式再校驗一次）
- token 花費寫入 `drafts.input_tokens / output_tokens / cost_usd`

---

## Stage 04 · Publish

### 輸入

- `platform_drafts` 表 where `status = 'approved'`（手動審核通過）
- `.env` 的 Meta Graph API token

### 輸出

- `publish_log` 表：每次發文一筆
- `news_items.status` ← `published`（所有平台都成功才算）

### 合約要求

- 永遠先查 `schedule.publishing_slots` + `jitter`，不要即時發文
- 同一個 `news_id` 兩次發文間隔至少 `schedule.min_interval_minutes`
- 單日總量不超過 `schedule.max_posts_per_day`

---

## Stage 05 · Feedback

### 輸入

- 過去 7 天 `publish_log` + Meta Graph Insights API
- 使用者在 CSV 的手動修改（`drafts_for_review.csv`）

### 輸出

- `engagement_stats` 表：每則貼文的互動數
- `reflection_events` 表：每週一筆反省紀錄
- 自動 append 到 `config/news_radar_soul.md` 的 `Ⅸ. Iteration Log`

### 合約要求

- 反省模型：`config.llm.premium`
- 單次反省成本 < 0.20 USD（寫入 `reflection_events.cost_usd`）

---

## Schema 變更流程

1. 在 `src/schema.py` 改 Pydantic model
2. 若涉及 SQLite 欄位 → 到 `data/01_harvest/schema.sql` 加 `ALTER TABLE`
3. 到 `scripts/` 寫一個 `migrate_<timestamp>.py`，幫 production DB 套用變更
4. 跑 `pytest tests/unit/test_schema.py`
5. 跑 `pytest tests/integration/test_db_roundtrip.py`
6. 在 `AGENT_WORKLOG.md` 記錄 schema 版本號

---

# Phase 8.11+ Extension · Module 3–7 閘道與品質評估

> **SUNSET (2026-04-26):** 本章描述的 Module 3–7 設計（multi-variant compose + LLM rubric scorer + gate.py）已**正式停用**，不再是後端建置目標。
> 取代方案：[../../PM_Radar/roadmap/phase_9_unified_reflector.md](../../PM_Radar/roadmap/phase_9_unified_reflector.md)（Hsin 2026-04-26 核可的 Phase 9 Unified Reflector 設計，Proposal A）。
> 原因：Phase 9 把 composer 端的回饋迴路統一到 reflector 的 top-Q / bot-Q sibling-draft 取樣 + LLM 規則萃取，使 Module 3–7 的 multi-variant + rubric 流程冗餘。
> 以下章節保留作為歷史紀錄與背景脈絡，**請勿據此實作**；新工作請參照 Phase 9 文件。

> 本章補上原 Stage 02–04 之間缺掉的 ranker / selector / quality-scorer / gate 四個環節。
>
> 原 Stage 02 "Score" 的 contract 其實是 **item-level ranking**（deterministic、不碰 LLM），本章將它重新命名為 **Module 3 Rank** 以避免與 Module 6（draft quality scorer）混淆。
>
> Legacy `src/scorer.py` 現有邏輯其實是 item-level rank（Gemini 做信心分），屬於 Module 3 的初代原型。Phase 8.11 會 refactor 拆出 `src/ranker.py`（deterministic 版）+ 保留 LLM 擴充選項。

---

## Module 3–7 總覽（延續 Module 1–2）

```
Module 1: fetch     ✓ shipped  → items.status = fetched
Module 2: clean     ✓ shipped  → items.status = cleaned / dropped
                                │
                                ▼
Module 3: rank      🆕 零 token → items.rank_score + rank_features
                                │
                                ▼
Module 4: select    🆕 零 token → items.selected_at (top-K pick)
                                │
                                ▼
Module 5: compose   🆕 吃 LLM  → drafts (N variants × 3 platforms per item)
                                │
                                ▼
Module 6: scorer    🆕 吃 LLM  → draft_scores (multi-component)
                                │
                                ▼
Module 7: gate      🆕 零 token → gate_decisions (publish / retry / skip)
                                │
                                ▼
Module 8: publish   🔄 重構     → 由 Module 7 觸發，不再靠手動 CSV 審核
```

**關鍵設計決策（這些都討論定案了，改動前請重讀對應區段）**：

| 決策 | 結論 | 理由 |
|---|---|---|
| 三平台策略 | **三平台同源 + 各自通關** | Module 4 挑 1 個 item、Module 5 三個寫手對同一 item 各寫 N variant、Module 7 的 gate 各平台獨立 |
| 選題 vs 品質 | **Module 3/4 管選題、Module 6 管品質** | 不同優化目標，合在一起打分會失焦 |
| Scorer 架構 | **rubric 組合（5 components）+ versioned YAML** | soul（style guide）跟 shell（prompt template）解耦 |
| 評分閾值 | **graduated fallback**（normal 0.9 → urgent 0.85 → critical 0.8 → 4h 後強制發）| 可見度下限（≥ 1/2h）跟品質下限（> 0.9）互斥時的梯度讓步機制 |
| Rate limit | **放在 Module 7 gate**（`cooldown_min_hours` + `max_silence_hours`）| 不另開模組，邏輯屬 gate 天職 |
| 冷啟動評分 | **用 proxy**（style adherence / structural / factual grounded / novelty / simulation fit）| 沒受眾資料前不能評「受眾反應」，只能評其代理指標 |
| Dynamic tuning | **week ritual, human-in-loop，不自動調**（Spearman correlation 跑出報告、人工改 rubric v2）| 資料稀疏期自動調會漂移，且失去 narrative 控制權 |

---

## Module 3 · Rank（deterministic，零 token）

### 輸入

- `news_items` where `status = 'cleaned'` AND 未被 Module 4 選過
- `config.rank.weights` + `config.rank.features`

### 輸出

- `news_items.rank_score: float` ∈ [0, 1]
- `news_items.rank_features: JSON`（各 feature 原始值，供 diagnose 回看）
- `news_items.ranked_at: datetime`
- `news_items.status` ← `'ranked'`

### Feature 清單（v1 建議，全部零 token）

| Feature | 計算方式 | 預設 weight |
|---|---|---|
| `signal_density` | 文本每 100 字含幾個數字 / 貨幣符號 / 百分號 / 專有名詞 | 0.25 |
| `freshness_decay` | `exp(-hours_since_published / 24)` | 0.20 |
| `source_tier_bonus` | primary tier = 1.0, secondary = 0.7 | 0.15 |
| `keyword_match_strength` | `must_include_any` 命中關鍵字數量 normalized | 0.15 |
| `has_og_image` | 有 og:image URL 則 1.0，否則 0.0 | 0.10 |
| `novelty_vs_recent` | 對近 7 天已 rank 過的 item 做 embedding 相似度，1 - max_sim | 0.10 |
| `word_count_sweet_spot` | 字數落在 300–2000 之間給高分，兩端遞減 | 0.05 |

### 合約要求

- `ranker.py` **不得**呼叫 LLM
- `rank_score` ∈ [0, 1]，權重和 = 1.0（由 config 校驗）
- 每次 rank 的 `weights_version` 寫進 DB（可回溯）
- Legacy `src/scorer.py` 的 LLM-based ranking **不進入 MVP**，保留為 A/B 選項（`config.rank.backend: deterministic | gemini_legacy`）

### 已知失敗模式

| 模式 | 表徵 | 排查 |
|---|---|---|
| 特徵分佈偏移 | `rank_score` 全集中在 0.4–0.5 | 跑 `tools/diagnose_rank.py` 看各 feature 直方圖 |
| 關鍵字命中爆炸 | `keyword_match_strength` = 1.0 的 item 過多 | 審視 `config.filters.keywords` 是否太寬 |
| Novelty 誤判 | 新題被判為與舊文近似 | embedding 模型要固定，換模型前先 backfill |

---

## Module 4 · Select（deterministic，零 token）

### 輸入

- `news_items` where `status = 'ranked'` AND `ranked_at` 在 config 指定視窗內
- `config.select.top_k`（預設 3）
- `config.select.diversity_constraints`

### 輸出

- 指定 K 個 items 的 `status` ← `'selected'`，`selected_at: datetime`
- 其餘 `status` 保持 `'ranked'`（下次 Module 3 重跑還可以參與）
- `logs/select_decisions.jsonl` 每次寫一筆（被選/被拒 + 原因）

### Diversity 約束（v1）

- 同一 feed 最多貢獻 top-K 的 1 個
- 同一 tags[] 交集 ≥ 2 的 item 不同時入選（避免 AI 新聞霸佔三席）
- 近 24 小時已 selected 過的 topic cluster 降權（用 rank_features 的 embedding 比對）

### 合約要求

- 選擇策略**確定性**：同一份 input 永遠選出同一組 output（利於 replay）
- Emergency re-select 模式（由 Module 7 gate 在 > 4h 冷掉時觸發）：放寬 diversity 約束、從 `rank_score > 0.5` 的 item 中取 top-1

### 已知失敗模式

| 模式 | 表徵 | 排查 |
|---|---|---|
| Diversity 過嚴導致 pool 空 | `select_decisions.jsonl` 顯示 K < 設定值 | 暫時降 diversity constraints |
| 同一 item 反覆被選但寫不出稿 | Module 5/6 狀態回不到正常 | 加「最近 7 天被 selected N 次以上降權」規則 |

---

## Module 5 · Compose（吃 LLM）

### 輸入

- `news_items` where `status = 'selected'`
- `config/news_radar_soul.md`（**Layer 1 · fused soul**：user + 三位 KOL 融合後的核心寫作 DNA）
- `config/platforms/{fb,ig,threads}_v2.md`（**Layer 2 · 平台特化**：同一 soul × 平台慣例）
- `config.compose.variants_per_platform`（預設 3）
- LLM（`config.llm.primary`）

### 輸出

- `drafts` 表：每個 (item, platform, variant_index) 一筆
  - Schema: `id, item_id, platform, variant_index, content, word_count, created_at, model, input_tokens, output_tokens, cost_usd, soul_version`
- `news_items.status` ← `'drafted'`（所有三平台都寫完才算）

### 合約要求

- 每個 item 產 `3 platforms × N variants = 3N` 個 draft
- 每份 draft 必須通過平台的 `char_count` 範圍 pre-check（違規 draft 直接 reject 不入 DB）
- 失敗的 LLM call 要 retry 最多 2 次，都失敗則該 (item, platform) 進 `compose_failures.jsonl`，不阻塞其他平台
- Token 花費逐筆寫 DB

### 與 legacy `composer.py` 的差異

- 舊版：1 item → 1 draft per platform（單次輸出）
- 新版：1 item → N variants per platform（給 Module 6 選）
- 保留舊介面 `composer.compose_single(item, platform)`，新增 `composer.compose_variants(item, platform, n)`

---

## Module 6 · Scorer（吃 LLM，品質閘）

### 輸入

- `drafts` where `scored = False`
- `config/scorers/{platform}_rubric_v{N}.yaml`（versioned）
- LLM（`config.llm.judge`，可與 composer 不同模型）

### 輸出

- `draft_scores` 表：每個 draft 一筆
  - Schema: `draft_id, total_score, components_json, rubric_version, soul_version, judge_model, scored_at, input_tokens, output_tokens, cost_usd`

### 5 個 Component（v1 設定）

| Component | 類型 | 預設 weight | 參照來源 |
|---|---|---|---|
| `style_fit` | LLM judge | 0.30 | `config/news_radar_soul.md`（fused soul）+ `config/platforms/{platform}_v2.md`（平台 adaptation）|
| `structural` | 全規則（零 token 子項）| 0.25 | rubric YAML 裡的 rules |
| `factual_grounded` | LLM judge | 0.20 | source item 的 `clean_markdown` |
| `novelty` | embedding 演算法 | 0.15 | 近 30 天 published posts |
| `simulation_fit` | LLM judge | 0.10 | 平台慣例 benchmark KOL（FB: IEObserve / IG: Fox Hsiao / Threads: 游庭皓；作為該平台發文慣例參照，非身份模板）|

### 合約要求

- `total_score = sum(weights × component_scores)`，∈ [0, 1]
- `components_json` 必須記錄**每個 sub-component 的原始值**，供 regression tool 事後分析
- `rubric_version` + `soul_version` + `judge_model` 三個版本號同時寫 DB（任一換了要能歸因）
- Structural 失敗（例如 IG 無 og:image）應該 penalty 強到讓 total_score < 0.6，不期待其他 component 救場

### 初始 rubric（v1）存放位置

```
config/scorers/
├── fb_rubric_v1.yaml      # fused soul × FB 平台慣例的評審（平台慣例參照 IEObserve）
├── ig_rubric_v1.yaml      # fused soul × IG 平台慣例的評審（平台慣例參照 Fox Hsiao）
└── threads_rubric_v1.yaml # fused soul × Threads 平台慣例的評審（平台慣例參照 游庭皓）
```

**重要**：三份 rubric 共用同一個 soul（`config/news_radar_soul.md`），差異只在「該平台慣例 adaptation」段與 benchmark KOL。KOL 在此處扮演**平台發文慣例的實證樣本**，非 voice identity。

### 已知失敗模式

| 模式 | 表徵 | 排查 |
|---|---|---|
| LLM judge 飄 | 同一 draft 兩次 score 差 > 0.1 | 降 judge 溫度、固定 seed、改 `response_schema` 強制 JSON |
| 分數集中高分 | 所有 draft 在 0.85–0.95 | rubric 需要收緊（提 component 分數要求）|
| 分數集中低分 | 所有 draft < 0.7 | 先檢查 composer 品質，再看是否 rubric 過嚴 |

---

## Module 7 · Gate（deterministic，閘道決策）

### 輸入

- `draft_scores` for current (item, platform) 的所有 variant
- `publish_log`（歷史發文時間，用於 rate limit 判斷）
- `config.gate.*`（thresholds, cooldown, max_silence）

### 輸出

- `gate_decisions` 表：每個 (item, platform) 一筆
  - Schema: `decision_id, item_id, platform, decision ∈ {publish, retry, skip}, chosen_draft_id, threshold_used, urgency_level, decided_at, reason`
- `drafts.status` ← `'published' | 'rejected_by_gate' | 'archived'`
- 若 `decision = publish` → trigger Module 8 (publisher) 並寫 `publish_log`

### Graduated fallback 邏輯

```yaml
# config.yaml
gate:
  cooldown_min_hours: 1.0       # ≤ 1 post / 1h 硬鎖
  urgency_start_hours: 2.0      # > 2h 後可以 relax
  max_silence_hours: 4.0        # > 4h 無論如何要發
  thresholds:
    normal:   0.90
    urgent:   0.85              # 2h 後的第一次 relax
    critical: 0.80              # 3h 後的第二次 relax
  max_drafts_normal:   9        # 3 輪 × 3 variant
  max_drafts_extended: 15       # urgent+critical 再加兩輪
```

決策狀態機：

```
時距上次發文 < 1h                        → decision: skip (cooldown_lock)
時距 1–2h, 有 draft ≥ 0.9                → decision: publish (normal)
時距 1–2h, 無 draft ≥ 0.9, drafts < 9    → decision: retry (compose 再 3 篇)
時距 1–2h, 無 draft ≥ 0.9, drafts = 9   → decision: skip (quality_gate)
時距 2–3h, 無 draft ≥ 0.9                → decision: retry (relax 到 0.85)
時距 3–4h, 無 draft ≥ 0.85               → decision: retry (relax 到 0.8)
時距 > 4h,  仍無 draft ≥ 0.8              → decision: publish (emergency_publish, 取最高分)
時距 > 4h, draft 池為空                   → trigger Module 4 emergency re-select
```

### 合約要求

- `gate.py` **不得**呼叫 LLM
- 每個 decision 必須包含 `urgency_level` 標籤（`normal | urgent | critical | emergency`），供週報統計 emergency 佔比
- `emergency_publish` 發的稿在 DB `drafts` 與 `publish_log` 雙邊都要標 `emergency: True`，讓 feedback loop 能分層評估

### 觀察指標（Phase 8.11 上線後 14–30 天要看的）

- `emergency_publish` 佔總發文比例：目標 < 10%，超過代表系統性問題
- 各 `urgency_level` 的 engagement 差異：normal vs emergency 的表現落差 = 「降閾值」的真實成本
- Cooldown lock 觸發頻率：若長期 > 30% 代表上游太多爆款稿、節奏壓不住

---

## 狀態機（Entity State Machines）

### Item 狀態

```
    fetched
       │
       ▼ (Module 2 cleaner)
    cleaned ─────────► dropped (drop_reason: extract_failed / keyword / too_short / blacklist)
       │
       ▼ (Module 3 ranker)
    ranked
       │
       ▼ (Module 4 selector)
    selected
       │
       ▼ (Module 5 composer, 三平台都完成)
    drafted
       │
       ▼ (Module 6 scorer, 所有 variant 都評分完)
    scored
       │
       ▼ (Module 7 gate)
       ├─► published   (至少一個平台有 draft publish)
       ├─► partial     (部分平台 publish、部分 skip；視為成功)
       └─► all_skipped (所有平台都 gate skip；item 重新進 ranked 等下輪)
```

### Draft 狀態

```
    pending (compose 剛產出)
       │
       ▼ (Module 6 scorer)
    scored
       │
       ▼ (Module 7 gate 決策)
       ├─► published           (被選為最佳、發出)
       ├─► rejected_by_gate    (分數不夠、gate 拒)
       └─► archived            (同一 (item, platform) 有別的 variant 被選，本稿封存)
```

---

## Module 3–7 I/O 契約速查表

| Module | 輸入 status | 輸出 status | LLM? | 新增 DB 欄位 |
|---|---|---|---|---|
| 3 Rank | `cleaned` | `ranked` | N | `rank_score`, `rank_features`, `ranked_at` |
| 4 Select | `ranked` | `selected` | N | `selected_at` |
| 5 Compose | `selected` | `drafted` | Y | drafts 表（新表）|
| 6 Scorer | drafts.pending | drafts.scored | Y | draft_scores 表（新表）|
| 7 Gate | drafts.scored | `published` / `all_skipped` | N | gate_decisions 表（新表）|

---

## 實作順序（Phase 8.11 推薦）

> 原則：先上零 token 模組（Module 3/4/7），最後才上吃 token 的（Module 5/6）。
> 這樣 MVP 的前半段可以對 172 筆全量跑，獲得 baseline 數據，再決定 Module 5/6 的 LLM 預算。

1. **Module 3 (rank)**：deterministic，Phase 8.7 的 diagnose 框架可直接延用。7 個 feature 寫完 + pytest。寫 `tools/diagnose_rank.py` 看分佈。
2. **Module 4 (select)**：小而完整，主要是 diversity 邏輯 + tests。
3. **schema + DB migration**：在 3/4 上線前把 drafts / draft_scores / gate_decisions 三張表加進 schema.sql，migration script 寫好。
4. **Module 7 (gate) 骨架**：先寫 deterministic 邏輯，mock draft_scores 餵進來驗證狀態機對。比 Module 5/6 先寫，讓 token 成本預估有依據。
5. **Module 5 (compose v2)**：refactor 現有 `composer.py`，加 N-variant 支援。保留舊介面以免 break 手動流程。
6. **Module 6 (scorer v1)**：**只實作 style_fit + structural 兩個 component**（其他三個留 Phase 8.12+）。rubric YAML 骨架 + 一次 LLM call。
7. **End-to-end dry run**：Module 3→4→5→6→7 全跑一輪、不觸發 Module 8 (publish)，人工看產出是否合理。
8. **Module 8 publisher 整合**：由 Module 7 `decision = publish` 觸發，取代手動 CSV 流程。
9. **14 天 baseline 跑起來**：這才是真正的 MVP 驗收標準。

---

## 三層 Soul / Shell 架構（與 Phase 8.6 的關係）

寫手系統為**三層**，非 KOL-per-platform 的 1-to-1 對應：

| Layer | 實體檔案 | 意義 | 版本化欄位 |
|---|---|---|---|
| **Layer 1 · Fused Soul** | `config/news_radar_soul.md` | user 自身風格 + 三位 benchmark KOL（IEObserve / Fox Hsiao / 游庭皓）融合後的核心寫作 DNA。**唯一 soul**，所有平台寫手共用。 | `soul_version` |
| **Layer 2 · Platform Adaptation** | `config/platforms/{fb,ig,threads}_v2.md` | 同一 soul × 各平台發文慣例 = 三位平台特化寫手。字數、版面、hook 形式依平台調整。 | `platform_guide_version` |
| **Layer 3 · Rubric Shell** | `config/scorers/{platform}_rubric_v{N}.yaml` 的 `judge_prompt_template` 與 weights | LLM judge 執行評分的 prompt + rubric。同一 soul 可對應多版 rubric。 | `rubric_version` |

**KOL 在本架構的角色**：benchmark KOL **不是**某平台寫手的身份模板，而是兩件事——
1. Layer 1 的 soul material：三位 KOL 的共同優點（IEObserve 的產業版圖 / Fox Hsiao 的冷靜工匠 / 游庭皓 的數據架構）已在 Phase 8.6 被萃取、融入 `news_radar_soul.md`。
2. Layer 2 的 platform convention benchmark：每位 KOL 碰巧各自在一個平台經營得好，其**平台操作慣例**（IEObserve 在 FB 的長文結構、Fox Hsiao 在 IG 的視覺排版、游庭皓 在 Threads 的金句節奏）就作為該平台 adaptation 的參照樣本。

### 版本升級的影響範圍

| 改動層 | 觸發 | 重跑範圍 |
|---|---|---|
| Layer 3 改 rubric | 調權重、加 component、改 judge prompt 措辭 | 只重跑 Module 6 scorer，不動 composer |
| Layer 2 改 platform adaptation | 某平台寫出來 engagement 長期落後、平台政策改變 | 重跑該平台的 Module 5 + 6 |
| Layer 1 改 fused soul | 擴張到新族群、品牌定位調整 | 全量 re-test（三平台 Module 5 + 6 都要重跑一輪校準）|

DB 每筆 score 同時記 `soul_version`、`platform_guide_version`、`rubric_version`，任一層改動可獨立歸因。

---

## Backlog 連結

未進 Phase 8.11 scope 的所有擴充（新主題 source、新人格、視覺處理 vertical slice）見 `docs/BACKLOG.md`。
MVP 跑完 14 天 baseline 後，依該檔順序解鎖。

---

# Phase 8.20 · Topic-weight Classifier + Back-prop

> 2026-04-21 加入。把「哪類主題值得發」從 soul.md 裡的模糊原則，搬到一張可以
> 被 engagement 數據持續調整的權重表。

## 為什麼要這層

- 原本 pipeline 只有「每篇的 confidence_score」這個 axis。同分時 composer
  無從選題，等於依 RSS 抓到的順序發。
- 不同主題 engagement 天生不同，若不分類直接訓練，composer 會被「標題黨新
  聞」拉走（近 30 天被轉發 10 次的那篇是 clickbait 還是硬新聞？分不出來）。
- 拆成兩層後：
  - **Classifier**（短期、確定性）：把每篇新聞歸到 10 個穩定類別之一
  - **Back-prop**（長期、統計）：把每類別被三平台接受的程度聚合成一個
    `topic_weights.weight`，回注到 `weighted_score = confidence × weight`

## 10 類 taxonomy

寫在 `src/topic_taxonomy.py`，也 seed 到 `topic_weights` 表。Hsin 初期設定：

| id | display | 初始權重 | 備註 |
|---|---|---:|---|
| `ai_model` | AI Model | 1.70 | 基礎模型本體 |
| `ai_agent` | AI Agent | 1.60 | 自主 / coding agent |
| `ai_application` | AI Application | 1.40 | 應用層 |
| `supply_chain` | Supply Chain | 1.40 | 半導體 / 封測 / 電池 |
| `earnings` | Earnings | 1.30 | 財報 / 指引 |
| `policy_geopolitics` | Policy / Geopolitics | 1.20 | 法案 / 制裁 / 貿易 |
| `us_stocks` | US Stocks | 1.20 | 指數 / Fed |
| `tech_product_launch` | Tech Product | 1.10 | 非 AI 新品 |
| `tw_stocks` | TW Stocks | 1.00 | 台股專題 |
| `other` | Other | 0.70 | 兜底 |

## Step 1：Schema + seed（`src/db.py::init_db`）

- `news_items` 新增 `topic_category / topic_confidence / topic_rationale / weighted_score`。
- 新表 `topic_weights`、`topic_weight_history`（見 `data/01_harvest/schema.sql`）。
- index 寫在 db.py::init_db 的 migration 之後，不是 schema.sql 裡——
  不然對舊 DB（還沒 ALTER TABLE）會炸。**regression 測：**`tests/unit/test_schema_migration_regression.py`。

## Step 2：Classifier（`src/topic_classifier.py`）

### 兩層策略
1. **Keyword fast-path**（免 LLM 費用）—— 讀 `config/topic_keywords.yaml`，命中即回
   `confidence=0.60`。排序按 taxonomy 順序，前面類別越具體越優先。
2. **LLM fallback**（只有 keyword 全 miss 才打）—— 經 `llm_brain.call_for_json`
   雙路（Gemini → Claude CLI），強制輸出 pydantic schema。

### 合約
- 永遠回一個 `TopicClassification`；就算兩路都失敗，也回 `other / 0.0 /
  'classifier_unavailable_or_all_missed'`——讓呼叫端看得出 signal 可不可信。
- 分類結果寫到 `news_items.topic_category`，但**不**寫 `status='classified'`；
  Stage 02 scorer 仍是唯一能改 `status='scored'` 的節點。

### Debug CLI
`scripts/classify_dryrun.py` —— 給邊界 case 手動驗算：
- `--title/--content` 試單則
- `--news-id` 從 DB 抓某則重跑
- `--recheck-recent 20` 批次對照，列分歧

## Step 3：Weighted Score（scorer + sort）

- `src/scorer.py` 計算完 `confidence_score` 後，查 `topic_weights.weight`、
  呼叫 `compute_weighted_score(confidence, weight)` → 寫回
  `news_items.weighted_score`（clip 0–2）。
- 下游（composer 排隊）改用 `weighted_score DESC, confidence_score DESC`。

## Step 4：Weekly Back-prop（`src/reflector_topic.py`）

### Engagement 公式（Hsin 拍板）

```
FB：      likes + 2*comments + 3*shares + 0.01*reach
IG：      likes + 2*comments + 3*shares + 1.5*saves + 0.01*reach
Threads： likes + 2*replies  + 3*reposts + 1.5*quotes + 0.005*views
```

每平台獨立做中位數正規化：`normalized_delta = 該類該平台中位數 / 平台全站中位數 − 1`。
一個類別的 `raw_delta` = 三平台 normalized_delta 的平均（某平台樣本 < 3 時該平台不算進平均）。

### 更新公式

```
proposed_new = old × (1 + η × raw_delta)          # η = 0.1
delta        = clip(proposed_new − old, ±0.30)    # 單週穩定性護欄
new          = clip(old + delta, [0.30, 2.00])    # 全域護欄（other 也受此底線）
```

### Guard rails

1. 該類跨平台合計樣本 < 5 → **整類跳過**（`skipped_reason='low_samples'`），weights 不動。
2. 某類某平台樣本 < 3 → 該平台不算進類別 delta 平均。
3. 單週絕對變動超過 0.30 → clip。
4. 全域 clip 到 [0.30, 2.00]。
5. 連續 3 週同方向才標 `trend='up'/'down'`（report-only，不改 math）。

### 產出

- `UPDATE topic_weights` + `INSERT topic_weight_history`（append-only，即使 skip 也留）。
- `INSERT reflection_events`（跟 daily soul reflector 共用這張表）。
- `docs/topic_weight_log/YYYY-MM-DD.md`（週一 06:00 TW 自動 commit 到 main）。

### 執行

- Cron：`.github/workflows/reflect_topic.yml`，週一 06:00 TW（Sun 22:00 UTC）。
- 手動：`python -m src.reflector_topic --dry-run` 或 workflow_dispatch。

## Step 5：Observability

每日由 GitHub Actions 產出兩份人類可讀的儀表板：

- `.github/workflows/morning_report.yml` → `docs/morning/YYYY-MM-DD.md`
  （queue 狀態 / 昨日發文 / 主題覆蓋 × 權重 / 7d feed 貢獻）
- `.github/workflows/feed_healthcheck.yml` → 有 feed 掛掉自動開 GitHub issue，
  綠了自動 close。

Debug CLI：`scripts/queue_inspect.py`（看單張 draft 的 platform variants /
publish_log 詳情）+ `scripts/classify_dryrun.py`（上面 Step 2）。
