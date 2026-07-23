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
| Runtime state transported by Release | PROVEN | Revision 10 from engagement run `30014862699`; SQLite `quick_check=ok`; 29,219 news / 239 drafts / 808 publish rows / 384 engagement rows / 717 quality evaluations |
| Cross-writer lease works | PROVEN | Remote lock acquisition/readback/release smoke on `runtime-state-v1` |
| Unit/integration behavior is regression-clean | PROVEN | Latest local gate on merge `696b373`: `python -X utf8 -m pytest -q` -> 513 passed |
| Worker API v4 is remotely deployed | PROVEN | Version `fe42ae43-e2cf-4988-9ba9-7360d3842b7d`; `/health` reports `2026-07-23.v4`; unauthenticated dashboard/submissions return 401 and legacy proxy returns 410 |
| D1 operational baseline is current | PROVEN | Migration `0005_content_quality.sql`; 384 engagement snapshots after run `30014862699`; latest quality summaries cover 177/177 recent candidates per platform |
| Meta publishing is paused | PROVEN | GitHub variable and Worker dashboard both report `paused` |
| Substack never auto-publishes | PROVEN | Worker returns `substack_auto_publish=false`; UI and policy are draft-only |
| Audience snapshots exist for all platforms | PROVEN | Audience run `30008413852` + D1 readback: Facebook 28, Instagram 9, Threads 3,748; all health=`healthy` at 2026-07-23T12:47:30Z |
| Updated Mac scripts and both LaunchAgents execute on macOS | UNKNOWN | Windows host cannot prove launchd or Substack session behavior; first Mac smoke remains required |
| A fresh Substack submission reaches a real draft | UNKNOWN | Requires updated Mac launchd scripts and a controlled submission canary |
| Facebook `post_clicks` is accepted | PROVEN | Engagement canary run `30006637331`; `post_clicks` produced no contract error |
| Current three-platform metric contract is healthy | PROVEN | Run `30014862699` captured one 168h bucket per platform, `OK=3/3`; D1 readback: FB zero response, IG views=2/reach=1, Threads views=235/likes=2, all `metric_status=ok` |
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
| Historic per-platform quality can be measured without status mutation | PROVEN | Production learning run `30014620742` inserted 717 evidence rows with `status_mutations=0`; Release revision 9 readback reported all 717 rows |
| Current metric coverage is sufficient for automatic cadence changes | BLOCKED | Latest Release-copy dry-run: Facebook current/baseline 0%; Instagram and Threads current 23/34 (67.6%), baseline 14/35 (40.0%); all below the 80% gate |
| Hourly bucket collector works in production | PROVEN | Workflow cron is `11 * * * *`; run `30014862699` selected three 168h tasks, committed `OK=3/3`, pushed Release revision 10, and synced all three rows to D1 |
| Content-quality and cadence gates are deployed | PROVEN | PR #12 / merge `b9d0461`; Pages run `30014546469`; Worker v4; learning run `30014620742` returned `insufficient_metric_coverage` and `proposal_id=null` for all platforms; D1 cadence proposal count remains 0 |
| One-off UI routing is deployed but processing is intentionally paused | PROVEN | Live page renders Meta/Substack plus FB/IG/Threads choices; `SUBMISSION_PROCESSOR_MODE=paused`; latest scheduled poller run was skipped |

## Platform-atomic publishing addendum

