# Substack Social Reach Design

## 1. Goal / non-goals

Goal: let Podcast and company research notice useful public X and Reddit
conversations without turning social posts, search snippets, login sessions, or
anti-bot workarounds into evidence.

Non-goals:

- install or execute the Agent-Reach bundle;
- export cookies, refresh credentials, automate login, solve CAPTCHA, or evade
  platform controls;
- treat engagement, a search snippet, or repeated community claims as factual
  corroboration;
- make X or Reddit availability a prerequisite for producing an article.

## 2. Evidence status

- `PROVEN`: three public Reddit RSS feeds and deterministic `old.reddit.com`
  rewriting already exist in this repository.
- `PROVEN`: the previous public RSSHub X route is disabled because it returned
  deprecated/404 behavior.
- `PROVEN`: the deep-research pack currently excludes X and Reddit domains.
- `ASSUMED`: public social discussion can improve question discovery when the
  writer is prevented from promoting it into evidence.
- `UNKNOWN`: public X pages will remain readable without an authenticated,
  transient browser session.

## 3. Options / decision

| Option | Reach | Risk | Decision |
|---|---|---|---|
| Install Agent-Reach bundle | Potentially broad | Cookie/session, account-ban, supply-chain and maintenance surface | Reject |
| Keep excluding social platforms | Safest | Misses useful objections and first-hand claims | Reject |
| Native public, read-only reach layer | Partial but observable | Search/platform degradation remains | Select |

The selected design borrows only Agent-Reach's capability registry, ordered
fallback, health reporting, and fail-soft behavior. X candidates must be real
`/status/` URLs and use the official public oEmbed endpoint; Reddit candidates
must be `/comments/` URLs and try public JSON before the existing page reader.

## 4. Contracts / invariants

`SocialSignal` records platform, canonical URL, title, excerpt, access method,
and one of two evidence states:

- `attributed_claim`: direct public page text was read. The writer may say who
  made the claim, but the claim does not corroborate itself.
- `discovery_only`: only discovery metadata or a search snippet was available.
  It may create an upstream research query but must not be quoted or presented
  in the article.

The reach report also records per-platform health (`available_public`,
`lead_only`, `unavailable`, or `degraded`). Social domains never enter the
five-to-ten-source evidence pack. Durable non-social sources found by following
a social lead may enter through the existing read-and-validate gate.

No credential or private-session input exists in the module interface.

## 5. Verification matrix

| Requirement | Executable evidence | Remaining limitation |
|---|---|---|
| Readable Reddit becomes attributed claim | Unit test with public-reader fake | Does not prove live Reddit availability |
| Unreadable X remains discovery-only | Unit test with failed-reader fake | Does not prove live X availability |
| Social lead creates upstream queries | Unit test | Search ranking can drift |
| Social domains cannot count among 5–10 evidence sources | Existing and new research tests | Source quality still needs owner review |
| Writer preserves evidence boundary | Prompt-contract test | Model output still receives deterministic audit and owner review |
| No hidden credential path | Interface/static inspection | OS/browser state remains outside this module |

## 6. Implementation slices

1. Add typed social-signal collection, ordered public fallbacks, and health.
2. Build a combined research bundle: social leads plus validated evidence.
3. Add the social boundary to the deep-writer prompt and metadata.
4. Run focused and full regression tests before generating any draft.

## 7. Risks / owner gates

- Platform changes may reduce reach to zero; this is `degraded`, not a reason to
  fabricate or silently switch to private-session scraping.
- A social claim can still be misleading even when read directly. It remains a
  named viewpoint until an independent primary source supports it.
- Any future authenticated X API, paid provider, or browser-session automation
  is a separate owner decision and requires its own credential and ToS review.

## 8. Decision changes

- 2026-08-07: selected the native public/read-only design; Agent-Reach remains a
  reviewed pattern source, not an installed runtime dependency.
