# Meta Recovery Mode — 2026-07-24

## Owner intent

Restart useful, attention-earning Meta publishing without a Mac, while learning whether stalled growth is primarily an interest, trust, utility, or format problem.

## Current evidence

- `PROVEN`: canonical runtime is `HsinTiger/news-radar`.
- `PROVEN`: 212 successful posts per platform were produced over 43 active days; the same 212 drafts were cross-posted to all three platforms.
- `PROVEN`: Threads metrics are usable. Historical median latest-snapshot views are 279.5 across 102 posts; `current_affairs` and `tech_product_launch` produced the strongest long-tail results.
- `PROVEN`: Facebook legacy engagement measurement is degraded; 126 of 127 samples contain API error markers.
- `PROVEN`: Instagram cold-start distribution is near zero and needs a visual-format experiment.
- `UNKNOWN`: the two follower snapshots on 2026-07-23 are only about one hour apart, so they do not prove a long-term plateau or its cause.

## Recovery contract

| Platform | Initial cadence | Purpose |
|---|---:|---|
| Threads | 1/day at 12:00 Asia/Taipei | Source-backed practical consequence; usable historical baseline |
| Facebook | Tue/Fri 20:00 | Evidence-backed explainer; measurement-first |
| Instagram | Wed/Sat 20:00 | Native carousel; format-first cold-start test |

Every recovery post has exactly one experiment type: `interest`, `trust`, `utility`, or `format`. It records the hypothesis, topic, format, follower baseline, primary-metric baseline, real platform post ID, and the latest 1h/24h/168h result.

Recovery safeguards:

1. Legacy queued drafts are ineligible.
2. Named source attribution and a concrete reader benefit are required.
3. Unsupported statistics trigger one rewrite; unresolved drafts are held.
4. Recovery cadence ignores old live-mode frequency overrides.
5. No recommendation may increase frequency before 168h evidence.

## Activation sequence

1. Keep repository variable `AUTOMATION_MODE=paused`.
2. Merge and deploy code.
3. Apply D1 migrations before Worker deployment.
4. Enable `full_pipeline.yml`, but leave the scheduler paused.
5. Dispatch one Threads-only run with `automation_mode=recovery` and `dispatch_reason=recovery-canary`.
6. Require all of:
   - Full Cloud Pipeline publish verification passed for the Threads scope.
   - `publish_log.success=1` with a non-empty Threads platform post ID.
   - latest-post metric probe can read that post from the platform API.
   - recovery experiment row is visible in D1/dashboard.
7. Only then set repository variable `AUTOMATION_MODE=recovery`.
8. Facebook and Instagram enter on their independent weekly slots. A failure on one platform does not widen or stop the others.

## Immediate rollback

```powershell
gh variable set AUTOMATION_MODE --body paused
gh workflow disable full_pipeline.yml
```

Rollback stops new automatic dispatch. It does not delete published posts or evidence.

## Truth boundary

A green workflow alone is not publishing proof. The minimum claim chain is:

`workflow scope -> publish_log success -> platform post ID -> platform API readback -> 1h/24h/168h snapshots -> follower delta`.

Substack remains draft-only and independent of this recovery path.
