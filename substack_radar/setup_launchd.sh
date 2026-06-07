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
#   4. Kick off today's PODCAST + EVENING drafts in background
#   5. Install macOS launchd cron for daily 08:00 · 13:00 · 13:30 · 17:00 starting tomorrow
#
# Safe to re-run: idempotent. Existing launchd jobs get replaced.
set -e

REPO="$HOME/news_radar"
cd "$REPO"

echo "================================================================"
echo "News Radar · Substack daily-4-draft pipeline setup"
echo "================================================================"
echo ""

# ----------------------------------------------------------------------
# Step 1: python-substack
# ----------------------------------------------------------------------
echo "[1/6] Checking python-substack..."
if .venv/bin/python -c "import substack" 2>/dev/null; then
    echo "  → already installed"
else
    echo "  → installing (from requirements-mac.txt)..."
    if [ -f requirements-mac.txt ]; then
        .venv/bin/pip install -q -r requirements-mac.txt
    else
        # Legacy fallback if requirements-mac.txt is missing
        .venv/bin/pip install -q "python-substack>=0.1.18"
    fi
    echo "  → installed"
fi
echo ""

# ----------------------------------------------------------------------
# Step 2: .env sanity check
# ----------------------------------------------------------------------
echo "[2/6] Checking .env..."
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
PODCAST1_LOG="logs/substack_podcast1_${TS}.log"
PODCAST2_LOG="logs/substack_podcast2_${TS}.log"
EVENING_LOG="logs/substack_evening_${TS}.log"

echo "[3/6] Triggering morning draft in background..."
nohup .venv/bin/python substack_radar/compose.py morning --harvest > "$MORNING_LOG" 2>&1 &
MORNING_PID=$!
echo "  → PID $MORNING_PID  log: $MORNING_LOG"
echo ""

echo "[3b/6] Triggering podcast draft #1 (with harvest) in background..."
nohup .venv/bin/python substack_radar/compose.py podcast --harvest > "$PODCAST1_LOG" 2>&1 &
PODCAST1_PID=$!
echo "  → PID $PODCAST1_PID  log: $PODCAST1_LOG"
echo ""

echo "[3c/6] Triggering podcast draft #2 (no harvest, picks next unused) in background..."
# Second podcast draft runs 5 min later to let #1 finish harvesting + composing
(sleep 300 && nohup .venv/bin/python substack_radar/compose.py podcast > "$PODCAST2_LOG" 2>&1) &
PODCAST2_PID=$!
echo "  → PID $PODCAST2_PID  log: $PODCAST2_LOG (delayed 5 min)"
echo ""

echo "[4/6] Triggering evening draft in background..."
nohup .venv/bin/python substack_radar/compose.py evening --harvest > "$EVENING_LOG" 2>&1 &
EVENING_PID=$!
echo "  → PID $EVENING_PID  log: $EVENING_LOG"
echo ""

# ----------------------------------------------------------------------
# Step 5: Install launchd plists for daily 09:00 + 18:00
# ----------------------------------------------------------------------
echo "[5/6] Installing launchd cron (daily 09:00 morning + 18:00 evening)..."
AGENT_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$AGENT_DIR"

MORNING_PLIST="$AGENT_DIR/com.newsradar.substack_morning.plist"
EVENING_PLIST="$AGENT_DIR/com.newsradar.substack_evening.plist"
PODCAST_PLIST="$AGENT_DIR/com.newsradar.substack_podcast.plist"
PODCAST2_PLIST="$AGENT_DIR/com.newsradar.substack_podcast2.plist"

# Unload existing first (idempotent re-run)
launchctl unload "$MORNING_PLIST" 2>/dev/null || true
launchctl unload "$EVENING_PLIST" 2>/dev/null || true
launchctl unload "$PODCAST_PLIST" 2>/dev/null || true
launchctl unload "$PODCAST2_PLIST" 2>/dev/null || true

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
    <string>-c</string>
    <string>cd $REPO &amp;&amp; .venv/bin/python -u substack_radar/compose.py morning --harvest</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>8</integer>
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
    <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key>
    <string>$HOME</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
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
    <string>-c</string>
    <string>cd $REPO &amp;&amp; .venv/bin/python -u substack_radar/compose.py evening --harvest</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>17</integer>
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
    <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key>
    <string>$HOME</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
</dict>
</plist>
EOF

