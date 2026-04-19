# News Radar · 雲端部署評估（Phase 8.17 前置）

> 2026-04-19 · 目標：把 `run_pipeline.py --loop` 從本機 laptop 搬到「穩定 / 免費 / 盡量不用管」的雲端環境，讓它真正 24/7 跑。
>
> 這份不是一份 step-by-step deploy guide —— 它是**評估紀錄**，讓你半年後回看「我們當初為什麼選這個」。實際部署步驟在選定方案後才寫成另一份。

---

## 1. Workload 形狀速記

先釐清這個專案要跑什麼，才能選對工具：

| 維度 | 實際值 |
|---|---|
| 主程式 | `run_pipeline.py --loop`（長跑進程）或「每 30 分一次 one-shot cycle」兩種皆可 |
| CPU / 記憶體 | 輕（httpx + sqlite + bs4）；峰值在 LLM API 呼叫時的等待 |
| 網路 | 出向主：RSS 抓取、OpenAI / Anthropic / Gemini API、Meta Graph API |
| 儲存 | **SQLite 單檔**（`data/01_harvest/news_radar.db`，目前 < 5 MB）；`logs/*.jsonl`；`archive/YYYY/MM/DD/` |
| Cadence | 30 min heartbeat；發文節奏 60–120 min；實際發 API 呼叫頻率 ≤ 48/day |
| 秘鑰 | FB / IG / Threads access tokens；OpenAI / Anthropic / Gemini keys；共 < 10 個 |
| 狀態持久性 | **必要**——`drafts` / `publish_log` / `engagement_stats` 沒了就沒了，reflector 會失去學習訊號 |

**關鍵觀察**：workload 極輕，幾乎永遠在 idle 等下一個 cron tick。這讓「不是 always-on」的 serverless / cron 方案完全夠用，**不用花錢 / 心力去維護一個 always-on VM**。

---

## 2. 候選方案客觀比較

| | **GitHub Actions cron** | **Oracle Cloud Always Free** | **Fly.io** | **Render / Railway** |
|---|---|---|---|---|
| 是否真的永久免費 | ✅ 公開 repo 無限 / 私有 repo 2000 min/月 | ⚠️ 名目永久，但 ARM VM 被動回收 | ❌ 2024 底取消完全免費，現為 $5/月 credit | ❌ 只有 web/background 的 trial，超過就要錢 |
| 24/7 持續進程 | ❌（但改成每 30 min cron 即可） | ✅ systemd long-running | ✅ long-running | ✅（Render 免費 tier 會 sleep） |
| 狀態持久化（SQLite） | ✅ commit 回 repo 或 Artifacts | ✅ local disk | ✅ volume | ✅ volume |
| 秘鑰管理 | ✅ 原生 secrets | 要自架 Vault 或 env | ✅ `fly secrets` | ✅ |
| 觀察日誌 | ✅ Actions UI 每 run 單獨頁 | ssh + journalctl | `fly logs` | dashboard |
| 設置成本（次） | 極低（幾個 yaml 檔） | 中（建戶 + CC 驗證 + VM + systemd） | 中（Dockerfile + fly.toml） | 低 |
| 營運成本（持續） | 0 | 0，但要記得重開 ARM 被回收的實例 | $5/月額度，目前 workload 用不完 | 開始付費 |
| 冷啟動 / 睡眠 | N/A（事件型） | 無 | 可設置睡眠，免費額度不保證 | 免費 web 會 sleep 15 min |
| 被平台主動收回的風險 | 極低 | **中—高**（多位使用者回報 ARM 實例被刪） | 低 | 低 |

### 2.1 Oracle ARM Always Free 的實際風險

Oracle 對 **ARM Ampere 實例**是「永久免費但可回收」——如果他們區域 ARM 庫存吃緊，會直接關閉並刪除你的實例（有 email 通知，但資料就沒了）。另外 Oracle 的 billing 系統偶爾會把帳戶降級要求補 CC 驗證。這兩件事讓 Oracle 對「無人值守 24/7」的場景**穩定性評分不高**——某天發現 pipeline 停了、連過去 VM 沒了、要重建整套環境。

若要用 Oracle：備份策略必須強（例如每日 `sqlite3 .backup` 丟到 S3 / R2 / GitHub Releases），否則風險過高。

### 2.2 Fly.io 的狀態

2024 Q4 Fly.io 取消了真正意義上的免費 tier，改為「每月 $5 credit」。對這個 workload 實際用量 < $1/月，credit 用不完——所以**實質免費**，但**名義上不是**。如果你的標準是「絕對不想看到任何扣款介面」，這個不符。

### 2.3 Render / Railway / Replit

- Render: 免費 web service 閒置 15 分會 sleep → 對 heartbeat 架構是硬傷；背景 worker 要付費
- Railway: 免費 $5 credit trial，過後要付
- Replit: 「Always On」已改付費

這三個對本 workload **都不適合免費長跑**。

---

## 3. 建議方案 · GitHub Actions cron + 公開 repo

### 3.1 為什麼是它

你已經有 GitHub 帳號，這點最輕。再加上：

