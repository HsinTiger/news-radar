#!/usr/bin/env bash
# ============================================================
# News Radar · push_state.sh · v1 (2026-04-22)
# ------------------------------------------------------------
# 把本機 ~/news_radar/data/01_harvest/news_radar.db 推到 GitHub 的 state branch，
# 推完之後 **強制做 post-condition 驗證**：fetch origin/state 回來、重新解出那顆 DB、
# 對照本機 DB 的 sha256，還可以指定 `--expect-draft <id>` 做 SQL 級的回查。
#
# 為什麼要這支腳本：
#   compose_hourly.sh 結尾的 state-branch push 是「log 印 ✅ 就算成功」的範式。
#   2026-04-20 夜班那次 compose_one 回報 qs=queued 但 DB 後來發現沒寫入 —— 根因
#   是 log 跟實際狀態脫節。這支把「我以為我成功」和「state branch 真的有那筆 row」
#   強制連成一條：post-condition 不過，exit code 非 0，絕不冒充成功。
#
# 使用方式：
#   # 基本：從 repo root 執行，把目前的 DB 推上去
#   bash scripts/push_state.sh
#
#   # 推完後驗證某 draft_id 在 state branch 的 DB 裡
#   bash scripts/push_state.sh --expect-draft 5a83ee9d97…
#
#   # Dry-run：只做推前檢查 + 列出會推什麼，不實際 push
#   bash scripts/push_state.sh --dry-run
#
# Exit codes：
#   0   推送成功 + post-condition 全過
#   1   post-condition 失敗（實際狀態跟預期不符 —— 最重要的失敗類型）
#   2   參數錯 / 環境前置條件不符（cwd 不對、DB 不存在等）
#   3   git 操作失敗（push / fetch / 認證 / 網路）
# ============================================================

set -u

# ----- 可調參數 -----
REPO_URL="${REPO_URL:-https://github.com/HsinTiger/news-radar.git}"
DB_REL_PATH="data/01_harvest/news_radar.db"
STATE_EXTRA_FILES=("state/last_harvest.txt")  # 選擇性：存在才帶
PROPOSALS_REL_DIR="data/05_reflect/proposals"  # Phase 9 Item 2: per-week jsonl
BRANCH="state"

# ----- 參數解析 -----
EXPECT_DRAFT=""
DRY_RUN=0
VERBOSE=0

usage() {
    cat <<'EOF'
push_state.sh — push local DB to state branch with post-condition verification

Usage: bash scripts/push_state.sh [options]

Options:
    --expect-draft <id>   Post-condition: 推完後 assert 此 draft id 存在於 state branch DB。
                          可以是完整 id 或前綴（>= 8 字元）。失敗 exit 1。
    --dry-run             不實際 push，只列出會做什麼。
    --verbose, -v         多印一些 debug 資訊。
    --help, -h            印此說明。

Exit codes:
    0 成功+驗證通過  1 post-condition 失敗  2 參數/環境錯  3 git 操作失敗
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --expect-draft)
            [[ $# -lt 2 ]] && { echo "❌ --expect-draft 需要一個值" >&2; exit 2; }
            EXPECT_DRAFT="$2"; shift 2;;
        --dry-run) DRY_RUN=1; shift;;
        --verbose|-v) VERBOSE=1; shift;;
        --help|-h) usage; exit 0;;
        *) echo "❌ Unknown argument: $1" >&2; usage >&2; exit 2;;
    esac
done

log() { echo "[push_state] $*"; }
vlog() { [[ $VERBOSE -eq 1 ]] && echo "[push_state.v] $*" || true; }

# ----- 前置檢查 -----
REPO_ROOT="$(pwd)"
# 如果呼叫者不在 repo root，試著找
if [[ ! -f "$REPO_ROOT/$DB_REL_PATH" ]]; then
    # 往上找 .git
    SEARCH="$REPO_ROOT"
    while [[ "$SEARCH" != "/" && ! -d "$SEARCH/.git" ]]; do
        SEARCH="$(dirname "$SEARCH")"
    done
    if [[ -f "$SEARCH/$DB_REL_PATH" ]]; then
        REPO_ROOT="$SEARCH"
        log "自動定位 repo root: $REPO_ROOT"
    fi
fi

if [[ ! -f "$REPO_ROOT/$DB_REL_PATH" ]]; then
    echo "❌ 找不到 DB: $REPO_ROOT/$DB_REL_PATH" >&2
    echo "   請確認 cwd 在 news_radar repo 根目錄，或用 LOCAL_REPO 環境變數指定。" >&2
    exit 2
fi

cd "$REPO_ROOT" || { echo "❌ cd $REPO_ROOT 失敗" >&2; exit 2; }

for cmd in git python3 shasum; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "❌ 找不到 $cmd" >&2
        exit 2
    fi
done

# ----- 本機狀態快照 -----
LOCAL_DB="$REPO_ROOT/$DB_REL_PATH"
LOCAL_SIZE="$(stat -f%z "$LOCAL_DB" 2>/dev/null || stat -c%s "$LOCAL_DB")"
LOCAL_SHA="$(shasum -a 256 "$LOCAL_DB" | cut -d' ' -f1)"
log "本機 DB: $LOCAL_DB"
log "   size=$LOCAL_SIZE bytes  sha256=${LOCAL_SHA:0:16}…"

