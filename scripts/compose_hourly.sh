#!/usr/bin/env bash
# Canonical Mac worker: Release state -> Substack drafts / Meta queue -> Release state.

set -uo pipefail

REPO="${REPO:-HsinTiger/news-radar}"
LOCAL_REPO="${LOCAL_REPO:-$HOME/news_radar}"
BUFFER_TARGET="${BUFFER_TARGET:-2}"
LOG_ROOT="${LOG_ROOT:-$HOME/news_radar_snapshots/_compose_logs}"
LOCAL_LOCK="$LOCAL_REPO/.runtime-state-local.lock.d"
LEASE_FILE="$LOCAL_REPO/.runtime-state-lease.json"
LOG_FILE="$LOG_ROOT/$(date +%Y%m%d_%H%M%S).log"
LEASED=0
LOCAL_LOCKED=0

mkdir -p "$LOG_ROOT"
exec > >(tee -a "$LOG_FILE") 2>&1

cleanup() {
  if [ "$LEASED" = "1" ] && [ -x "$LOCAL_REPO/.venv/bin/python" ]; then
    "$LOCAL_REPO/.venv/bin/python" "$LOCAL_REPO/scripts/state_store.py" unlock \
      --repo "$REPO" --lease-file "$LEASE_FILE" || true
  fi
  if [ "$LOCAL_LOCKED" = "1" ]; then
    rmdir "$LOCAL_LOCK" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "===== News Radar canonical Mac worker: $(date '+%Y-%m-%d %H:%M:%S') ====="

for command in git python3 gh shasum; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $command"
    exit 2
  fi
done

if [ ! -d "$LOCAL_REPO/.git" ]; then
  git clone "https://github.com/${REPO}.git" "$LOCAL_REPO" || exit 3
fi
cd "$LOCAL_REPO" || exit 3

if ! mkdir "$LOCAL_LOCK" 2>/dev/null; then
  echo "INFO: another local state writer is active; skip this tick"
  exit 0
fi
LOCAL_LOCKED=1

if git fetch --quiet origin main; then
  if ! git merge --ff-only origin/main >/dev/null 2>&1; then
    echo "WARN: local clone cannot fast-forward; using current code"
    git status --short
  fi
else
  echo "WARN: main fetch failed; using current code"
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv || exit 4
fi
source .venv/bin/activate

REQ_HASH=$(shasum -a 256 requirements.txt requirements-mac.txt | shasum -a 256 | awk '{print $1}')
INSTALLED_HASH=$(cat .venv/.requirements.sha256 2>/dev/null || true)
if [ "$REQ_HASH" != "$INSTALLED_HASH" ]; then
  python -m pip install --quiet --upgrade pip || exit 5
  python -m pip install --quiet -r requirements.txt -r requirements-mac.txt || exit 5
  printf '%s\n' "$REQ_HASH" > .venv/.requirements.sha256
fi

python scripts/state_store.py lock --repo "$REPO" \
  --producer "mac:$(hostname -s):compose" \
  --lease-file "$LEASE_FILE" --lease-seconds 7200 --wait-seconds 1800 || exit 6
LEASED=1

python scripts/state_store.py pull --repo "$REPO" --root . || exit 7

python -u scripts/drain_substack.py
DRAIN_EXIT=$?
echo "Substack drain exit: $DRAIN_EXIT"

python -u run_pipeline.py --harvest-now --compose-only --buffer-target "$BUFFER_TARGET"
PIPELINE_EXIT=$?
echo "Meta compose exit: $PIPELINE_EXIT"

python scripts/state_store.py push --repo "$REPO" --root . \
  --producer "mac_compose:$(hostname -s):$(date -u +%Y%m%dT%H%M%SZ)" \
  --lease-file "$LEASE_FILE"
PUSH_EXIT=$?
echo "Canonical state push exit: $PUSH_EXIT"

if [ "$DRAIN_EXIT" -ne 0 ] || [ "$PIPELINE_EXIT" -ne 0 ] || [ "$PUSH_EXIT" -ne 0 ]; then
  exit 1
fi
echo "===== Completed: $(date '+%Y-%m-%d %H:%M:%S') ====="
