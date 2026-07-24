# HsinTiger Social Automation operations

This is the current runbook for the canonical `HsinTiger/news-radar` system.
Historical documents that mention an orphan `state` branch are not operational
instructions. Runtime SQLite now lives in verified assets under the
`runtime-state-v1` GitHub Release.

## Truth hierarchy

1. Platform response and visible post/draft.
2. Canonical SQLite bundle with hash, `PRAGMA quick_check`, and readback.
3. D1 post/submission/metric record.
4. Workflow and collector logs.
5. AI or human narrative.

`pytest PASS`, workflow success, or a queue status does not prove a live post or
a Substack draft.

## Current safety state

```text
AUTOMATION_MODE=paused
SUBMISSION_PROCESSOR_MODE=paused
ENABLE_META_PUBLISH_NOW=false
SUBSTACK_AUTO_PUBLISH=false (invariant)
```

Keep these values until the corresponding owner canary passes. Engagement,
audience, knowledge sync, and draft-only composition may operate while Meta
publishing is paused.

## Owner surfaces

- Submission: <https://hsintiger.github.io/news-radar/substack-submit/>
- Dashboard: <https://hsintiger.github.io/news-radar/dashboard/>
- Worker health: <https://news-radar-submit.smartmmmoney.workers.dev/health>

The dashboard owner token belongs in browser `sessionStorage`, never source
code. Owner and service tokens are distinct.

## Daily checks

### GitHub Actions

```bash
gh run list --repo HsinTiger/news-radar --limit 20
gh workflow view engagement-monitor.yml --repo HsinTiger/news-radar
gh workflow view audience-monitor.yml --repo HsinTiger/news-radar
gh workflow view operational-sync.yml --repo HsinTiger/news-radar
gh workflow view learning-review.yml --repo HsinTiger/news-radar
```

Healthy means the latest expected run completed and its exact collector output
is credible. A scheduled workflow with no recent run is stale, not healthy.

### Canonical runtime state

```bash
python scripts/state_store.py inspect --repo HsinTiger/news-radar
python scripts/state_store.py pull --repo HsinTiger/news-radar --root /tmp/news-radar-state-audit
```

The pull command verifies manifest identity, SHA-256, bundle structure,
SQLite `quick_check`, expected row counts, and full download readback. Never
replace Release assets manually.

Every writer must follow:

```text
lock -> pull -> mutate -> verify -> push -> download readback -> unlock
```

If the lease is held, wait or diagnose its producer. Do not bypass the lease.

### Social Ops D1

From `cloudflare-worker/`:

```bash
npx wrangler d1 migrations list hsintiger-social-ops --remote
npx wrangler d1 execute hsintiger-social-ops --remote --command \
  "SELECT platform,MAX(captured_at) latest,COUNT(*) samples FROM audience_snapshots GROUP BY platform;"
npx wrangler d1 execute hsintiger-social-ops --remote --command \
  "SELECT platform,metric,status,detail,captured_at FROM data_health_snapshots ORDER BY captured_at DESC LIMIT 20;"
npx wrangler d1 execute hsintiger-social-ops --remote --command \
  "SELECT platform,guard_version,evaluated,evidence_coverage,rewrite_count,block_count,legacy_excluded_count,captured_at FROM content_quality_snapshots ORDER BY captured_at DESC LIMIT 9;"
```

Follower health is owned by `audience-monitor.yml`. Engagement sync must not
write placeholder audience health rows.

## Metric contract

Platform metrics are not interchangeable:

| Platform | Current evidence | Dashboard / learning use |
|---|---|---|
| Facebook | `post_clicks`, reactions, comments | clicks and action score; impressions are not used |
| Instagram | views, reach, saves, likes, comments | native views/reach and actions |
| Threads | views, likes, replies, reposts, quotes | native views and actions |

Facebook `post_impressions` and `post_impressions_unique` are invalid for the
current account/API contract. `post_engaged_users` also failed the live canary.
Do not convert those failures into zero reach or healthy data. The initial
proposal score uses `likes + 2*comments + 3*shares + 0.25*clicks`; the click
coefficient is an assumption to recalibrate only after sufficient samples.

The engagement workflow runs hourly at minute 11. Bucket selection is
idempotent and accepts the nearest tick within ±45 minutes of post age 1h, 24h,
or 168h. A successful workflow with zero due buckets is healthy; a six-hour
schedule is not, because it silently misses most publish minutes.

