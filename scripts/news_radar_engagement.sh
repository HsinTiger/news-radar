#!/usr/bin/env bash
# ============================================================
# News Radar · 每小時抓 engagement（Mac launchd 入口）
# ------------------------------------------------------------
# 由 ~/Library/LaunchAgents/com.hsin.news-radar.engagement.plist 每 3600 秒觸發。
#
# 為什麼需要這支：
#   src/engagement.py 從 2026-04-25 起被排程跑——之前一直沒排程，DB 裡的
#   engagement_stats 是手動跑了 2 次的結果，dashboard 看到的數字 11+ 小時不更新。
#
# 流程：
#   1. cd ~/news_radar
#   2. source .venv（拿 httpx / dotenv）
#   3. SELECT MAX(fetched_at) FROM engagement_stats   # before-shot
#   4. python -m src.engagement → 寫 engagement_stats 到本機 DB
#   5. SELECT MAX(fetched_at) FROM engagement_stats   # after-shot
#   6. 若 MAX(fetched_at) 有前進 → 立刻 bash scripts/push_state.sh 推到 state branch
#
# 為什麼要立刻 push_state（2026-04-26 修正）：
#   原本設計以為「等下一次 compose_hourly 順手帶上去」是安全的，但實測發現
#   compose_hourly 的第一步是 `git show origin/state:db > local_db`，會把本機
#   engagement worker 剛寫好的 row 全部蓋掉。symptom：engagement worker log 一路
#   都是 "OK=N committed"，但 state branch DB 永遠停在 worker 上次「剛好搶贏
#   compose 競態」的時間點（例：2026-04-26 一整天只活下來 07:02 那批 3 row，
#   因為那次 compose 在 15:02:11 開始 restore，engagement 在 15:02:50 寫入，剛好
#   在 compose push 之前的 3 分鐘空窗）。
#
#   修法：engagement 自己把 DB 推到 state branch（orphan force-push，跟 compose
#   走同一條路徑）。force-push 的競態語意原本就是「最後寫的贏」，而 engagement
#   寫的欄位（engagement_stats）跟 compose 寫的欄位（drafts / news_items）是
#   disjoint 的；即使被 compose 覆蓋，下一個 cycle compose restore 時拿到的也是
#   含 engagement row 的版本，會被保留再推回去。SSOT §4.3 已明確：state branch
#   並發寫者各自做 read-modify-push、disjoint table 是可接受的競態。
#
# 為什麼不改 compose_hourly.sh 把 restore 改成「保留本機 engagement_stats」：
#   compose 的 restore 是 sqlite blob 整檔覆蓋；改成 schema-aware merge 複雜度
#   高、容易引入新 bug。直接讓 engagement 用 push_state.sh 推回去，符合 SSOT
#   §5.1 orphan-commit pattern，最小改動。
#
# 為什麼用 MAX(fetched_at) 比對而不是 python exit code：
#   exit 0 + total=0（沒有 bucket 對齊）是常態，不該 push（避免無變動 state
#   branch churn）。用 MAX(fetched_at) 是否前進判斷「真的有新 row 寫進來」。
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

export DB_PATH="$LOCAL_REPO/data/01_harvest/news_radar.db"

# Helper：印出本機 engagement_stats 最新一筆 fetched_at（沒有就回空字串）
# 注意：DB_PATH 必須在呼叫前 export（見下方註釋）。bash 不會把 `VAR=val func`
# 形式的前綴傳到 shell function 內部 spawn 的 subprocess（python3 heredoc）—
# 這是 Phase 1 (commit abff524) 的 bug，Phase 1.5 修正：改用 export。
read_max_fetched_at() {
    python3 - <<'PY' 2>/dev/null
import sqlite3, os, sys
db = os.environ.get("DB_PATH")
if not db or not os.path.exists(db):
    sys.exit(0)
try:
    c = sqlite3.connect(db)
    r = c.execute("SELECT MAX(fetched_at) FROM engagement_stats").fetchone()
    print(r[0] if r and r[0] else "")
except Exception:
    pass
PY
}

# Before-shot：跑 python 之前的 MAX(fetched_at)
BEFORE_MAX=$(read_max_fetched_at)
echo "[engagement] before MAX(fetched_at) = ${BEFORE_MAX:-<none>}"

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

# After-shot：python 跑完之後的 MAX(fetched_at)
AFTER_MAX=$(read_max_fetched_at)
echo "[engagement] after  MAX(fetched_at) = ${AFTER_MAX:-<none>}"

# 比對：MAX(fetched_at) 有前進才推 state branch
if [[ -n "$AFTER_MAX" && "$AFTER_MAX" != "$BEFORE_MAX" ]]; then
    echo "[engagement] 🆕 偵測到新 engagement_stats row → 推 state branch"
    if bash "$LOCAL_REPO/scripts/push_state.sh"; then
        echo "[engagement] ✅ state branch push + post-condition 通過"
    else
        PUSH_RC=$?
        # push 失敗不算致命：本機已寫入，下次 cycle 會再嘗試。但 log 要顯眼。
        echo "[engagement] ⚠️ push_state.sh 失敗 (rc=$PUSH_RC) — 本機 row 仍存在，下次 cycle 再推"
    fi
else
    echo "[engagement] ⚪ 無新 row（MAX 沒前進）→ 不推 state branch"
fi

echo "[engagement] ✅ 完成"
exit 0
