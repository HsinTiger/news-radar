# News Radar · GitHub Actions Deploy Runbook

> 2026-04-19 · Phase 8.17 · 配合 `docs/CLOUD_DEPLOYMENT.md` 的決策落地。
>
> 目標：把 `run_pipeline.py` 從本機 laptop 搬到 GitHub Actions 雲端 cron，做到 24/7 運轉、免費、無 VM 可以壞。

---

## 0. 一眼看懂架構

```
GitHub repo（public）
├── main branch        ← 程式碼、docs、config（所有 commit 都在這裡做 code review）
└── state branch       ← orphan branch，每次 pipeline run 會被 force-push
                          只存 data/01_harvest/news_radar.db、logs/、archive/、state/last_harvest.txt

GitHub Actions
├── .github/workflows/pipeline.yml    ← 每 30 min cron，跑 run_pipeline.py
└── .github/workflows/reflect.yml     ← 每天 UTC 16:00（台北 00:00），跑 run_reflect.py

Secrets（加密存 GitHub，workflow 以環境變數拿到）
└── 12 個（見 docs/SECRETS_CHECKLIST.md）
```

關鍵設計：
- **main 永遠乾淨**：只有程式碼，人類可安心 review。
- **state 是個 orphan branch**：每次跑完 pipeline 都 `git push --force` 蓋掉，所以這個 branch 永遠只有 1 個 commit、約 2–5 MB，不會膨脹。
- **logs 雙備份**：既在 state branch（最新一份），也用 `actions/upload-artifact` 保留 14–30 天，出事時有兩條回溯路徑。
- **Secrets 完全不進 repo**：`.env` 在 `.gitignore` 裡，workflow 用 `${{ secrets.XXX }}` 注入環境變數。

---

## 1. 首次部署 · 操作步驟

以下指令都在本機 news_radar/ 工作目錄下、使用 venv Python 跑。

### 1.1 確認 repo 狀態乾淨

```bash
cd ~/path/to/news_radar   # 換成你的實際路徑
ls -la .git 2>/dev/null || echo "還沒 git init"
```

如果已經是 git working dir 請先確認沒有 `.env` 被 track：

```bash
git ls-files | grep -E '\.env$' && echo "⚠️ .env 已被 track，必須 git rm --cached .env"
```

### 1.2 初始化 git（如果還沒）

```bash
cd ~/path/to/news_radar
git init -b main
git add .gitignore
git commit -m "chore: initial .gitignore"
git add .
git commit -m "feat: news radar initial public release (phase 8.17)"
```

驗證 `.env` 確實被忽略：

```bash
git ls-files | grep -c '^\.env$'
# 應該輸出 0
```

### 1.3 在 GitHub 建 repo（使用 gh CLI）

```bash
# 先登入（第一次需要）
gh auth login

# 建一個 public repo，名字自取（這裡用 news-radar）
gh repo create news-radar --public --source=. --remote=origin --push
```

或手動：到 <https://github.com/new> 建 public repo → 回本機設 remote：

```bash
git remote add origin git@github.com:<your-username>/news-radar.git
git push -u origin main
```

### 1.4 上架 Secrets（這是整個部署的關鍵 5 分鐘）

有兩種方式：

**方式 A（推薦）：用 gh CLI 從 .env 一次灌好**

```bash
# 先進 repo 目錄（repo 要先用 gh repo create 建好）
cd ~/path/to/news_radar

# 從現成的 .env 讀，逐行 gh secret set
while IFS='=' read -r key value; do
  [ -z "$key" ] && continue
  [[ "$key" == \#* ]] && continue
  [ -z "$value" ] && continue
  echo "Setting secret: $key"
  echo -n "$value" | gh secret set "$key"
done < .env
```

**方式 B（手動）：GitHub UI**

到 `https://github.com/<user>/news-radar/settings/secrets/actions` → `New repository secret` → 一個一個貼。

不論哪種，完整清單見 `docs/SECRETS_CHECKLIST.md`。

### 1.5 驗證 workflow 被 GitHub 識別

