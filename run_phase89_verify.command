#!/bin/bash
# ============================================================
# News Radar · Phase 8.8 + 8.9 驗收啟動器
# ------------------------------------------------------------
# 一次跑完：
#   Step 1 · 安裝/更新 deps (pytest + requirements)
#   Step 2 · 全部 pytest（unit + integration）
#   Step 3 · make harvest（僅在 tests 綠燈時）
#   Step 4 · make diag（Reddit 改造後的實際通過率）
#
# 所有輸出雙管輸出：螢幕 + logs/phase89_verify_<timestamp>.log
# 方便 Claude 會後讀 log 做驗收判讀。
#
# 使用方式：Finder 雙擊此檔案（首次可能要 chmod +x）
# ============================================================
set +e  # 單步失敗不中斷整批（讓 Claude 看得到每一步的完整結果）
cd "$(dirname "$0")"

TS=$(date '+%Y%m%d_%H%M%S')
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/phase89_verify_${TS}.log"

# 用 tee 同時印到螢幕和檔案
exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "============================================================"
echo " News Radar · Phase 8.8 + 8.9 驗收"
echo " 開始時間：$(date '+%Y-%m-%d %H:%M:%S')"
echo " Log 檔：$LOG_FILE"
echo "============================================================"
echo ""

# ---- 自動啟用 venv ----
# 優先順序：~/.virtualenvs/news_radar > 本地 .venv > 本地 venv
if [ -f "$HOME/.virtualenvs/news_radar/bin/activate" ]; then
    source "$HOME/.virtualenvs/news_radar/bin/activate"
    echo "[env] 已啟用 ~/.virtualenvs/news_radar"
elif [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    echo "[env] 已啟用 ./.venv"
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    echo "[env] 已啟用 ./venv"
else
    echo "[env] ⚠️  找不到 venv — 用系統 python；若套件不全會失敗"
fi
echo "[env] python = $(which python)"
echo "[env] python --version = $(python --version 2>&1)"
echo ""

# ---- Step 1：確保 pytest 存在 ----
echo "────────────────────────────────────────"
echo " Step 1/4 · 安裝/確認 pytest"
echo "────────────────────────────────────────"
python -m pip install -q pytest pytest-asyncio
STEP1=$?
if [ $STEP1 -ne 0 ]; then
    echo "❌ pytest 安裝失敗（離線？pip 權限？）"
else
    echo "✅ pytest 就緒：$(python -m pytest --version 2>&1)"
fi
echo ""

# ---- Step 2：跑 pytest ----
echo "────────────────────────────────────────"
echo " Step 2/4 · pytest（unit + integration）"
echo "────────────────────────────────────────"
python -m pytest -v --tb=short
STEP2=$?
if [ $STEP2 -eq 0 ]; then
    echo "✅ 全部測試通過"
else
    echo "❌ 有測試失敗（exit=$STEP2）"
fi
echo ""

# ---- Step 3：make harvest（僅 tests 綠燈才跑，避免髒資料進 DB）----
echo "────────────────────────────────────────"
echo " Step 3/4 · make harvest"
echo "────────────────────────────────────────"
if [ $STEP2 -eq 0 ]; then
    python run_harvest.py
    STEP3=$?
    if [ $STEP3 -eq 0 ]; then
        echo "✅ Harvest 完成"
    else
        echo "❌ Harvest 失敗（exit=$STEP3）"
    fi
else
    echo "⏭  Tests 未過，跳過 harvest（避免髒資料進 DB）"
    STEP3=-1
fi
echo ""

# ---- Step 4：make diag ----
echo "────────────────────────────────────────"
echo " Step 4/4 · make diag（harvest + feeds 診斷）"
echo "────────────────────────────────────────"
echo "── 4a · diagnose_harvest（純 SQLite，快）──"
python tools/diagnose_harvest.py
STEP4A=$?
echo ""
echo "── 4b · diagnose_feeds（會打網路，慢）──"
python tools/diagnose_feeds.py
STEP4B=$?
echo ""

# ---- 總結 ----
echo "============================================================"
echo " ✅ Phase 8.8 + 8.9 驗收完成"
echo " 結束時間：$(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo " 步驟結果："
echo "   Step 1 (pytest 安裝)  : exit=$STEP1"
echo "   Step 2 (pytest 跑)    : exit=$STEP2"
echo "   Step 3 (harvest)      : exit=$STEP3  (-1 = skipped)"
echo "   Step 4a (diag-harvest): exit=$STEP4A"
echo "   Step 4b (diag-feeds)  : exit=$STEP4B"
echo ""
echo " 報告位置："
echo "   $LOG_FILE"
echo "   data/01_harvest/diagnostic_report.md"
echo "   data/01_harvest/feeds_health.md"
echo "============================================================"
echo ""
read -n 1 -s -r -p "按任意鍵關閉..."
echo ""