1. **真正免費永久**：公開 repo 的 Actions 分鐘數無限。即使未來 pipeline 變重，也不會碰到額度牆。
2. **workload 形狀剛好對**：本 pipeline 本來就是事件驅動（cadence controller 判「這 cycle 要不要發」），不需要 long-running process。把 `--loop` 拆成「每 30 min 觸發一次 one-shot」邏輯完全等價。
3. **秘鑰原生支援**：Actions Secrets 加密存在 GitHub，在 workflow 裡當環境變數用，不會洩露在 code / log。
4. **日誌零額外成本**：Actions UI 每個 run 獨立一頁、stderr/stdout 自動保留 90 天。想看「3 天前 16:00 這輪發生什麼」點兩下就好。
5. **沒有 VM 可以被收回**——最大的穩定性保證來自「沒有東西能壞」。
6. **DB 持久化有兩條可行路徑**：
   - 每輪 commit 回 repo 的 `state/` 目錄（DB < 5 MB 還沒到 git 上限；`.gitattributes` 標 binary 即可）
   - 或用 `actions/upload-artifact` + `actions/download-artifact`（90 天保留，對學習訊號夠用）

### 3.2 要付出的代價

| 代價 | 輕重 | 怎麼吸收 |
|---|---|---|
| **原始碼需要公開**（才能無限分鐘） | 中 | 秘鑰仍加密在 Secrets；soul.md / prompts / persona 會公開 —— 若這些被視為 IP 不想公開，改用私有 repo + 2000 min/月 額度，heartbeat 改為每 60 min = 1440 min/月，還在額度內 |
| 從 `--loop` 重構成 one-shot | 輕 | `run_pipeline.py` 本來就有 `run_one_cycle()` 這層；加一個 `--once` flag 直接呼叫一次 cycle 後退出 |
| DB 在 repo 裡 commit 回來的 commit 紀錄吵 | 輕 | 用專屬 branch（`state`）放 DB，`main` branch 保持乾淨只有 code |
| Actions cron 觸發精度 ±5 min | 輕 | 本專案 cadence 是 60/120 min 粒度，±5 min 影響可忽略 |

### 3.3 若 repo 不能公開：降級方案

改為**私有 repo + 60 min cron**：1440 min/月 在 2000 免費額度裡。若想拉回 30 min cron，可以考慮：
- 搬到 **Fly.io $5 credit**：本 workload 估計月用 < $1，credit 自動續，實質免費
- 或 **Oracle Always Free**：承擔 ARM 回收風險 + 強制備份機制

---

## 4. 秘鑰管理（使用者選擇：雲端 secret 服務）

選定 GitHub Actions 則直接用 **GitHub Actions Secrets**：

| 類別 | 建議的 Secret 名稱 | 來源 |
|---|---|---|
| Meta | `FB_PAGE_ACCESS_TOKEN`, `FB_PAGE_ID`, `IG_USER_ID`, `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN` | 目前 `.env` / Meta Developer Console |
| LLM | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | 各 provider console |
| GitHub（workflow commit DB 回來用） | `GIT_PUSH_TOKEN`（PAT with `contents:write`） | GitHub Settings → Developer settings |

Secret 設完後 `.env` 就只留本機開發用。Repo `.gitignore` 繼續擋 `.env`。

---

## 5. 需要微調的 pipeline code（預估工作量 S）

1. `run_pipeline.py` 加 `--once` flag：跑一次 `run_one_cycle()` 就 return，不進 heartbeat 迴圈
2. `src/db.py` 加 `backup_to_bytes()` helper（SQLite `.backup` API）供 workflow commit 用
3. `.github/workflows/pipeline.yml` 新檔：cron `*/30 * * * *` → checkout → setup-python → pip install → 下載 state branch DB → run pipeline --once → 上傳 DB 回 state branch
4. `.github/workflows/reflect.yml` 新檔：每天 UTC 16:00（台北 00:00）跑一次 `run_reflect.py`
5. Repo README 加「cloud runbook」段落，說明怎麼手動觸發 workflow（`workflow_dispatch`）做「publish now」等效操作

**不需要改動的**：harvester、cleaner、composer、scorer、publisher、reflector 一律維持不變。只有 pipeline 入口從「長跑迴圈」改成「單次 cycle」。

---

## 6. 決策後的下一步

選定方向後，依序做：

1. 建一個 private 或 public GitHub repo（本機已經是 git working dir 的話直接推就行）
2. 上架 Secrets
3. 推 workflow yaml，看第一個 cron 跑起來
4. 盯 3 天 Actions log：Cloud 端 cycle 是不是有順利 harvest / publish / 正常結束
5. 本機 laptop 可關、或繼續跑當 warm backup（兩邊都寫 DB 到不同位置即可）

---

## 7. 本輪評估的隱含 trade-off

- **公開 repo vs 私有 repo**：公開拿無限分鐘，代價是 prompt engineering / soul.md 也公開。本專案的 moat 不在「prompt 本身」（那些 public framework 都有），moat 在「持續迭代 + 自家 reflector 數據飛輪」。所以公開原始碼**不損失真正的 moat**，只是暴露當前心智模型——這點可接受。
- **把 DB commit 回 repo 有點「hacky」**：正統做法是 managed DB（Turso 免費 tier / Supabase SQL）。但 Turso 對 SQLite 的 fork 需要 migration / driver 置換，工作量大；本 workload 的 DB 行為（寫入頻率低、讀取點在本 process 內）完全不需要分散式 SQLite 能給的東西。commit 回 repo 的 hacky 做法在這個 scale 反而是最小設計的體現。
- **Actions cron 精度 ±5 min 不是缺陷**：我們本來就不需要秒級精度。發文節奏的變異對讀者來說比嚴格 30 min 的機器感**更自然**。

---

## 8. 決策待確認

在動手 deploy 前需要你確認一件事：

**原始碼要公開還是私有？**
- 公開 → GitHub Actions 無限分鐘免費、設置最簡
- 私有 → 2000 min/月 夠用前提下，cron 改為 60 min（不影響節奏）；或改走 Fly.io $5 credit 實質免費

其他決策已在 Phase 8.17 的 planning 階段定好，寫在本文件裡作為契約。
