---
name: project-spec
description: Ground the session in the News Radar repo's architectural SSOT before touching anything. Use this skill at the start of any session on this repo — any time the user hands off work, asks for an architectural change, asks about DB paths or workflow triggers, mentions the launchd agent or state branch, or begins debugging a run failure. Also use it before closing out a session that made architectural discoveries — those must be written back into the SSOT in the same pass. If a claim about how this system works can't be cited to a file:line, this skill applies.
---

# project-spec — News Radar architectural SSOT anchor

This repo has been bitten three times by "conversation-memory vs. file-reality
drift". The failure pattern: Claude (or a human) makes a confident claim about
a DB path, a cwd contract, or a workflow trigger, proceeds to act on it, and
the claim turns out to be one version stale. `docs/System_Architecture.md` is
the contract that stops that — everything actionable about this system's
*current* operational reality lives there with file:line citations. This skill
forces you to read it first and write it back last.

## When to use this skill — really

Trigger eagerly, not conservatively. Concretely:

- Any session opening on this repo. "Session opening" includes a handoff,
  a new Dispatch orchestrator message, or a user question that starts with
  "can you look at news_radar…".
- Before writing any code that touches the DB, the workflows, the state
  branch, or the launchd plists.
- Before answering a user question that contains words like "path", "cwd",
  "which clone", "state branch", "launchd", "compose", "publish queue",
  "sync", "drift".
- Before closing out a session in which you discovered *any* new operational
  fact (a path that changed, a new failure mode, a behavior not previously
  documented). That fact must land in System_Architecture.md in the same
  session, or it's lost.

When in doubt, use it. The cost is one file read.

## The two files this skill revolves around

| File | Role | How to treat it |
|---|---|---|
| `docs/System_Architecture.md` | Operational SSOT — paths, cwd contracts, triggers, sync contract, case studies | **Ground truth.** Always read first, always update when you discover drift. |
| `docs/architecture.md` | Mermaid dataflow picture (Opus 4.7 pass 2026-04-19) | Read-alongside. If it contradicts System_Architecture.md on operational specifics, System_Architecture wins. |

`docs/OPERATIONS.md` is the "how do I do thing X" runbook. If the user is
asking a "how" question, they want that file; System_Architecture.md is
the "what/why" reference.

## The protocol

1. **Read `docs/System_Architecture.md` first.** Before any tool call that
   edits code, before any architectural answer, before any draft of a plan.
   Don't skim. Especially §1–§5 (topology, DB paths, cwd contracts, triggers,
   sync) and §7 (case studies — those are the landmines).

2. **Verify what you're about to claim against the file.** If you're about
   to say "the DB is at X" or "launchd runs Y at Z", find that claim in
   System_Architecture.md. If it's there and cites code, cite it in your
   answer too. If it's there but not cited, go read the code it references
   and confirm.

3. **If you can't find the claim in System_Architecture.md, stop and look at
   the code.** Use `grep -rn` or the equivalent to trace the claim to a
   file:line. Then *add it to System_Architecture.md* before acting on it.
   This is non-negotiable — the undocumented fact is exactly the kind of
   thing that bites the next session.

4. **When you discover new ground truth, update the SSOT in the same
   session.** New failure modes go in §7 as case studies. New paths go in §2.
   New workflow triggers go in §4. New open items go in §9. Don't keep it
   in the conversation — write it to the file.

5. **Never let conversation memory override the file.** If the user says
   "I think the DB is at /foo/bar" and System_Architecture.md says
   `/Users/hsin/news_radar/data/01_harvest/news_radar.db`, the file is
   right until you've re-verified from code. Say so politely.

## What "updating the SSOT" looks like

Concretely, it's an `Edit` or `Write` call with these properties:

- New facts are cited to file:line (`src/db.py:19-20`) or to a command whose
  output is quoted (`find ~/news_radar -name "*.db"`).
- Ambiguities are called out, not papered over. If you verified X but
  couldn't verify Y, say so in the doc.
- Update §9 (open items) if the new fact implies work that hasn't been done.
- The doc's timestamp header ("Last ground-truth pass: YYYY-MM-DD")
  is bumped to today if you did a meaningful verification pass.

See `references/ground_truth_commands.md` for the canonical set of
cheap read-only commands for re-verification; use them liberally.

## Companion skills

- `scoped-vdd` — For the mechanics of *editing* a module safely
  (post-condition verify, scope declaration). project-spec tells you what
  the system looks like; scoped-vdd tells you how to change it without
  breaking the invariants documented here.
- `cto` — Meta-process discipline (log cadence, red lines, decision
  escalation). project-spec says "read the file"; cto says "decide whether
  you should be making this change at all".

All three should fire together at session start.

## Failure modes this skill prevents

- "I remember the DB being at…" when the path was changed three sessions
  ago. Fix: always re-read the file.
- "Logs show ✅ so it worked" without checking the actual state. (Covered in
  scoped-vdd too, but project-spec's §7.3 case study is what makes you
  remember *why*.)
- Silent architectural drift — each session making a small undocumented
  change until reality and docs are completely disconnected. Fix: the
  write-back step is mandatory, not optional.

## A short checklist to paste into your plan

```
- [ ] Read docs/System_Architecture.md (full, not skim)
- [ ] Cross-check claim X against §<section>
- [ ] If new fact discovered → updated §<section> of SSOT
- [ ] If session changed operational reality → bumped "Last ground-truth pass" timestamp
```

If all boxes are ticked by session close, the next agent that inherits this
work has what they need. If not, you've left a landmine.
