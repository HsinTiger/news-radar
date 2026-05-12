#!/bin/bash
# News Radar · Substack Pipeline One-Shot Setup
# ===============================================
# Usage:
#   bash ~/news_radar/tools/setup_substack_launchd.sh
#
# This script does FIVE things in order:
#   1. Install python-substack if missing
#   2. Verify .env has SUBSTACK_AUTO_DRAFT / COOKIES_STRING / PUBLICATION_URL
#   3. Kick off today's MORNING draft in background (catches up any missed slot)
#   4. Kick off today's EVENING draft in background (parallel — different source)
#   5. Install macOS launchd cron for daily 09:00 + 18:00 starting tomorrow
#
# Safe to re-run: idempotent. Existing launchd jobs get replaced.
set -e

REPO="$HOME/news_radar"
cd "$REPO"

echo "================================================================"
echo "News Radar · Substack daily-2-draft pipeline setup"
echo "================================================================"
echo ""

# ----------------------------------------------------------------------
# Step 1: python-substack
# ----------------------------------------------------------------------
echo "[1/5] Checking python-substack..."
if .venv/bin/python -c "import substack" 2>/dev/null; then
    echo "  → already installed"
else
    echo "  → installing..."
    .venv/bin/pip install -q python-substack
    echo "  → installed"
fi
echo ""

# ----------------------------------------------------------------------
# Step 2: .env sanity check
# ----------------------------------------------------------------------
echo "[2/5] Checking .env..."
ENV_OK=$(
    .venv/bin/python <<'PYEOF'
from dotenv import load_dotenv
import os, sys
load_dotenv(".env")
required = ('SUBSTACK_AUTO_DRAFT', 'SUBSTACK_COOKIES_STRING', 'SUBSTACK_PUBLICATION_URL')
missing = [k for k in required if not os.getenv(k)]
if missing:
    print(f"MISSING:{','.join(missing)}")
    sys.exit(0)
if os.getenv('SUBSTACK_AUTO_DRAFT') != '1':
    print(f"BAD_AUTO_DRAFT:{os.getenv('SUBSTACK_AUTO_DRAFT')!r}")
    sys.exit(0)
print("OK")
PYEOF
)
if [ "$ENV_OK" != "OK" ]; then
    echo "  ❌ $ENV_OK"
    echo ""
    echo "Edit ~/news_radar/.env first. Required block:"
    echo ""
    echo "  SUBSTACK_AUTO_DRAFT=1"
    echo "  SUBSTACK_PUBLICATION_URL=https://hsin73.substack.com"
    echo "  SUBSTACK_COOKIES_STRING='ab_experiment_sampled=...; substack.sid=...; ...'"
    echo ""
    echo "Then re-run: bash $0"
    exit 1
fi
echo "  → all 3 env vars set, AUTO_DRAFT=1"
echo ""

# ----------------------------------------------------------------------
# Step 3 + 4: Kick off today's 2 ad-hoc runs in background
# ----------------------------------------------------------------------
mkdir -p logs
TS=$(date +%Y%m%d_%H%M%S)
MORNING_LOG="logs/substack_morning_${TS}.log"
EVENING_LOG="logs/substack_evening_${TS}.log"

echo "[3/5] Triggering morning draft in background..."
nohup .venv/bin/python tools/substack_compose.py morning > "$MORNING_LOG" 2>&1 &
MORNING_PID=$!
echo "  → PID $MORNING_PID  log: $MORNING_LOG"
echo ""

echo "[4/5] Triggering evening draft in background..."
nohup .venv/bin/python tools/substack_compose.py evening > "$EVENING_LOG" 2>&1 &
EVENING_PID=$!
echo "  → PID $EVENING_PID  log: $EVENING_LOG"
echo ""

# ----------------------------------------------------------------------
# Step 5: Install launchd plists for daily 09:00 + 18:00
# ----------------------------------------------------------------------
echo "[5/5] Installing launchd cron (daily 09:00 morning + 18:00 evening)..."
AGENT_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$AGENT_DIR"

MORNING_PLIST="$AGENT_DIR/com.newsradar.substack_morning.plist"
EVENING_PLIST="$AGENT_DIR/com.newsradar.substack_evening.plist"

# Unload existing first (idempotent re-run)
launchctl unload "$MORNING_PLIST" 2>/dev/null || true
launchctl unload "$EVENING_PLIST" 2>/dev/null || true

cat > "$MORNING_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.newsradar.substack_morning</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>cd $REPO &amp;&amp; .venv/bin/python tools/substack_compose.py morning</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$REPO/logs/launchd_morning.log</string>
  <key>StandardErrorPath</key>
  <string>$REPO/logs/launchd_morning.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
EOF

cat > "$EVENING_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.newsradar.substack_evening</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>cd $REPO &amp;&amp; .venv/bin/python tools/substack_compose.py evening</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>18</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$REPO/logs/launchd_evening.log</string>
  <key>StandardErrorPath</key>
  <string>$REPO/logs/launchd_evening.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
EOF

launchctl load -w "$MORNING_PLIST"
launchctl load -w "$EVENING_PLIST"

echo "  → installed: $MORNING_PLIST"
echo "  → installed: $EVENING_PLIST"
echo "  → cron active. Next fire: tomorrow 09:00 (morning) + today 18:00 (evening if not yet passed)."
echo ""

# ----------------------------------------------------------------------
# Final summary
# ----------------------------------------------------------------------
echo "================================================================"
echo "✅ Setup complete."
echo "================================================================"
echo ""
echo "Today's drafts (running NOW in background, ~3-5 min each):"
echo "  Morning  PID $MORNING_PID  →  tail -f $REPO/$MORNING_LOG"
echo "  Evening  PID $EVENING_PID  →  tail -f $REPO/$EVENING_LOG"
echo ""
echo "When you get home, check:"
echo "  https://hsin73.substack.com/publish/drafts"
echo "  ~/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/文件/antigravity_workspace/substack/autogen/$(date +%Y-%m-%d)/"
echo ""
echo "Tomorrow onwards: daily 09:00 morning + 18:00 evening, fully automatic."
echo "To uninstall: launchctl unload ~/Library/LaunchAgents/com.newsradar.substack_{morning,evening}.plist"
echo ""
