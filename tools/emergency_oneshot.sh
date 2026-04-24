#!/usr/bin/env bash
# ============================================================
# News Radar · emergency_oneshot.sh
# ------------------------------------------------------------
# 一條指令跑完整套 emergency 搶發：
#   (1) fetch + extract  (tools/emergency_oneshot.py)
#   (2) real scorer ≥ AUTO_PUBLISH_THRESHOLD 才往下
#   (3) compose_multi_platform + finalize_variant
#   (4) 人工 YES gate
#   (5) publish FB → Threads → IG
#   (6) DB 寫入 drafts / platform_drafts / publish_log
#   (7) scripts/push_state.sh --expect-draft <draft_id>  ← post-condition 驗證
#
# Usage:
#   # (A) 預設：你給 URL，腳本自己 fetch
#   bash tools/emergency_oneshot.sh "https://www.reuters.com/..."
#
#   # (B) URL 抓不到（反爬 / paywall）— 你手邊有 PDF
#   bash tools/emergency_oneshot.sh "https://..." \
#        --pdf ~/Downloads/article.pdf \
#        --title "Meta to capture employee mouse..." \
#        --og-image "https://cloudfront.net/xxx.jpg"
#
#   # (C) 你從網頁複製了正文到 .md 檔
#   bash tools/emergency_oneshot.sh "https://..." \
#        --content-file ./pasted.md --title "..." --og-image "..."
#
#   # (D) 你想發原創構思（沒有原始新聞源）— 用 note:// 這種假 URL 當 id
#   bash tools/emergency_oneshot.sh "note://hsin/2026-04-23/meta-surveillance" \
#        --content-file ./my_brief.md --title "我對這波監控新聞的看法" \
#        --og-image "https://..."
#
#   # 其他選項
#   bash tools/emergency_oneshot.sh "https://..." --dry-run
#   bash tools/emergency_oneshot.sh "https://..." --editorial-note-file ./note.md
#   bash tools/emergency_oneshot.sh "https://..." --force
#
#   # (E) 手機 chat-gate：取代 terminal YES，讓 chat orchestrator 寫 GO 進檔案
#   bash tools/emergency_oneshot.sh "https://..." --pdf ... --og-image "..." \
#        --approve-file /tmp/approve_emerg.txt
#   # 三份 draft 會寫到 data/emergency_last_drafts.json，Python 會 poll 那個檔，
#   # chat 端看完 draft 後：  echo GO > /tmp/approve_emerg.txt  → Python 繼續
#
# Exit codes (同 emergency_oneshot.py 一致；push_state 失敗另設 50)：
#   0  三平台都發成功 + state branch push + sha256 驗證過
#  1-7  emergency_oneshot.py 的錯
#  50  Python 成功但 push_state.sh 失敗（DB 還在本地，沒上雲）
# ============================================================

set -euo pipefail

# ---- 前置：找 repo root -----------------------------------------------------
# 預期有 data/01_harvest/news_radar.db + tools/emergency_oneshot.py
if [[ -f "$(pwd)/tools/emergency_oneshot.py" ]]; then
    REPO_ROOT="$(pwd)"
elif [[ -f "$(dirname "$0")/emergency_oneshot.py" ]]; then
    # 腳本本身的目錄 = tools/
    REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
else
    echo "❌ 找不到 news_radar repo root（cwd=$PWD）" >&2
    echo "   請 cd 到 ~/news_radar 再跑：bash tools/emergency_oneshot.sh \"<URL>\"" >&2
    exit 2
fi

cd "$REPO_ROOT"
echo "[emergency] repo_root = $REPO_ROOT"

# ---- 前置：activate venv ---------------------------------------------------
VENV="$REPO_ROOT/.venv/bin/activate"
if [[ ! -f "$VENV" ]]; then
    echo "❌ 找不到 venv：$VENV" >&2
    echo "   請先建 venv：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 2
fi
# shellcheck disable=SC1090
source "$VENV"
echo "[emergency] venv activated：$(which python)"

