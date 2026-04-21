# Morning Checklist · 2026-04-22（12h 自動化交接）

晚安。你給的 12 小時「我只看你的匯報」的模式，我這邊全部落地。**本機 commit 7 個，還沒 push**（沙箱 proxy 擋 GitHub，一條指令）。

> 原話：「你有12小時的工作時間」「這十二小時你也好花時間替這個工作流程搭建24小時全自動工作環境」「為系統的每個節點都撰寫一份需要加強或調研的report，我回到家後可以幫你把報告一個個交給我在web gemini有的免費ai 額度」「記得用系統設計的思維來做 避免重工或者 spaghetti code」

---

## 🎯 一句話總結

**Phase 8.20 Step 4（週一 back-prop reflector）完整落地 + 24/7 自動化監控三件套上線 + 6 份 Gemini 調研交接書寫好**。系統從今天起，每週一自己 tune 主題權重、每天早上自己出「昨晚怎樣」的報告、任何 RSS 壞掉會自動開 GitHub Issue 叫你。你不需要手動跑任何東西。

---

## 📦 本次新增 commit（7 個，都在 `main`）

```
2b4d82a docs(research): 6 Gemini deep-research briefs for node enhancement
38e1f72 docs(pipeline): Phase 8.20 topic-weight + observability integration
0f4e716 test(schema): regression guard for Phase 8.18/8.20 migration bug
79da44d feat(debug-cli): classify_dryrun.py + queue_inspect.py
13434a1 feat(morning_report): daily system-state one-pager + GH Actions workflow
0cb61ea ci(feed_healthcheck): daily feed-URL ping + auto-open GH Issue on failure
d4f8b6b ci(reflect_topic): Phase 8.20 Step 4 — weekly topic-weight back-prop workflow
f52e927 feat(reflector): Phase 8.20 Step 4 — weekly topic-weight back-prop
```

確認：

```bash
cd ~/Library/CloudStorage/OneDrive-*/*/*/科技商業國際新聞自動化流程研究/news_radar
git log --oneline -10
git push origin main   # 記得這一步
```

---

## 🔁 第 1 件：週一 back-prop reflector（Task #50，Step 4）

**為什麼做**：前一晚的 `MORNING_CHECKLIST_2026-04-21.md` 把 Step 4 明確延後。今天把它補上，讓系統能從真實 engagement 自己校準主題權重，不需要我或你每週手動調整。

**架構**：純統計 + 純函式，零 LLM。

- `src/reflector_topic.py`（649 行）
  - `compute_engagement_score`：平台特化公式
    - FB: `likes + 3·comments + 2·shares`
    - IG: `likes + 2·comments + 0.5·saves`
    - Threads: `likes + 2·replies + reposts`
  - `compute_platform_medians`：同平台 median 做 normalization（避免 FB 普遍高把其他平台吃掉）
  - `compute_category_delta`：對 10 類主題各算「實際 engagement 平均 / 該平台 median」- 1
  - `apply_weight_update`：EMA-style，η=0.1，週上限 ±30%
  - 守門: `TREND_CONSECUTIVE_WEEKS=3`（連續 3 週同向才打破±30% 週上限）
- 共 6 條 guard rails（寫在 `docs/PIPELINE.md` Phase 8.20 Step 4 appendix）：
  1. `MIN_SAMPLES_TOTAL=5` + `MIN_SAMPLES_PER_PLATFORM=3`
  2. `MAX_WEEKLY_DELTA=0.30`
  3. `GLOBAL_WEIGHT_FLOOR=0.30` / `GLOBAL_WEIGHT_CEIL=2.00`
  4. Dry-run 預設（worfklow input 可切 apply）
  5. 產出 markdown 報告 `docs/topic_weight_log/YYYY-MM-DD.md` 給人看
  6. `--lookback-days` 預設 7，可覆蓋

**GH Actions**：`.github/workflows/reflect_topic.yml`
- `cron: "0 22 * * 0"` = 週日 22:00 UTC = **週一 06:00 台北**
- 手動 `workflow_dispatch` 支援 `dry_run` + `lookback_days`
- state-branch DB restore → run → commit markdown 回 main → persist state
- 與 `pipeline.yml` 共用 concurrency group `news-radar-pipeline`，避免 DB 寫競爭

