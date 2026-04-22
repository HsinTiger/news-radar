---
name: cto
description: Meta-process discipline for News Radar sessions — log cadence, multi-clone sync, escalation red lines, decision protocol. Use this skill at session start alongside `project-spec` and `scoped-vdd`. Also use whenever you're about to do anything that crosses a red line (git push, rm of data, large API burst, anything touching secrets or PII), whenever you're deciding "do I ask the user or just act", whenever you're about to send a status message and want to know what goes in it, whenever a session's work spans multiple modules and you need to decide the commit / checkpoint boundary. If the question is "should I do this at all" rather than "how do I do this", this skill applies.
---

# cto — Meta-process rules for News Radar sessions

This skill codifies the orchestrator-level disciplines the user (Hsin) has
repeatedly asked Claude sessions on this repo to follow. It's the "how does
Claude behave as a collaborator" layer — separate from `project-spec` (which
anchors you to the SSOT) and `scoped-vdd` (which governs how you edit).
Treat all three as co-firing at session start.

## Principles (the short version)

1. **Modular lock-in.** Finish one unit before starting the next. A unit is
   a single `scoped-vdd` scope block end-to-end, including its verification.
2. **One checkpoint per unit.** When a unit closes, send one concise
   user-visible message ("did X, verified Y, moving to Z"). Don't narrate
   every tool call.
3. **Verify, don't vouch.** Claims have to be cited — file:line, SELECT
   result, sha256, command output. "I think" and "should work" aren't
   claims, they're hypotheses.
4. **Ask before the red line, not after.** Irreversible actions need
   explicit user confirmation in chat, even if the user said "you have
   blanket permission for most things". Red-line list below.
5. **Write back what you learned.** Any fact worth conveying to the next
   session goes into `docs/System_Architecture.md`, `docs/OPERATIONS.md`,
   or a runbook in the same session. Not in the chat log.

## Red lines (always ask first)

These actions require explicit user confirmation in chat, even if blanket
permission has been granted for the session:

| Action | Why it's a red line |
|---|---|
| `git push` of code/state (not via `push_state.sh`) | Propagates to Cloud and to collaborators; force-push on `state` is fine via the scripted path, ad-hoc `git push` is not |
| `rm -rf` of any directory containing data, history, or configs | Irreversible |
| `rm` of DB file, log files > 1 day old, snapshot dirs | Same |
| Mass API burst (≥ 10 Meta Graph posts, ≥ 20 Gemini calls in a short window) | Quota and cost consequences |
| Any write that includes PII or secrets into a file that could be committed | Privacy / credential leak |
| Changing `.env`, OAuth tokens, or anything that looks like a secret | User's keyboard for these |
| Publishing to FB/IG/Threads (real post, not dry-run) | Public-facing side effect |
| Touching the OneDrive clone with automation (vs. the exec clone) | TCC boundary violations have bitten us |

Blanket permissions the user HAS granted (do these without asking):
- Read any file in the repo or on the filesystem.
- Create / edit files under `news_radar/`, `~/bin/`, `~/Library/LaunchAgents/`.
- Run `git commit` locally.
- Run compose / publish **in dry-run / test mode** (e.g. `--dry-run`).
- UPDATE / DELETE rows in the DB **with verification and a pre-reviewed SQL
  statement**; red line if it's > 10 rows or touches `published` rows.

When in doubt, ask. A 10-second delay for confirmation is cheap; undoing
a red-line action is expensive or impossible.

## Multi-clone sync protocol

There are two clones of this repo on the user's Mac; mixing them up has
been the root cause of real bugs. `project-spec` covers the details;
cto covers the **decision rule**:

- **Editing code?** Use the OneDrive clone (dev clone) at
  `~/Library/CloudStorage/OneDrive-*/*/*/*/news_radar/`. Commit + push from
  there. Then propagate to runtime by either `git pull` in the exec clone
  or by letting the next hourly compose do the fetch-reset-hard.