Content-quality evidence is a separate signal. `block` fails closed. `rewrite`
gets one new-composition retry and, if unresolved, holds the new draft outside
the automatic queue. `warn` remains publishable but visible in the dashboard.
Historical backfill stores issue codes and a text hash only; it must report
`status_mutations=0` and must not rewrite old queue/status fields.

## Governed learning review

The weekly learning writer has no publishing credentials or publishing step.
Its required order is:

```text
Release lock -> verified pull -> remote lease assert
-> mirror D1 owner decisions -> apply exact approved topic/cadence actions
-> backfill quality evidence -> generate topic and per-platform cadence proposals
-> verified Release push -> D1 sync -> unlock
```

An approved proposal executes only when its JSONL and SQLite lineage identity
match, the current weight has not drifted, the values remain in `0.3..2.0`,
the absolute delta is at most `0.30`, and compare-and-swap plus readback pass.
A rejected proposal is mirrored but never executed. Stale operational sync
cannot downgrade `approved`, `rejected`, `applied`, or `superseded` status.

Cadence actions additionally require the same platform and exact current
cadence, both adjacent windows to retain their original sample/coverage/signal
evidence, a ratio that still crosses the proposal gate, a change of at most one
post/day, bounded slots/spacing, compare-and-swap, and readback. Until both
14-day windows reach 80% metric coverage, `insufficient_metric_coverage` is the
correct result and no frequency proposal should exist.

If any gate fails, keep the proposal in its current D1 state, preserve the
Release evidence, and inspect the `learning-review-<run_id>` artifact. Do not
edit `topic_weights` manually to make the workflow green.

## Deploy Worker and D1

Run deterministic gates first:

```bash
python -X utf8 -m pytest -q
python -m compileall -q src scripts
node --check cloudflare-worker/worker.js
node --check dashboard/app.js
npx wrangler deploy --dry-run --config cloudflare-worker/wrangler.toml
git diff --check
```

Apply D1 migrations before deploying Worker code that reads new columns:

```bash
cd cloudflare-worker
npx wrangler d1 migrations apply hsintiger-social-ops --remote
npx wrangler deploy
```

Then verify unauthenticated denial, authenticated dashboard read, CORS,
submission idempotency, `publish-now=409`, paused modes, and D1 readback. Never
print bearer tokens in logs.

## Pages deployment

`pages-deploy.yml` publishes these in-repo surfaces:

- `/substack-submit/`
- `/dashboard/`

After deployment, use an incognito browser or Playwright to verify page load,
token unlock, D1 data rendering, and the absence of console errors. A Pages
workflow success does not prove the Worker API is reachable from the browser.

## Mac workers

Installation and update commands live in
[`scripts/INSTALL_COMPOSE_LAUNCHAGENT.md`](../scripts/INSTALL_COMPOSE_LAUNCHAGENT.md).

Expected workers:

- `com.hsin.news-radar.compose`: hourly composition and full Substack drain.
- `com.hsin.news-radar.substack-fast`: immediate Substack drain every 5 minutes.

Both use `~/news_radar`, the Release-backed lease, and a local directory lock.
They do not read or push a `state` branch.

Enable them in two stages. Load `substack-fast` first and require
`mac_worker_doctor.py --require-remote-proof` after one immediate canary. Do not
load the hourly worker first when a backlog exists; that can create many drafts
before the cookie/API path has one current proof.

## Canary ladder

### Substack

1. Keep Meta paused.
2. Submit one small, non-sensitive source to Substack with immediate mode.
3. Confirm `source_queued` and the workflow URL.
4. Confirm Mac composition log and a real Substack draft.
5. Confirm local output via `substack_written_at`, then confirm remote evidence via
   `substack_draft_id` and `substack_drafted_at` in Release state.
6. Run operational sync and confirm D1 `draft_created`; local output alone must
   leave the submission at `source_queued`.

### Meta

1. Keep scheduled automation paused.
2. Compose three platform variants; publish Threads only.
3. Verify visible content, platform post ID, SQLite, D1, and first metrics.
4. Wait at least eight hours and review quality.
5. Canary Facebook and Instagram separately.
6. Only then consider `AUTOMATION_MODE=live` at bootstrap cadence.

## Incident response

Stop and keep evidence when any of these occurs:

- duplicate post or mismatched platform identity;
- Release hash/readback/SQLite failure;
- lease conflict that does not expire normally;
- auth failure or suspected credential exposure;
- invalid metric presented as healthy;
- Substack published instead of drafted;
- unexpected Meta post while paused.

Do not delete posts, Release assets, audit events, or local logs during triage.
Pause the relevant workflow/LaunchAgent, capture the run URL and timestamps,
then restore from the last verified Release asset if state corruption is proven.
