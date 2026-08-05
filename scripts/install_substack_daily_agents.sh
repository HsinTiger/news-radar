#!/usr/bin/env bash
# 安裝／更新每日 Substack 排程 agent（canon 命名 com.hsin.news-radar.substack-*）。
#
#   bash scripts/install_substack_daily_agents.sh          # 安裝並載入
#   bash scripts/install_substack_daily_agents.sh --status  # 只顯示狀態
#   bash scripts/install_substack_daily_agents.sh --uninstall
#
# 為什麼需要這個檔：2026-07-23 的 rebuild 把 Mac worker 收斂成
# com.hsin.news-radar.{compose,substack-fast}，但那兩個都**不產排程稿**——
# compose_hourly.sh 只跑 drain_substack.py（只撈 user_substack 投稿）
# 與 run_pipeline.py（Meta 線）。舊的 com.newsradar.substack_* 沒有被
# 對應遷移，於是每日草稿無聲停產：每一步都正常結束、沒有任何錯誤。
#
# 排程沿用既定設計（每日 5 篇、3 篇 podcast），但修掉一個舊 bug：
# 舊的 substack_podcast 與 substack_podcast2 都排 13:00 同時觸發，
# 而 compose.py 的鎖只等 30 秒（compose.py:198 _acquire_lock timeout_s=30），
# 組稿需數分鐘 → 第二篇幾乎必然搶不到鎖而放棄，實際只產 4 篇。
# 這裡把第二篇改到 13:45。
set -euo pipefail

REPO="$HOME/news_radar"
AGENT_DIR="$HOME/Library/LaunchAgents"
PREFIX="com.hsin.news-radar"

# label|時|分|mode|額外旗標
SCHEDULE=(
  "substack-morning|8|0|morning|--harvest"
  "substack-podcast1|13|0|podcast|--harvest"
  "substack-podcast2|13|45|podcast|"
  "substack-evening|17|0|evening|--harvest"
  "substack-podcast3|21|0|podcast|"
)

mode="${1:-install}"

if [ "$mode" = "--status" ]; then
  for entry in "${SCHEDULE[@]}"; do
    IFS='|' read -r label hh mm _ _ <<< "$entry"
    state=$(launchctl list 2>/dev/null | awk -v l="$PREFIX.$label" '$3==l{print "loaded"}')
    printf "  %-22s %02d:%02d  %s\n" "$label" "$hh" "$mm" "${state:-NOT LOADED}"
  done
  exit 0
fi

for entry in "${SCHEDULE[@]}"; do
  IFS='|' read -r label hh mm cmode flag <<< "$entry"
  plist="$AGENT_DIR/$PREFIX.$label.plist"

  launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
  if [ "$mode" = "--uninstall" ]; then
    rm -f "$plist"
    echo "  removed $label"
    continue
  fi

  mkdir -p "$AGENT_DIR" "$REPO/logs"
  # --harvest 只掛在當日第一次抓取的那幾篇；其餘沿用已抓進來的池子挑下一個未用過的來源。
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PREFIX.$label</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>$hh</integer>
        <key>Minute</key><integer>$mm</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>cd $REPO &amp;&amp; .venv/bin/python -u substack_radar/compose.py $cmode $flag</string>
    </array>
    <key>StandardOutPath</key>
    <string>$REPO/logs/launchd_$label.log</string>
    <key>StandardErrorPath</key>
    <string>$REPO/logs/launchd_$label.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
PLIST

  plutil -lint "$plist" > /dev/null
  launchctl bootstrap "gui/$(id -u)" "$plist"
  printf "  installed %-22s %02d:%02d  compose.py %s %s\n" "$label" "$hh" "$mm" "$cmode" "$flag"
done

if [ "$mode" != "--uninstall" ]; then
  echo
  echo "舊的 com.newsradar.substack_* 若仍存在，請確認未載入以免重複產稿："
  launchctl list 2>/dev/null | grep "com.newsradar.substack" || echo "  （沒有舊 agent 載入，正常）"
fi