# ---- 前置：.env 必要 key 檢查（soft check，不 abort） ---------------------
if [[ -f "$REPO_ROOT/.env" ]]; then
    for k in FB_PAGE_ID FB_PAGE_ACCESS_TOKEN IG_BUSINESS_ACCOUNT_ID IG_ACCESS_TOKEN THREADS_USER_ID THREADS_ACCESS_TOKEN; do
        if ! grep -q "^${k}=" "$REPO_ROOT/.env"; then
            echo "[emergency] ⚠ .env 沒看到 $k（若 publish 失敗先查這邊）"
        fi
    done
else
    echo "[emergency] ⚠ 找不到 .env；publisher 會拿不到 access token，publish 100% 會失敗"
fi

# ---- 參數解析：URL 必須是第一個位置參數 -----------------------------------
if [[ $# -lt 1 || "$1" == --* ]]; then
    echo "❌ 用法：bash tools/emergency_oneshot.sh <URL> [--dry-run] [--editorial-note-file ./note.md] [--force] [--title \"...\"]" >&2
    exit 2
fi
URL="$1"
shift  # 剩下的 $@ 原封不動 pass 給 Python

# ---- Step A：emergency_oneshot.py -----------------------------------------
echo ""
echo "============================================================"
echo " Step A · 跑 tools/emergency_oneshot.py"
echo "============================================================"

set +e
python "$REPO_ROOT/tools/emergency_oneshot.py" --url "$URL" "$@"
PYRC=$?
set -e

if [[ $PYRC -ne 0 ]]; then
    echo ""
    echo "[emergency] ❌ Python 階段失敗 (exit $PYRC)"
    echo "  1 = score < threshold  2 = env/argparse  3 = fetch/extract"
    echo "  4 = composer None      5 = char 超限     6 = 使用者非 YES"
    echo "  7 = 至少一平台 publish 失敗"
    exit $PYRC
fi

# dry-run 時不跑 push_state（DB 沒變動）
# 注意：URL 已 shift 掉，$@ 只剩下選項
for a in "$@"; do
    if [[ "$a" == "--dry-run" ]]; then
        echo ""
        echo "[emergency] ✅ dry-run 完成（DB 沒寫、API 沒打、也不 push_state）"
        exit 0
    fi
done

# ---- Step B：把 draft_id 從 breadcrumb 讀出來 ------------------------------
BREADCRUMB="$REPO_ROOT/tools/.last_emergency_draft_id"
if [[ ! -f "$BREADCRUMB" ]]; then
    echo "[emergency] ⚠ 找不到 $BREADCRUMB；跑 push_state 但不做 --expect-draft 回查"
    DRAFT_ID=""
else
    DRAFT_ID="$(cat "$BREADCRUMB")"
    echo ""
    echo "[emergency] draft_id = $DRAFT_ID  (from breadcrumb)"
fi

# ---- Step C：push_state.sh + post-condition 驗證 ---------------------------
echo ""
echo "============================================================"
echo " Step C · scripts/push_state.sh（推 state branch + sha256 驗）"
echo "============================================================"

set +e
if [[ -n "$DRAFT_ID" ]]; then
    bash "$REPO_ROOT/scripts/push_state.sh" --expect-draft "$DRAFT_ID"
else
    bash "$REPO_ROOT/scripts/push_state.sh"
fi
PSRC=$?
set -e

if [[ $PSRC -ne 0 ]]; then
    echo ""
    echo "[emergency] ❌ push_state.sh 失敗 (exit $PSRC)"
    echo "  DB 已經寫好了、貼文也發出去了，但 state branch 沒對齊；"
    echo "  手動再跑一次：bash scripts/push_state.sh --expect-draft $DRAFT_ID"
    exit 50
fi

echo ""
echo "============================================================"
echo " ✅ 全部完成"
echo "============================================================"
echo "  draft_id          : $DRAFT_ID"
echo "  state branch      : pushed + sha256 驗證過"
echo "  publish_log       : 三筆已 INSERT（查 DB 可見）"
echo ""
echo "  下一步自助查驗："
echo "    sqlite3 data/01_harvest/news_radar.db \\"
echo "      \"SELECT platform, platform_post_id, success FROM publish_log WHERE draft_id='$DRAFT_ID';\""
echo ""