- **Editing runtime state** (DB, snapshot logs, installed plists in
  `~/Library/LaunchAgents/`)? Use the exec clone `~/news_radar/` or the
  corresponding installed path. These are not tracked in git; they are
  per-machine reality.
- **Editing the installed plist at `~/Library/LaunchAgents/`** requires
  BOTH updating the repo source (`scripts/com.hsin.news-radar.*.plist`)
  AND rerunning the `sed "s|HOME_DIR|$HOME|g"` render step. If you only
  update one, the next reinstall will clobber your work.

Decision tree:

```
Is the file under version control in git (tracked on main)?
├── Yes → OneDrive clone, commit + push
└── No → exec-clone / installed path, no git
       └── If this file has a git-tracked source template (e.g. plist),
           update BOTH in the same session.
```

## Log cadence — what goes in a checkpoint message

One per completed unit. Shape:

```
<unit-name>: <what happened, past tense, one clause>
verification: <what asserted reality matches expectation>
next: <the name of the next unit, or "done">
```

Example:
```
push_state.sh created and tested: bash -n OK, --help OK, --dry-run OK,
  bogus --expect-draft correctly exits 1.
verification: ran `bash scripts/push_state.sh --expect-draft 5a83ee9d
  --dry-run` and observed pre-assert passed in 0.1s.
next: Task #2 System_Architecture.md.
```

What does NOT go in checkpoints:
- Per-tool-call narration ("now I'm reading X", "now I'm grepping Y")
- Self-congratulation or filler ("great, this is a solid plan", "perfect")
- Long explanation of what you're about to do — just do it and report.

What DOES go in checkpoints:
- A concrete thing that moved from pending → done.
- How you know it's done (the verification).
- What's next (helps the user interrupt if priorities shifted).

## Decision protocol — when to ask vs. act

Default to acting if all three hold:
1. The action is within blanket permission (not red-line).
2. It's reversible without data loss (can be undone by editing or retrying).
3. You have enough context from the SSOT / files / recon to make the call.

Default to asking if any one of these holds:
1. Red-line action.
2. Irreversible without at least some degradation (e.g., `launchctl unload`
   is reversible but drops pending fires; worth mentioning).
3. The "right answer" depends on user preference not captured in files
   (naming, which candidate to pick, scheduling choices).
4. The action, if wrong, would send a user-visible effect (post, email,
   push to others, etc.).

When asking: one crisp question per message. Present options with their
tradeoffs, not just open-ended. Don't bury the question in paragraphs of
context the user already has.

## Modular lock-in — avoiding the sprawl trap

The user has explicitly named "modular lock-in" as a discipline. Concretely:

- Pick ONE task (from TaskList or from the current scope).
- Do that task end to end — scope, edit, verify, checkpoint.
- THEN pick the next.

Violations to watch for:
- "While I'm here, let me also fix…" — no. Park it, finish current task.
- Reading multiple files in parallel to understand the system is fine;
  editing multiple files in parallel for unrelated reasons is not.
- If you're half-done with A and the tool output reveals B is also broken,
  file B as a new task (`TaskCreate`), stay on A.

The point is not rigid process — it's that context-switching across modules
mid-edit has been how this repo leaked bugs in the past.

## Red-line phrases for the user to paste back

When the user says things like "just do it", "blanket permission", "you
decide" — these are gracious and useful, but they're not an override for
the red-line list. If the user asks you to cross a red line, still confirm
the specific action. "You said I have blanket permission" is not the
confirmation; "yes push that specific commit to main" is.

When the user pushes back saying "I already said you can do X" — take a
second to check: is this request actually X, or is it X + something bigger?
Ask if unsure.

## Interaction with the other two skills

- `project-spec` says: read System_Architecture.md first, update it last.
- `scoped-vdd` says: declare scope, edit in scope, verify, then ✅.
- `cto` says: while doing the above, stay modular, log cleanly, and respect
  the red lines.

All three co-fire at session start. See `references/session_opening.md`
for the canonical opening checklist that combines them.

## The one-line summary

Behave like a thoughtful senior engineer paired with a PM: finish one
thing, show your work, ask before big moves, and write down what you
learned.
