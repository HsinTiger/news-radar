#!/usr/bin/env bash
# News Radar · 環境修復腳本
# 用途：把 venv 從 OneDrive 搬到本機 ~/.virtualenvs/news_radar，
#       解決 Playwright node driver 被 OneDrive File Provider 卡死的問題。
#
# 用法（在 news_radar/ 目錄下）：
#   bash setup_env.sh
#
# 這個腳本會做六件事：
#   1. 檢查系統是 macOS 且有 python3
#   2. 備份並刪除 OneDrive 裡的 .venv
#   3. 建立新 venv 到 ~/.virtualenvs/news_radar
#   4. 安裝 requirements.txt 與必要套件
#   5. 下載 Chromium (playwright install)
#   6. 建 symlink 讓 .venv/bin/python 仍可用
#
# 跑完後用這條測試：
#   .venv/bin/python diagnose_playwright.py
#   .venv/bin/python src/competitor_agent.py --login

set -u  # 不用 -e：遇錯我們要印清楚訊息，而不是靜默退出

# ---- 顏色輸出 ----
if [[ -t 1 ]]; then
    C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'; C_RED='\033[0;31m'; C_BOLD='\033[1m'; C_OFF='\033[0m'
else
    C_GREEN=''; C_YELLOW=''; C_RED=''; C_BOLD=''; C_OFF=''
fi
log()  { echo -e "${C_BOLD}[$(date +%H:%M:%S)]${C_OFF} $*"; }
ok()   { echo -e "${C_GREEN}✅ $*${C_OFF}"; }
warn() { echo -e "${C_YELLOW}⚠️  $*${C_OFF}"; }
fail() { echo -e "${C_RED}❌ $*${C_OFF}"; exit 1; }

# ---- 可調整路徑 ----
TARGET_VENV="${HOME}/.virtualenvs/news_radar"
LINK_NAME=".venv"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log "專案目錄: ${PROJECT_DIR}"
log "目標 venv: ${TARGET_VENV}"

# ---- 1. 系統檢查 ----
log "── 步驟 1/6：系統檢查 ──"
[[ "$(uname -s)" == "Darwin" ]] || fail "這個腳本只適用 macOS。偵測到：$(uname -s)"
command -v python3 >/dev/null || fail "找不到 python3。請先安裝：brew install python"
PY_VER=$(python3 --version 2>&1)
log "系統 python3：${PY_VER}"
[[ "$(uname -m)" == "arm64" ]] && log "架構：Apple Silicon (arm64)" || log "架構：$(uname -m)"
ok "系統 OK"

# ---- 2. 備份並刪除 OneDrive 裡的 .venv ----
log "── 步驟 2/6：清理舊 venv ──"
cd "${PROJECT_DIR}" || fail "無法 cd 到專案目錄"

if [[ -L "${LINK_NAME}" ]]; then
    log "偵測到 ${LINK_NAME} 是 symlink，先移除"
    rm "${LINK_NAME}" && ok "舊 symlink 已移除"
elif [[ -d "${LINK_NAME}" ]]; then
    SIZE=$(du -sh "${LINK_NAME}" 2>/dev/null | awk '{print $1}')
    log "發現 ${LINK_NAME} 目錄 (${SIZE})，開始刪除（僅虛擬環境，非你的程式碼）..."
    rm -rf "${LINK_NAME}" || fail "刪除 ${LINK_NAME} 失敗"
    ok "舊 .venv 已刪除"
else
    log "沒有舊的 .venv，跳過"
fi

# ---- 3. 建立新 venv 到本機 ----
log "── 步驟 3/6：建立新 venv 到 ${TARGET_VENV} ──"
mkdir -p "$(dirname "${TARGET_VENV}")" || fail "無法建立 ~/.virtualenvs/"

if [[ -d "${TARGET_VENV}" ]]; then
    warn "${TARGET_VENV} 已存在。"
    read -r -p "要砍掉重建嗎？(y/N) " ans
    if [[ "${ans:-N}" =~ ^[Yy]$ ]]; then
        rm -rf "${TARGET_VENV}" || fail "刪除失敗"
        python3 -m venv "${TARGET_VENV}" || fail "建立 venv 失敗"
        ok "重建完成"
    else
        log "保留現有 venv，只更新套件"
    fi
else
    python3 -m venv "${TARGET_VENV}" || fail "建立 venv 失敗"
    ok "venv 建立完成"
fi

PIP="${TARGET_VENV}/bin/pip"
PY="${TARGET_VENV}/bin/python"

# ---- 4. 安裝套件 ----
log "── 步驟 4/6：升級 pip 與安裝依賴 ──"
"${PIP}" install --upgrade pip setuptools wheel >/tmp/pip_setup.log 2>&1 \
    && ok "pip 升級完成" \
    || { warn "pip 升級出錯，見 /tmp/pip_setup.log（可忽略繼續）"; }

if [[ -f "${PROJECT_DIR}/requirements.txt" ]]; then
    log "安裝 requirements.txt ..."
    "${PIP}" install -r "${PROJECT_DIR}/requirements.txt" 2>&1 | tail -20
    ok "requirements.txt 安裝完畢"
else
    warn "找不到 requirements.txt，將只裝 Playwright 相關套件"
fi

log "確保關鍵套件齊全（playwright / python-dotenv / google-generativeai）..."
"${PIP}" install playwright python-dotenv google-generativeai 2>&1 | tail -10
ok "關鍵套件 OK"

# ---- 5. 下載 Chromium ----
log "── 步驟 5/6：下載 Chromium（首次可能需要 1-3 分鐘） ──"
"${PY}" -m playwright install chromium 2>&1 | tail -20
if [[ ${PIPESTATUS[0]} -eq 0 ]]; then
    ok "Chromium 安裝完成"
else
    warn "playwright install chromium 回傳非 0，但可能之前已裝過——照樣往下跑，等等診斷會確認"
fi

# ---- 6. 建立 symlink ----
log "── 步驟 6/6：建立 .venv symlink ──"
cd "${PROJECT_DIR}" || fail "無法 cd 回專案目錄"
if [[ -e "${LINK_NAME}" || -L "${LINK_NAME}" ]]; then
    rm -f "${LINK_NAME}"
fi
ln -s "${TARGET_VENV}" "${LINK_NAME}" || fail "建立 symlink 失敗"
ok "已建立 ${LINK_NAME} → ${TARGET_VENV}"

# ---- 收尾 ----
echo ""
echo "════════════════════════════════════════════════════════════"
ok "環境修復完成！"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "現在請依序執行："
echo ""
echo -e "  ${C_BOLD}1) 診斷 Playwright 是否啟動正常${C_OFF}"
echo "     .venv/bin/python diagnose_playwright.py"
echo ""
echo -e "  ${C_BOLD}2) 開瀏覽器手動登入 FB（首次）${C_OFF}"
echo "     .venv/bin/python src/competitor_agent.py --login"
echo ""
echo -e "  ${C_BOLD}3) 正式抓三位 KOL${C_OFF}"
echo "     .venv/bin/python src/competitor_agent.py"
echo ""
