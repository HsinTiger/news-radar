# Meta Recovery Mode — 2026-07-24

## Owner intent

Restart useful, attention-earning Meta publishing without a Mac, while learning whether stalled growth is primarily an interest, trust, utility, or format problem.

## Current evidence

- `PROVEN`: canonical runtime is `HsinTiger/news-radar`.
- `PROVEN`: before Recovery, 212 successful posts per platform were produced over 43 active days and the same drafts were cross-posted everywhere. The first Recovery canary raised Threads to 213 while Facebook and Instagram remain at 212.
- `PROVEN`: the current latest-snapshot cohort contains 104 posts per platform. Threads median views are 274; 58.7% have zero likes, 83.7% zero replies, and 93.3% zero reposts. Reach exists, but useful interaction is sparse.
- `PROVEN`: robust medians disagree with the earlier outlier-led ranking. `earnings` has median 376 views over 18 posts and `supply_chain` 337 over 18; `current_affairs` is 260 over 7 and `tech_product_launch` 206 over 9. Topic weights therefore use robust medians plus editorial usefulness, not the largest viral post.
- `PROVEN`: primary-tier sources have median 379 Threads views over 29 posts versus 256 over 74 secondary-tier posts. Recovery ranking now uses source tier as a bounded multiplier.
- `PROVEN`: carousel-backed Threads posts have median 287.5 views over 94 posts versus 121 over 9 feed-only posts.
- `PROVEN`: Facebook has 104 latest engagement snapshots with only four total likes and no comments or shares. Facebook reach/view is `UNKNOWN`, not zero: the API collector currently receives post clicks and reaction totals but not reach/impressions.
- `PROVEN`: Instagram has 104 latest snapshots with median reach zero; 67.3% have zero reach and 96.2% zero likes. This is a cold-start distribution problem, not evidence of a permanent ban.
- `PROVEN`: platform API audience snapshots at 2026-07-24 14:07 Asia/Taipei report Facebook 28 followers, Instagram 9, and Threads 3,749. The stored history spans only about 18 hours: Facebook and Instagram are unchanged while Threads moved from 3,748 to 3,749.
- `PROVEN`: active publishing days averaged 4.93 successful Facebook posts, 4.93 Instagram posts, and 4.84 Threads posts; the maximum was nine per platform in one UTC day. This is materially above the owner's remembered three-to-four posts.
- `ASSUMED`: frequency amplified weak per-post signals and audience fatigue. The available Threads cohort shows 68–75% zero-interaction posts on days with four or more measured posts, but the relationship is observational, incomplete, and confounded by topic, time, and format. Do not claim frequency alone caused low reach.
- `PROVEN`: Threads post `18059705018757201` proves API delivery only. It was composed and checked under `2026-07-24.recovery-v2`; it is excluded from the current editorial-quality and recovery-performance cohort.
- `PROVEN`: Facebook runs `30085392911` and `30086522743` produced only quality-held drafts; publish had zero attempts. Run `30087468749` was cancelled before publish when a rolling-window verifier contamination bug was found. No Facebook post ID exists for 2026-07-24.
- `PROVEN`: PRs #38–#40 isolate the Taiwan public-interest scorer/composer, introduce guard `2026-07-24.taiwan-daily-v6`, and scope compose verification to the current run's exact UTC boundary. Seven production drafts from run `30086522743` replay 7/7 PASS through v6; this is deterministic guard evidence, not publishing proof.
- `UNKNOWN`: Account Status / recommendation eligibility inside the Meta products has not been observed. Low metrics alone do not prove a shadowban or account-level recommendation penalty.

## Why volume did not create distribution

Meta does not describe one permanent page score that can be overcome by posting
more. Its published explanations describe per-person predictions combining many
signals. Facebook says sharing can indicate value, combines behavioral and survey
signals such as whether a post was worth people's time, and reduces distribution
for problematic or low-quality content. Instagram says Feed predicts whether a
person will spend a few seconds, comment, like, share, or tap the profile; Explore
places greater weight on the amount and speed of likes, saves, and shares.

Primary references:

