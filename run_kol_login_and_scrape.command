#!/bin/bash
# ============================================================
# News Radar · 一鍵登入 + 抓三位 KOL 各 10–20 篇完整圖文
# ============================================================
# 使用方式：雙擊本檔
#   Phase 1  開啟 Chromium → FB 登入頁 → 你登入一次（session 存入 profile）
#            偵測到登入成功會自動進入 Phase 2
#   Phase 2  自動抓三位 KOL 各 15 篇（展開「查看更多」、抓 FB CDN 圖片）
#   Phase 3  用 Claude 產出 kol_tactics.md（Gemini 備援）
#
# profile 位置：~/.cache/news_radar/chrome_profile（絕不放 OneDrive）
# 重複執行：下次雙擊會直接用已登入的 profile，Phase 1 會提前結束
# ============================================================

set -u
cd "$(dirname "$0")"

echo "================================================================"
echo " News Radar · KOL Login + Full Scrape"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================================"
echo ""

# ---- venv 啟動（優先本地 .venv）----
if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    echo "✅ 使用 .venv"
elif [ -f "$HOME/.virtualenvs/news_radar/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.virtualenvs/news_radar/bin/activate"
    echo "✅ 使用 ~/.virtualenvs/news_radar"
else
    echo "⚠️ 找不到 venv，使用系統 python3"
fi

echo ""
echo "----------------------------------------------------------------"
echo " Phase 1/3  登入 FB（最多 4 分鐘，登入成功會提前結束）"
echo "----------------------------------------------------------------"
echo "   ⚠️ 會開啟 Chromium 視窗，請在該視窗完成登入"
echo "   ⚠️ 若 profile 已登入過，視窗會開啟→偵測→立刻關閉"
echo ""

python3 src/competitor_agent.py --login
LOGIN_RC=$?

if [ "$LOGIN_RC" -ne 0 ]; then
    echo ""
    echo "❌ 登入階段異常結束（exit code $LOGIN_RC）"
    echo "   常見原因：Playwright 未安裝、Chromium 被 macOS 權限擋"
    read -r -p "按 Enter 結束..." _
    exit "$LOGIN_RC"
fi

echo ""
echo "----------------------------------------------------------------"
echo " Phase 2/3  抓三位 KOL 各最多 15 篇完整貼文"
echo "----------------------------------------------------------------"
echo "   ⚠️ 會再次開啟 Chromium，請勿關閉"
echo "   ⚠️ 單位 KOL 上限 6 分鐘，三位總計最多 ~18 分鐘"
echo ""

# --skip-ai 讓第二階段專注在爬蟲；AI 戰術產出放到 Phase 3 單獨跑
python3 src/competitor_agent.py --skip-ai
SCRAPE_RC=$?

if [ "$SCRAPE_RC" -ne 0 ]; then
    echo ""
    echo "❌ 抓取階段異常結束（exit code $SCRAPE_RC）"
    read -r -p "按 Enter 結束..." _
    exit "$SCRAPE_RC"
fi

echo ""
echo "----------------------------------------------------------------"
echo " Phase 3/3  產出 kol_tactics.md（Claude / Gemini）"
echo "----------------------------------------------------------------"
echo ""

# 單獨呼叫 generate_tactics（讀現有 DB 即可，不用重跑爬蟲）
python3 - <<'PYEOF'
import json
from pathlib import Path
from src import competitor_agent as ca

db_path = Path("data/00_competitors/competitor_db.json")
db = json.loads(db_path.read_text(encoding="utf-8"))
print(f"📊 DB 載入：{len(db)} 位 KOL，共 {sum(len(v.get('live_posts',[])) for v in db.values())} 篇貼文")
ca.generate_tactics(db)
PYEOF

echo ""
echo "================================================================"
echo " ✅ 全流程完成。產出檔案："
echo "   data/00_competitors/competitor_db.json  ← 原始貼文 JSON"
echo "   data/00_competitors/kol_tactics.md      ← 寫手戰術指導"
echo "================================================================"
echo ""
read -r -p "按 Enter 結束..." _
