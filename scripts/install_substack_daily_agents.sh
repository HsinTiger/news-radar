#!/usr/bin/env bash
# 安裝 Substack 編輯排程（2026-10-15 起）：
#   每天 06:00 只寫一篇（一三五 吹牛免稅、二四 智取未來、六日 賺錢有道），寫不好當天再重試一次，
#   07:30 由 daily-publish 從當天的稿子挑一篇發布並寄信，其餘留草稿。

set -euo pipefail

REPO="${LOCAL_REPO:-$HOME/news_radar}"
AGENT_DIR="$HOME/Library/LaunchAgents"
BIN_DIR="$HOME/bin"
MODE="${1:-install}"
AGENTS=(
  "com.hsin.news-radar.substack-podcast-noon"
  "com.hsin.news-radar.substack-ai"
  "com.hsin.news-radar.company-compose"
  "com.hsin.news-radar.daily-publish"
  "com.hsin.news-radar.fb-scheduled"
)
LEGACY_AGENTS=(
  "com.hsin.news-radar.substack-podcast-second"   # 2026-10-18 起一天只寫一篇
  "com.hsin.news-radar.substack-podcast-noon-1"
  "com.hsin.news-radar.substack-podcast-noon-2"
  "com.hsin.news-radar.company-pick"
  "com.hsin.news-radar.substack-daily"
  "com.hsin.news-radar.substack-morning"
  "com.hsin.news-radar.substack-podcast1"
  "com.hsin.news-radar.substack-podcast2"
  "com.hsin.news-radar.substack-podcast3"
  "com.hsin.news-radar.substack-evening"
  "com.newsradar.substack_morning"
  "com.newsradar.substack_evening"
  "com.newsradar.substack_podcast"
  "com.newsradar.substack_podcast2"
  "com.newsradar.substack_podcast3"
  "com.newsradar.company_pick"
  "com.newsradar.substack_company"
)

if [ "$MODE" = "--status" ]; then
  for label in "${AGENTS[@]}"; do
    state=$(launchctl list 2>/dev/null | awk -v l="$label" '$3==l{print "loaded"}')
    printf "  %-42s %s\n" "$label" "${state:-NOT LOADED}"
  done
  exit 0
fi

mkdir -p "$AGENT_DIR" "$BIN_DIR" "$HOME/Library/Logs/news-radar"
for label in "${AGENTS[@]}"; do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)" "$AGENT_DIR/$label.plist" 2>/dev/null || true
  if [ "$MODE" = "--uninstall" ]; then
    rm -f "$AGENT_DIR/$label.plist"
  fi
done
for label in "${LEGACY_AGENTS[@]}"; do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)" "$AGENT_DIR/$label.plist" 2>/dev/null || true
  rm -f "$AGENT_DIR/$label.plist"
done
if [ "$MODE" = "--uninstall" ]; then
  echo "removed governed Substack editorial agents"
  exit 0
fi
if [ "$MODE" != "install" ]; then
  echo "usage: $0 [install|--status|--uninstall]"
  exit 2
fi

cp "$REPO/scripts/substack_editorial_worker.sh" "$BIN_DIR/news_radar_substack_editorial.sh"
chmod +x "$BIN_DIR/news_radar_substack_editorial.sh"

for label in "${AGENTS[@]}"; do
  sed "s|HOME_DIR|$HOME|g" "$REPO/scripts/$label.plist" > "$AGENT_DIR/$label.plist"
  plutil -lint "$AGENT_DIR/$label.plist" >/dev/null
  launchctl bootstrap "gui/$(id -u)" "$AGENT_DIR/$label.plist"
done

echo "installed: 每天 06:00 寫一篇（一三五 吹牛免稅、二四 智取未來、六日 賺錢有道），當天重試一次；07:30 自動發布；FB 12:30／20:30"
echo "owner submission immediate/hourly workers are unchanged"
