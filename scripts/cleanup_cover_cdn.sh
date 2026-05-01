#!/usr/bin/env bash
# ============================================================
# News Radar · cleanup_cover_cdn.sh
# ------------------------------------------------------------
# Prunes PNGs from the cover-cdn branch that are older than
# RETENTION_DAYS. Designed to run weekly via a GH Actions cron.
#
# Why TTL exists:
#   Every published draft pushes 2 PNGs (FB + IG, ~70 KB each).
#   At 5-15 posts/day that's ~700 KB - 2 MB per day. After a year,
#   the cover-cdn branch becomes a multi-GB blob graveyard with
#   refs that Meta long since cached. A 30-day window keeps the
#   branch lean while still covering any reposts/edits that might
#   re-fetch the URL.
#
# How "age" is determined:
#   We use git log --diff-filter=A on each PNG to find when it
#   was first added. Files first-added more than RETENTION_DAYS
#   ago are deleted. We do NOT use file mtime because git-based
#   workflows don't preserve mtime.
#
# Usage:
#   bash scripts/cleanup_cover_cdn.sh           # actually delete
#   bash scripts/cleanup_cover_cdn.sh --dry-run # list what would go
#
# Exit codes:
#   0   cleanup ran (or dry-run); branch consistent
#   2   environment / arg error
#   3   git operation failed
# ============================================================

set -u

REPO_URL="${REPO_URL:-https://github.com/HsinTiger/news-radar.git}"
BRANCH="cover-cdn"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

DRY_RUN=0
VERBOSE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift;;
        --verbose|-v) VERBOSE=1; shift;;
        --help|-h)
            sed -n '3,30p' "$0"
            exit 0;;
        *) echo "Unknown arg: $1" >&2; exit 2;;
    esac
done

log() { echo "[cleanup_cover_cdn] $*"; }
vlog() { [[ $VERBOSE -eq 1 ]] && echo "[cleanup_cover_cdn.v] $*" || true; }

# Auth: prefer GITHUB_TOKEN (works on GH Actions). Falls back to
# git credential helper if absent. Empty token → leave URL clean.
AUTHED_URL="$REPO_URL"
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    AUTHED_URL="${REPO_URL/https:\/\//https:\/\/x-access-token:${GITHUB_TOKEN}@}"
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

log "cloning $BRANCH branch into $WORKDIR ..."
if ! git clone --quiet --branch "$BRANCH" --single-branch "$AUTHED_URL" "$WORKDIR" 2>/dev/null; then
    log "branch $BRANCH does not exist remotely — nothing to clean"
    exit 0
fi

cd "$WORKDIR" || { echo "cd failed" >&2; exit 3; }

# Find PNGs first-added more than RETENTION_DAYS ago.
# git log -1 --diff-filter=A <file> gives the commit that ADDED it.
# Compare its commit timestamp to (now - RETENTION_DAYS * 86400).
NOW_EPOCH="$(date -u +%s)"
THRESHOLD_EPOCH=$((NOW_EPOCH - RETENTION_DAYS * 86400))

DELETED_COUNT=0
KEPT_COUNT=0
TO_DELETE=()

while IFS= read -r -d '' f; do
    # Skip non-PNG and bookkeeping files
    [[ "$f" == *.png ]] || continue
    f="${f#./}"

    add_epoch="$(git log --diff-filter=A --format=%ct -- "$f" | tail -n 1)"
    if [[ -z "$add_epoch" ]]; then
        # File exists but git can't trace it — be conservative, keep
        vlog "no add-commit for $f; keeping"
        KEPT_COUNT=$((KEPT_COUNT + 1))
        continue
    fi
    if [[ "$add_epoch" -lt "$THRESHOLD_EPOCH" ]]; then
        TO_DELETE+=("$f")
        vlog "expire $f (added at $add_epoch < $THRESHOLD_EPOCH)"
    else
        KEPT_COUNT=$((KEPT_COUNT + 1))
    fi
done < <(find . -maxdepth 2 -type f -print0)

log "scan: ${#TO_DELETE[@]} expired, $KEPT_COUNT kept (retention=${RETENTION_DAYS}d)"

if [[ ${#TO_DELETE[@]} -eq 0 ]]; then
    log "nothing to clean"
    exit 0
fi

if [[ $DRY_RUN -eq 1 ]]; then
    log "DRY RUN — would delete:"
    for f in "${TO_DELETE[@]}"; do echo "  $f"; done
    exit 0
fi

# Configure committer
git config user.name "news-radar-cover-cleanup"
git config user.email "noreply@local"

for f in "${TO_DELETE[@]}"; do
    git rm --quiet "$f"
    DELETED_COUNT=$((DELETED_COUNT + 1))
done

git commit --quiet -m "cleanup: remove $DELETED_COUNT PNG(s) older than ${RETENTION_DAYS}d"
if ! git push origin "$BRANCH"; then
    echo "git push failed" >&2
    exit 3
fi

log "deleted $DELETED_COUNT files; pushed to $BRANCH"
exit 0
