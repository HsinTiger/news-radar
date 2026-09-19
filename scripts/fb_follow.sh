#!/bin/bash
# FB 粉專「主力爸爸我錯了」跟發 Substack 草稿（substack_radar/fb_follow.py）。
# launchd：com.hsin.news-radar.fb-follow，每個時段跑一次、一次最多發一篇。
#
# 日誌放 ~/Library/Logs 而不是 /tmp：2026-09-18 的 podcast 排程出事，隔天重開機
# 把 /tmp 清掉，原因再也查不到。
#
# 不拿 .runtime-state-local.lock.d：這條線只讀本機草稿資料夾、只寫自己的帳本
# （data/substack_drafts/.fb_posted.json），不碰 DB 和 runtime state。
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$HOME/Library/Logs/news-radar"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/fb-follow.log"

# 同時只跑一個：上一輪 agy 還在寫，下一個時段就不要再疊一個。
LOCK="$REPO/.fb-follow.lock.d"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -f "$LOCK/pid" ] && kill -0 "$(cat "$LOCK/pid")" 2>/dev/null; then
    echo "[$(date '+%F %T')] 上一輪還在跑（pid $(cat "$LOCK/pid")），跳過" >> "$LOG"
    exit 0
  fi
  rm -rf "$LOCK" && mkdir "$LOCK"
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

cd "$REPO" || exit 3
{
  echo "===== [$(date '+%F %T')] fb-follow start (LIVE=${FB_FOLLOW_LIVE:-0}) ====="
  "$REPO/.venv/bin/python" -u -m substack_radar.fb_follow "$@"
  echo "===== [$(date '+%F %T')] fb-follow exit=$? ====="
} >> "$LOG" 2>&1
