# 安裝 News Radar 週度 Snapshot LaunchAgent

一次安裝，之後每週日早上 10:30 AM 自動把雲端 DB 拉回本機 `~/news_radar_snapshots/`。

## ⚠️ 重要：為什麼腳本要 copy 到 `~/bin/`

macOS **TCC (Transparency, Consent & Control)** 會阻擋 `launchd` daemon 存取 `CloudStorage/` 底下的檔案（OneDrive / iCloud Drive / Google Drive 都一樣），錯誤訊息是 `Operation not permitted`。

因此雖然 repo 裡有 `scripts/weekly_snapshot.sh`，但 LaunchAgent 實際執行的副本必須放在 `~/bin/`（真實磁碟，不走雲端）。INSTALL 腳本會幫你 copy。

## 一鍵安裝

```bash
# 到 repo 根目錄
cd ~/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/文件/antigravity_workspace/substack/科技商業國際新聞自動化流程研究/news_radar

# 1) 把腳本 copy 到 ~/bin/（避開 TCC）
mkdir -p ~/bin
cp scripts/weekly_snapshot.sh ~/bin/news_radar_weekly_snapshot.sh
chmod +x ~/bin/news_radar_weekly_snapshot.sh

# 2) 產生 plist（把 HOME_DIR 替換為 $HOME 實際絕對路徑）
sed "s|HOME_DIR|$HOME|g" scripts/com.hsin.news-radar.snapshot.plist \
  > ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist

# 3) 載入 agent（先 unload 以防舊版本殘留）
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist

# 4) 立刻跑一次確認能動
launchctl start com.hsin.news-radar.snapshot
sleep 5
ls -la ~/news_radar_snapshots/
tail -30 ~/news_radar_snapshots/_logs/*.log 2>/dev/null | tail -40
```

## 驗證已排程

```bash
launchctl list | grep news-radar
# 預期看到：
# -   0   com.hsin.news-radar.snapshot
# （中間那欄：0 = 上次 exit code 成功；126 = bash 拒絕執行 → 通常是 TCC 擋）
```

## 手動觸發（任何時候）

```bash
launchctl start com.hsin.news-radar.snapshot
```

或直接跑 shell script：

```bash
bash ~/bin/news_radar_weekly_snapshot.sh
```

## 看 log

```bash
# 最新一次完整 log
ls -lt ~/news_radar_snapshots/_logs/*.log | head -1 | awk '{print $NF}' | xargs cat

# launchd 自己的 stderr（排程本身出事時看這個）
cat /tmp/news-radar-snapshot.err.log
```

## 更新腳本

當 repo 裡的 `scripts/weekly_snapshot.sh` 有改動時，重新 copy：

```bash
cp scripts/weekly_snapshot.sh ~/bin/news_radar_weekly_snapshot.sh
chmod +x ~/bin/news_radar_weekly_snapshot.sh
```

plist 內容變動才需要重跑 `sed` + `launchctl unload/load`。

## 停用 / 移除

```bash
# 暫停（保留檔案）
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist

# 完全移除
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist
rm ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist
rm ~/bin/news_radar_weekly_snapshot.sh
```

## 設計說明

- **為什麼 launchd 不用 Cowork 排程**：Cowork 排程跑在 sandbox，寫 `$HOME` 只在 sandbox 內持久，你 Mac 看不到。launchd 直接在你 Mac user context 執行，`~/news_radar_snapshots/` 就是真實的 `/Users/hsin/news_radar_snapshots/`。
- **為什麼腳本要 copy 到 `~/bin/` 而不是從 repo 跑**：macOS TCC 擋 launchd 存取 `CloudStorage/`（OneDrive/iCloud/GDrive）。腳本本身 self-contained，不需要 repo 其他檔案，copy 到 `~/bin/` 是最乾淨解。
- **為什麼 snapshot 不放 OneDrive 裡**：DB 是二進制，每週變動大，放 OneDrive 會觸發頻繁同步浪費頻寬。放 `$HOME` 直接在 Mac 本地磁碟。
- **為什麼週日 10:30 不是 10:00**：避開整點大家都在執行的工作（cowork 排程、系統備份等），散開負載。
- **Mac 睡眠時段怎麼辦**：`StartCalendarInterval` 的行為是「錯過就補」——Mac 醒來後 launchd 會立刻補跑。不會漏掉。
- **這份 plist 要不要 commit 進 repo**：應該 commit（當成文件記錄）。實際載入的是 `~/Library/LaunchAgents/` 底下的 copy，不是 repo 裡這份，所以兩邊可以分別演進。
