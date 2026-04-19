#!/bin/bash
# ============================================================
# News Radar · Phase 8.10 Kill-the-X 驗收啟動器
# ------------------------------------------------------------
# 一次跑完：
#   Step 1 · 確認 pytest 就緒
#   Step 2 · 全部 pytest（41 個應全綠；social helper 測試保留）
#   Step 3 · make harvest（config 從 32 → 13 feed 後的第一次重跑）
#   Step 4 · make diag（看 2 個新源 Howard Marks Memos / Peter Zeihan 是否 HEALTHY）
#
# 驗收重點：
#   ✓ pytest 41/41 綠
#   ✓ feeds_health.md 不能再出現 17 個 X · xxx 的 DEAD_FEED
#   ✓ Howard Marks Memos / Peter Zeihan 兩個新源至少有一個 HEALTHY
#   ✓ 既有 Reddit / HN / Simon Willison / NVIDIA / Stratechery 等維持 HEALTHY
#
# 所有輸出雙管輸出：螢幕 + logs/phase810_verify_<timestamp>.log
# 使用方式：Finder 雙擊此檔案（首次若不能跑：chmod +x run_phase810_verify.command）
# ============================================================
set +e
cd "$(dirname "$0")"

TS=$(date '+%Y%m%d_%H%M%S')
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/phase810_verify_${TS}.log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "============================================================"
echo " News Radar · Phase 8.10 Kill-the-X 驗收"
echo " 開始時間：$(date '+%Y-%m-%d %H:%M:%S')"
echo " Log 檔：$LOG_FILE"
echo "============================================================"
echo ""

# ---- venv ----
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

# ---- Step 1 ----
echo "────────────────────────────────────────"
echo " Step 1/4 · 確認 pytest 就緒"
echo "────────────────────────────────────────"
python -m pip install -q pytest pytest-asyncio
STEP1=$?
if [ $STEP1 -ne 0 ]; then
    echo "❌ pytest 安裝失敗"
else
    echo "✅ pytest: $(python -m pytest --version 2>&1)"
fi
echo ""

# ---- Step 2 ----
echo "────────────────────────────────────────"
echo " Step 2/4 · pytest（unit + integration）"
echo " 預期：41/41 passed（8.10 Kill-the-X 沒動測試，但 social helper 的 2 個"
echo "        通用化 case 仍保留，對未來其他 social feed 有用）"
echo "────────────────────────────────────────"
python -m pytest -v --tb=short
STEP2=$?
if [ $STEP2 -eq 0 ]; then
    echo "✅ 全部測試通過"
else
    echo "❌ 有測試失敗（exit=$STEP2）"
fi
echo ""

# ---- Step 3 ----
echo "────────────────────────────────────────"
echo " Step 3/4 · make harvest（config 從 32 → 13 feed）"
echo " 預期：不再有任何 rsshub.app 的 404；Howard Marks / Zeihan 兩個新源"
echo "        至少其一應回 200 並入庫"
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
    echo "⏭  Tests 未過，跳過 harvest"
    STEP3=-1
fi
echo ""

# ---- Step 4 ----
echo "────────────────────────────────────────"
echo " Step 4/4 · make diag"
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
echo " ✅ Phase 8.10 驗收完成"
echo " 結束時間：$(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo " 步驟結果："
echo "   Step 1 (pytest 安裝)  : exit=$STEP1"
echo "   Step 2 (pytest 跑)    : exit=$STEP2"
echo "   Step 3 (harvest)      : exit=$STEP3  (-1 = skipped)"
echo "   Step 4a (diag-harvest): exit=$STEP4A"
echo "   Step 4b (diag-feeds)  : exit=$STEP4B"
echo ""
echo " 必看檔案："
echo "   $LOG_FILE"
echo "   data/01_harvest/diagnostic_report.md"
echo "   data/01_harvest/feeds_health.md  ← 重點：新的 2 個源 verdict"
echo "============================================================"
echo ""
read -n 1 -s -r -p "按任意鍵關閉..."
echo ""