**測試**：`tests/unit/test_reflector_topic.py`（25 條，沙箱全綠）
- 其中 `test_category_delta_averages_three_platforms` 有個 pre-compaction 的 math bug 我修掉了（ai_model likes 150 → 500，才能真的讓 norm_delta > 0.3 trigger）

**第一次會跑的時間**：**2026-04-27（下週一）06:00 台北**。
- 前提：`drafts` 表需要累積 ≥ 5 則有 engagement 數據的 published row。
- 若不足，reflector 會自動 skip 並寫一行 `insufficient samples, skipped` 到 log，不會亂動權重。

---

## ☀️ 第 2 件：morning_report 每日系統狀態（Task #59–#60）

**為什麼做**：你說「我只看你的匯報，不必幫你跑實驗」。所以我寫了一份系統自己每天早上回報「昨晚怎樣」的報告。**你早上看一份 markdown 就知道系統健康不健康、昨晚發了什麼、佇列有沒有塞、有沒有 warning**。

**檔案**：`scripts/morning_report.py`（~290 行，stdlib only，不吃 pydantic）

**6 個 section**（優先序）：
1. `warnings`：queue 裡是否有 stale 超過 24h 的 draft、今日 publish 失敗率
2. `queue_status`：queued / stale / failed / null 四類各幾則
3. `publish_activity`：過去 24h 每平台發了幾則、平均 engagement
4. `last_activity`：最新一則 news_item / draft / publish_log 的時間戳（檢查 pipeline 沒死）
5. `topic_distribution`：過去 7 天 news_items 各類主題命中數（檢查覆蓋率）
6. `feed_coverage`：每個 feed 過去 7 天有無新 item（找假活 feed）

**技術特點**：
- 只開 SQLite read-only (`file:path?mode=ro`)，絕不改 DB
- `PRAGMA table_info` 先檢查欄位存在才 SELECT（相容 pre-Phase-8.18/8.20 DB）
- 若 DB 不存在就印佔位，不 crash
- CLI: `--dry-run`（不寫檔）/ `--stdout`（印到終端）/ `--db-path`（覆寫）

**GH Actions**：`.github/workflows/morning_report.yml`
- `cron: "30 23 * * *"` = 每天 UTC 23:30 = **07:30 台北**（比 feed_healthcheck 晚 30 分）
- 產出 `docs/morning/YYYY-MM-DD.md` commit 回 main
- 首次生成將在 **明天（2026-04-22）07:30**

**手動試跑**：
```bash
python -m scripts.morning_report --stdout --dry-run
```

---

## 🏥 第 3 件：feed_healthcheck 每日 feed 健康巡檢（Task #62）

**為什麼做**：我們每兩週就會有一個 RSS 壞掉（最近一次是 The Diff 改網址）。壞 feed 不會讓 pipeline 崩，但會讓 ingestion 漸漸「偷偷少一條輸入」。

**邏輯**：
- 跑 `python -m scripts.audit_feeds --urls-only`（pure HTTP HEAD/GET）
- 所有 feed 綠 → 自動關閉所有開著的 `feed-healthcheck` label Issue
- 任何 feed 紅 → 若已有 open issue 就 **comment 在同一個**（不洗版），沒有就新開一個掛 `feed-healthcheck` label
- **Workflow 永遠綠色** — 通知走 Issue 不走 CI red status（避免通知疲勞）

**GH Actions**：`.github/workflows/feed_healthcheck.yml`
- `cron: "0 23 * * *"` = 每天 UTC 23:00 = **07:00 台北**
- Permissions: `contents:read` + `issues:write`
- 首次執行 **2026-04-22 07:00 台北**

**你要做的**：早上起來看 `gh issue list --label feed-healthcheck` 就知道有沒有紅掉。正常情況列表應該是空的。

---

## 🔍 第 4 件：兩個 debug CLI（Task #61）

非排程、手動用的，當你看到奇怪分類或 queue 行為想查的時候：

### `scripts/classify_dryrun.py`

