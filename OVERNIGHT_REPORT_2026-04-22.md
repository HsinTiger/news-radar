# Overnight Report — 2026-04-22

> **Read this first.** Single entry-point for what happened during the
> overnight session, what you need to do manually today, and what's still
> open. Ordered by urgency for you (the human), not by when I did the work.

---

## TL;DR

- **System is architecturally healthier than it was last night.** New SSOT
  doc, operations runbook, three discipline skills, a post-condition-verified
  state-branch push script, and a diagnosed (and fixed-in-plist) root cause
  for the silent compose stall are all in place.
- **System is NOT yet producing new drafts.** The plist was edited to fix
  the PATH trap, but launchd is still running with the *old* environment
  because the agent hasn't been reloaded. You need to run the **3 commands
  in §A** below to make the fix go live.
- **No article was auto-published today.** Two reasons: (a) no queued
  drafts currently exist, (b) the silent-stall was active all night so no
  new drafts were produced. `docs/OPERATIONS.md` §6.2 documents the
  decision tree for publishing today if that's still the goal — but it
  requires a decision from you because all eligible candidates need human
  review first. See §C.

---

## A. Action items for you (run in this order)

These take about 2 minutes total. Run them from Terminal, not from Finder.

### A1. Reload the launchd agent so the PATH fix goes live

```bash
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
launchctl load   ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
launchctl list | grep news-radar-compose
```

Expected: one line like `- 0 com.hsin.news-radar.compose` (the `-` is the
PID when not running, `0` is the last exit code).

### A2. Smoke test — is Claude CLI visible under launchd's PATH?

```bash
PLIST_PATH=$(plutil -extract EnvironmentVariables.PATH raw -o - \
  ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist)
echo "plist PATH = $PLIST_PATH"
env -i PATH="$PLIST_PATH" HOME="$HOME" which claude
env -i PATH="$PLIST_PATH" HOME="$HOME" claude --version
```

Expected:
- `plist PATH = /Users/hsin/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`
- `which claude` prints a path (most likely `/Users/hsin/.local/bin/claude`)
- `claude --version` prints a version string

If either of the last two fails ("command not found") — **do not trigger a
compose run**. Ping me with the output and we'll diagnose. Most likely
`~/.local/bin/claude` doesn't exist and Claude CLI needs to be reinstalled.

### A3. Kick off one compose run to verify the full pipeline

Only after A1 and A2 both pass:

```bash
launchctl start com.hsin.news-radar.compose
sleep 10
tail -F $(ls -t ~/news_radar_snapshots/_compose_logs/*.log | head -1)
# Ctrl-C out of tail after a minute or two
```

**What to look for in the log:**
- If Gemini quota is still depleted: you should see the message pivot from
  `claude 不在 PATH，嘗試 Gemini fallback` (broken state, yesterday) to
  something like `claude CLI available, using as fallback` (fixed state).
- If Gemini quota has reset: scorer just uses Gemini normally, you see
  `[DB] draft … written` lines.
- Either way, the end of the log should show either a new draft or a clean
  "nothing viable this cycle" — NOT the silent `skipped_no_llm` pattern
  that was running all night.

---

## B. What I did overnight (and where to find it)

### B1. Path index — every file touched, grouped by purpose

| Deliverable | Path | Status |
|---|---|---|
| Architectural SSOT (v1) | `docs/System_Architecture.md` | New, 337 lines |
| Operations runbook | `docs/OPERATIONS.md` | New, 538 lines |
| State-branch push with post-condition | `scripts/push_state.sh` | New, 8144 bytes, executable |
| Launchd PATH fix (repo source) | `scripts/com.hsin.news-radar.compose.plist` | Edited — added `HOME_DIR/.local/bin` |
| Launchd PATH fix (installed copy) | `~/Library/LaunchAgents/com.hsin.news-radar.compose.plist` | Edited — same fix, `/Users/hsin/.local/bin`. **Not yet reloaded; see §A1.** |
| `project-spec` skill | `.claude/skills/project-spec/` | New (SKILL.md + references/ground_truth_commands.md) |
| `scoped-vdd` skill | `.claude/skills/scoped-vdd/` | New (SKILL.md + references/verification_patterns.md) |
| `cto` skill | `.claude/skills/cto/` | New (SKILL.md + references/session_opening.md) |
| This report | `OVERNIGHT_REPORT_2026-04-22.md` | You're reading it |

### B2. Per-task summary

**#2 System_Architecture.md** — The operational-reality SSOT (complements
the existing Mermaid-diagram `docs/architecture.md`, does not duplicate
it). Covers two-clone topology, every DB path with code citations,
entry-point cwd contracts, workflow triggers, Mac↔Cloud sync contract,
queue state machine, and three case studies in §7 (Gemini 429 × PATH trap,
Launchd minimal PATH generalization, post-condition mandate). Ends with a
§9 "open items not yet in code" list for anything that surfaced during
the session but wasn't fixed. Verified against `src/db.py`, `compose_hourly.sh`,
the installed plist, and 8 hours of recent compose logs.

