---
name: scoped-vdd
description: Verification-driven development with an explicit scope declaration before any edit. Use this skill whenever you're about to modify code in the News Radar repo, whenever you're about to add a "success ✅" log line, whenever a pipeline step writes to the DB or to the state branch, whenever a shell script says "it worked" without backing that with a SELECT or a sha compare. Also use when reviewing a PR or a handoff that makes claims about what changed — force it through the scope / edge-case / post-condition lens. If the change touches a module and the last observed failure was "log said success but state was wrong", this skill applies.
---

# scoped-vdd — Scoped, Verification-Driven Development

This system has a known illness: **success claims that aren't backed by
observed post-conditions**. 2026-04-19's DB confusion, 2026-04-20's silent
stall where `pipeline exit code: 0` masked zero drafts written, and several
log-line-said-✅-but-state-was-wrong incidents before that all have the same
shape — a script prints a success message, nobody verifies the side-effect,
the next step proceeds on a false premise.

This skill is the counter-discipline: **scope → edit → verify** as one unit,
never split across steps or across sessions. Also covers the "scope
declaration" discipline that stops Claude from drifting into adjacent
modules mid-edit.

## When to use

Trigger before any of these:

- Editing any file under `src/`, `scripts/`, `run_*.py`, or `.github/workflows/`.
- Writing a new shell script that mutates state (DB, state branch, local files).
- Writing or editing a function that ends with a log line like `print("✅ ...")`
  or `log("...done")`. Those log lines are the smell — every one of them
  needs to be backed by a verification clause *before* it fires.
- Handling a handoff that says "I did X successfully" — don't trust the
  claim; run the post-condition check yourself.
- Reviewing a PR or accepting a patch.
- Adding a new entry point (new CLI script, new workflow trigger, new
  launchd agent).

Companion skill: `project-spec` anchors you to the architectural SSOT first;
scoped-vdd then governs how you mutate it.

## The three-step protocol

### 1. Scope declaration

Before writing any code, write a scope block (3–6 lines, in prose or as a
checklist, in your working notes or in a comment above the edit). It covers:

- **What module(s) you're touching.** Explicit file paths, not "the composer
  area".
- **What modules you're NOT touching.** This is the fence. If mid-edit you
  find yourself wanting to edit a file outside the fence, stop and
  re-declare — don't silently sprawl.
- **The one behavioral change** you're trying to make, in user-visible
  terms. "Add post-condition SELECT after compose_one's commit" is good.
  "Improve compose_one" is not.
- **At least two edge cases** the change has to handle. These are what you
  write the verification against in step 3.

Worked example:

```
Scope: scripts/compose_one.py only.
Out of scope: src/llm_brain.py, composer.py, DB schema.
Change: after conn.commit() on line 95, add a SELECT that asserts the new
        draft_id row exists in DB before printing ✅.
Edge cases:
  1. conn.commit() succeeded but draft_id != what we expected (shouldn't
     happen, but let's catch it).
  2. No draft got written (process_item returned skipped_no_llm) — the
     ✅ line must NOT fire in this case.
```

If you can't fit your change into 3–6 scope lines, the scope is too big.
Break it up.

### 2. The edit

Make the edit. Keep it within the fence declared in step 1. The test for
"am I in scope" is simple: if the file path isn't in the "what I'm
touching" list, stop and re-declare.

Any log line that announces success must structurally *depend on* the
verification result, not run before it.

### 3. Verification — the post-condition

Every edit ends with an observable assertion that matches one of these
shapes:

| Side effect | Verification |
|---|---|
| SQL INSERT/UPDATE | A SELECT that returns the new/changed row with the expected fields |
| File write | A `stat` (size non-zero) + `shasum` comparison if reproducibility matters |
| State branch push | `push_state.sh` with `--expect-draft` (or an inline fetch + sha256 compare) |
| Subprocess call | Exit code check + output shape check (not "the log printed 'done'") |
| HTTP POST | Response code in [200, 299] AND response body parsed successfully |
| launchctl load | `launchctl list \| grep <label>` returns a matching line |

The log line that says ✅ fires **after** the assertion passes, and
**doesn't fire** if the assertion fails. In Python this is a plain
`assert` or an early-return-with-error. In bash it's `[[ $? -eq 0 ]] ||
exit 1`. If you find yourself writing "TODO: verify later", you're doing
this wrong.

See `references/verification_patterns.md` for concrete code snippets
organised by side-effect type.

## Case: compose_one.py — the pattern to copy

`scripts/compose_one.py` lines 95–110 get this right already (verified
2026-04-22):

```python
conn.commit()
# ... then immediately:
row = conn.execute("SELECT id, queue_status FROM drafts WHERE id = ?",
                   (draft_id,)).fetchone()
assert row is not None, f"draft {draft_id} not in DB after commit"
print(f"[ComposeOne] ✅ DB latest draft: {row[0]} qs={row[1]}")
```

The `assert` is load-bearing. If the row isn't there, the `✅` never
prints — failure is visible as a Python exception, not as silent
success. Every other write site in this repo should match that shape.

Case that got it wrong: 2026-04-20's `compose_hourly.sh` pushed state
branch with a printf `✅` after `git push`, without fetching back. The
push "succeeded" from `git push`'s exit code, but the observed remote
state was never confirmed. `push_state.sh` (new 2026-04-22) closes that
gap for manual pushes; the hourly script still has the hole (low
priority — force-push semantics make silent loss cheap).

## Scope violations to watch for

- Opening one file, then finding "oh this import is broken, let me fix
  that too" — stop, re-declare, or file it as out-of-scope for later.
- "While I'm here, let me also refactor" — no. One change per scope block.
- Adding a helper function in a file you don't own. If you need the helper,
  add it in-scope or skip.
- Running a test suite and fixing an unrelated failing test because "it
  should be green". That's its own scope.

When you catch yourself in one of these, the fix is cheap: abort the
edit, write down the out-of-scope finding as a new task / todo, restore
scope, finish the original change.

## Interaction with the log cadence

One checkpoint message per completed scope block. "Completed" means the
post-condition passed. If the post-condition failed, the message is
still one message, but it reports the failure and the hypothesis — it
is not a "in-progress" update.

This aligns with the `cto` skill's "one checkpoint per unit" rule.

## A short checklist to paste into your plan

```
- [ ] Scope declared (files in / out, one change, ≥2 edge cases)
- [ ] Edit completed, within fence
- [ ] Post-condition matches side-effect type and its assertion RUNS
- [ ] ✅ log line depends on assertion passing
- [ ] Scope checkpoint message sent (one per block, not per tool call)
```

## Why this exists

The point is not paperwork. The point is that **the thing that guarantees
the pipeline is correct** is the assertion, not the log line. Log lines are
observations we hope correspond to reality; assertions are observations
that force reality to match. Every time this repo has been burned, it's
been by a log line that got believed. This skill is the antibody.
