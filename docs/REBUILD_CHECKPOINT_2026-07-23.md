# Social Automation rebuild checkpoint — 2026-07-23

## Design intent

- Earn qualified attention with helpful, platform-native Meta content.
- Adapt cadence and subject mix from each platform's own audience evidence.
- Generate high-quality Substack drafts for owner review; never auto-publish.
- Route one-off owner material to Meta or Substack from one authenticated UI.
- Accumulate content, knowledge, audience, engagement, and learning evidence in
  a professional dashboard.

## Autonomy envelope

May proceed autonomously:

- collect public sources and platform metrics;
- compose drafts and queue reversible work;
- run deterministic tests, sync metadata, and produce proposals;
- preserve and verify canonical state.

Requires owner decision:

- first Meta canary and any move to `AUTOMATION_MODE=live`;
- enabling Worker `ENABLE_META_PUBLISH_NOW`;
- frequency increases or applying a learning proposal;
- publishing a Substack draft;
- renaming the GitHub repository if it would change the live Pages URL.

Must stop:

- duplicate-post or state-integrity evidence;
- credential/authentication failure;
- invalid or stale platform metrics presented as healthy evidence;
- a state lease conflict or failed bundle readback;
- any conflict between Meta publishing and Substack draft-only intent.

## Evidence ledger

| Claim | State | Evidence |
|---|---|---|
| Old full-pipeline and Reels cron stopped | PROVEN | GitHub workflow readback reported `disabled_manually` before reconstruction |
| Runtime state transported by Release | PROVEN | Revision 8; SQLite `quick_check=ok`; versioned bundle plus manifest readback |
| Cross-writer lease works | PROVEN | Remote lock acquisition/readback/release smoke on `runtime-state-v1` |
| Unit/integration behavior is regression-clean | PROVEN | `python -X utf8 -m pytest -q` -> 485 passed |
| Worker API v3 is remotely reachable | PROVEN | health/auth/create/idempotency/service-sync/canary-lock remote smoke; v4 quality API is local-smoke proven and pending deploy |
| D1 legacy baseline imported | PROVEN | 807 posts, 381 engagement snapshots, 29,219 knowledge metadata, 1 proposal |
| Meta publishing is paused | PROVEN | GitHub variable and Worker dashboard both report `paused` |
| Substack never auto-publishes | PROVEN | Worker returns `substack_auto_publish=false`; UI and policy are draft-only |
| Audience snapshots exist for all platforms | PROVEN | Audience run `30008413852` + D1 readback: Facebook 28, Instagram 9, Threads 3,748; all health=`healthy` at 2026-07-23T12:47:30Z |
| Updated Mac scripts and both LaunchAgents execute on macOS | UNKNOWN | Windows host cannot prove launchd or Substack session behavior; first Mac smoke remains required |
| A fresh Substack submission reaches a real draft | UNKNOWN | Requires updated Mac launchd scripts and a controlled submission canary |
| Facebook `post_clicks` is accepted | PROVEN | Engagement canary run `30006637331`; `post_clicks` produced no contract error |
| Current three-platform metric contract is healthy | PROVEN | Main engagement run `30008220682`: Facebook and Instagram contract healthy (zero latest signal); Threads healthy with nonzero views |
| Facebook impressions / engaged-users metrics are usable | BLOCKED | Live canaries rejected `post_impressions`, `post_impressions_unique`, and `post_engaged_users`; they are excluded from current truth |
| Legacy repos no longer compete for ownership | PROVEN | GitHub readback reports `news-radar-pm` and `news-radar-dashboard` archived; canonical repo metadata points to the in-repo dashboard |
| Instagram low values are real audience response | UNKNOWN | API contract may be healthy, but signal coverage requires canary evidence |
| Live Meta publish works without duplicates | BLOCKED | Intentionally held until owner canary approval |

## Safety invariants

- No existing social post is deleted by this rebuild.
- A successful post has a partial unique DB index and pre-publish idempotency
  checks; Reels use platform-specific identities.
- State writers acquire the same Release lease before pull-modify-push.
- Submissions use an owner token; service claims and sync use a separate token.
- Browser storage is `sessionStorage`; the GitHub credential proxy was removed.
- Worker legacy `GITHUB_PAT` was deleted after authenticated remote smoke.
- Knowledge sync exports metadata and evidence summaries, not article bodies.
- Content-quality evidence stores rule metadata and SHA-256 only. New rewrite
  findings receive one retry and then hold for review; historical backfill is
  observation-only.
- Platform cadence is independent and proposal-only. Runtime overrides require
  an exact owner-approved proposal with drift, sample, coverage, ratio, bounds,
  compare-and-swap, and readback gates.

## Completion audit addendum

| Claim | State | Evidence |
|---|---|---|
| Historic per-platform quality can be measured without status mutation | PROVEN | Local dry-run evaluated 717 variants; copy backfill inserted 717 evidence rows, `status_mutations=0`, SQLite `quick_check=ok` |
| Current metric coverage is sufficient for automatic cadence changes | BLOCKED | Latest Release-copy dry-run: Facebook current/baseline 0%; Instagram and Threads current 23/34 (67.6%), baseline 14/35 (40.0%); all below the 80% gate |
| Hourly collector prevents future bucket-alignment misses | ASSUMED | Deterministic minute-coverage tests pass with ±45 minutes; future scheduled production rows still require observation |
| Content-quality and cadence changes are deployed in production | UNKNOWN | Requires merged commit, D1 migration 0005, Worker/Pages deploy, and a production learning-review readback |

## Owner canary packet

Recommended first canary after observability workflows and Mac scripts pass:

1. Keep scheduled automation paused.
2. Select one current, useful, non-sensitive source with a clear factual hook.
3. Compose all three platform variants but publish Threads only.
4. Verify platform post ID, visible content, publish log, D1 post record, and
   initial engagement snapshot.
5. Wait at least eight hours; inspect quality and metric health.
6. If clean, canary Facebook and Instagram separately. Do not batch all three
   into the first proof.
7. Only then consider `AUTOMATION_MODE=live`; keep frequency at bootstrap values
   until the 14-day/minimum-sample gate is satisfied.

Default if owner defers: remain paused while engagement, audience, knowledge,
and draft-only Substack evidence continue to accumulate.
