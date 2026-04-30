#!/usr/bin/env bash
# ============================================================
# News Radar · 每小時 compose（Mac launchd 入口）· Phase 8.18 + 8.22
# ------------------------------------------------------------
# 由 ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist 每小時觸發。
#
# 流程：
#   1. cd 到 ~/news_radar/（本機鏡像 repo，避開 CloudStorage TCC 限制）
#   2. git fetch + reset 到 main 最新（拿到 OneDrive 裡剛 push 的程式碼變動）
#   3. 從 state branch 拉最新 DB（內含 Cloud publisher 剛更新的 queue 狀態）
#   4. 跑 `python run_pipeline.py --harvest-now --compose-only --buffer-target 2`
#      Phase 8.22 修：必須加 --harvest-now，否則 --compose-only 不會觸發 harvest
#      (maybe_run_harvest 只在 --loop 或 --harvest-now/--publish-now 時被呼叫)。
#      Gemini 429 時 claude_cli 會自動接手（見 src/llm_brain.py Phase 8.19 fallback）。
#   5. 把更新後的 DB 推回 state branch
#
# 手動跑：bash ~/bin/news_radar_compose.sh
# 自動跑：launchctl start com.hsin.news-radar.compose
# ============================================================

set -u

# ---- 使用者可調參數 ----
REPO="HsinTiger/news-radar"
LOCAL_REPO="$HOME/news_radar"              # 本機鏡像，不在 CloudStorage/
BUFFER_TARGET=2                             # queue buffer 目標筆數（1-2 小時 buffer）
LOG_ROOT="$HOME/news_radar_snapshots/_compose_logs"
LOG_FILE="$LOG_ROOT/$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_ROOT"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "===== News Radar Compose: $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "LOCAL_REPO: $LOCAL_REPO"

# ---- 依賴檢查 ----
for cmd in git python3; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "❌ 找不到 $cmd。請 brew install $cmd 後再執行。"
        exit 1
    fi
done

# ---- 首次執行：如果本機還沒有 clone，clone 下來 ----
if [ ! -d "$LOCAL_REPO/.git" ]; then
    echo "📥 本機尚無 $LOCAL_REPO → 首次 clone..."
    if ! git clone "https://github.com/${REPO}.git" "$LOCAL_REPO"; then
        echo "❌ Clone 失敗。請確認網路與 GitHub 存取權。"
        exit 2
    fi
fi

cd "$LOCAL_REPO" || { echo "❌ cd $LOCAL_REPO 失敗"; exit 3; }

# ---- 拉 main 最新（另一台機可能剛 push 程式碼變動）----
# 設計決策：用 `merge --ff-only` 而非 `reset --hard`：
#   clean tree + 可 ff → fast-forward（與 reset 效果相同）
#   dirty tree       → ff-only 衝突檔才拒絕；reset --hard 會無聲吞 WIP
#   local 有未 push commit + origin 也前進 → ff-only 拒絕；reset --hard 會吞掉 commit
# 失敗都 loud-log，不用 `|| true` 靜默吞掉。
echo "🔄 fetch origin main..."
if ! git fetch --quiet origin main; then
    echo "⚠️ fetch main 失敗，沿用本機 main"
fi
if ! git merge --ff-only origin/main >/dev/null 2>&1; then
    echo "⚠️ 不能 fast-forward 到 origin/main —— 本輪沿用現有 code"
    echo "   可能原因：working tree 髒、或 local 有未 push commit"
    echo "   手動處理：cd $LOCAL_REPO && git status && git log --oneline origin/main..HEAD"
fi