# 預計要帶的額外檔案（存在才帶）
EXTRA_PRESENT=()
for f in "${STATE_EXTRA_FILES[@]}"; do
    if [[ -f "$REPO_ROOT/$f" ]]; then
        EXTRA_PRESENT+=("$f")
        vlog "額外檔: $f (將一併推)"
    fi
done

# Phase 9 Item 2: enumerate proposals/*.jsonl files and capture per-file
# sha256 for the post-condition. Approach: per-file sha256 (not tarball)
# — file count is bounded (≤ ~52 weeks of jsonl) and the per-file shape
# matches the DB blob's existing verification pattern, so failure
# diagnosis can pinpoint a specific week-file.
PROPOSALS_FILES=()       # paths relative to REPO_ROOT
PROPOSALS_LOCAL_SHA=()   # sha256 hex (parallel-indexed with PROPOSALS_FILES)
PROPOSALS_LOCAL_SIZE=()  # bytes (same)
PROPOSALS_DIR_ABS="$REPO_ROOT/$PROPOSALS_REL_DIR"
if [[ -d "$PROPOSALS_DIR_ABS" ]]; then
    # NUL-delimited iteration; tolerates an empty dir.
    while IFS= read -r -d '' fpath; do
        rel="${fpath#$REPO_ROOT/}"
        PROPOSALS_FILES+=("$rel")
        PROPOSALS_LOCAL_SHA+=("$(shasum -a 256 "$fpath" | cut -d' ' -f1)")
        PROPOSALS_LOCAL_SIZE+=("$(stat -f%z "$fpath" 2>/dev/null || stat -c%s "$fpath")")
        vlog "proposals: $rel (will be pushed)"
    done < <(find "$PROPOSALS_DIR_ABS" -maxdepth 1 -type f -name '*.jsonl' -print0 | sort -z)
fi
log "proposals: ${#PROPOSALS_FILES[@]} jsonl file(s) staged"

# Post-condition: 如果指定 --expect-draft，先確認本機 DB 真的有這筆
if [[ -n "$EXPECT_DRAFT" ]]; then
    FOUND=$(python3 - <<PY
import sqlite3, sys
conn = sqlite3.connect("$LOCAL_DB")
r = conn.execute("SELECT id, queue_status FROM drafts WHERE id LIKE ?", ("$EXPECT_DRAFT" + "%",)).fetchone()
if r:
    print(f"{r[0]}|{r[1]}")
PY
)
    if [[ -z "$FOUND" ]]; then
        echo "❌ 本機 DB 裡找不到 draft id 前綴 '$EXPECT_DRAFT'。推上去也會驗證失敗 → 提前拒絕。" >&2
        exit 1
    fi
    log "本機 assert OK: draft $FOUND"
fi

if [[ $DRY_RUN -eq 1 ]]; then
    log "🔸 DRY-RUN: 不實際 push。會帶的檔案："
    log "   - $DB_REL_PATH"
    for f in ${EXTRA_PRESENT[@]+"${EXTRA_PRESENT[@]}"}; do log "   - $f"; done
    for f in ${PROPOSALS_FILES[@]+"${PROPOSALS_FILES[@]}"}; do log "   - $f"; done
    log "🔸 DRY-RUN: 結束（exit 0）"
    exit 0
fi

# ----- 推送 -----
STATE_DIR="$(mktemp -d)"
trap 'rm -rf "$STATE_DIR"' EXIT

log "📤 準備推送到 $BRANCH branch..."
(
    cd "$STATE_DIR" || exit 3
    git init -q -b "$BRANCH"
    git config user.name "news-radar-push-state"
    git config user.email "noreply@local"

    mkdir -p "$(dirname "$DB_REL_PATH")"
    cp "$LOCAL_DB" "$DB_REL_PATH"

    for f in ${EXTRA_PRESENT[@]+"${EXTRA_PRESENT[@]}"}; do
        mkdir -p "$(dirname "$f")"
        cp "$REPO_ROOT/$f" "$f"
    done

    # Phase 9 Item 2: bundle reflector proposals jsonl files (one per ISO week).
    for f in ${PROPOSALS_FILES[@]+"${PROPOSALS_FILES[@]}"}; do
        mkdir -p "$(dirname "$f")"
        cp "$REPO_ROOT/$f" "$f"
    done

    cat > LAST_RUN.txt <<EOF
last_run_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)
kind: push_state_sh
host: $(hostname -s)
local_db_size: $LOCAL_SIZE
local_db_sha256: $LOCAL_SHA
EOF

    git add -A
    git commit -q -m "state: push_state.sh @ $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    git push --force "$REPO_URL" "$BRANCH" 2>&1
) || { echo "❌ git push 失敗（認證 / 網路 / 權限）" >&2; exit 3; }

log "✅ git push 完成。進入 post-condition 驗證..."