```bash
gh workflow list
# 應該看到：
#   News Radar · Pipeline        active   pipeline.yml
#   News Radar · Reflect (Daily) active   reflect.yml
```

### 1.6 手動觸發第一次 pipeline（重要）

**不要直接等 cron。** 手動跑一次確認環境正確：

```bash
# 先用 harvest-now 模式跑，確保 DB 從零開始初始化
gh workflow run pipeline.yml -f mode=harvest-now

# 盯著跑
gh run watch
```

首次 run 預期結果：
1. `Checkout state branch` step 會 fail（還不存在），但標記成 `continue-on-error: true` → 繼續
2. `Restore runtime state` 印出 `state branch 空 / 首次執行，從零開始`
3. `Run pipeline` 會 init 新 DB、做一次完整 harvest + compose + publish
4. `Persist state` 會 orphan-init + force-push 建立 state branch
5. 下次 run 時 state branch 就能被 checkout 到

驗證 state branch 確實被建起來：

```bash
gh api repos/<user>/news-radar/branches/state --jq .name
# 應該輸出：state
```

### 1.7 盯 3 天

前 72 小時手動到 Actions UI 看每個 cron 是不是成功：
`https://github.com/<user>/news-radar/actions/workflows/pipeline.yml`

指標：
- 每 30 min 有一個新 run
- 成功率 > 95%（偶有 LLM API 暫時 503 可接受）
- state branch 的 `LAST_RUN.txt` 持續更新
- 每 60–120 min 有實際 publish 發生（看 Facebook Page / Threads 後台）

---

## 2. 日常操作

### 2.1 看最新 run 狀態

```bash
gh run list -L 5 --workflow=pipeline.yml
```

### 2.2 手動觸發「立即發文」

```bash
gh workflow run pipeline.yml -f mode=harvest-now
```

### 2.3 立即推一篇（放寬門檻）

```bash
gh workflow run pipeline.yml -f mode=publish-now
```

`publish-now` 會強制 harvest + 把發文門檻降到 `MIN_SCORE_THRESHOLD`（約 0.6），用於「我現在就要一篇上架」的場景。

### 2.4 手動跑 reflect

```bash
gh workflow run reflect.yml -f dry_run=true  # 只看 prompt
gh workflow run reflect.yml                  # 真的更新 soul.md
```

### 2.5 抓最新 DB 回本機做 debug

```bash
# 方法 1：從 state branch 拉
mkdir -p /tmp/nr_state
cd /tmp/nr_state
git clone --branch state --single-branch --depth 1 \
  git@github.com:<user>/news-radar.git .
ls -lh data/01_harvest/news_radar.db

# 方法 2：從最近的 run artifact 拉
gh run list -L 1 --workflow=pipeline.yml --json databaseId --jq '.[0].databaseId' \
  | xargs -I{} gh run download {} -n pipeline-logs-{} -D /tmp/nr_latest
```

### 2.6 臨時停掉 cron（例如 Meta API 在 maintenance）

```bash
gh workflow disable pipeline.yml
gh workflow disable reflect.yml

# 恢復
gh workflow enable pipeline.yml
gh workflow enable reflect.yml
```

---

## 3. 觀察 / 除錯

### 3.1 常見錯誤

| 症狀 | 可能原因 | 怎麼修 |
|---|---|---|
| `KeyError: 'GEMINI_API_KEY'` | Secret 沒設好 | `gh secret list` 確認；用 1.4 的迴圈重灌 |
| Publisher 一直 401 | FB / IG / Threads token 過期 | 在本機跑 `python -m src.token_utils` 換發長效 token → 更新 GitHub Secret |
| `state branch 空 / 首次執行` 永遠出現 | Persist 那步 push 失敗（權限？） | 確認 Settings → Actions → Workflow permissions 是 `Read and write permissions` |
| cron 沒觸發 | GitHub 對太久沒 push 的 repo 會暫停 cron | 每月至少 push 一次 commit（例如更新 docs） |
| DB 越來越肥 | 正常，但 > 50 MB 要注意 | 看 9.4 DB 瘦身章節 |

