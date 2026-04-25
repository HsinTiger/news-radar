#!/usr/bin/env bash
# ============================================================
# News Radar · 每 4 小時抓 engagement（Mac launchd 入口）
# ------------------------------------------------------------
# 由 ~/Library/LaunchAgents/com.hsin.news-radar.engagement.plist 每 14400 秒觸發。
#
# 為什麼需要這支：
#   src/engagement.py 從 2026-04-25 起被排程跑——之前一直沒排程，DB 裡的
#   engagement_stats 是手動跑了 2 次的結果，dashboard 看到的數字 11+ 小時不更新。
#
# 流程（簡單，不碰 state branch）：
#   1. cd ~/news_radar
#   2. source .venv（拿 httpx / dotenv）
#   3. python -m src.engagement → 寫 engagement_stats 到本機 DB
#   4. 不 push_state，等下一次 compose_hourly.sh（每小時 :05）順手帶上去
#
# 為什麼不 push_state：避免跟 compose 撞 race condition（兩個 worker 同時
# orphan-push 到 state branch，後跑的會 force-overwrite 前跑的）。延遲最壞
# 1 小時 dashboard 才看到，acceptable。
#
# 為什麼 4 小時不是 1 小時：FB / IG Insights 有 hourly rate limit；engagement
# 數字本來就沒有那麼即時，4 小時夠覆蓋一日內的爬升曲線。
#
# 手動跑：bash ~/bin/news_radar_engagement.sh
# 自動跑：launchctl start com.hsin.news-radar.engagement
# ============================================================

set -u

LOCAL_REPO="$HOME/news_radar"
LOG_ROOT="$HOME/news_radar_snapshots/_engagement_logs"
LOG_FILE="$LOG_ROOT/$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_ROOT"

# 所有輸出統一到 log；尾巴留 30 個 log 自動 rotate
exec > >(tee -a "$LOG_FILE") 2>&1

echo "============================================================"
echo " News Radar Engagement Worker"
echo " started: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo " log    : $LOG_FILE"
echo "============================================================"

if [[ ! -d "$LOCAL_REPO" ]]; then
    echo "[engagement] ❌ 找不到 $LOCAL_REPO" >&2
    exit 2
fi

cd "$LOCAL_REPO"

# venv 必要（拿 httpx / python-dotenv）
VENV="$LOCAL_REPO/.venv/bin/activate"
if [[ ! -f "$VENV" ]]; then
    echo "[engagement] ❌ 找不到 venv：$VENV" >&2
    exit 2
fi
# shellcheck disable=SC1090
source "$VENV"

# .env 必要（FB_PAGE_ACCESS_TOKEN / IG_ACCESS_TOKEN / THREADS_ACCESS_TOKEN）
if [[ ! -f "$LOCAL_REPO/.env" ]]; then
    echo "[engagement] ❌ 找不到 .env" >&2
    exit 2
fi

# 跑 engagement
python -m src.engagement
PYRC=$?

echo ""
echo "[engagement] python exit code = $PYRC"

# 簡單 log rotate：保留最近 30 份
ls -1t "$LOG_ROOT"/*.log 2>/dev/null | tail -n +31 | xargs -I {} rm -f {}

if [[ $PYRC -ne 0 ]]; then
    echo "[engagement] ❌ python 階段失敗 (exit $PYRC)"
    exit $PYRC
fi

echo "[engagement] ✅ 完成 — 下次 compose_hourly 會把 DB 推到 state branch"
exit 0