cat > "$PODCAST_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.newsradar.substack_podcast</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>cd $REPO &amp;&amp; .venv/bin/python -u substack_radar/compose.py podcast --harvest</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>13</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$REPO/logs/launchd_podcast.log</string>
  <key>StandardErrorPath</key>
  <string>$REPO/logs/launchd_podcast.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key>
    <string>$HOME</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
</dict>
</plist>
EOF

cat > "$PODCAST2_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.newsradar.substack_podcast2</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>cd $REPO &amp;&amp; .venv/bin/python -u substack_radar/compose.py podcast</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>13</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$REPO/logs/launchd_podcast2.log</string>
  <key>StandardErrorPath</key>
  <string>$REPO/logs/launchd_podcast2.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key>
    <string>$HOME</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
</dict>
</plist>
EOF

launchctl load -w "$MORNING_PLIST"
launchctl load -w "$EVENING_PLIST"
launchctl load -w "$PODCAST_PLIST"
launchctl load -w "$PODCAST2_PLIST"

echo "  → installed: $MORNING_PLIST"
echo "  → installed: $EVENING_PLIST"
echo "  → installed: $PODCAST_PLIST"
echo "  → installed: $PODCAST2_PLIST"
echo "  → cron active. Daily fire: 08:00 morning · 13:00 podcast×2 · 17:00 evening."
echo ""

# ----------------------------------------------------------------------
# Step 6: Notify channel verification (2026-05-13)
# ----------------------------------------------------------------------
echo "[6/6] Notify channel test..."
NOTIFY_CH=$(grep "^SUBSTACK_NOTIFY_CHANNEL=" .env 2>/dev/null | cut -d= -f2 | tr -d '"')
if [ -z "$NOTIFY_CH" ] || [ "$NOTIFY_CH" = "none" ]; then
    echo "  ⚠️ SUBSTACK_NOTIFY_CHANNEL not set → 跑完不會自動通知你"
    echo ""
    echo "  要打開的話, 在 .env 加："
    echo "    SUBSTACK_NOTIFY_CHANNEL=gmail   # 或 macos / both"
    echo "    SUBSTACK_NOTIFY_EMAIL=hsin290525@gmail.com"
    echo "    GMAIL_APP_PASSWORD=<16-字 app password>"
    echo ""
    echo "  取得 App Password: https://myaccount.google.com/apppasswords"
    echo "  （Google 帳號 → 2FA → App passwords → 給此 app 取名「news_radar」"
    echo "    → 複製 16 字字串貼進 .env）"
else
    echo "  → channel=$NOTIFY_CH, sending test ping..."
    if .venv/bin/python -m src.notify; then
        echo "  ✅ test 已送出。檢查手機 Gmail 看有沒有收到「[Substack 🧪] notify channel 測試 OK」"
    else
        echo "  ⚠️ test 出錯。常見原因："
        echo "     - Gmail App Password 不對（拿一般密碼塞進去不會成功）"
        echo "     - 寄件 Gmail 帳號還沒開 2FA (App Password 必須先 2FA)"
        echo "     - GMAIL_APP_PASSWORD 環境變數沒設"
    fi
fi
echo ""

# ----------------------------------------------------------------------
# Final summary
# ----------------------------------------------------------------------
echo "================================================================"
echo "✅ Setup complete."
echo "================================================================"
echo ""
echo "Today's drafts (running NOW in background, ~3-5 min each):"
echo "  Morning   PID $MORNING_PID  →  tail -f $REPO/$MORNING_LOG"
echo "  Podcast 1 PID $PODCAST1_PID  →  tail -f $REPO/$PODCAST1_LOG"
echo "  Podcast 2 PID $PODCAST2_PID  →  tail -f $REPO/$PODCAST2_LOG (delayed 5 min)"
echo "  Evening   PID $EVENING_PID  →  tail -f $REPO/$EVENING_LOG"
echo ""
echo "When you get home, check:"
echo "  https://hsin73.substack.com/publish/drafts"
echo "  ~/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/文件/antigravity_workspace/substack/autogen/$(date +%Y-%m-%d)/"
echo ""
echo "Tomorrow onwards: daily 08:00 morning · 13:00 podcast×2 · 17:00 evening, fully automatic."
echo "To uninstall: launchctl unload ~/Library/LaunchAgents/com.newsradar.substack_{morning,evening,podcast,podcast2}.plist"
echo ""
