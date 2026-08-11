#!/usr/bin/env bash
# Canonical scheduled Substack editorial worker.
# One two-article Podcast batch or one Weekly company draft; draft-only, never publish.

set -uo pipefail

PROFILE="${1:-}"
case "$PROFILE" in
  podcast-batch|weekly) ;;
  *) echo "usage: $0 podcast-batch|weekly"; exit 2 ;;
esac

REPO="${REPO:-HsinTiger/news-radar}"
LOCAL_REPO="${LOCAL_REPO:-$HOME/news_radar}"
PY="$LOCAL_REPO/.venv/bin/python"
LOCAL_LOCK="$LOCAL_REPO/.runtime-state-local.lock.d"
LEASE_FILE="$LOCAL_REPO/.runtime-state-editorial-lease.json"
LEASED=0
LOCAL_LOCKED=0

cleanup() {
  if [ "$LEASED" = "1" ] && [ -x "$PY" ]; then
    "$PY" "$LOCAL_REPO/scripts/state_store.py" unlock \
      --repo "$REPO" --lease-file "$LEASE_FILE" || true
  fi
  if [ "$LOCAL_LOCKED" = "1" ]; then
    rmdir "$LOCAL_LOCK" 2>/dev/null || true
  fi
}
trap cleanup EXIT

cd "$LOCAL_REPO" 2>/dev/null || { echo "[editorial] repo missing: $LOCAL_REPO"; exit 3; }
if [ ! -x "$PY" ]; then
  echo "[editorial] venv missing; run news_radar_compose.sh --setup-only first"
  exit 3
fi

if ! mkdir "$LOCAL_LOCK" 2>/dev/null; then
  echo "[editorial] another local state writer is active; skip"
  exit 0
fi
LOCAL_LOCKED=1

# fetch 與 merge 分開判斷。原本兩者共用一句「cannot be fast-forwarded」，
# 於是 2026-08-11 中午 GitHub 連不上時，日誌宣稱是合併問題——實際 merge
# 是 Already up to date，掛的是 fetch。那句話讓診斷往錯的方向走了一輪。
# fetch 失敗不再 fail closed。2026-08-11 中午整批沒產文，根因是本機臨時埠
# 被耗盡（TIME_WAIT 24,769 > 埠池 16,384），connect() 對 github.com 立即回
# EADDRNOTAVAIL；流量一停約 30 秒就自己退乾淨。為了一次半小時的網路抖動
# 賠掉一整天的稿子並不划算——而且寫稿這件事根本不需要 GitHub。
# 所以：重試三次，仍失敗就帶著警告照常產文，只是不更新程式碼。
FETCH_OK=0
for attempt in 1 2 3; do
  if git fetch --quiet origin main; then
    FETCH_OK=1
    break
  fi
  echo "[editorial] git fetch 第 ${attempt}/3 次失敗"
  [ "$attempt" -lt 3 ] && sleep 30
done
if [ "$FETCH_OK" -ne 1 ]; then
  echo "[editorial] ⚠️ git fetch 三次都失敗；跳過程式碼更新，改用目前本地版本繼續產文"
  echo "[editorial] 診斷：curl -I https://github.com；若 connect 立即失敗，查 netstat -an -p tcp | grep -c TIME_WAIT"
  osascript -e 'display notification "連不上 GitHub，改用本地版本產文" with title "News Radar"' 2>/dev/null || true
fi
# 分歧是真的該停：本地有人動過、或歷史對不上，硬跑會產出說不清來源的稿子。
# 這跟上面的網路抖動不同層級，所以維持 fail closed。
if [ "$FETCH_OK" -eq 1 ] && ! git merge --ff-only origin/main >/dev/null 2>&1; then
  echo "[editorial] current main cannot be fast-forwarded; fail closed"
  git status --short
  osascript -e 'display notification "排程未產文：本地 main 無法快進" with title "News Radar"' 2>/dev/null || true
  exit 4
fi

"$PY" scripts/state_store.py lock --repo "$REPO" \
  --producer "mac:$(hostname -s):substack-editorial-$PROFILE" \
  --lease-file "$LEASE_FILE" --lease-seconds 7200 --wait-seconds 1800 || exit 5
LEASED=1
"$PY" scripts/state_store.py pull --repo "$REPO" --root . || exit 6

if [ "$PROFILE" = "podcast-batch" ]; then
  "$PY" -u substack_radar/compose.py podcast --harvest --editorial-profile weekly --require-substack-draft
  FIRST_EXIT=$?
  "$PY" -u substack_radar/compose.py podcast --editorial-profile weekly --require-substack-draft
  SECOND_EXIT=$?
  if [ "$FIRST_EXIT" -ne 0 ] || [ "$SECOND_EXIT" -ne 0 ]; then
    COMPOSE_EXIT=1
  else
    COMPOSE_EXIT=0
  fi
else
  "$PY" scripts/pick_company_candidate.py
  PICK_EXIT=$?
  if [ "$PICK_EXIT" -ne 0 ] && [ "$PICK_EXIT" -ne 1 ]; then
    echo "[editorial] company picker warning exit=$PICK_EXIT; compose will use its configured fallback"
  fi
  "$PY" -u substack_radar/compose.py company --editorial-profile weekly --require-substack-draft
  COMPOSE_EXIT=$?
fi

# Push even after a compose failure: a remote draft may exist while its durable
# receipt/evidence still needs to be carried forward. Idempotent receipt
# reconciliation prevents the next run from calling post_draft twice.
"$PY" scripts/state_store.py push --repo "$REPO" --root . \
  --producer "mac_substack_editorial_${PROFILE}:$(hostname -s):$(date -u +%Y%m%dT%H%M%SZ)" \
  --lease-file "$LEASE_FILE"
PUSH_EXIT=$?

if [ "$COMPOSE_EXIT" -ne 0 ] || [ "$PUSH_EXIT" -ne 0 ]; then
  exit 1
fi
echo "[editorial] $PROFILE draft completed and canonical state read back"