### 3.2 單次 run 失敗的 SOP

1. 到 Actions UI 點進該 run
2. 展開失敗的 step，看 stderr
3. 常見分類：
   - 網路暫時 fail：等下一輪
   - Secret 錯：修 secret，手動 retry
   - Code bug：在本機 reproduce → 修 code → push main → 等下一輪 cron
4. 失敗不影響 state branch（`Persist state` step 有 `if: always()`，會盡量推一份）

---

## 4. 成本 / 額度

| 項目 | 用量估計 | 上限 |
|---|---|---|
| Actions 分鐘數 | 48 run/天 × ~2 min/run ≈ 100 min/天 ≈ 3000 min/月 | **公開 repo 無限** |
| Secrets 數量 | 12 個 | 1000 個 |
| Artifact 儲存 | 14 天保留，每 run ~5 MB → 最多 48×14×5 ≈ 3.4 GB | 500 MB 免費，超過按 $/GB 算 |

⚠️ **Artifact 可能會讓你踩到付費門檻**。  
如果怕超額，把 pipeline.yml 的 `retention-days: 14` 改 `1`（保留一天），或在 `Upload logs` 那一步改 `if: failure()` 只在失敗時上傳。

---

## 5. 升級 / 改動流程

一般 code 改動：

```bash
# 本機
git add -A
git commit -m "feat: ..."
git push origin main
```

Push 之後下一個 cron tick 就會用新程式碼。**不需要重啟 workflow**，每次 run 都是獨立 `git checkout`。

Secrets 改動：直接 `gh secret set XXX`，下一次 run 就會拿到新值。

---

## 6. 緊急下架 / 回本機

如果雲端跑得有問題想回本機：

```bash
# 1. 停掉 cron
gh workflow disable pipeline.yml
gh workflow disable reflect.yml

# 2. 把雲端最新 DB 拉回本機
cd ~/path/to/news_radar
mkdir -p /tmp/nr_backup
git clone --branch state --single-branch --depth 1 \
  git@github.com:<user>/news-radar.git /tmp/nr_backup
cp /tmp/nr_backup/data/01_harvest/news_radar.db \
   data/01_harvest/news_radar.db

# 3. 本機繼續跑
~/.virtualenvs/news_radar/bin/python run_pipeline.py --loop
```

雲端 → 本機的資料遷移，只要複製 `data/01_harvest/news_radar.db` 一個檔即可。

---

## 7. Deprecated / 不做的事

- ❌ **不**用 private repo：雖然 2000 min/月 對本 workload 夠（60 min cron = 1440 min/月），但為了保留 30 min 節奏 + 未來擴充彈性，選擇 public。
- ❌ **不**自建 Oracle / Fly.io：Workload 太輕，不需要 always-on VM，多開一個反而多一個會壞的東西。
- ❌ **不**把 DB 往 managed 服務（Turso / Supabase）搬：會破壞目前 SQLite 簡單設計的 minimalism。
- ❌ **不**用 GitHub Release / Packages 存 DB：state branch + artifact 已經夠。

這些選擇的 trade-off 都記在 `docs/CLOUD_DEPLOYMENT.md`，半年後回看也能理解當初為什麼這樣選。

---

## 8. Checklist

部署前 (1.1–1.7)：

- [ ] 本機 git init 完成，`.env` 不在 tracked files
- [ ] GitHub public repo 建好
- [ ] 12 個 Secrets 全部上架（對照 `docs/SECRETS_CHECKLIST.md`）
- [ ] `gh workflow list` 看到兩個 workflow
- [ ] `gh workflow run pipeline.yml -f mode=harvest-now` 首次觸發成功
- [ ] state branch 出現在 `gh api repos/.../branches`
- [ ] 連續看 3 個 cron tick 都成功
- [ ] 本機 laptop `run_pipeline.py --loop` 可以關了

部署後常態：

- [ ] 每週看一次 Actions UI（花 2 分鐘）
- [ ] 每月手動 commit 一次（避免 GitHub 暫停 cron）
- [ ] token 到期前換發 + 更新 Secret
