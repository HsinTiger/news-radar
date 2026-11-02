#!/bin/bash
# 設定寫稿失敗時的 Gmail 通知（2026-11-03：之前只有 Mac 通知，一週沒稿都沒人發現）。
# 用法：先到 Google 帳號產生「應用程式密碼」並複製，再執行：
#   bash scripts/save_gmail_notify.sh 你的Gmail地址
# 密碼從剪貼簿讀，不會印出來。
set -euo pipefail
cd "$(dirname "$0")/.."
ADDR="${1:?請在後面加上 Gmail 地址}"
PW="$(pbpaste | tr -d '[:space:]')"
[[ ${#PW} -eq 16 && "$PW" =~ ^[a-z]+$ ]] || { echo "剪貼簿不像 Google 應用程式密碼（應該是 16 個英文小寫字母）"; exit 1; }
grep -vE '^(GMAIL_APP_PASSWORD|SUBSTACK_NOTIFY_EMAIL|SUBSTACK_NOTIFY_CHANNEL)=' .env > .env.tmp || true
{ echo "GMAIL_APP_PASSWORD=$PW"; echo "SUBSTACK_NOTIFY_EMAIL=$ADDR"; echo "SUBSTACK_NOTIFY_CHANNEL=both"; } >> .env.tmp
mv .env.tmp .env; chmod 600 .env
.venv/bin/python -c "from dotenv import load_dotenv; load_dotenv('.env', override=True); from src.notify import notify_test; print('測試信', '已寄出' if notify_test() else '寄送失敗')"
