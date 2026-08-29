#!/usr/bin/env bash
# 手動產一篇賺錢有道（非排程）。流程比照正式排程：取鎖 → lease → pull → compose → push，
# 所以四道閘門、稽核迴圈、封面、tag、SEO 全部沿用，不會因為是手動跑就繞過品質關卡。
#
#   scripts/compose_one_company.sh 3034.TW    # 台股
#   scripts/compose_one_company.sh COIN       # 美股
#   scripts/compose_one_company.sh BTC        # 幣圈（走 crypto_facts）
#
# 這支原本放在暫存目錄，2026-08-20 被清掉害一次執行整個失敗。手動工具跟排程
# 一樣是產線的一部分，就該跟程式碼一起進版控。
set -uo pipefail
TICKER="${1:?usage: $0 <ticker>}"
REPO="${REPO:-HsinTiger/news-radar}"; LOCAL_REPO="$HOME/news_radar"
PY="$LOCAL_REPO/.venv/bin/python"
LOCAL_LOCK="$LOCAL_REPO/.runtime-state-local.lock.d"
WANT_FILE="$LOCAL_REPO/.runtime-state-editorial-want"
LEASE_FILE="$LOCAL_REPO/.runtime-state-editorial-lease.json"
LEASED=0; LOCAL_LOCKED=0; WANTED=0
cleanup() {
  [ "$LEASED" = 1 ] && "$PY" "$LOCAL_REPO/scripts/state_store.py" unlock --repo "$REPO" --lease-file "$LEASE_FILE" || true
  [ "$LOCAL_LOCKED" = 1 ] && rm -rf "$LOCAL_LOCK" 2>/dev/null || true
  [ "$WANTED" = 1 ] && rm -f "$WANT_FILE" 2>/dev/null || true
}
trap cleanup EXIT
cd "$LOCAL_REPO" || exit 3
touch "$WANT_FILE" && WANTED=1
waited=0
until mkdir "$LOCAL_LOCK" 2>/dev/null; do
  [ "$waited" -ge 900 ] && { echo "等鎖逾時"; exit 1; }
  [ "$waited" -eq 0 ] && echo "[adhoc] 等本機鎖…"
  sleep 15; waited=$((waited+15))
done
echo $$ > "$LOCAL_LOCK/pid"; LOCAL_LOCKED=1
rm -f "$WANT_FILE"; WANTED=0
"$PY" scripts/state_store.py lock --repo "$REPO" --producer "mac:$(hostname -s):adhoc-company-$TICKER" \
  --lease-file "$LEASE_FILE" --lease-seconds 7200 --wait-seconds 1800 || exit 5
LEASED=1
"$PY" scripts/state_store.py pull --repo "$REPO" --root . || exit 6
"$PY" -u substack_radar/compose.py company --ticker "$TICKER" --editorial-profile weekly --require-substack-draft
RC=$?
"$PY" scripts/state_store.py push --repo "$REPO" --root . \
  --producer "mac_adhoc_company:$(hostname -s):$(date -u +%Y%m%dT%H%M%SZ)" --lease-file "$LEASE_FILE"
echo "[adhoc] compose exit=$RC"
exit $RC
