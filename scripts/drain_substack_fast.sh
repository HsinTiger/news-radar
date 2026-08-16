#!/usr/bin/env bash
# Fast Substack draft lane. Uses the same Release state and write lease as all writers.

set -uo pipefail

LOCAL_REPO="${LOCAL_REPO:-$HOME/news_radar}"
REPO="${REPO:-HsinTiger/news-radar}"
PY="$LOCAL_REPO/.venv/bin/python"
LOCAL_LOCK="$LOCAL_REPO/.runtime-state-local.lock.d"
EDITORIAL_WANT="$LOCAL_REPO/.runtime-state-editorial-want"
TMPROOT=""
LEASE_FILE=""
LEASED=0
LOCAL_LOCKED=0

cleanup() {
  if [ "$LEASED" = "1" ] && [ -n "$LEASE_FILE" ]; then
    "$PY" "$LOCAL_REPO/scripts/state_store.py" unlock \
      --repo "$REPO" --lease-file "$LEASE_FILE" || true
  fi
  [ -n "$TMPROOT" ] && rm -rf "$TMPROOT"
  if [ "$LOCAL_LOCKED" = "1" ]; then rm -rf "$LOCAL_LOCK" 2>/dev/null || true; fi
}
trap cleanup EXIT

cd "$LOCAL_REPO" 2>/dev/null || { echo "[fast-drain] no repo at $LOCAL_REPO"; exit 0; }

# A queued immediate request must never be composed by a stale writer.  Update
# the runtime checkout before importing any production Python.  Diverged or
# unreachable main is a hard stop: creating no draft is safer than sending an
# obsolete authoring contract to Substack.
if ! git fetch --quiet origin main; then
  echo "[fast-drain] cannot fetch origin/main; refusing to use stale writer"
  exit 2
fi
if ! git merge --ff-only origin/main >/dev/null 2>&1; then
  echo "[fast-drain] local checkout diverged from origin/main; refusing to compose"
  exit 2
fi

if [ ! -x "$PY" ]; then echo "[fast-drain] missing venv; run compose_hourly.sh once"; exit 2; fi

# Say so when the installed copy has drifted from the tracked one. INSTALL doc
# already prescribes this shasum check, but a check someone has to remember to
# run is not a check: on 2026-07-29 the installed copy was found still draining
# --only-immediate long after the tracked one moved to --only-current-control,
# and nothing had reported it. Warn only; a drifted script that still works
# must not be prevented from running.
CANONICAL="$LOCAL_REPO/scripts/drain_substack_fast.sh"
if [ -f "$CANONICAL" ] && [ "$(readlink -f "$0" 2>/dev/null || echo "$0")" != "$(readlink -f "$CANONICAL" 2>/dev/null || echo "$CANONICAL")" ]; then
  if ! cmp -s "$0" "$CANONICAL"; then
    echo "[fast-drain] ⚠️ 這支腳本 ($0) 與 repo 版本不同步；差異："
    diff "$CANONICAL" "$0" | sed 's/^/[fast-drain]   /' | head -20
    echo "[fast-drain]   對齊：cp $CANONICAL $0"
  fi
fi

# Re-kick whichever scheduled workflow is running late. GitHub coalesces
# submission-poller.yml and operational-sync.yml to 1-3 hours apart under load
# despite their sub-hourly crons, so this launchd tick is the reliable clock
# that covers for both. Runs before both locks: read-only plus an idempotent
# workflow dispatch, one HTTPS call, and it must still happen on ticks where
# the drain itself is locked out by a long-running compose. Never allowed to
# fail the drain.
"$PY" -u scripts/nudge_stuck_submissions.py || true

# 這支每 300 秒跑一次，是搶這把鎖最兇的一個（compose 每小時才一次）。
# 09:00 的週報與 12:00 的 podcast 專欄整批消失，主要就是被它擋掉的。
# 編輯排程在等鎖時就讓位：drain 少跑一輪，5 分鐘後就有下一輪；
# 專欄漏掉就是那一天沒有稿。60 分鐘以上的 want 檔視為殘骸。
if [ -f "$EDITORIAL_WANT" ]; then
  if [ -n "$(find "$EDITORIAL_WANT" -maxdepth 0 -mmin +60 2>/dev/null)" ]; then
    echo "[fast-drain] 殘留的編輯排程 want 檔（>60 分鐘），忽略並清掉"
    rm -f "$EDITORIAL_WANT" 2>/dev/null || true
  else
    echo "[fast-drain] 編輯排程正在等本機鎖；這一輪讓位"
    exit 0
  fi
fi

if ! mkdir "$LOCAL_LOCK" 2>/dev/null; then echo "[fast-drain] another local writer is active; skip"; exit 0; fi
echo $$ > "$LOCAL_LOCK/pid" 2>/dev/null || true
LOCAL_LOCKED=1

TMPROOT=$(mktemp -d -t nr_immediate_state.XXXXXX)
LEASE_FILE="$TMPROOT/.runtime-state-lease.json"

"$PY" scripts/state_store.py lock --repo "$REPO" \
  --producer "mac:$(hostname -s):substack-fast" \
  --lease-file "$LEASE_FILE" --lease-seconds 7200 --wait-seconds 300 || exit 3
LEASED=1
"$PY" scripts/state_store.py pull --repo "$REPO" --root "$TMPROOT" || exit 4

DB_PATH="$TMPROOT/data/01_harvest/news_radar.db"
BEFORE_SHA=$(shasum -a 256 "$DB_PATH" | awk '{print $1}')

# Serve every current control-plane submission.  The selector prioritizes
# explicit immediate requests and excludes legacy rows without lineage.
NEWS_RADAR_DB="$DB_PATH" \
  "$PY" -u scripts/drain_substack.py --only-current-control
DRAIN_EXIT=$?

AFTER_SHA=$(shasum -a 256 "$DB_PATH" | awk '{print $1}')
if [ "$BEFORE_SHA" = "$AFTER_SHA" ]; then
  echo "[fast-drain] no canonical DB change; skip Release upload"
  exit "$DRAIN_EXIT"
fi

"$PY" scripts/state_store.py push --repo "$REPO" --root "$TMPROOT" \
  --producer "mac_substack_fast:$(hostname -s):$(date -u +%Y%m%dT%H%M%SZ)" \
  --lease-file "$LEASE_FILE" || exit 5

# Keep the local runtime readback aligned with the just-verified Release state.
# This runs under both the remote lease and the local writer lock.  The doctor
# can therefore require a real remote draft id before the hourly backlog lane
# is enabled, without another network mutation.
LOCAL_DB="$LOCAL_REPO/data/01_harvest/news_radar.db"
LOCAL_DB_TMP="$LOCAL_DB.tmp.$$"
mkdir -p "$(dirname "$LOCAL_DB")"
cp "$DB_PATH" "$LOCAL_DB_TMP" || exit 6
mv "$LOCAL_DB_TMP" "$LOCAL_DB" || exit 6
LOCAL_SHA=$(shasum -a 256 "$LOCAL_DB" | awk '{print $1}')
if [ "$LOCAL_SHA" != "$AFTER_SHA" ]; then
  echo "[fast-drain] local canonical DB readback hash mismatch"
  exit 6
fi
echo "[fast-drain] local canonical DB readback verified: $LOCAL_SHA"

exit "$DRAIN_EXIT"