用途：看「如果現在重新分類這則 news，它會被丟到哪一類」，對比現有 DB 裡的 `topic_category`。

```bash
# 任意 title 試分類
python -m scripts.classify_dryrun --title "台積電 Q1 法說會：毛利率創新高"

# 回看最近 50 則 news_item 的分類（找系統前後不一致）
python -m scripts.classify_dryrun --recheck-recent 50

# 特定 news_id 的完整分類 trace
python -m scripts.classify_dryrun --news-id <sha1>

# 省 LLM token，只跑 keyword 層
python -m scripts.classify_dryrun --title "..." --keyword-only
```

已知 edge case：「台積電 Q1 法說會：毛利率創新高」會被歸到 `supply_chain`（因為 `台積電` 關鍵字排序優先 `法說會`）。這是 editorial 選擇還是 bug 我留給 `research_briefs/03` 的 Gemini 研究判斷。

### `scripts/queue_inspect.py`

用途：看 queue 現況、debug 卡住的 draft。

```bash
# 預設：weighted_score DESC 排序的 queue 總覽
python -m scripts.queue_inspect

# 只看 stale（卡超過 24h）
python -m scripts.queue_inspect --state stale

# 最近 6 小時的 queue 變動
python -m scripts.queue_inspect --last-hours 6

# 特定 draft 的完整狀態（platform_drafts + publish_log 合併）
python -m scripts.queue_inspect --id 1234

# JSON 輸出給後續處理
python -m scripts.queue_inspect --state failed --json
```

兩個都 read-only，不會動 DB；都相容 pre-Phase-8.18/8.20 的舊 DB（欄位不存在就略過顯示）。

---

## 🛡️ 第 5 件：schema migration 回歸測試（Task #63）

**為什麼做**：pre-compaction 修過一個 `schema.sql` 的 bug — 在 CREATE TABLE 之前就 CREATE INDEX，在舊 DB 上重跑會爆。為了不讓這類 bug 再發生，我寫了 4 條回歸測試。

**檔案**：`tests/unit/test_schema_migration_regression.py`（138 行，沙箱全綠）

1. **行為測試**：合成一個 pre-8.20 DB，跑 `executescript(schema.sql)`，不能炸
2. **文字測試 A**：scan `schema.sql`，CREATE INDEX 行內不能出現 Phase 8.20 欄位名（`topic_category`, `weighted_score`）
3. **文字測試 B**：同上，不能出現 Phase 8.18 欄位名（`queue_status`）
4. **正向測試**：fresh DB 套用 schema.sql 後必須有 `topic_weights` 和 `topic_weight_history` 兩表

兩個維度（行為 + 文字），任何一個都能抓到這類 bug 的復發。

---

## 📖 第 6 件：Gemini Deep Research 交接 6 份（Task #65）

你說「我回到家後可以幫你把報告一個個交給我在web gemini有的免費ai 額度」——所以我把系統每個節點寫成一份 paste-and-go 的 Gemini prompt。

**位置**：`docs/research_briefs/`

| # | 題目 | 約預期輸出長度 | 優先級 |
|---|------|------|------|
| 01 | Composer KOL 風格研究（蕭上農 / 游庭皓 / IEO） | 中長 | **1** |
| 02 | Scorer 選題信心啟發式補強 | 中 | 2 |
| 03 | Topic keywords 覆蓋率審核 | 中 | 6 |
| 04 | 內容品質紅旗擴充 | 中長 | 4 |
| 05 | Hashtag 策略 2026 | 短中 | 3 |
| 06 | 發文時段 & 節奏 7×24 權重表 | 中 | 5 |

**使用方式**：
1. 打開 `docs/research_briefs/README.md` 看優先序（寫在裡面）
2. 打開某份 brief，複製整份 Paste-to-Gemini prompt 區塊（`---` 之間）
3. 貼到你 web Gemini 的 Deep Research，等它跑
4. 跑完把 report 拉回成 markdown 給我，我就能按每份 brief 下面「**用完 report 後 Claude 要做的事**」區塊把結論 wire 進具體 config 或程式碼

