#!/usr/bin/env bash
# Compatibility wrapper retained for old operator notes.
# The former script immediately generated five drafts and reinstalled five
# independent launchd agents. The canonical setup now lives in the governed
# noon two-draft Podcast + Weekly installer, which also removes those legacy agents.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "[substack] setup_launchd.sh is a compatibility entry point."
echo "[substack] Installing one governed noon Podcast batch (two drafts) + Weekly company cadence."
exec bash "$REPO/scripts/install_substack_daily_agents.sh" "$@"
