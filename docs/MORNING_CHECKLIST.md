# Morning Checklist · 2026-04-20（夜班交接）

早安！你睡覺期間我完成了 Phase 8.18 + 8.19 的所有程式碼修改、測試驗證、以及本地 commit。
**唯一沒完成的是 `git push`**——沙箱環境的 proxy 擋 GitHub，你自己推一下就完成部署。

---

## 🎯 一句話總結

LLM 寫稿改成「Gemini 主 → Claude CLI 備援 → 兩者都失敗就 skip」，徹底移除 emergency template 硬編垃圾回退；同時補上完整測試（28/28 綠）。

---

## 📦 本次 commit 清單（3 個，在 `main` branch 上）

```
cb5bccf test: coverage for Phase 8.18/8.19 + architect addendum
f631dae feat: Phase 8.19 Claude CLI fallback brain + remove emergency template
622fbbc feat: Phase 8.18 hybrid compose/publish architecture
```

確認方式：
```bash
cd ~/Library/CloudStorage/OneDrive-*/*/*/科技商業國際新聞自動化流程研究/news_radar
git log --oneline -5
```
應該會看到上面三筆在最頂端。

---

## ✅ 已驗證的項目（別再 run 一次浪費時間）

| 項目 | 驗證方式 | 結果 |
|------|----------|------|
| `src/llm_brain.py` 決策樹 | `validate_llm_brain_sandbox.py`（12 cases） | 12 ✅ |
| publish queue 狀態機 | `validate_queue_flow_sandbox.py`（7 cases） | 7 ✅ |
| cadence 規則 + dry-run | `validate_publish_queue_sandbox.py`（9 cases） | 9 ✅ |
| 所有修改檔案語法 | `python -m py_compile` | 9/9 ✅ |

**沒辦法在沙箱測的**（你醒來後第一件事要測）：
- `claude -p` subprocess 真的能跑（沙箱無 claude CLI）
- Meta Graph API 真的收稿（沙箱無 live token）
- launchd 真的每小時觸發（sandbox 不是 macOS）

---

## 🔴 你醒來要做的 5 件事（照順序）

### 1. Push 到 GitHub（1 分鐘）

```bash
cd ~/Library/CloudStorage/OneDrive-*/*/*/科技商業國際新聞自動化流程研究/news_radar
# 如果有 .git/index.lock 殘留，先清掉（沙箱有時會留）
rm -f .git/index.lock .git/HEAD.lock
# 推上去
git push origin main
```

**驗證**：去 https://github.com/HsinTiger/news-radar/commits/main 確認三個新 commit 都在。

### 2. 安裝 Mac launchd compose agent（5 分鐘）

照 `scripts/INSTALL_COMPOSE_LAUNCHAGENT.md` 裡的「一鍵安裝」三步做：

```bash
# 到 repo 根
cd ~/Library/CloudStorage/OneDrive-*/*/*/科技商業國際新聞自動化流程研究/news_radar

# (a) 放 script 到 ~/bin/
mkdir -p ~/bin
cp scripts/compose_hourly.sh ~/bin/news_radar_compose.sh
chmod +x ~/bin/news_radar_compose.sh

# (b) 展開 plist 放到 LaunchAgents/
sed "s|HOME_DIR|$HOME|g" scripts/com.hsin.news-radar.compose.plist \
  > ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist

# (c) 首次手動跑（會 clone 一份到 ~/news_radar/，後續 compose 都在這裡跑）
bash ~/bin/news_radar_compose.sh
```

**期望看到**：
- `📥 本機尚無 ~/news_radar → 首次 clone...`
- `✅ Clone 完成`
- python 報錯說「.env 不存在 / GEMINI_API_KEY missing」——**正常，下一步補**

### 3. 放 .env 到 `~/news_radar/`（1 分鐘）

```bash
cp ~/Library/CloudStorage/OneDrive-*/*/*/科技商業國際新聞自動化流程研究/news_radar/.env \
   ~/news_radar/.env
```

**重要**：日後如果你輪換 token / 換 Gemini key，**兩邊都要更新**（OneDrive 跟 `~/news_radar/` 不自動同步）。

### 4. 再跑一次 compose（驗證整條鏈）

```bash
bash ~/bin/news_radar_compose.sh
```

**期望看到**：
- 有新聞 harvest → score → compose（走 Gemini）→ 入 queue
- 結尾 `✅ Queue 中現有 N 筆 queued`

**若 Gemini 429**（你有 Pro 應該不容易遇到，但以防萬一）：
- 會看到 `⚠️  Gemini 失敗: ... → 嘗試 Claude CLI`
- 接著 `🧠 Claude CLI 回應 (provider=claude_cli, cost=$0.00)`
- **確認 Claude CLI 真的能跑**：`which claude` 應該回 `/opt/homebrew/bin/claude` 或你裝的路徑
- 如果 Claude CLI 也掛：`⚠️  寫作 LLM 雙路徑皆失敗 → skip 本篇（不入 queue）` → 這是正確行為，不是 bug

### 5. 載入 launchd 排程

```bash
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
launchctl list | grep news-radar-compose
# 應看到：-   0   com.hsin.news-radar.compose
```

之後每 3600s 會自動跑一次（閉蓋充電時 OK，深睡時跳過直到醒來）。

---

## 🧪 接下來一整天觀察什麼

