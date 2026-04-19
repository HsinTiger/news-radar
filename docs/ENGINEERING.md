# News Radar · Engineering Playbook

> 一份給「一個月後的你自己」看的工程守則。這個檔案只寫「**怎麼做**」，
> 不寫「為什麼這樣做」。`architecture.md` 負責後者。

---

## 1. 專案哲學（三條鐵律）

### 1.1 Deterministic first, LLM last
任何能用純程式解決的事 — 抓 RSS、清洗 HTML、關鍵字過濾、打分、去重 —
都**不得呼叫 LLM**。LLM 只做兩件事：`composer.py` 生成平台貼文、
`reflector.py` 做週迭代。其他層寫成 deterministic 模組，才能重跑、重測、重送。

### 1.2 一個訊號一個檔
- `feed` 層壞了 → `tools/diagnose_feeds.py`
- `cleaner` 層壞了 → `tools/diagnose_harvest.py` + `tools/replay_item.py`
- `composer` 層壞了 → `scratch/compose_replay.py`（TODO）
- 跨層的疑難雜症 → 在 issue / worklog 留下 **replay 指令**，別只留結論。

### 1.3 寫 DB 必須可重跑
每個 pipeline 階段（harvest / score / compose / publish）都要能：
- 幂等：同一 item 跑兩次不會重複寫
- 單步重跑：只重跑某一個 item，不用全量重做
- dry-run：`--dry-run` 不寫 DB、不呼叫外部 API

---

## 2. 目錄結構（定稿）

```
news_radar/
├── config/                 # YAML + soul 檔，管線的儀表板
│   ├── config.yaml
│   ├── news_radar_soul.md  # 速報靈魂（第 4 個 soul）
│   └── platforms/          # 每個平台的寫手指南（fb.md / ig.md / threads.md）
├── src/                    # 核心管線模組
│   ├── fetcher.py          # Module 1  RSS → HTML
│   ├── cleaner.py          # Module 2  HTML → markdown + 過濾
│   ├── scorer.py           # Module 3  信心分
│   ├── composer.py         # Module 4  LLM 生成貼文
│   ├── publisher.py        # Module 5  Meta Graph API 發文
│   ├── reflector.py        # Module 6  週迭代
│   ├── schema.py           # Pydantic schemas
│   └── db.py               # SQLite CRUD
├── data/                   # 分層存放資料
│   ├── 01_harvest/         # SQLite DB、診斷報告、feeds_health.md
│   ├── 02_score/           # （scorer 輸出）
│   ├── 03_compose/         # （composer 草稿 JSON）
│   ├── 04_publish/         # （已發文紀錄）
│   └── 05_feedback/        # （互動數據、反省紀錄）
├── tools/                  # ✅ 新增：診斷腳本（零 token）
│   ├── diagnose_harvest.py
│   ├── diagnose_feeds.py
│   ├── replay_item.py
│   └── README.md
├── tests/                  # ✅ 新增：pytest 測試
│   ├── conftest.py
│   ├── fixtures/
│   ├── unit/
│   ├── integration/
│   └── README.md
├── docs/                   # 人類讀的文件
│   ├── architecture.md     # 系統架構與決策
│   ├── ENGINEERING.md      # 本檔：日常工程守則
│   ├── DEBUGGING.md        # 「壞掉了怎麼辦」分層排查手冊
│   ├── PIPELINE.md         # 每個階段的輸入 / 輸出 / 失敗模式
│   ├── META_API_SETUP.md
│   └── NEXT_STEPS.md
├── scripts/                # 一次性腳本（DB migration、資料搬遷 ...）
├── scratch/                # 拋棄式探針腳本（放心砍）
├── logs/                   # execution_log.jsonl、publisher_errors.log
├── state/                  # workflow_state.json
├── assets/                 # 靜態資產（字體、icon ...）
├── Makefile                # ✅ 新增：標準化指令集
├── pytest.ini              # ✅ 新增
├── requirements.txt
├── run_harvest.py
├── run_pipeline.py
├── run_reflect.py
├── run_diagnose.command    # ✅ 新增：雙擊診斷
└── AGENT_WORKLOG.md
```

