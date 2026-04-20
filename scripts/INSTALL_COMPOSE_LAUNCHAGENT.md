# 安裝 News Radar 每小時 Compose LaunchAgent（Phase 8.18）

每小時由 Mac 本機執行 compose（用 Gemini API 寫稿），把達門檻的 draft 入 publish queue。Cloud 端 GitHub Actions 每小時整點再從 queue 挑最新一筆發文。

## ⚠️ 背景：為什麼 Mac 要有本機 repo 鏡像

macOS **TCC (Transparency, Consent & Control)** 會阻擋 `launchd` 存取 `CloudStorage/` 底下的檔案（OneDrive / iCloud / Google Drive 都一樣）。所以：

- **開發（人類）**：在 OneDrive 的 repo 編輯 → `git push`
- **執行（launchd）**：從 `~/news_radar/` 本機鏡像跑 → 不碰 CloudStorage
- **同步**：compose 腳本每次執行前會 `git fetch origin main` + `reset --hard`，自動抓最新程式碼

## 前置需求

1. **Homebrew 已裝** + `brew install git python@3.11`
2. **GitHub credential 已設定**：`git push` 不會彈帳密視窗。常見做法：
   ```bash
   # 如果用 HTTPS，一次手動 push 觸發 osxkeychain 存 credential
   git push origin main
   # 或用 PAT: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
   ```
3. **OneDrive 的 repo 有最新程式碼**：確保 `main` branch 已 push 到 GitHub（本機鏡像是從 GitHub 拉的）

## 一鍵安裝

```bash
# 到 OneDrive 的 repo 根目錄
cd ~/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/文件/antigravity_workspace/substack/科技商業國際新聞自動化流程研究/news_radar

# 1) 把 script copy 到 ~/bin/（避開 TCC）
mkdir -p ~/bin
cp scripts/compose_hourly.sh ~/bin/news_radar_compose.sh
chmod +x ~/bin/news_radar_compose.sh

# 2) 產生 plist（HOME_DIR → $HOME）
sed "s|HOME_DIR|$HOME|g" scripts/com.hsin.news-radar.compose.plist \
  > ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist

# 3) 首次手動跑，驗證 script 本身能動 + 首次 clone 本機 repo
#    這一步會在 ~/news_radar/ 建立 git clone，之後每次執行會 git fetch 更新
bash ~/bin/news_radar_compose.sh
# 預期看到：
#   - 📥 本機尚無 ~/news_radar → 首次 clone...
#   - ✅ Clone 完成 → fetch state branch → python run_pipeline.py --compose-only
#   - 📤 Push DB 回 state branch...

# 4) 載入 agent
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
```

## 驗證已排程

```bash
launchctl list | grep news-radar-compose
# 預期看到：
# -   0   com.hsin.news-radar.compose
# （中間那欄：0 = 上次 exit code 成功）
```

## 設定 .env（第一次必做）

`~/news_radar/` 是 launchd 跑的位置，它需要自己的 `.env`（OneDrive 的 `.env` 不會被 launchd 讀）。

```bash
# 從 OneDrive 的 repo 複製 .env 過去（.env 不進 git，所以 clone 時不會自動帶）
cp ~/Library/CloudStorage/OneDrive-*/*/*/科技商業國際新聞自動化流程研究/news_radar/.env \
   ~/news_radar/.env
```

**注意**：之後如果在 OneDrive 更新 `.env`（例如輪換 token），記得也 copy 到 `~/news_radar/.env`。兩邊不自動同步。

## 手動觸發（任何時候）

```bash
launchctl start com.hsin.news-radar.compose
# 或
bash ~/bin/news_radar_compose.sh
```

## 看 log

```bash
# 最新一次完整 log
ls -lt ~/news_radar_snapshots/_compose_logs/*.log | head -1 | awk '{print $NF}' | xargs cat

# launchd 自己的 stderr（排程本身出事時看這個）
cat /tmp/news-radar-compose.err.log
```

## 更新 script / plist

**script 變動**（`scripts/compose_hourly.sh` 改過）：
```bash
cp scripts/compose_hourly.sh ~/bin/news_radar_compose.sh
chmod +x ~/bin/news_radar_compose.sh
```

**plist 變動**（頻率 / 路徑改過）：
```bash
sed "s|HOME_DIR|$HOME|g" scripts/com.hsin.news-radar.compose.plist \
  > ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
launchctl load   ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
```

## 停用 / 移除

```bash
# 暫停（保留檔案）
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist

# 完全移除（保留 ~/news_radar/ 鏡像）
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
rm ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
rm ~/bin/news_radar_compose.sh
# 可選：連鏡像一併刪
# rm -rf ~/news_radar/
```

## 設計說明

- **為什麼 compose 要 Mac 跑 / publish 要 Cloud 跑**：compose 呼叫 Gemini / Claude，API key 綁本機訂閱；publish 純 HTTP POST，Cloud friendly。見 `docs/architect_plan_disscussion.md` Phase 8.18 章節。
- **為什麼用 StartInterval 3600 而非 cron-style**：launchd 的 `StartCalendarInterval` 寫 `*/1` 不支援；`StartInterval` 是最乾淨的每小時解法。代價：起始時間隨 launchd load 的時刻而定（load 後每 3600s 觸發一次）。可接受——Cloud publisher 本身也會 honour 1h cadence。
- **buffer_target=2 的設計**：queue 保持 1-2 筆 queued，多了就跳過（避免 Mac 累積過多過期文稿）。Cloud freshness-first 會挑最新一筆，舊的自動標 stale。
- **Gemini 429 時怎麼辦**：Phase 8.18 不動 `composer.py` 的 LLM 呼叫路徑。實際遇到 429 → pipeline log 會印「寫作 AI 配額用盡，啟動『緊急範本發布』」→ **不應讓它走 compose-only 入 queue**（垃圾模板入 queue 會汙染發文）。若發生，立刻手動 `launchctl unload`、等 Gemini 額度恢復或 Phase 8.19 把 LLM 切到 claude CLI。
- **MacBook 閉蓋/電池模式**：launchd 的 `StartInterval` 在 Mac 深睡時會跳過觸發，醒來後下一個 3600s 才繼續（不會補跑）。使用者已接受這個 degradation（假日通勤 < 1 小時，影響可忽略）。
