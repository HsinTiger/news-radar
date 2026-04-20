#!/usr/bin/env bash
# ============================================================
# News Radar · 每週 DB snapshot（Mac launchd 入口）
# ------------------------------------------------------------
# 從 GitHub state branch 拉最新 DB 到 ~/news_radar_snapshots/YYYYMMDD/
# 設計目標：idempotent、不需要 Cowork、純 Mac 原生。
#
# 手動跑：bash scripts/weekly_snapshot.sh
# 自動跑：由 ~/Library/LaunchAgents/com.hsin.news-radar.snapshot.plist 觸發
#          （見同目錄的 .plist 檔案與 INSTALL.md）
# ============================================================

set -u   # 不用 -e 以便每步都有空間 recover

REPO="HsinTiger/news-radar"
SNAP_ROOT="$HOME/news_radar_snapshots"
TODAY="$(date +%Y%m%d)"
SNAP="$SNAP_ROOT/$TODAY"
LOG_DIR="$SNAP_ROOT/_logs"
LOG_FILE="$LOG_DIR/$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$SNAP_ROOT" "$LOG_DIR"

# 全部輸出都重導向到 log file + stdout（給 launchd 收）
exec > >(tee -a "$LOG_FILE") 2>&1

echo "===== News Radar Snapshot: $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "Repo:   $REPO"
echo "Target: $SNAP"
echo ""

# ---- 依賴檢查 ----
for cmd in git sqlite3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "❌ 找不到 $cmd。請 brew install $cmd 後再執行。"
        exit 1
    fi
done

# ---- idempotent：已經有今天的 snapshot 就跳過 clone，只報 stats ----
if [ -d "$SNAP" ]; then
    echo "⏭  $SNAP 已存在，跳過 clone，只重算 stats"
else
    echo "📥 Clone state branch (shallow)..."
    if ! git clone --branch state --single-branch --depth 1 \
         "https://github.com/${REPO}.git" "$SNAP" 2>&1; then
        echo "❌ Clone 失敗。可能原因："
        echo "   - state branch 不存在（pipeline 還沒成功跑過）"
        echo "   - 網路問題"
        echo "   - GitHub API rate limit"
        exit 2
    fi
    echo "✅ Clone 完成"
fi

# ---- 統計 ----
DB="$SNAP/data/01_harvest/news_radar.db"
if [ ! -f "$DB" ]; then
    echo "⚠️ DB 不在預期路徑 ($DB)"
    echo "state branch 內容："
    ls -la "$SNAP"
    exit 3
fi

DB_SIZE_HUMAN=$(ls -lh "$DB" | awk '{print $5}')
NEWS_ITEMS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM news_items;" 2>/dev/null || echo "N/A")
PUBLISHED=$(sqlite3 "$DB" "SELECT COUNT(*) FROM publish_log WHERE posted_at IS NOT NULL;" 2>/dev/null || echo "0")
LAST_POST=$(sqlite3 "$DB" "SELECT MAX(posted_at) FROM publish_log;" 2>/dev/null || echo "N/A")
PENDING=$(sqlite3 "$DB" "SELECT COUNT(*) FROM drafts WHERE status='pending';" 2>/dev/null || echo "N/A")

LAST_RUN="(not found)"
[ -f "$SNAP/LAST_RUN.txt" ] && LAST_RUN=$(cat "$SNAP/LAST_RUN.txt" | head -3 | tr '\n' ' ')

LAST_HARVEST="(not found)"
[ -f "$SNAP/state/last_harvest.txt" ] && LAST_HARVEST=$(cat "$SNAP/state/last_harvest.txt")

SNAP_DIR_SIZE=$(du -sh "$SNAP_ROOT" | cut -f1)
SNAP_COUNT=$(ls -1 "$SNAP_ROOT" | grep -E '^[0-9]{8}$' | wc -l | tr -d ' ')

echo ""
echo "📦 Snapshot stats"
echo "   路徑:         $SNAP"
echo "   DB size:      $DB_SIZE_HUMAN"
echo "   news_items:   $NEWS_ITEMS"
echo "   published:    $PUBLISHED"
echo "   last post:    $LAST_POST"
echo "   pending:      $PENDING"
echo "   last harvest: $LAST_HARVEST"
echo "   last run:     $LAST_RUN"
echo ""
echo "📊 Archive"
echo "   snapshots:   $SNAP_COUNT 個"
echo "   總佔用:      $SNAP_DIR_SIZE"

# ---- 過大提醒（>500 MB）----
SNAP_DIR_BYTES=$(du -sk "$SNAP_ROOT" | awk '{print $1*1024}')
if [ "$SNAP_DIR_BYTES" -gt 524288000 ]; then
    echo ""
    echo "⚠️ snapshot 資料夾 > 500 MB，考慮刪除舊的："
    echo "   ls -1 $SNAP_ROOT | grep -E '^[0-9]{8}\$' | sort | head -n -8 | \\"
    echo "     xargs -I{} rm -rf $SNAP_ROOT/{}"
fi

echo ""
echo "===== 完成: $(date '+%Y-%m-%d %H:%M:%S') ====="
