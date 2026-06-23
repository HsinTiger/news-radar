#!/usr/bin/env bash
# ============================================================
# News Radar · Substack「立即寫稿」快速通道（Mac launchd 入口）
# ------------------------------------------------------------
# 由 ~/Library/LaunchAgents/com.newsradar.substack_drain_fast.plist 每 5 分鐘觸發。
#
# 為什麼要這支：常規 drain（com.newsradar.substack_drain）每小時一次，而本機主 DB
# 也是每小時才由 compose_hourly.sh 從 state branch 同步一次。使用者在提交前端勾「立即
# 寫稿」時，希望幾分鐘內出稿、而不是等整點。
#
# 設計（零互踩）：本腳本【不碰主 DB】。它把 state branch 的 DB 拉到一份 /tmp 暫存檔，
# 用 NEWS_RADAR_DB 環境變數指向它，只跑 `drain_substack.py --only-immediate`。drain 與
# compose.py 都會讀這份暫存 DB（見兩處的 NEWS_RADAR_DB 覆寫）；去重檔
# data/substack_drafts/.substack_submissions.json 仍在主 repo，故與每小時的常規 drain
# 共用同一份去重狀態，不會重複寫稿。compose_hourly.sh 對主 DB 的讀寫完全不受影響。
#
# 手動跑：bash scripts/drain_substack_fast.sh
# ============================================================
set -u

LOCAL_REPO="${LOCAL_REPO:-$HOME/news_radar}"
REPO_SLUG="HsinTiger/news-radar"
PY="$LOCAL_REPO/.venv/bin/python"
LOCKDIR="$LOCAL_REPO/.fast_drain.lock.d"

cd "$LOCAL_REPO" 2>/dev/null || { echo "[fast-drain] no repo at $LOCAL_REPO"; exit 0; }

# 同一支快速 drain 不重入（上一輪還在 Whisper/寫稿時，這輪就跳過）。mkdir 是原子操作，
# macOS 無 flock 也能用。
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    echo "[fast-drain] previous tick still running; skip"
    exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null; rm -f "$TMPDB" 2>/dev/null' EXIT

TMPDB="$(mktemp -t nr_immediate_db.XXXXXX)"

# 從 state branch 把最新 DB 倒進暫存檔（唯讀，不動主 DB）。
if ! git fetch --quiet origin state 2>/dev/null; then
    echo "[fast-drain] fetch state failed; skip this tick"
    exit 0
fi
if ! git show origin/state:data/01_harvest/news_radar.db > "$TMPDB" 2>/dev/null; then
    echo "[fast-drain] state branch has no DB yet; nothing to do"
    exit 0
fi

# 只處理被標 immediate 的投稿。沒有就安靜結束（不洗 log）。
NEWS_RADAR_DB="$TMPDB" "$PY" -u scripts/drain_substack.py --only-immediate
