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
- A remote Substack draft ID is written to a durable local receipt before the
  SQLite evidence update. The next worker reconciles that receipt without
  calling `post_draft` again, closing the API-success/DB-crash duplicate window.

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

Bootstrap the runtime clone and Python environment without pulling state or
composing any backlog item:

```bash
bash ~/bin/news_radar_compose.sh --setup-only
```

Place runtime-only credentials in `~/news_radar/.env`. At minimum, Substack
draft creation needs the existing Substack session configuration and
`SUBSTACK_AUTO_DRAFT=1`. That flag means create a draft through `post_draft`;
there is no auto-publish path.

## Stage 1: immediate canary only

Do not load the hourly worker yet. Production currently has an existing
Substack backlog; enabling hourly first could create many drafts before the
cookie/API path has one proven success.

Load only the five-minute immediate lane:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist 2>/dev/null || true
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.substack-fast.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.substack-fast.plist

~/news_radar/.venv/bin/python ~/news_radar/scripts/mac_worker_doctor.py
```

The doctor prints only key presence and evidence counts; it never prints the
cookie or other secret values. It must report `READY` before the canary.

Submit one non-sensitive Substack source with `immediate` enabled, then run the
fast lane once rather than waiting five minutes:

```bash
bash ~/bin/news_radar_substack_fast.sh
```

Require all six canary evidence items in the verification section below. After
the Release push, this command must PASS before hourly enablement:

```bash
~/news_radar/.venv/bin/python ~/news_radar/scripts/mac_worker_doctor.py --require-remote-proof
```

## Stage 2: owner-approved hourly enablement

Only after the immediate canary has a visible Substack draft ID and the doctor
passes `--require-remote-proof`, load the hourly backlog worker:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist

launchctl print gui/$(id -u)/com.hsin.news-radar.compose
launchctl print gui/$(id -u)/com.hsin.news-radar.substack-fast
```

- Compose worker: every 60 minutes.
- Immediate Substack drain: every 5 minutes.

## Optional: mlx-whisper (Apple Silicon only)

```bash
~/news_radar/.venv/bin/pip install mlx-whisper
```

**Do not add this to `requirements.txt`.** `mlx` is Apple-Silicon-only and
would break the Linux CI install for every cloud workflow.
`enrich_youtube_sources.py` treats it as an optional accelerator: if the
import fails it falls back to `faster-whisper` on CPU, so the Mac works
either way and the cloud is unaffected.

Worth installing. Measured on one 3-minute Mandarin clip (M1):

| path | time | Chinese output |
|---|---|---|
| faster-whisper `base` + CPU | 26.9s | unusable |
| mlx `small` + Metal | 18.7s | usable |
| mlx `medium` + Metal | 347.4s | best, but 18× slower — memory-bound, not viable |

"Unusable" is literal, and character counts hide it — `base` renders
領域 as 旅渔, 發表 as 坟表, and OpenAI as 欧浩人爱. Override the model
with `MLX_WHISPER_MODEL` if needed; default is `whisper-small-mlx`.

## Weekly company analysis (independent of Stage 1/2)

Two agents, installed the same way (`sed HOME_DIR` → `plutil -lint` →
`launchctl bootstrap`):

| Agent | When | What |
|---|---|---|
| `com.hsin.news-radar.company-pick` | Sat 10:00 | rank watchlist × news heat → `.company_next` |
| `com.hsin.news-radar.company-compose` | Sun 09:00 | one deep company draft |

The gap between them is deliberate: it is the window in which the owner
can edit `.company_next` to override the pick. Leaving it alone is safe —
`compose.py:_resolve_company_ticker` falls back to the watchlist top, so
composition never blocks on a human decision.

These replace the legacy `com.newsradar.company_pick` /
`com.newsradar.substack_company` pair, which had a real bug: pick was
weekly (Sat) but compose was **daily** at 11:30, so a single candidate
would be rewritten six times over. Both legacy plists are left on disk
but unloaded; do not bootstrap them.

This pair is independent of the Stage 1/Stage 2 gating above — company
mode draws from the watchlist, not from the Substack submission backlog,
so enabling it cannot trigger a backlog flush.

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

Normally `data/substack_drafts/.substack_remote_receipts.json` is absent because
the receipt is cleared immediately after the SQLite update. If it exists, do
not manually re-run composition for that source: the next worker tick will
reconcile the saved draft ID into SQLite. A malformed or conflicting receipt
stops the drain fail-closed and must be inspected before retrying.

## Pause or rollback

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.hsin.news-radar.substack-fast.plist
```

Do not delete the runtime clone or Release assets during incident triage. The
manifest and retained bundles are the rollback evidence.