**#3 OPERATIONS.md** — Runbook-style. §0 quick-reference card, §1 daily
morning checklist, §2 install/reinstall procedures, §3 manual triggers,
§4 workflow trigger index, §5 state-branch handshake procedure, §6 five
recovery runbooks (silent stall, empty queue, state drift, launchd stopped,
bad draft in queue), §7 action items for you today (same as §A above),
§8 philosophy. Every procedure ends with a verification step whose output
determines success.

**#4 DB-write mystery — diagnosed.** The "compose_one reported queued but
DB unchanged" report from last session was almost certainly misread logs.
Root cause is the Gemini 429 + Claude CLI PATH trap combo (see §D below
for the confirmation I got from the 14:30 log). Written up in
`System_Architecture.md` §7.1.

**#5 push_state.sh** — Manual-triggered state-branch push with sha256
post-condition verification. Optional `--expect-draft <id>` does SQL-level
assertion pre-push AND post-push. Exit codes distinguish real failures
(1 = post-condition failed) from env issues (2) and git failures (3).
Tested: `--help`, `--dry-run`, bogus `--expect-draft` correctly exits 1
at pre-push stage, real draft id `5a83ee9d` passes pre-assert. Did NOT
run a real `git push` in testing (that would have overwritten state
branch). Usage documented in `OPERATIONS.md` §3.2.

**#9 Claude CLI PATH fix** — Both plists (`scripts/com.hsin.news-radar.compose.plist`
in repo, `~/Library/LaunchAgents/com.hsin.news-radar.compose.plist`
installed) now have `~/.local/bin` first in the EnvironmentVariables.PATH.
`diff` between the two (after sed substitution) is empty — they match.
**Action A1 is required to make this live.** Root-cause write-up in
`System_Architecture.md` §7.1 and §7.2.

**#1 Three skills.** `project-spec` anchors every session to the SSOT
before it acts. `scoped-vdd` enforces the scope-declare → edit → verify
discipline, with a patterns reference for every side-effect type.
`cto` codifies the meta-process: modular lock-in, log cadence, red lines,
decision protocol. All three are ≤ 180 lines for the SKILL.md files, with
larger detail pushed into `references/` per progressive-disclosure best
practice. The user's pick (b) — stored at `news_radar/.claude/skills/` so
they travel with the repo via git.

---

## C. Why no article was published today (and what we can do about it)

**Current queue state** (verified 2026-04-22 ~06:30 UTC):

| status | queue_status | count |
|---|---|---|
| `auto_approved` | `failed` | 18 |
| `pending_review` | `failed` | 12 |
| `pending_review` | `NULL` | 3 |
| `published` | `published` | 1 |

Zero drafts with `queue_status='queued'`. The three `qs=NULL` `pending_review`
drafts from 2026-04-20 are the last real composer outputs; everything else
is either already failed (by legitimate guard checks) or already published.

**To publish one article today, we need either:**

**Option 1 — Wait for the fix to produce a queued draft.** After you run
§A, the next compose cycle should work (either Gemini quota recovered or
Claude CLI fallback activates). If a new piece scores ≥ 0.9 confidence,
it gets `queue_status='queued'` automatically and Cloud publisher picks it
up at :00. Zero manual risk; depends on feed activity + scoring.

**Option 2 — Manually promote one of the 3 pending_review drafts.** These
are confidence 0.65-0.90, which means a human should read them first. This
is the "your publish today" path but it needs you in the loop:

```bash
cd ~/news_radar
# Candidates
python3 -c "
import sqlite3
c = sqlite3.connect('data/01_harvest/news_radar.db')
for r in c.execute(\"SELECT id, title, confidence FROM drafts WHERE status='pending_review' AND queue_status IS NULL ORDER BY confidence DESC\"):
    print(r)
"

# Read one fully (replace <id> with one of the above)
python3 -c "
import sqlite3
c = sqlite3.connect('data/01_harvest/news_radar.db')
r = c.execute(\"SELECT title, fb_body, ig_caption, threads_body FROM drafts WHERE id = ?\", ('<id>',)).fetchone()
for field, val in zip(['title','fb','ig','threads'], r):
    print(f'--- {field} ---')
    print(val)
"
```

