#!/bin/bash
# 更新 Substack 登入 cookie（2026-10-09：cookie 過期讓草稿推送全部 401，一週沒有新草稿）。
#
# 用法：
#   1. Chrome 打開 https://hsin73.substack.com/publish/home（確認已登入）
#   2. 按 ⌥⌘I 開開發者工具 → Network 分頁 → 重新整理頁面 → 點第一筆請求
#      → Headers → Request Headers → 找到「cookie:」→ 對它的值按右鍵 Copy value
#   3. 執行：bash ~/news_radar/scripts/update_substack_cookie.sh
#
# cookie 只從剪貼簿讀、只寫進 .env（已被 gitignore），全程不印出來。
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO/.env"

COOKIE="$(pbpaste | tr -d '\r\n')"
if [[ "$COOKIE" != *"substack.sid="* ]]; then
  echo "❌ 剪貼簿裡沒有 substack.sid。請照檔頭步驟重新複製 cookie 的值再執行。"
  exit 1
fi

COOKIE="$COOKIE" "$REPO/.venv/bin/python" - "$ENV_FILE" <<'PY'
import os, sys
path = sys.argv[1]
cookie = os.environ["COOKIE"].replace("'", "")
lines = open(path, encoding="utf-8").read().splitlines()
out, done = [], False
for line in lines:
    if line.startswith("SUBSTACK_COOKIES_STRING="):
        out.append(f"SUBSTACK_COOKIES_STRING='{cookie}'")
        done = True
    else:
        out.append(line)
if not done:
    out.append(f"SUBSTACK_COOKIES_STRING='{cookie}'")
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
print("✅ 已寫入 .env")
PY

# 立刻驗證：用新 cookie 讀一次自己的帳號
"$REPO/.venv/bin/python" - <<PY
import os, sys
from dotenv import load_dotenv
load_dotenv("$ENV_FILE", override=True)
from substack import Api
try:
    api = Api(cookies_string=os.environ["SUBSTACK_COOKIES_STRING"],
              publication_url=os.environ["SUBSTACK_PUBLICATION_URL"])
    api.get_user_id()
    api.get_drafts(limit=1)
    print("✅ Substack 登入成功，草稿推送已恢復")
except Exception as exc:
    print(f"❌ 還是登入不了：{str(exc)[:120]}")
    sys.exit(2)
PY

# 登入恢復了，把「登入失效」的暫停標記拿掉，排程下一輪就會照常寫稿
rm -f "$REPO/data/substack_drafts/.substack_auth_failed"
echo "✅ 完成。不用重開任何東西，下一輪排程就會照常推草稿。"