- [How AI Influences What You See on Facebook and Instagram](https://about.fb.com/news/2023/06/how-ai-ranks-content-on-facebook-and-instagram/)
- [How Does News Feed Predict What You Want to See?](https://about.fb.com/news/2021/01/how-does-news-feed-predict-what-you-want-to-see/)
- [Instagram Ranking Explained](https://about.instagram.com/blog/announcements/instagram-ranking-explained)

The operational interpretation is therefore:

1. More posts create more recommendation tests; they do not create entitlement to reach.
2. A post that receives almost no saves, shares, comments, replies, or profile interest gives the next ranking stages little positive evidence.
3. Repeated generic frames (`護城河`, `真正的賽局`, `代價`, `神話破滅`) make separate stories look interchangeable and weaken trust.
4. Cross-posting the same editorial shape ignores platform-specific predictions: Facebook needs worth-time conversation, Instagram needs save/share-worthy visual utility, and Threads needs concise native relevance.
5. Political or food-safety exaggeration can damage both trust and recommendation eligibility; correctness and attributed evidence remain hard gates even when a sharper hook might get more clicks.

## Recovery contract

| Platform | Initial cadence | Purpose |
|---|---:|---|
| Threads | Daily at 08:00 Asia/Taipei | Morning-commute attention experiment; historical timing evidence is confounded, so confidence remains LOW |
| Facebook | Daily at 18:00 Asia/Taipei | Evening-commute evidence explainer; measurement-first |
| Instagram | Daily at 20:00 Asia/Taipei | Post-commute native carousel; format-first cold-start test |

GitHub schedule delivery is best-effort. The governed scheduler has four
off-peak opportunities inside each approved local window (`:07`, `:22`, `:37`,
`:47`) and does not wake during unrelated hours. The per-platform daily quota
and 20-hour interval remain the duplicate-prevention authority; redundant
scheduler runs never widen the publishing envelope.

Every recovery post has exactly one experiment type: `interest`, `trust`, `utility`, or `format`. It records the hypothesis, topic, format, follower baseline, primary-metric baseline, real platform post ID, and the latest 1h/24h/168h result. The initial platform hypotheses are deliberately different: Facebook tests trust with a sourced accountability explainer; Instagram tests utility/format with a standalone five-card carousel; Threads tests interest with a concise consequence-first post.

Recovery safeguards:

1. Legacy queued drafts are ineligible.
2. Source authority is explicit: official record > exchange/company disclosure > public broadcaster/wire > independent fact-check > named media. Feed curation tier alone never turns a media article into a primary record.
3. A high-risk political, food-safety, legal, corruption, or health allegation from ordinary media is held unless the same event has authoritative corroboration. Broad same-beat matches do not count.
4. Freshness is measured from actual execution time; a fully stale batch and future-dated poison rows fail closed.
5. Every factual paragraph needs a named source. Generic `根據報導` attribution, unattributed allegations, and unsupported measured claims trigger a rewrite.
6. The first 45 Chinese characters must contain a named actor and a verifiable number or concrete consequence; background-first hooks trigger a rewrite.
7. A concrete reader consequence plus a usable next action are both required; risk words or rhetorical questions alone do not count.
8. Strategy jargon and dramatic frames are held when they obscure the useful point.
9. Unsupported statistics trigger one rewrite; unresolved drafts are held.
10. Recovery cadence ignores old live-mode frequency overrides.
11. No recommendation may increase frequency before seven posts per platform have complete 168h evidence.
12. Compose verification uses the exact UTC boundary recorded immediately before the current compose stage. Earlier held drafts may not fail or prove the current release.

Every slot is a bounded timing hypothesis, not a proven optimum. Do not move a
slot until at least seven posts on that platform complete their 168h windows;
topic, format, and source-quality effects remain possible confounders.

Timing analysis uses the actual Asia/Taipei `publish_log.posted_at`, one latest
168h snapshot per real platform post, and only the current quality-guard cohort.
Legacy cadence, engagement-fetch timestamps, duplicated polling rows, and API
error payloads are ineligible. Results remain proposal-only until seven complete
posts exist on that platform.

## Activation sequence

1. Keep repository variable `AUTOMATION_MODE=paused`.
2. Merge and deploy code.
3. Apply D1 migrations before Worker deployment.
4. Enable `full_pipeline.yml`, but leave the scheduler paused.
5. Dispatch one platform-only run inside that platform's approved local slot with `automation_mode=recovery` and a governed scheduler reason.
6. Require all of:
   - Full Cloud Pipeline compose and publish verification passed for the exact platform scope and current-run boundary.
   - `publish_log.success=1` with a non-empty Threads platform post ID.
   - latest-post metric probe can read that post from the platform API; a database row alone is insufficient.
   - recovery experiment row is visible in D1/dashboard.
7. Keep repository variable `AUTOMATION_MODE=recovery` only while each platform remains inside its independent daily quota and slot envelope.
8. A failure on one platform does not authorize replay outside its slot and does not block another platform's independent canary.

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
