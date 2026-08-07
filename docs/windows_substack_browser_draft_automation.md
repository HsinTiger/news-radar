# Windows Substack Browser Draft Automation

## Goal / non-goals

- Goal: after the Windows writer produces the daily 2 Podcast artifacts or the
  Sunday company artifact, create Substack drafts in the owner's existing
  signed-in browser session and prove each result with an editor draft ID.
- Non-goal: publish a post, extract or persist browser cookies, change the
  owner's GitHub token, revive Mac composition, or alter Meta automation.

## Evidence status

| Statement | Status | Evidence |
|---|---|---|
| Windows writes two Podcast artifacts and one Weekly company artifact | PROVEN | worker contract and unit tests |
| The in-app browser is signed in to the owner publication | PROVEN | 2026-08-07 drafts-page readback |
| Draft IDs `210206803`, `210207938`, and `210208078` exist remotely | PROVEN | authenticated drafts-list readback on 2026-08-07 |
| A standalone scheduled task can reuse that session unattended | UNKNOWN | first scheduled run is 2026-08-08 12:00 Asia/Taipei |
| Both daily drafts finish by 13:00 | UNKNOWN | requires a timed unattended run receipt |

## Options / decision

1. Export browser cookies into `.env`: rejected. It creates a new credential
   copy, expires independently, and violates the owner's no-reauthorization
   boundary.
2. Keep local-only artifacts: rejected. The owner requires drafts visible in
   Substack by the daily review time.
3. Use the signed-in Substack Browser session: selected. It reuses the proven
   interactive path without exposing credentials and can read back the durable
   editor URL.

## Contracts and invariants

1. The Python writer remains credential-free and produces reader-ready article,
   metadata, and `cover.png` artifacts.
2. `windows_substack_browser_handoff.py prepare` accepts exactly two new
   `podcast_` artifacts for `podcast-batch` or one new `company_` artifact for
   `weekly`, all created after the run start time.
3. The browser task searches the drafts list for the exact title before Create.
   If an exact matching draft already exists for the current handoff, it reuses
   and records that ID instead of creating a duplicate.
4. New posts are drafts only. Audience is `Everyone`; Paid is forbidden.
5. Title, subtitle, article body, and `cover.png` come from the manifest paths.
   The browser must preserve rendered headings, links, lists, quotes, source
   section, and CTA instead of exposing raw Markdown syntax. `cover.png` is the
   post cover/thumbnail and social preview; it is not an inline image prompt.
6. Success requires an HTTPS `*.substack.com/publish/post/<numeric-id>` editor
   URL for every artifact, followed by a drafts-list readback of the exact title.
7. `record` writes the ID into the handoff manifest and artifact metadata.
   `verify` fails until the full expected set is complete.
8. Authentication loss, CAPTCHA, missing browser capability, missing artifact,
   cover failure, duplicate ambiguity, or ID mismatch stops the task. Local
   files remain available; no publication is attempted.

## Verification matrix

| Requirement | Executable evidence | Remaining limitation |
|---|---|---|
| Exact 2/1 artifact scope | unit tests for `prepare_handoff` | does not prove future LLM completion time |
| Everyone/draft-only | manifest contract plus browser UI readback | UI labels may change |
| No false remote success | URL/ID validation and `verify` | Substack has no public draft-read API |
| No credential replication | code/static inspection | signed-in session can still expire |
| Production readiness | first unattended Scheduled run with IDs | computer and Codex app must remain running |

## Recovery

- Keep the generated local/OneDrive artifacts when Browser fails.
- Fix the login/session interactively; rerun the same handoff.
- Search exact title before creating anything. Never blind-retry Create.
- Do not report `complete` until every expected editor draft ID is recorded.
