# Meta Recovery Mode — 2026-07-24

## Owner intent

Restart useful, attention-earning Meta publishing without a Mac, while learning whether stalled growth is primarily an interest, trust, utility, or format problem.

## Current evidence

- `PROVEN`: canonical runtime is `HsinTiger/news-radar`.
- `PROVEN`: before Recovery, 212 successful posts per platform were produced over 43 active days and the same drafts were cross-posted everywhere. The first Recovery canary raised Threads to 213 while Facebook and Instagram remain at 212.
- `PROVEN`: Threads metrics are usable. The current audited set has 103 latest snapshots with median 277 views. The first experiment keeps its frozen 102-post / 279.5-view baseline so the benchmark does not move during measurement.
- `PROVEN`: robust medians disagree with the earlier outlier-led ranking. `earnings` has median 376 views over 18 posts and `supply_chain` 337 over 18; `current_affairs` is 260 over 7 and `tech_product_launch` 206 over 9. Topic weights therefore use robust medians plus editorial usefulness, not the largest viral post.
- `PROVEN`: primary-tier sources have median 379 Threads views over 29 posts versus 256 over 74 secondary-tier posts. Recovery ranking now uses source tier as a bounded multiplier.
- `PROVEN`: carousel-backed Threads posts have median 287.5 views over 94 posts versus 121 over 9 feed-only posts.
- `PROVEN`: median Threads actions are zero and only 48 of 103 posts have any nonzero action. Reach outliers exist, but useful interaction and follower conversion remain the main unproven problem.
- `PROVEN`: Facebook legacy engagement measurement is degraded; 126 of 127 samples contain API error markers.
- `PROVEN`: Instagram cold-start distribution is near zero and needs a visual-format experiment.
- `UNKNOWN`: the two follower snapshots on 2026-07-23 are only about one hour apart, so they do not prove a long-term plateau or its cause.

## Recovery contract

| Platform | Initial cadence | Purpose |
|---|---:|---|
| Threads | 1/day at 16:00 Asia/Taipei | Timing experiment: historical 16:00 median 625 views over 5 posts; frequency unchanged |
| Facebook | Tue/Fri 20:00 | Evidence-backed explainer; measurement-first |
| Instagram | Wed/Sat 20:00 | Native carousel; format-first cold-start test |

Every recovery post has exactly one experiment type: `interest`, `trust`, `utility`, or `format`. It records the hypothesis, topic, format, follower baseline, primary-metric baseline, real platform post ID, and the latest 1h/24h/168h result.

Recovery safeguards:

1. Legacy queued drafts are ineligible.
2. Primary-tier sources are preferred inside each topic; social sources are down-ranked, not silently promoted to evidence.
3. Every factual paragraph needs a named source. Generic `根據報導` attribution, unattributed allegations, and unsupported measured claims trigger a rewrite.
4. A concrete reader consequence plus a usable next action are both required; risk words or rhetorical questions alone do not count.
5. Strategy jargon and dramatic frames are held when they obscure the useful point.
6. Unsupported statistics trigger one rewrite; unresolved drafts are held.
7. Recovery cadence ignores old live-mode frequency overrides.
8. No recommendation may increase frequency before 168h evidence.

The 16:00 Threads slot is a bounded timing hypothesis, not a proven optimum.
Keep it only after at least three Recovery posts complete their 168h windows;
topic, format, and source-quality effects remain possible confounders.

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