# ---- 拉 state branch 的 DB ----
echo "🔄 fetch state branch..."
if git fetch --quiet origin state 2>/dev/null; then
    # 從 state branch 取 DB（用 git show 直接把 blob 倒到工作區）
    mkdir -p data/01_harvest state archive data/05_reflect/proposals
    if git show origin/state:data/01_harvest/news_radar.db > data/01_harvest/news_radar.db 2>/dev/null; then
        echo "✅ DB 從 state branch 還原：$(du -h data/01_harvest/news_radar.db | cut -f1)"
    else
        echo "⚠️ state branch 沒有 DB → 會初始化新的"
    fi
    if git show origin/state:state/last_harvest.txt > state/last_harvest.txt 2>/dev/null; then
        :
    else
        rm -f state/last_harvest.txt
    fi
    # Phase 9 Item 2: carry proposals dir forward so reflector cron contributions
    # survive each hourly compose cycle. Without this, compose's orphan-push wipes
    # any proposals/ jsonl files written by reflect_*.yml workflows.
    if git show origin/state:data/05_reflect/proposals 2>/dev/null | head -1 > /dev/null 2>&1; then
        # state branch has proposals dir; checkout into a temp + copy
        TMP_PROPOSALS=$(mktemp -d)
        git archive origin/state -- data/05_reflect/proposals/ 2>/dev/null | tar -x -C "$TMP_PROPOSALS" 2>/dev/null
        if [ -d "$TMP_PROPOSALS/data/05_reflect/proposals" ]; then
            cp -r "$TMP_PROPOSALS/data/05_reflect/proposals/." data/05_reflect/proposals/ 2>/dev/null || true
            echo "✅ proposals/ carried forward from state branch ($(ls data/05_reflect/proposals/ 2>/dev/null | wc -l) jsonl files)"
        fi
        rm -rf "$TMP_PROPOSALS"
    fi
else
    echo "⚠️ state branch 尚未存在 → 這是第一次跑，DB 會初始化"
fi

# ---- 確認 Python 依賴（pip install 在 requirements.txt 有變動時才跑）----
if [ ! -d ".venv" ]; then
    echo "📦 首次建立 venv..."
    python3 -m venv .venv || { echo "❌ venv 建立失敗"; exit 4; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt || { echo "❌ pip install 失敗"; exit 5; }

# ---- 跑 harvest + compose-only pipeline ----
# Phase 8.22: 加 --harvest-now，否則 --compose-only 不會觸發 harvest → RSS 永遠是舊的
echo ""
echo "🧠 Running run_pipeline.py --harvest-now --compose-only --buffer-target $BUFFER_TARGET"
python run_pipeline.py --harvest-now --compose-only --buffer-target "$BUFFER_TARGET"
PIPELINE_EXIT=$?
echo ""
echo "↳ pipeline exit code: $PIPELINE_EXIT"

# ---- 把 DB 推回 state branch（orphan commit，force-push 覆蓋）----
echo ""
echo "📤 Push DB 回 state branch..."
STATE_DIR="$(mktemp -d)"
(
    cd "$STATE_DIR" || exit 10
    git init -q -b state
    git config user.name "news-radar-mac-compose"
    git config user.email "noreply@local"

    mkdir -p data/01_harvest state archive data/05_reflect/proposals
    cp "$LOCAL_REPO/data/01_harvest/news_radar.db" data/01_harvest/news_radar.db 2>/dev/null || true
    if [ -f "$LOCAL_REPO/state/last_harvest.txt" ]; then
        cp "$LOCAL_REPO/state/last_harvest.txt" state/last_harvest.txt
    fi
    if [ -d "$LOCAL_REPO/archive" ]; then
        cp -r "$LOCAL_REPO/archive/." archive/ 2>/dev/null || true
    fi
    # Phase 9 Item 2: include proposals dir in orphan-push staging
    if [ -d "$LOCAL_REPO/data/05_reflect/proposals" ]; then
        cp -r "$LOCAL_REPO/data/05_reflect/proposals/." data/05_reflect/proposals/ 2>/dev/null || true
    fi

    cat > LAST_RUN.txt <<EOF
last_run_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)
kind: mac_compose
pipeline_exit: ${PIPELINE_EXIT}
host: $(hostname -s)
EOF

    git add -A
    if git diff --cached --quiet; then
        echo "↳ 無變化，不推送"
        exit 0
    fi
    git commit -q -m "state: mac_compose @ $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    # 用本機 git config 的 credential helper（通常是 osxkeychain）推送
    if git push --force "https://github.com/${REPO}.git" state 2>&1; then
        echo "✅ state branch 已更新"
    else
        echo "❌ push state branch 失敗（檢查 GitHub credential / osxkeychain）"
        exit 11
    fi
)

echo ""
echo "===== 完成: $(date '+%Y-%m-%d %H:%M:%S') ====="
