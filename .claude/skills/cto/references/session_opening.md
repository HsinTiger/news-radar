# Session opening checklist — combining project-spec, scoped-vdd, cto

Run this at the very start of any session on the News Radar repo. Doesn't
replace any one skill; stitches them into a sequence.

## 0. Context

- Read the user's message end to end.
- If it's a handoff / orchestrator message: treat every factual claim in it
  as *unverified* until checked against files. This has burned us before.

## 1. project-spec — anchor

- `Read` `docs/System_Architecture.md` (full).
- `Read` `docs/architecture.md` (skim the Mermaid if system is being
  mutated).
- `Read` `docs/OPERATIONS.md` (if the task has an operational flavor —
  running compose, publishing, debugging launchd).

If any of those files don't exist yet or look stale (>1 month old without
ops changes), flag that to the user — it's a signal the SSOT hasn't been
maintained.

## 2. Recon — cheap verification

Before trusting the handoff's factual claims, run 2-3 of these:

```bash
# What DB files actually exist?
find ~/news_radar/ -name "*.db" -not -path "*/.venv/*"

# Is launchd still scheduled?
launchctl list | grep news-radar

# What's on state branch right now?
cd /tmp && rm -rf state_peek && mkdir state_peek && cd state_peek
git init -q
git fetch --depth=1 https://github.com/HsinTiger/news-radar.git state --quiet
git show FETCH_HEAD:LAST_RUN.txt

# Most recent drafts
python3 -c "
import sqlite3
c = sqlite3.connect('$HOME/news_radar/data/01_harvest/news_radar.db')
for r in c.execute('SELECT id, status, queue_status, generated_at FROM drafts ORDER BY generated_at DESC LIMIT 5'):
    print(r)
"
```

Write results to a recon snapshot in your session notes; don't throw them
away. If a handoff claim doesn't match the recon output, that's the first
thing to address.

## 3. cto — plan the session

- What's the task list? (`TaskList` if the harness supports it.)
- What's the first unit (smallest end-to-end scope)?
- Any red lines expected (git push, rm, API bursts)? If yes, confirm now
  rather than surprise-asking mid-flow.
- What's the checkpoint cadence? Usually: one per unit closed.

## 4. Loop (per unit)

For each unit:

### 4a. scoped-vdd — scope declaration

Write 3-6 lines:
```
Unit: <name>
Scope: <files in>
Out of scope: <files not touched>
Change: <one user-visible change>
Edge cases: (1) … (2) …
Post-condition: <the assertion that proves success>
```

### 4b. Edit

Make the edit(s). Stay in scope.

### 4c. Verify

Run the post-condition assertion. If it fails, report the failure in a
checkpoint; do NOT proceed to the next unit until addressed or explicitly
parked.

### 4d. Checkpoint

One message:
```
<unit>: <what happened>
verification: <what passed>
next: <next unit or "done">
```

### 4e. Write-back

If this unit revealed new architectural truth, update
`docs/System_Architecture.md` (project-spec §4) IN THE SAME PASS.

## 5. Session close

Before closing the session:

- Any new facts discovered that aren't in SSOT yet? Write them in.
- Any TODOs you parked as out-of-scope? Make sure they landed in a task
  list / follow-up file so the next session picks them up.
- Final checkpoint: list completed units, pending units, any red flags.

## What this checklist guards against

- Starting work on an outdated mental model of the system (project-spec).
- Editing a module and then not verifying the change (scoped-vdd).
- Sprawling across modules and losing track of what's done (cto modular
  lock-in).
- Handing off to the next session without the hard-won facts (project-spec
  write-back).
- Crossing a red line without user confirmation (cto red lines).

A session that follows this ends with: files updated, SSOT current, tasks
resolved, user can see what happened in checkpoint messages, next session
inherits accurate context.
