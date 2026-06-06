#!/bin/bash
# ============================================================
# Install Manual Submit Server as a macOS LaunchAgent
# ============================================================
# 用法：
#   bash scripts/install_manual_submit_server.sh
#   # 然後打開 http://localhost:8765/docs 確認 API 在跑
#
# 移除：
#   launchctl unload -w ~/Library/LaunchAgents/com.hsin.news-radar.manual-submit-server.plist
#   rm ~/Library/LaunchAgents/com.hsin.news-radar.manual-submit-server.plist
# ============================================================

set -euo pipefail

REPO="$HOME/news_radar"
PLIST_SRC="$REPO/scripts/com.hsin.news-radar.manual-submit-server.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.hsin.news-radar.manual-submit-server.plist"
LOG_DIR="$HOME/Library/Logs/news-radar/manual-submit-server"

echo ""
echo "============================================================"
echo " News Radar · Manual Submit Server Installer"
echo "============================================================"
echo ""

# ---- Step 1: Check venv ----
echo "[1/4] Checking Python environment..."
if [ ! -f "$REPO/.venv/bin/python" ]; then
    echo "  ❌ 找不到 venv：$REPO/.venv"
    echo "  → 請先建 venv：cd $REPO && python3 -m venv .venv"
    exit 1
fi
echo "  ✅ venv OK"

# Step 1b: verify uvicorn is installed
if ! "$REPO/.venv/bin/python" -c "import uvicorn" 2>/dev/null; then
    echo "  📦 Installing uvicorn..."
    "$REPO/.venv/bin/pip" install -q "uvicorn[standard]>=0.32"
fi
echo "  ✅ uvicorn OK"

# ---- Step 2: Create log directory ----
echo "[2/4] Creating log directory..."
mkdir -p "$LOG_DIR"
echo "  ✅ $LOG_DIR"

# ---- Step 3: Copy plist ----
echo "[3/4] Installing LaunchAgent plist..."
cp "$PLIST_SRC" "$PLIST_DST"
echo "  ✅ $PLIST_DST"

# ---- Step 4: Load plist ----
echo "[4/4] Loading LaunchAgent..."
if launchctl list | grep -q com.hsin.news-radar.manual-submit-server; then
    echo "  ↳ Agent 已存在，先卸載再重新載入..."
    launchctl unload -w "$PLIST_DST" 2>/dev/null || true
fi
launchctl load -w "$PLIST_DST"
echo "  ✅ 已載入"

# ---- Verify ----
echo ""
echo "============================================================"
echo " ✅ Manual Submit Server installed!"
echo "============================================================"
echo ""
echo " Services:"
echo "   API Server  → http://localhost:8765"
echo "   API Docs    → http://localhost:8765/docs"
echo "   Health      → http://localhost:8765/api/health"
echo ""
echo " Commands:"
echo "   Start now   → launchctl start com.hsin.news-radar.manual-submit-server"
echo "   Stop        → launchctl stop com.hsin.news-radar.manual-submit-server"
echo "   Status      → launchctl list | grep news-radar.manual"
echo "   Logs        → tail -f $LOG_DIR/stdout.log"
echo "   Errors      → tail -f $LOG_DIR/stderr.log"
echo ""
echo " Frontend:"
echo "   Open http://localhost:8765 and the manual-submit/ frontend"
echo "   Or: cd $REPO/manual-submit && python3 -m http.server 8080"
echo ""

# Quick health check
sleep 1
if curl -sf http://127.0.0.1:8765/api/health >/dev/null 2>&1; then
    echo " 🟢 Server is running! (health check passed)"
else
    echo " 🟡 Server may still be starting (check logs: tail -f $LOG_DIR/stdout.log)"
    echo "    Try in a few seconds: curl http://127.0.0.1:8765/api/health"
fi
