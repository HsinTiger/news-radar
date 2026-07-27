#!/usr/bin/env bash
# Fast Substack draft lane. Uses the same Release state and write lease as all writers.

set -uo pipefail

LOCAL_REPO="${LOCAL_REPO:-$HOME/news_radar}"
REPO="${REPO:-HsinTiger/news-radar}"
PY="$LOCAL_REPO/.venv/bin/python"
LOCAL_LOCK="$LOCAL_REPO/.runtime-state-local.lock.d"
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
  if [ "$LOCAL_LOCKED" = "1" ]; then rmdir "$LOCAL_LOCK" 2>/dev/null || true; fi
}
trap cleanup EXIT

cd "$LOCAL_REPO" 2>/dev/null || { echo "[fast-drain] no repo at $LOCAL_REPO"; exit 0; }
if [ ! -x "$PY" ]; then echo "[fast-drain] missing venv; run compose_hourly.sh once"; exit 2; fi
if ! mkdir "$LOCAL_LOCK" 2>/dev/null; then echo "[fast-drain] another local writer is active; skip"; exit 0; fi
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
