#!/bin/bash
# 每晚把寫稿紀錄（logs/writing/*.csv）提交並推上 GitHub（2026-10-18，信哥要工作回放可以在 GitHub 看）。
# 只提交 logs/writing，不碰工作區其他改動；推送被拒（遠端有新 commit）就 rebase 一次再推。
set -uo pipefail
cd "${LOCAL_REPO:-$HOME/news_radar}" || exit 1
git add -- logs/writing/*.csv 2>/dev/null || exit 0
if git diff --cached --quiet -- logs/writing; then
  echo "[logs] 今天沒有新的寫稿紀錄"
  exit 0
fi
git commit -q -m "chore(logs): 寫稿紀錄 $(date +%F)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -- logs/writing || exit 1
if ! git push -q origin main; then
  git pull -q --rebase --autostash origin main && git push -q origin main || {
    osascript -e 'display notification "寫稿紀錄推不上 GitHub" with title "News Radar"' 2>/dev/null
    exit 1
  }
fi
echo "[logs] 已推上 GitHub"
