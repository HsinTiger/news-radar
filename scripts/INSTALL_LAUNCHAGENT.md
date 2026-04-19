# 安裝 News Radar 週度 Snapshot LaunchAgent

一次安裝，之後每週日早上 10:30 AM 自動把雲端 DB 拉回本機 `~/news_radar_snapshots/`。

## 一鍵安裝

```bash
# 到 repo 根目錄
cd ~/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/文件/antigravity_workspace/substack/科技商業國際新聞自動化流程研究/news_radar

# 1) 讓 shell script 可執行
chmod +x scripts/weekly_snapshot.sh

# 2) 產生 plist（把 PATH_TO_REPO 替換為實際絕對路徑）
REPO_DIR="$(pwd)"
sed "s|PATH_TO_REPO|$REPO_DIR|g" scripts/com.hsin.news-radar.snapshot.plist \
  > ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist

# 3) 載入 agent
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist

# 4) 立刻跑一次確認能動
launchctl start com.hsin.news-radar.snapshot
sleep 3
ls -la ~/news_radar_snapshots/
tail -30 ~/news_radar_snapshots/_logs/*.log | tail -40
```

## 驗證已排程

```bash
launchctl list | grep news-radar
# 預期看到：
# -   0   com.hsin.news-radar.snapshot
# （中間那欄 0 = 上次 exit code；第一次還沒跑時是 "-"）
```

## 手動觸發（任何時候）

```bash
launchctl start com.hsin.news-radar.snapshot
```

或直接跑 shell script：

```bash
bash scripts/weekly_snapshot.sh
```

## 看 log

```bash
# 最新一次完整 log
ls -lt ~/news_radar_snapshots/_logs/*.log | head -1 | awk '{print $NF}' | xargs cat

# launchd 自己的 stderr（排程本身出事時看這個）
cat /tmp/news-radar-snapshot.err.log
```

## 停用 / 移除

```bash
# 暫停（保留檔案）
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist

# 完全移除
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist
rm ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist
```

## 設計說明

- **為什麼 launchd 不用 Cowork 排程**：Cowork 排程跑在 sandbox，寫 `$HOME` 只在 sandbox 內持久，你 Mac 看不到。launchd 直接在你 Mac user context 執行，`~/news_radar_snapshots/` 就是真實的 `/Users/hsin/news_radar_snapshots/`。
- **為什麼 snapshot 不放 OneDrive 裡**：DB 是二進制，每週變動大，放 OneDrive 會觸發頻繁同步浪費頻寬。放 `$HOME` 直接在 Mac 本地磁碟。
- **為什麼週日 10:30 不是 10:00**：避開整點大家都在執行的工作（cowork 排程、系統備份等），散開負載。
- **Mac 睡眠時段怎麼辦**：`StartCalendarInterval` 的行為是「錯過就補」——Mac 醒來後 launchd 會立刻補跑。不會漏掉。
- **這份 plist 要不要 commit 進 repo**：應該 commit（當成文件記錄）。實際載入的是 `~/Library/LaunchAgents/` 底下的 copy，不是 repo 裡這份，所以兩邊可以分別演進。