# ----- Post-condition: 從 origin/state 抓回來對照 -----
VERIFY_DIR="$(mktemp -d)"
# shellcheck disable=SC2064
trap "rm -rf '$STATE_DIR' '$VERIFY_DIR'" EXIT

# Serialize the proposals parallel arrays for the subshell. Format per
# line: "<rel_path>\t<size>\t<sha256>". Empty string if no proposals.
PROPOSALS_MANIFEST=""
if [[ ${#PROPOSALS_FILES[@]} -gt 0 ]]; then
    for i in "${!PROPOSALS_FILES[@]}"; do
        PROPOSALS_MANIFEST+="${PROPOSALS_FILES[$i]}	${PROPOSALS_LOCAL_SIZE[$i]}	${PROPOSALS_LOCAL_SHA[$i]}
"
    done
fi

(
    cd "$VERIFY_DIR" || exit 3
    git init -q
    if ! git fetch --depth=1 "$REPO_URL" "$BRANCH" 2>/dev/null; then
        echo "❌ fetch origin/$BRANCH 失敗（推上去了但驗證拿不到 —— 網路？）" >&2
        exit 3
    fi
    # 把 DB blob 倒出來
    git show "FETCH_HEAD:$DB_REL_PATH" > fetched.db 2>/dev/null \
        || { echo "❌ state branch 沒有 $DB_REL_PATH (push 可能沒帶到)" >&2; exit 1; }

    REMOTE_SIZE="$(stat -f%z fetched.db 2>/dev/null || stat -c%s fetched.db)"
    REMOTE_SHA="$(shasum -a 256 fetched.db | cut -d' ' -f1)"

    echo "[post-condition] remote DB: size=$REMOTE_SIZE  sha256=${REMOTE_SHA:0:16}…"

    if [[ "$REMOTE_SIZE" != "$LOCAL_SIZE" ]]; then
        echo "❌ size 對不上 (local=$LOCAL_SIZE remote=$REMOTE_SIZE)" >&2
        exit 1
    fi
    if [[ "$REMOTE_SHA" != "$LOCAL_SHA" ]]; then
        echo "❌ sha256 對不上 (local=$LOCAL_SHA remote=$REMOTE_SHA)" >&2
        exit 1
    fi
    echo "[post-condition] ✅ DB size + sha256 一致"

    # Phase 9 Item 2: per-file sha256 verification of proposals jsonl.
    PROPOSALS_OK=0
    PROPOSALS_TOTAL=0
    while IFS=$'\t' read -r rel_path expected_size expected_sha; do
        [[ -z "$rel_path" ]] && continue
        PROPOSALS_TOTAL=$((PROPOSALS_TOTAL + 1))
        if ! git show "FETCH_HEAD:$rel_path" > fetched_proposal.bin 2>/dev/null; then
            echo "❌ state branch 沒有 $rel_path (push 可能沒帶到)" >&2
            exit 1
        fi
        actual_size="$(stat -f%z fetched_proposal.bin 2>/dev/null || stat -c%s fetched_proposal.bin)"
        actual_sha="$(shasum -a 256 fetched_proposal.bin | cut -d' ' -f1)"
        if [[ "$actual_size" != "$expected_size" ]]; then
            echo "❌ proposals size 對不上: $rel_path local=$expected_size remote=$actual_size" >&2
            exit 1
        fi
        if [[ "$actual_sha" != "$expected_sha" ]]; then
            echo "❌ proposals sha256 對不上: $rel_path local=$expected_sha remote=$actual_sha" >&2
            exit 1
        fi
        PROPOSALS_OK=$((PROPOSALS_OK + 1))
    done <<EOF
$PROPOSALS_MANIFEST
EOF
    rm -f fetched_proposal.bin
    if [[ $PROPOSALS_TOTAL -gt 0 ]]; then
        echo "[post-condition] ✅ proposals jsonl: $PROPOSALS_OK/$PROPOSALS_TOTAL 一致"
    else
        echo "[post-condition] (no proposals jsonl staged — Item 3 cron has not yet written one)"
    fi

    # 如果 --expect-draft 有指定，跑 SQL 級回查
    if [[ -n "$EXPECT_DRAFT" ]]; then
        FOUND=$(python3 - <<PY
import sqlite3
conn = sqlite3.connect("fetched.db")
r = conn.execute("SELECT id, queue_status, generated_at FROM drafts WHERE id LIKE ?", ("$EXPECT_DRAFT" + "%",)).fetchone()
if r:
    print(f"{r[0]}|{r[1]}|{r[2]}")
PY
)
        if [[ -z "$FOUND" ]]; then
            echo "❌ state branch DB 裡找不到 draft '$EXPECT_DRAFT'（SQL 回查失敗）" >&2
            exit 1
        fi
        echo "[post-condition] ✅ draft assert OK: $FOUND"
    fi
)
RC=$?

if [[ $RC -eq 0 ]]; then
    log "🎉 完成：推送 + post-condition 全過"
    exit 0
else
    log "❌ 推送完成但 post-condition 失敗 (rc=$RC) —— 請檢查 state branch"
    exit "$RC"
fi
