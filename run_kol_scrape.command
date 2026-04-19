#!/bin/bash
# 雙擊即跑：重抓 Fox Hsiao (hinet) + 游庭皓 (yutinghaosfinance)
# IEObserve 已完成，這次只補齊另外兩位。

set -u
cd "$(dirname "$0")"

echo "====================================================="
echo " News Radar · KOL Retry Scraper (hinet + yutinghaosfinance)"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "====================================================="

if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    echo "✅ 使用 .venv"
elif [ -f "/Users/hsin/.virtualenvs/news_radar/bin/activate" ]; then
    source /Users/hsin/.virtualenvs/news_radar/bin/activate
    echo "✅ 使用 ~/.virtualenvs/news_radar"
else
    echo "⚠️ 找不到 venv，使用系統 python3"
fi

echo ""
echo "🚀 開始重抓兩位（每位最多 5 分鐘，共約 10 分鐘）..."
echo "   ⚠️ 會開啟 Chromium 視窗，請勿關閉"
echo ""

python3 scratch/retry_kol.py

echo ""
echo "====================================================="
echo " ✅ 爬蟲完成。結果在："
echo "   data/00_competitors/competitor_db.json"
echo "====================================================="
echo ""
read -r -p "按 Enter 結束..." _
