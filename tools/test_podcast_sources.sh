#!/bin/bash
# ============================================================
# News Radar · Test All Podcast YouTube Sources
# ============================================================
# Tests all 40+ YouTube channels in substack_podcast_sources.yaml
# for:
#   (1) URL resolution (yt-dlp can list videos)
#   (2) Subtitle availability for recent videos
#   (3) Approximate transcript length
#
# Usage:
#   bash tools/test_podcast_sources.sh
#   bash tools/test_podcast_sources.sh --quick   # test first video only per source
# ============================================================

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SOURCES_YAML="$REPO/substack_radar/config/substack_podcast_sources.yaml"
LOG_DIR="$REPO/logs/podcast_test"
mkdir -p "$LOG_DIR"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo ""
echo "============================================================"
echo " 🎙️  News Radar · Podcast Source Test"
echo "============================================================"
echo ""

# ---- Parse YAML sources ----
echo "[1/3] Parsing $SOURCES_YAML..."
URLS=()
TOPICS=()
while IFS= read -r line; do
  if [[ "$line" =~ ^[[:space:]]*-\ url:\ (.*) ]]; then
    URLS+=("${BASH_REMATCH[1]}")
  elif [[ "$line" =~ ^[[:space:]]*topic_category:\ (.*) ]]; then
    TOPICS+=("${BASH_REMATCH[1]}")
  fi
done < "$SOURCES_YAML"