| Claim | State | Evidence |
|---|---|---|
| Compose, buffer, cadence, selection, and retry are platform-scoped | PROVEN | PR #14 / merge `9dff7df`; regression suite `501 passed`; tests cover Threads-only compose, cross-platform buffer isolation, platform cadence, partial retry, and success-tuple idempotency |
| `published` requires all intended platform variants to have success evidence | PROVEN | `pending_publish_platforms()` drives queue/direct terminal state; partial/all-failed workflows remain red and queued; failure-injection tests retry only missing tuples |
| Publish-now has durable lineage and truthful terminal state | PROVEN | Deterministic `submission -> news -> draft -> platform_drafts -> publish_log`; workflow reads result JSON; Worker/Dashboard v5 support `partial` and `quality_held` |
| Queue submissions preserve per-submission platform intent | PROVEN | `control_submission` plus `control_route` tags; operational sync independently derives each submission's requested-platform completion |
| Local Substack output is no longer mistaken for a remote draft | PROVEN | `substack_written_at` is local evidence; only `post_draft` id writes `substack_draft_id` + `substack_drafted_at`; control submissions use `--require-substack-draft` |
| Worker v5 is deployed with all publish switches locked | PROVEN | Version `561ed2b7-81de-4c28-b1db-1af8ad7c4bb9`; cache-busted `/health`=`2026-07-23.v5`; deploy bindings show `AUTOMATION_MODE=paused`, `SUBMISSION_PROCESSOR_MODE=paused`, `ENABLE_META_PUBLISH_NOW=false` |
| Owner surfaces and metadata sync are current | PROVEN | Pages run `30018723051`; dashboard and submit page HTTP 200; operational sync `30018941803` restored Release `quick_check=ok` and sent 480 posts / 325 engagement / 500 knowledge / 17 proposals |
| Scheduled publishing stayed paused after deploy | PROVEN | Scheduler run `30019137215` skipped on merge `9dff7df`; manual poller smoke `30019167635` returned `NO_SUBMISSION` |
| Updated Mac LaunchAgents create a real Substack draft | UNKNOWN | Code and fail-closed preflight are deployed, but Windows cannot prove the Mac environment, cookie, launchd load state, or a new remote draft id |
| Live Meta partial-retry behavior is duplicate-free | BLOCKED | Unit/integration evidence is complete; a real Threads-only canary remains intentionally unrun until separate owner canary approval |

## Owner-submission routing addendum

| Claim | State | Evidence |
|---|---|---|
| Meta and Substack owner submissions cannot enter each other's automatic source pools | PROVEN | PR #17 / merge `696b373`; focused routing/lineage gate `28 passed`; full regression `513 passed` |
| Owner-directed Meta material may bypass news relevance dropping but cannot bypass the deterministic quality guard | PROVEN | `process_item` route tests cover low relevance -> quality path, compose block, unresolved rewrite hold, and requested-platform isolation |
| Unreadable URL submissions fail closed instead of being reported as queued content | PROVEN | Both submitters reject insufficient readable text; CLI/workflow failure propagation and retained-pending tests pass |
| Duplicate owner submissions preserve every control-plane lineage and priority tag | PROVEN | Meta and Substack duplicate-lineage tests pass; no duplicate content row is created |
| Submission status is platform-aware and does not let a later PASS on one platform mask a quality hold on another | PROVEN | Per-platform latest quality evidence plus per-submission route tests; `partial` and `quality_held` are deployed in the owner UI |
| Updated owner UI is live | PROVEN | Pages run `30021866231`; cache-busted HTTP readback returned 200 and contained `partial`, `quality_held`, and the no-false-published policy |
| Production metadata sync remains healthy after the routing change | PROVEN | Operational sync `30021928175`: Release DB `quick_check=ok`; sent 480 posts / 325 engagement / 3 quality summaries / 500 knowledge / 17 proposals / 6 health rows / 0 submission updates |
| External publishing remains locked | PROVEN | GitHub variables read back `AUTOMATION_MODE=paused` and `SUBMISSION_PROCESSOR_MODE=paused`; Full Cloud Pipeline and Reels workflows remain `disabled_manually` |

Resumable next action: keep all publishing switches paused. On the Mac, update
the clone/LaunchAgents and run one non-sensitive Substack control submission;
require a visible draft id plus canonical `substack_drafted_at` readback. Only
after that evidence should the owner consider the Threads-only Meta canary.

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