**推薦順序**：**01 → 06 → 05 → 04 → 02 → 03**
- 01 最直接影響每篇文章品質（寫作風格）
- 06 立竿見影（把發垃圾時段的 post 截掉）
- 05 effort 最小（改幾個 config）
- 04 是保險（長期不崩）
- 02、03 是精細調校

每份 brief 都完全自包含——Gemini 不需要你額外給它 context。可以丟 1 份、等結果、丟下 1 份，不需要 batch。

---

## ✅ 本次驗證通過的測試

| 檔案 | 條數 | 狀態 |
|------|------|------|
| `test_reflector_topic.py` | 25 | ✅ |
| `test_schema_migration_regression.py` | 4 | ✅ |

沙箱跑的，stdlib only。你 Mac 上 `pytest tests/unit -q` 應該全綠。

**沒跑的**：`morning_report.py`、`classify_dryrun.py`、`queue_inspect.py` 沒有 unit test，但都有 smoke test（合成 DB 跑一次確認 render 正常），且都是「read-only 唯讀 DB」+「tolerant of missing columns」寫法，相對安全。若之後要補 unit test 是 low-effort，但對 24/7 運行不阻塞。

---

## 🧭 系統設計後記（避免 spaghetti）

這次刻意守了幾條邊界：

1. **reflector 是純統計**：不呼叫 LLM，`src/reflector_topic.py` 可以離線跑，產出 markdown + DB 寫入。pure function 部份（`compute_*`）完全 testable，寫入部份（`run_backprop`）是 IO 外殼。
2. **observability 三件套不碰 DB**：morning_report / classify_dryrun / queue_inspect 全部 `file:path?mode=ro`，連 write lock 都不會取得。晚上 pipeline 在跑時你也可以 query 不衝突。
3. **alert 走 GH Issue 不走 red CI**：feed_healthcheck 每天跑，壞了就開 issue、好了就關 issue。workflow 本身永遠綠色，避免每次出錯都收一封 email（notification fatigue）。
4. **schema 規範內化成測試**：Phase 8.20 Step 1 的 bug 不會再發生，因為 4 條 regression 測試把它的 pattern 凍結了。
5. **研究交接是 prompt 不是 task**：6 份 brief 都是 self-contained、paste-and-go；Gemini 跑完你只需把 markdown 給我，我自己 parse + 下游落地。你不用在 Gemini 跟 Claude 之間轉述。
6. **Phase 8.18 + 8.20 契約繼續守**：publisher 不 LLM、freshness-first 不加權、scorer 不知 topic / reflector；reflector 只動 `topic_weights` 表，不碰其他。

---

## ⏳ 還沒做的（明確推遲）

- **把 7 個 commit push 上 GitHub** — 沙箱 proxy 擋，需要你在 Mac 上 `git push origin main`。**Push 之後三個 cron 就會真的跑**（reflect_topic 等到下週一，morning_report 和 feed_healthcheck 明天就會跑第一次）。
- **Gemini 研究實際執行** — 我只能寫 brief，要等你 feed 到 Gemini。沒有 brief 對應的功能增強就不會 landing。
- **Phase 8.20 Step 5 — composer 用 topic_weights 做加權** — 目前 weight 只影響 queue 排序（Step 3）。要讓 weight 也回饋到「哪些主題多寫幾篇」需要改 scorer 或 composer。等 reflector 跑過 2–3 週累積信號再做，過早會把 cold-start 的偏見放大。
- **Research brief 的 post-research wire-up** — 每份 brief 底下都寫好「用完後 Claude 要做什麼」。等你給我 Gemini report 我再執行。

---

## 🌅 明天早上起來你第一件事

1. `git log --oneline -10` 確認 7 個 commit 在 `main` 上
2. `git push origin main` — 讓 3 個 cron 有機會跑
3. 開 `docs/morning/2026-04-22.md`（workflow 跑完會出現）看系統健康度
4. `gh issue list --label feed-healthcheck` 看 feed 有沒有紅
5. 選一份 brief（建議從 `01_composer_personae.md` 開始），貼給 Gemini Deep Research
6. 把 Gemini report 丟回 Claude，我繼續下游

—— 2026-04-22 overnight, Cowork Claude
