#!/usr/bin/env bash
# Canonical scheduled Substack editorial worker.
# Draft-only, never publish（發布由 substack_radar/daily_publish.py 在 07:30 負責）。
#   2026-10-18 起一天只寫一篇（信哥：不再多寫備案稿），寫不好靠審稿退稿重寫＋當天重試：
#   podcast-first   吹牛免稅（含抓新素材）：週一三五 06:00，10:00 重試
#   ai-first        智取未來（含抓 AI 來源池）：週二、四 06:00，10:00 重試
#   weekly          賺錢有道：週六、日 06:00，13:00 重試
#   podcast-second  舊的第二篇（排程已停用，保留相容）
#   podcast-batch   舊的一次兩篇（保留相容，排程已不用）

set -uo pipefail

PROFILE="${1:-}"
case "$PROFILE" in
  podcast-batch|podcast-first|podcast-second|ai-first|weekly) ;;
  *) echo "usage: $0 podcast-first|podcast-second|ai-first|weekly|podcast-batch"; exit 2 ;;
esac

REPO="${REPO:-HsinTiger/news-radar}"
LOCAL_REPO="${LOCAL_REPO:-$HOME/news_radar}"
PY="$LOCAL_REPO/.venv/bin/python"
LOCAL_LOCK="$LOCAL_REPO/.runtime-state-local.lock.d"
WANT_FILE="$LOCAL_REPO/.runtime-state-editorial-want"
LEASE_FILE="$LOCAL_REPO/.runtime-state-editorial-lease.json"
LOCK_WAIT_SECONDS="${LOCK_WAIT_SECONDS:-10800}"   # 3 小時（2026-10-17：NIM 改能力優先、不管速度，一篇可能寫 2 小時以上）
LEASED=0
LOCAL_LOCKED=0
WANTED=0