TOTAL=${#URLS[@]}
echo "  Found $TOTAL sources"
echo ""

# ---- Test each source ----
echo "[2/3] Testing sources..."
echo ""

RESULTS_FILE="$LOG_DIR/results.json"
echo "[" > "$RESULTS_FILE"
FIRST=true
PASS=0
FAIL=0
WARN=0

for i in "${!URLS[@]}"; do
  URL="${URLS[$i]}"
  TOPIC="${TOPICS[$i]:-other}"
  SOURCE_NUM=$((i + 1))

  echo -ne "  [${SOURCE_NUM}/${TOTAL}] Testing: ${URL} ... "

  # Step A: Get the latest video ID
  LATEST_ID=$(yt-dlp --flat-playlist -I 1:1 --no-warnings --print "%(id)s" "$URL" 2>"$LOG_DIR/err_${i}.txt" || echo "")

  if [ -z "$LATEST_ID" ]; then
    echo -e "${RED}✗ FETCH FAIL${NC}"
    echo "    └ URL doesn't resolve or no videos found"
    FAIL=$((FAIL + 1))
    # Write JSON result
    if [ "$FIRST" = false ]; then echo "," >> "$RESULTS_FILE"; fi
    echo "{\"index\":$i,\"url\":\"$URL\",\"topic\":\"$TOPIC\",\"status\":\"FETCH_FAIL\",\"video_id\":null,\"has_subs\":null,\"transcript_chars\":null,\"error\":\"URL does not resolve\"}" >> "$RESULTS_FILE"
    FIRST=false
    continue
  fi

  VIDEO_URL="https://www.youtube.com/watch?v=${LATEST_ID}"
  echo -e "${CYAN}id=${LATEST_ID}${NC}"

  # Step B: Check subtitle availability by trying to list available subs
  SUB_CHECK=$(yt-dlp --skip-download --list-subs --no-warnings "$VIDEO_URL" 2>"$LOG_DIR/subs_err_${i}.txt" || echo "")
  HAS_SUBS=false
  SUB_LANG=""

  if echo "$SUB_CHECK" | grep -qi "Available subtitles\|has automatic captions\|vtt\|en\."; then
    HAS_SUBS=true
    SUB_LANG=$(echo "$SUB_CHECK" | grep -E "^\s*(en|zh|ja)\s" | head -1 | awk '{print $1}' || echo "en")
    echo -e "    └ ${GREEN}Subtitles available (lang: ${SUB_LANG:-en})${NC}"
  else
    echo -e "    └ ${YELLOW}⚠ No subtitles found${NC}"
    WARN=$((WARN + 1))
    if [ "$FIRST" = false ]; then echo "," >> "$RESULTS_FILE"; fi
    echo "{\"index\":$i,\"url\":\"$URL\",\"topic\":\"$TOPIC\",\"status\":\"NO_SUBS\",\"video_id\":\"$LATEST_ID\",\"has_subs\":false,\"transcript_chars\":null,\"error\":\"No subtitles\"}" >> "$RESULTS_FILE"
    FIRST=false
    continue
  fi

  # Step C: Try to download subtitles and measure
  TRANS_DIR=$(mktemp -d)
  if yt-dlp --skip-download --write-auto-subs --sub-lang "${SUB_LANG:-en}" --convert-subs srt \
    -o "${TRANS_DIR}/%(id)s" --no-warnings "$VIDEO_URL" >/dev/null 2>"$LOG_DIR/trans_err_${i}.txt"; then
    TRANS_FILE=$(find "$TRANS_DIR" -name "*.${LATEST_ID}*.srt" -o -name "*.${LATEST_ID}*.vtt" 2>/dev/null | head -1)
    if [ -n "$TRANS_FILE" ] && [ -f "$TRANS_FILE" ]; then
      CHARS=$(wc -c < "$TRANS_FILE")
      WORDS=$(wc -w < "$TRANS_FILE")
      rm -rf "$TRANS_DIR"
      if [ "$CHARS" -gt 3000 ]; then
        echo -e "    └ ${GREEN}✅ ${CHARS} chars (${WORDS} words)${NC}"
        PASS=$((PASS + 1))
        STATUS="OK"
      else
        echo -e "    └ ${YELLOW}⚠ Too short: ${CHARS} chars (< 3000)${NC}"
        WARN=$((WARN + 1))
        STATUS="SHORT_TRANSCRIPT"
      fi
      if [ "$FIRST" = false ]; then echo "," >> "$RESULTS_FILE"; fi
      echo "{\"index\":$i,\"url\":\"$URL\",\"topic\":\"$TOPIC\",\"status\":\"$STATUS\",\"video_id\":\"$LATEST_ID\",\"has_subs\":true,\"transcript_chars\":$CHARS,\"error\":null}" >> "$RESULTS_FILE"
    else
      rm -rf "$TRANS_DIR"
      echo -e "    └ ${YELLOW}⚠ Transcript file not found after download${NC}"
      WARN=$((WARN + 1))
      if [ "$FIRST" = false ]; then echo "," >> "$RESULTS_FILE"; fi
      echo "{\"index\":$i,\"url\":\"$URL\",\"topic\":\"$TOPIC\",\"status\":\"TRANS_FILE_MISSING\",\"video_id\":\"$LATEST_ID\",\"has_subs\":true,\"transcript_chars\":null,\"error\":\"File not found after download\"}" >> "$RESULTS_FILE"
    fi
  else
    rm -rf "$TRANS_DIR"
    echo -e "    └ ${YELLOW}⚠ Subtitle download failed${NC}"
    WARN=$((WARN + 1))
    if [ "$FIRST" = false ]; then echo "," >> "$RESULTS_FILE"; fi
    echo "{\"index\":$i,\"url\":\"$URL\",\"topic\":\"$TOPIC\",\"status\":\"DOWNLOAD_FAIL\",\"video_id\":\"$LATEST_ID\",\"has_subs\":true,\"transcript_chars\":null,\"error\":\"Download failed\"}" >> "$RESULTS_FILE"
  fi
  FIRST=false
done

echo "]" >> "$RESULTS_FILE"

# ---- Summary ----
echo ""
echo "============================================================"
echo " 📊 Results"
echo "============================================================"
echo ""
echo -e "  ${GREEN}✅ Pass:  ${PASS}${NC}"
echo -e "  ${YELLOW}⚠ Warn:  ${WARN}${NC}"
echo -e "  ${RED}✗ Fail:  ${FAIL}${NC}"
echo "  ─────────────────"
echo -e "  ${CYAN}Total:  ${TOTAL}${NC}"
echo ""

# Print failures
if [ "$FAIL" -gt 0 ]; then
  echo -e "${RED}--- Failed Sources ---${NC}"
  python3 -c "
import json
data = json.load(open('$RESULTS_FILE'))
for d in data:
    if d['status'] == 'FETCH_FAIL':
        print(f\"  ✗ {d['url']} — {d['error']}\")
" 2>/dev/null || echo "  (unable to parse results)"
  echo ""
fi

# Print warnings
if [ "$WARN" -gt 0 ]; then
  echo -e "${YELLOW}--- Sources with Issues ---${NC}"
  python3 -c "
import json
data = json.load(open('$RESULTS_FILE'))
for d in data:
    if d['status'] in ('NO_SUBS', 'SHORT_TRANSCRIPT', 'TRANS_FILE_MISSING', 'DOWNLOAD_FAIL'):
        detail = d.get('error') or f\"{d.get('transcript_chars', '?')} chars\"
        print(f\"  ⚠ {d['url']} — [{d['status']}] {detail}\")
" 2>/dev/null || echo "  (unable to parse results)"
  echo ""
fi

# Print working sources summary
echo -e "${GREEN}--- Working Sources (${PASS}/${TOTAL}) ---${NC}"
python3 -c "
import json
data = json.load(open('$RESULTS_FILE'))
for d in data:
    if d['status'] == 'OK':
        print(f\"  ✅ {d['url']} — {d['topic']} ({d['transcript_chars']} chars)\")
" 2>/dev/null || echo "  (unable to parse results)"

echo ""
echo "Tests ran from: $LOG_DIR/"
echo "Full JSON: $RESULTS_FILE"
echo ""

# Exit code
if [ "$FAIL" -gt 0 ]; then
  exit 1
elif [ "$PASS" -eq 0 ]; then
  exit 2
fi

exit 0
