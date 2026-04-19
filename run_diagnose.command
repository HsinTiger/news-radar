#!/bin/bash
# ============================================================
# News Radar · 雙擊診斷啟動器
# ------------------------------------------------------------
# 一次跑完 harvest + feeds 兩層診斷，輸出在 data/01_harvest/
# 使用方式：Finder 雙擊此檔案（首次可能需要 chmod +x）
# ============================================================
set -e
cd "$(dirname "$0")"

echo ""
echo "=========================================="
echo " News Radar · 診斷啟動器"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="
echo ""

# 自動啟用 venv（如果存在）
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    echo "[run_diagnose] 已啟用 venv"
elif [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    echo "[run_diagnose] 已啟用 .venv"
fi

echo ""
echo "── Step 1/2：Harvest DB 診斷（純 SQLite）──"
python tools/diagnose_harvest.py

echo ""
echo "── Step 2/2：Feeds 即時存活探測（會打網路）──"
python tools/diagnose_feeds.py

echo ""
echo "=========================================="
echo " ✅ 完成！報告存放："
echo "    data/01_harvest/diagnostic_report.md"
echo "    data/01_harvest/feeds_health.md"
echo "=========================================="
echo ""
echo "（本視窗可自行關閉）"
read -n 1 -s -r -p "按任意鍵結束..."
echo ""