cleanup() {
  if [ "$LEASED" = "1" ] && [ -x "$PY" ]; then
    "$PY" "$LOCAL_REPO/scripts/state_store.py" unlock \
      --repo "$REPO" --lease-file "$LEASE_FILE" || true
  fi
  if [ "$LOCAL_LOCKED" = "1" ]; then
    rm -rf "$LOCAL_LOCK" 2>/dev/null || true
  fi
  if [ "$WANTED" = "1" ]; then
    rm -f "$WANT_FILE" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# 鎖裡留一份 PID，等待方才分得出「真的有人在跑」跟「上一輪被 kill 留下的殘骸」。
# 舊版腳本留下的鎖沒有 pid，那種只能靠時間判定（2 小時 = 遠端 lease 上限）。
lock_acquire() {
  [ -n "$LOCAL_LOCK" ] || return 1
  if mkdir "$LOCAL_LOCK" 2>/dev/null; then
    echo $$ > "$LOCAL_LOCK/pid" 2>/dev/null || true
    return 0
  fi
  local owner
  owner="$(cat "$LOCAL_LOCK/pid" 2>/dev/null || true)"
  if [ -n "$owner" ]; then
    kill -0 "$owner" 2>/dev/null && return 1
    echo "[editorial] 鎖的持有者 pid=$owner 已不存在，回收殘留鎖"
  elif [ -n "$(find "$LOCAL_LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    echo "[editorial] 無主鎖且已超過 120 分鐘，回收殘留鎖"
  else
    return 1
  fi
  rm -rf "$LOCAL_LOCK" 2>/dev/null || true
  if mkdir "$LOCAL_LOCK" 2>/dev/null; then
    echo $$ > "$LOCAL_LOCK/pid" 2>/dev/null || true
    return 0
  fi
  return 1
}

# 今天這個專欄是否已有「通過事實稽核」的稿（一天一篇制的重試保護，2026-10-18）
clean_column_draft_today() {
  "$PY" - "$LOCAL_REPO/data/substack_drafts/$(date +%F)" "$1" <<'PYCHECK2'
import json, sys, pathlib
day, column = pathlib.Path(sys.argv[1]), sys.argv[2]
for meta in day.glob("*/metadata.json"):
    m = json.loads(meta.read_text())
    if str(m.get("title", "")).endswith(f"｜{column}") and not any(
            str(w).startswith("[品質迴圈未通過]") for w in m.get("audit_warnings") or []):
        sys.exit(0)
sys.exit(1)
PYCHECK2
}

cd "$LOCAL_REPO" 2>/dev/null || { echo "[editorial] repo missing: $LOCAL_REPO"; exit 3; }
if [ ! -x "$PY" ]; then
  echo "[editorial] venv missing; run news_radar_compose.sh --setup-only first"
  exit 3
fi

# 這裡以前是「拿不到鎖就 exit 0」。每小時的 compose tick 跟 09:00／12:00 的
# 編輯排程用同一把鎖，正點撞在一起的機率很高——2026-08-15、08-16 中午的
# podcast 專欄與 08-16 早上的週報就是這樣整批不見的，而且 exit 0 讓 launchd
# 看起來一切正常。每天兩篇 podcast、每週一篇財報是產出的主線，值得等；
# 每小時跑一次的 compose 少跑一輪沒有損失，所以由它讓位（見 WANT_FILE）。
touch "$WANT_FILE" 2>/dev/null && WANTED=1
waited=0
until lock_acquire; do
  if [ "$waited" -ge "$LOCK_WAIT_SECONDS" ]; then
    echo "[editorial] ❌ 等了 ${waited}s 仍拿不到本機鎖；這一輪 $PROFILE 沒有產文"
    osascript -e "display notification \"等鎖逾時，$PROFILE 未產文\" with title \"News Radar\"" 2>/dev/null || true
    exit 1
  fi
  [ "$waited" -eq 0 ] && echo "[editorial] 另一個本機寫入者持有鎖，開始等待（上限 ${LOCK_WAIT_SECONDS}s）"
  sleep 20
  waited=$((waited + 20))
done
LOCAL_LOCKED=1
rm -f "$WANT_FILE" 2>/dev/null || true
WANTED=0
[ "$waited" -gt 0 ] && echo "[editorial] 等待 ${waited}s 後取得本機鎖"

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
  --lease-file "$LEASE_FILE" --lease-seconds 14400 --wait-seconds 1800 || exit 5
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
elif [ "$PROFILE" = "podcast-first" ]; then
  # 2026-10-18 起一天只寫一篇；10:00 是重試，今天已經有通過稽核的吹牛免稅就不再寫。
  if clean_column_draft_today "吹牛免稅"; then
    echo "[editorial] 今天已經有通過稽核的吹牛免稅，略過。"
    COMPOSE_EXIT=0
  else
    "$PY" -u substack_radar/compose.py podcast --harvest --editorial-profile weekly --require-substack-draft
    COMPOSE_EXIT=$?
  fi
elif [ "$PROFILE" = "ai-first" ]; then
  if clean_column_draft_today "智取未來"; then
    echo "[editorial] 今天已經有通過稽核的智取未來，略過。"
    COMPOSE_EXIT=0
  else
    "$PY" -u substack_radar/compose.py podcast --column ai --harvest --editorial-profile weekly --require-substack-draft
    COMPOSE_EXIT=$?
  fi
elif [ "$PROFILE" = "podcast-second" ]; then
  "$PY" -u substack_radar/compose.py podcast --editorial-profile weekly --require-substack-draft
  COMPOSE_EXIT=$?
else
  # 週日 09:00 失敗就整週沒有賺錢有道（2026-09-20：agy 全模型 429、claude 不在 PATH
  # 且登入過期、gemini 503 → 三個後備同時倒下，一篇都沒產出）。改成當天可以重試，
  # 這個 guard 保證已經產出過就不會再寫第二篇。
  # 只算「通過事實稽核」的那篇：帶著未解違規的草稿不能發（07:30 也會跳過它），不該擋住重寫。
  # 2026-10-17 凌晨一篇 PepsiCo 帶 1 項違規出稿，舊規則會讓 06:00 直接略過、整個週六沒有公司分析可發。
  if "$PY" - "$LOCAL_REPO/data/substack_drafts/$(date +%F)" <<'PYCHECK'
import json, sys, pathlib
day = pathlib.Path(sys.argv[1])
ok = any(
    not any(str(w).startswith("[品質迴圈未通過]") for w in json.loads(m.read_text()).get("audit_warnings") or [])
    for m in day.glob("company_*/metadata.json")
)
sys.exit(0 if ok else 1)
PYCHECK
  then
    echo "[editorial] 今天已經有通過稽核的賺錢有道草稿，略過。"
    exit 0
  fi
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
