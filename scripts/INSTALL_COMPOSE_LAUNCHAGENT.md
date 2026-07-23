# Install the canonical Mac workers

The Mac owns high-quality composition and Substack draft creation. Runtime
SQLite is pulled from and pushed to the `runtime-state-v1` GitHub Release; the
legacy `state` branch is not used.

## Safety contract

- Substack may create drafts only. It never publishes them.
- Meta composition may create reversible queue records, but cloud publishing
  remains paused until the owner approves a live canary.
- Both workers use the same remote lease and a local lock, so they do not write
  runtime state concurrently.

## Prerequisites

```bash
brew install git gh python@3.11
gh auth login
gh auth status
```

The GitHub account needs read/write access to `HsinTiger/news-radar` Releases.
Keep the runtime clone outside OneDrive because macOS `launchd` may be blocked
from `CloudStorage` by TCC.

## Install or update

Run this from any current clone of `HsinTiger/news-radar`:

```bash
mkdir -p ~/bin ~/Library/LaunchAgents

cp scripts/compose_hourly.sh ~/bin/news_radar_compose.sh
cp scripts/drain_substack_fast.sh ~/bin/news_radar_substack_fast.sh
chmod +x ~/bin/news_radar_compose.sh ~/bin/news_radar_substack_fast.sh

sed "s|HOME_DIR|$HOME|g" scripts/com.hsin.news-radar.compose.plist \
  > ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
sed "s|HOME_DIR|$HOME|g" scripts/com.hsin.news-radar.substack-fast.plist \
  > ~/Library/LaunchAgents/com.hsin.news-radar.substack-fast.plist

plutil -lint ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
plutil -lint ~/Library/LaunchAgents/com.hsin.news-radar.substack-fast.plist
```

Run the hourly worker once. It creates `~/news_radar`, the Python environment,
pulls and verifies canonical state, composes, then performs a verified push.

```bash
bash ~/bin/news_radar_compose.sh
```

Place runtime-only credentials in `~/news_radar/.env`. At minimum, Substack
draft creation needs the existing Substack session configuration and
`SUBSTACK_AUTO_DRAFT=1`. That flag means create a draft through `post_draft`;
there is no auto-publish path.

Test the fast lane without creating a source:

```bash
bash ~/bin/news_radar_substack_fast.sh
```

Expected safe no-op: `[fast-drain]` exits 0 when there is no immediate source.

## Load the schedules

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist 2>/dev/null || true
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.substack-fast.plist 2>/dev/null || true

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.substack-fast.plist

launchctl print gui/$(id -u)/com.hsin.news-radar.compose
launchctl print gui/$(id -u)/com.hsin.news-radar.substack-fast
```

- Compose worker: every 60 minutes.
- Immediate Substack drain: every 5 minutes.

## Verification

```bash
shasum -a 256 scripts/compose_hourly.sh ~/bin/news_radar_compose.sh
shasum -a 256 scripts/drain_substack_fast.sh ~/bin/news_radar_substack_fast.sh

tail -100 /tmp/news-radar-compose.err.log
tail -100 /tmp/news-radar-substack-fast.err.log
ls -lt ~/news_radar_snapshots/_compose_logs/ | head
```

A zero exit proves only that the script completed. A real Substack canary must
also show all of these:

1. GitHub submission reaches `source_queued`.
2. Mac log records one source as composed.
3. A real draft is visible in the Substack draft box.
4. `news_items.substack_written_at` is non-null for the local/OneDrive artifact.
5. `substack_draft_id` and `substack_drafted_at` are non-null after Substack accepts the remote draft.
6. D1 submission status becomes `draft_created` only after operational sync sees that remote evidence.

## Pause or rollback

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.substack-fast.plist
```

Do not delete the runtime clone or Release assets during incident triage. The
manifest and retained bundles are the rollback evidence.