---

## 3. 開發循環（改 bug 的標準動作）

**Red → Green → Diagnose → Commit**。

```
1. 發現 bug
   → 先用 tools/replay_item.py <id> 或 diagnose_harvest.py 定位哪一層
2. 寫一個會重現 bug 的 pytest（放 tests/unit/test_xxx.py）
   → pytest 跑 → 紅
3. 改 src/xxx.py 的實作
   → pytest 跑 → 綠
4. 跑 tools/diagnose_harvest.py --print 看 production DB 上有多少類似 item 受影響
   → 若影響 > 5 筆，寫一個 scripts/replay_affected_items.py 掃過去重跑
5. 更新 AGENT_WORKLOG.md 的當日 Phase，寫清楚：症狀 / 定位 / fix / 影響範圍
6. Commit 訊息格式：「<layer>: <短說明>（e.g. cleaner: YouTube 不再短路 trafilatura）」
```

---

## 4. 新增一個 Feed 的 SOP

1. 在 `config/config.yaml` 新增一筆 `feeds:` 項目
2. 跑 `python tools/diagnose_feeds.py --feed <name>` 確認：
   - feed URL 回 200
   - 前 3 篇文章抓取成功率 > 0
3. 若 verdict ≠ `HEALTHY`，不要留下。要嘛換 URL（找官方 RSS），要嘛放棄。
4. 新 feed 第一次跑 `run_harvest.py` 後，跑 `diagnose_harvest.py` 看這個 feed 的
   平均字數、通過率。若通過率 < 30%，要調整關鍵字或字數門檻。

---

## 5. 新增一個 Pipeline 階段的 SOP

（例：要插入 `dedup_entity.py` 在 cleaner 與 scorer 之間）

1. 在 `docs/PIPELINE.md` 新增該階段的**輸入 schema / 輸出 schema / 失敗模式**
2. 在 `src/schema.py` 新增 Pydantic model（如 `EntityCluster`）
3. 新增 `src/dedup_entity.py`，強制接受 `List[NewsItem]` 回傳 `List[NewsItem]`
4. 補 `tests/unit/test_dedup_entity.py` 與 `tests/integration/test_dedup_db.py`
5. 新增 `tools/diagnose_dedup.py` 至少包含「今日有多少 cluster / 合併了哪些 item」
6. 在 `run_pipeline.py` 插入新階段，**保留 feature flag**（可從 config 關掉）
7. 在 `Makefile` 加 `make diag-dedup` 指令

---

## 6. 依賴升級的紅線

- `pydantic` 升級 → 全部 schema 都要跑 pytest 再 push
- `trafilatura` 升級 → 要跑 `diagnose_harvest.py` 比對前後字數直方圖
- `httpx` 升級 → `diagnose_feeds.py` 的成功率要維持在升級前 ±5% 內

---

## 7. Secret / Token 管理

- Meta Graph API token、Anthropic API key 只能放 `.env`（已在 .gitignore）
- **絕對不要 print token**，連 debug 都不行
- `Makefile` 的 `make publish` 會先讀 `.env`，若缺少任一 key 直接 abort

---

## 8. Logging 規範

| 檔案 | 用途 | 輪替策略 |
|---|---|---|
| `logs/execution_log.jsonl` | 每次 harvest 的 HarvestReport | append-only，每月歸檔 |
| `logs/publisher_errors.log` | Meta API 錯誤明細 | 超過 10 MB 手動歸檔 |
| `logs/reflector.jsonl` | 每週反省紀錄 | 永久保留（訓練語料） |

> **禁止新增 log 檔**，除非在本表登記。散落 log 是除錯的最大敵人。

---

## 9. 什麼時候重構

1. 同一段邏輯出現第 3 次 → 抽成函式
2. 一個檔案 > 400 行 → 拆模組
3. 一個函式 > 60 行 → 拆掉內部迴圈為助手函式
4. 一個 config key 三個月沒改過 → 考慮寫死或移到預設

**不要**為了美觀重構。每一次重構都要有「解決什麼痛」的 issue。