After you read one and approve it, the promote + push + verify flow is:

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('data/01_harvest/news_radar.db')
c.execute(\"UPDATE drafts SET queue_status='queued', status='auto_approved' WHERE id = ?\", ('<id>',))
c.commit()
print('updated:', c.total_changes)
"
bash scripts/push_state.sh --expect-draft <id-prefix>
# exit 0 = safe to trigger publish
```

Then trigger the Cloud publisher manually via github.com → Actions →
News Radar Pipeline → Run workflow.

**My recommendation:** do Option 1 first — run §A, wait one cycle, see if
anything lands. If nothing lands by ~10:00 (Gemini quota reset should be
by then in UTC if it's daily), go to Option 2 with me in the loop for the
final human read.

**Option 3 — reopen an old qs='failed' draft.** I checked and deliberately
did NOT do this. They failed for guard reasons (some unsafe content filter,
likely), reopening without re-running the guard would be a violation of
the post-condition mandate. If you specifically want this, we can discuss.

---

## D. Evidence for the silent-stall diagnosis (§7.1 confirmed)

From `~/news_radar_snapshots/_compose_logs/20260422_143029.log` (the
14:30 UTC run, 06:30 this morning, captured during my overnight pass):

- Every processed news item logs `[llm_brain] ℹ️ 'claude' 不在 PATH，嘗試
  Gemini fallback.` — this is `src/llm_brain.py`'s explicit line when
  `shutil.which("claude")` returns None. Direct confirmation that the PATH
  trap is active under launchd.
- Every Gemini call returns `429 RESOURCE_EXHAUSTED` with quota message
  `limit: 20, model: gemini-3-flash`. Direct confirmation of the free-tier
  cap.
- Every item ends with `[Scorer 2.0] ❌ 所有 LLM 路徑皆失敗 → skip`.
- Hunter: `掃描 8 / 發布 0`.
- Pipeline: `exit code: 0` + `✅ state branch 已更新`.

That is the exact shape `System_Architecture.md` §7.1 predicted, verbatim.
The fix (plist PATH + launchctl reload) should clear both the PATH message
and — on the next Gemini 429 — let Claude CLI take over.

---

## E. Decisions pending for you (not blocking, but good to hear back on)

1. **Is it acceptable that the Cloud publisher is still idle today?** If
   yes, we proceed with Option 1 tomorrow too (pipeline self-heals after
   your §A commands). If no, we need to do Option 2 today and you need
   to pick a draft.

2. **Gemini free tier (20/day) is structurally insufficient.** Once the
   CLI fallback is live, this is papered over — but it means every day
   has an outage window where we're running on Claude-only. Worth
   evaluating: upgrade Gemini plan? Accept the fallback-always pattern?
   Reduce compose frequency? (Documented as open item in
   `System_Architecture.md` §9.)

3. **`morning_report.py` doesn't yet count `skipped_no_llm` returns.**
   Fine for now, but the detection gap means the next silent stall
   could also go unnoticed for a day. Worth a future session to add
   that metric to morning_report's output.

---

## F. What next session inherits (context block)

If I (or another Claude session) picks up this workstream next:

- **Start by reading `docs/System_Architecture.md` and `docs/OPERATIONS.md`.**
  Both are current as of 2026-04-22.
- **Three skills at `.claude/skills/`** should co-fire. If the Claude
  interface supports user-level skill discovery from the repo, those will
  auto-inject; if not, read them as regular files at session start.
- **The 2026-04-20 `pending_review/NULL` drafts** (`4fcc4d45…`, `a1cd9e68…`,
  `91930381…`) are still the newest real composer outputs and are the
  fallback pool for "we need to publish today". Don't reopen `qs='failed'`
  without guard re-run.
- **`scripts/export_drafts.py:11`** still has the wrong DB path
  (points at `db/news_radar.db` which doesn't exist). Parked bug, low
  priority, noted in `System_Architecture.md` §9.
- **`scripts/compose_hourly.sh`** still prints ✅ after state-branch push
  without fetching back to verify. Low priority per §7.3 discussion.
- **Gemini quota cap** is a structural tech debt item, not a bug — see §E2.

---

## G. Task ledger

| # | Task | Status |
|---|---|---|
| 8 | Recon — verify handoff facts | ✅ done |
| 2 | `docs/System_Architecture.md` SSOT v1 | ✅ done |
| 3 | `docs/OPERATIONS.md` runbook | ✅ done |
| 5 | `scripts/push_state.sh` with post-condition | ✅ done |
| 9 | Diagnose launchd-can't-find-claude | ✅ done (fix in repo + installed plist; requires §A reload) |
| 4 | DB-write mystery investigation | ✅ done (root cause = §7.1; previous "queued" was misread log) |
| 1 | Three skills (project-spec, scoped-vdd, cto) | ✅ done (in `.claude/skills/`) |
| 7 | This overnight report | ✅ done (you're reading it) |
| 6 | Publish ONE article today | ⚠️ blocked — see §C for decision tree |

Nine tasks, one still blocked on you. The blocker is benign — just a
decision to make about Option 1 vs. Option 2 once you've run §A.

---

**If anything in §A fails, or anything in the latest compose log looks
wrong, ping me before doing §C. No silent stall should go undiagnosed
again.**
