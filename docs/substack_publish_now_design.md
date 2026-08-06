# Substack publish-now design

## Goal / non-goals

- `PROVEN` Goal: the one-time owner submission page lets the owner explicitly choose either `draft_priority` or `publish_now` for Substack.
- `PROVEN` Default: Substack remains draft-first. Scheduled Podcast and company jobs remain draft-only.
- `PROVEN` Non-goal: no global auto-publish switch, no change to the GitHub owner token, Substack cookies, or existing authorization storage.
- `PROVEN` Delivery boundary: a workflow dispatch or remote draft ID is not publication proof. `published` requires a public post ID, public URL, publication timestamp, and unauthenticated URL readback.
- `UNKNOWN` Live boundary: the Mac cookie, launchd job, and current Substack backend cannot be proven from Windows tests. A live canary needs an owner-approved article.

## Options / decision

1. Publish from Cloudflare or GitHub Actions. Rejected: Substack has no public write API and the current cookie is intentionally held only on the Mac.
2. Reuse the Mac fast lane and publish the draft it just created. Selected: it preserves the existing authorization boundary and keeps writing, cover, and quality gates in one runtime.
3. Turn every scheduled draft into an auto-published post. Rejected: it removes the human safety boundary from unrelated daily and weekly schedules.

## Contracts / invariants

1. UI mode is explicit per submission. Missing mode means `draft_priority`; `publish_now` is never inferred.
2. `publish_now` is admitted only when the control-plane submission processor is live and the feature flag is enabled.
3. The canonical source row carries a `publish_now` tag. Only that tag lets the Mac drain pass `--publish-now`.
4. Draft creation is durable before publication. A retry reuses `substack_draft_id`; it never calls `post_draft` again for the same source.
5. The runtime calls `prepublish_draft`, then records a publish intent, then calls `publish_draft` on that same ID.
6. A publish intent makes an ambiguous retry fail closed: later runs check public readback first and do not blindly resend the publish/email request.
7. `published` requires `substack_post_id`, `substack_post_url`, `substack_published_at`, and an unauthenticated successful URL readback. A draft without those fields is `partial` for `publish_now`, and `draft_created` for draft mode.
8. Cover upload may continue without a cover, matching the existing draft behavior; article quality and reader-ready gates remain mandatory.
9. Newsletter delivery uses the wrapper default `send=true`; automatic social sharing remains false. `SUBSTACK_PUBLISH_SEND=0` is an explicit operational override.

## Verification matrix

| Requirement | Executable evidence | Remaining limit |
|---|---|---|
| UI offers draft and publish-now, default draft | Node payload test and static dashboard test | Browser rendering checked after deploy |
| Worker accepts and gates Substack publish-now | Worker contract tests and deployed health readback | Health cannot prove Mac cookies |
| Mode reaches the Mac source row | dispatch, workflow, and lineage unit tests | GitHub-to-Mac timing needs live observation |
| Retry reuses one draft | compose/drain unit tests with a canonical draft ID | Real Substack idempotency remains external |
| Published status needs public evidence | receipt, sync, and Worker contract tests | Live URL needs owner-approved canary |
| Existing schedules remain draft-only | plist/editorial regression tests | launchd state remains a Mac readback |

## Implementation slices

1. Dashboard and Worker contract: add explicit mode, capability/readiness, status evidence fields, and D1 migration.
2. Dispatch contract: carry `publish_now` through GitHub workflow and source tags.
3. Mac runtime: reuse/create one draft, publish it, save intent/result receipts, and persist canonical publication evidence.
4. Operational readback: sync `published`/`partial`, public URL, and post ID to the dashboard.
5. Deploy Worker and Pages, then verify public DOM, responsive layout, console, and runtime health. Do not create a live Substack post without owner-approved canary content.

## Risks / owner gates

- The wrapper is unofficial and can drift with Substack. Its publish methods are present in both the minimum supported 0.1.18 and current 0.2.0 releases, but only a live canary proves the current backend.
- `send=true` emails subscribers. The UI therefore requires an explicit publish-now choice every time.
- An ambiguous publish call is not retried automatically. It remains `partial` until public readback proves delivery or the owner resolves it in Substack.
- Deployment can prove routing and UI, not a live publication. The final production claim remains `BLOCKED` until a public URL is read back.