| 時段 | 檢查點 |
|------|--------|
| **T+1h** | 第一次 hourly compose 完成。看 `~/news_radar_snapshots/_compose_logs/*.log` 最新檔。期望：1 筆 queued |
| **T+1h15m** | GitHub Actions cron 跑 publish_queue。看 https://github.com/HsinTiger/news-radar/actions 最新 run 是綠的、且有 fb post ID 出現在 log |
| **T+2h** | Threads/FB page 看到新貼文（取決於 publish queue 是不是挑到你滿意的題目） |
| **T+4h** | 第 2 篇發文（cadence 2h 下限） |
| **T+12h** | 累積 5-6 篇發文、queue 維持 1-2 筆 |

**紅旗訊號**（看到就停）：
- GitHub Actions 連跑 2 次 publish 都 red（看 Actions log → Meta API error）
- Threads 發了一篇內容是「**News Radar 緊急範本 · 請編輯後手動發布**」→ 這表示舊的 emergency template 餘孽還在某個 codepath（理論上已刪）→ 立刻 `launchctl unload` 停排程、貼 log 給我
- Mac `compose_log` 連續 5 次都 `skipped_no_llm` → Gemini 可能真的 ban 了，且 `claude` CLI 沒裝好 → 檢查 `which claude`

---

## 🐛 Debug 指南（依現象）

| 現象 | 先看哪裡 |
|------|----------|
| launchd 沒觸發 | `cat /tmp/news-radar-compose.err.log` |
| compose 跑起來但 queue 沒加 | 最新的 `~/news_radar_snapshots/_compose_logs/*.log`，搜 `auto_approved` / `skipped_no_llm` |
| cloud 端 publish fail | GitHub Actions → 最新 failing run → 展開 `Run publish queue` step |
| Meta API 401 | token 過期，去 Meta Business → 重產 long-lived token → 更新 GitHub Secret `META_ACCESS_TOKEN` |
| Claude CLI 找不到 | `which claude` 應在 PATH。若裝在 `~/.local/bin/`，在 `~/bin/news_radar_compose.sh` 最前面加 `export PATH="$HOME/.local/bin:$PATH"` |

**想看完整架構思路**：`docs/architect_plan_disscussion.md` 最底下有 Phase 8.19 addendum（10 個決策點）。

---

## 💰 Token 使用估算

這場夜班我大概用了：
- Phase 8.19 程式碼（llm_brain + 3 個 refactor + 1 個 pipeline 修改）：~80k input / ~25k output
- 測試（單元 + 整合 × 2 + sandbox validator × 3）：~60k input / ~30k output
- Debug（sqlite schema 對不上、FK 約束、index.lock）：~40k input / ~15k output
- 文件（架構 addendum + overnight worklog + 這份 checklist）：~25k input / ~15k output

**粗估**：~205k input / ~85k output（未含 cached）。以 Opus 4.7 計價約 $4.5—很值，換到一個 production-ready fallback 系統。

---

## ❓ 如果我做錯了什麼

- **Commit 裡有不想要的 file**：`git reset --soft HEAD~3` 退到夜班前，再手動挑 file。三個 commit 各有清楚的範圍，個別 revert 也很安全。
- **LLM brain contract 你想改**：`src/llm_brain.py` 只有 `call_for_json` 一個對外函式，改 signature 時 scorer.py / composer.py 要同步。
- **你想先只跑 Gemini、不要 Claude CLI fallback**：`src/llm_brain.py` 裡把 `_try_claude_cli` 的調用處加 `if False:`（或乾脆 `settrace` 讓 `shutil.which("claude")` 回 None）。不推薦——這違背 Phase 8.19 的 whole point。
- **你發現 emergency template 還在某處**：`grep -r "緊急範本" .` 應該全 miss（run_pipeline.py 那段已刪）。若還有殘留 → 我漏掉了 → 回報給我。

---

## 📁 夜班動到的檔案總清單

**新增**：
- `src/llm_brain.py`（Gemini→Claude CLI→None 決策樹）
- `run_publish_queue.py`（Phase 8.18 publish loop，原本有，這次 commit）
- `scripts/compose_hourly.sh`, `scripts/com.hsin.news-radar.compose.plist`, `scripts/INSTALL_COMPOSE_LAUNCHAGENT.md`
- `tests/unit/test_llm_brain.py`（18 cases）
- `tests/integration/test_publish_queue_flow.py`（7 cases）
- `docs/overnight_worklog.md`（夜班規劃筆記，可留作歷史紀錄）
- `docs/MORNING_CHECKLIST.md`（你正在讀的這份）

**修改**：
- `data/01_harvest/schema.sql`（drafts 加 4 個 queue 欄位）
- `src/db.py` / `src/schema.py`（queue helper fns）
- `src/scorer.py` / `src/composer.py`（refactor 用 llm_brain）
- `run_pipeline.py`（刪 emergency template、回傳值新增 skipped_no_llm）
- `config/config.yaml`（cadence 規則）
- `.github/workflows/pipeline.yml`（改跑 run_publish_queue.py）
- `docs/architect_plan_disscussion.md`（Phase 8.19 addendum，10 個決策點）
- `scripts/weekly_snapshot.sh`（permission mode 改 755）

---

祝早安 ☀️ 睡得好就是最大的產能。
