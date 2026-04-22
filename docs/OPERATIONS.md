# News Radar — Operations Runbook

> **Scope.** How to run, monitor, and recover this system. Architectural "why"
> lives in `docs/System_Architecture.md` (SSOT); Mermaid dataflow lives in
> `docs/architecture.md`. This file is the "hands on keyboard" companion —
> every procedure below has been executed or transcribed from a real command,
> not recalled.
>
> **Golden rule.** A log line that says ✅ is **not** evidence. Evidence is a
> SELECT result, a sha256 match, a `launchctl list` row, or a post-condition
> assert. See `docs/System_Architecture.md` §7.3.
>
> **Last updated:** 2026-04-22 (Claude overnight pass).

---

## 0. Quick reference card

| Question | Command |
|---|---|
| Is hourly compose still scheduled? | `launchctl list \| grep news-radar-compose` |
| When did it last run? | `ls -lt ~/news_radar_snapshots/_compose_logs/*.log \| head -3` |
| What did the last run produce? | `ls -lt ~/news_radar_snapshots/_compose_logs/*.log \| head -1 \| awk '{print $NF}' \| xargs tail -60` |
| Is there anything in the queue? | `python3 -c "import sqlite3; c=sqlite3.connect('$HOME/news_radar/data/01_harvest/news_radar.db'); print(list(c.execute(\"SELECT queue_status, COUNT(*) FROM drafts GROUP BY queue_status\")))"` |
| Did last hour's publish actually post anywhere? | Check `drafts` WHERE `queue_status='published'` ordered by `updated_at` DESC; `publish_log` column has per-platform response JSON |
| Is the Mac DB in sync with `state` branch? | `cd ~/news_radar && git fetch origin state && git show origin/state:data/01_harvest/news_radar.db \| shasum -a 256` and compare to `shasum -a 256 data/01_harvest/news_radar.db` |

---

## 1. Daily morning checklist (≤ 2 minutes)

Run these in order. Any red flag → jump to the matching recovery runbook in §6.

```bash
# 1) Did launchd fire anything overnight?
ls -lt ~/news_radar_snapshots/_compose_logs/*.log | head -8
# Expect: 8 files, one per hour. Gaps ≥ 2 hours = Mac was asleep or unloaded.

# 2) Did any of those runs actually produce a draft?
cd ~/news_radar
python3 -c "
import sqlite3, datetime as dt
c = sqlite3.connect('data/01_harvest/news_radar.db')
cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=12)).isoformat()
rows = list(c.execute('SELECT id, status, queue_status, generated_at FROM drafts WHERE generated_at > ? ORDER BY generated_at DESC', (cutoff,)))
print(f'Drafts in last 12h: {len(rows)}')
for r in rows[:5]:
    print(' ', r)
"
# Expect: ≥ 1 new draft in the last ~12 h (depends on feed activity).
# 0 drafts + compose logs exist = silent stall (usually §6.1 Gemini 429 + PATH trap).

# 3) Is anything queued for Cloud publisher?
python3 -c "import sqlite3; c=sqlite3.connect('$HOME/news_radar/data/01_harvest/news_radar.db'); print(list(c.execute('SELECT queue_status, COUNT(*) FROM drafts GROUP BY queue_status')))"
# Empty queue is NOT a bug by itself — Cloud publisher no-ops when empty.
# But combined with #2 = 0, it's the silent-stall signature.

# 4) Is state branch fresh?
cd ~/news_radar && git fetch origin state --quiet && git show origin/state:LAST_RUN.txt
# Expect: last_run_utc within the last ~1 h, kind=mac_compose (or push_state_sh if manual).
```

If all four green → you're done. Go make coffee.

---

## 2. Install / reinstall the hourly compose LaunchAgent

First-time install is covered in `scripts/INSTALL_COMPOSE_LAUNCHAGENT.md` (keep
reading it — this section assumes the initial install already happened and
you're reinstalling or updating).

### 2.1 Reinstall after plist change

The plist source-of-truth is `scripts/com.hsin.news-radar.compose.plist` (in
repo). The installed plist at `~/Library/LaunchAgents/...` is a **rendered
copy** with `HOME_DIR` substituted to `/Users/hsin`. These must stay in sync
after any repo change.

```bash
# From the local clone (單一 clone，2026-04-23 起)
cd ~/news_radar

# 1) Render HOME_DIR → $HOME into ~/Library/LaunchAgents/
sed "s|HOME_DIR|$HOME|g" scripts/com.hsin.news-radar.compose.plist \
  > ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist

# 2) Verify the substitution actually worked
plutil -extract ProgramArguments.1 raw -o - \
  ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
# Expect: /Users/hsin/bin/news_radar_compose.sh  (no "HOME_DIR" substring)

plutil -extract EnvironmentVariables.PATH raw -o - \
  ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
# Expect: /Users/hsin/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin

# 3) Reload the agent
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist

# 4) Verify it's scheduled
launchctl list | grep news-radar-compose
# Expect one line: "- 0 com.hsin.news-radar.compose" (0 = last exit OK, or "-" = not yet run)
```

### 2.2 Reinstall after shell-script change

The shell script has TWO copies: repo (`scripts/compose_hourly.sh`) and
launchd runtime (`~/bin/news_radar_compose.sh`). launchd only reads the
second one. After editing the repo version:

```bash
cp ~/news_radar/scripts/compose_hourly.sh ~/bin/news_radar_compose.sh
chmod +x ~/bin/news_radar_compose.sh

# Sanity: file sizes should match
shasum -a 256 ~/news_radar/scripts/compose_hourly.sh ~/bin/news_radar_compose.sh
# Expect two identical hashes.
```

No launchctl reload needed for script-only changes — the plist points to a
file path; launchd just re-reads the file on each fire.

### 2.3 Pre-deployment smoke test (always run this after plist PATH change)

The most common failure mode is "works in my terminal, fails under launchd"
because of the PATH trap (`docs/System_Architecture.md` §7.2). To catch this
before a bad plist ships:

```bash
# Pull the exact PATH the plist declares
PLIST_PATH=$(plutil -extract EnvironmentVariables.PATH raw -o - \
  ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist)
echo "plist PATH = $PLIST_PATH"

# Simulate launchd's env and check every binary the pipeline depends on
for bin in python3 git claude gemini shasum sqlite3; do
    env -i PATH="$PLIST_PATH" HOME="$HOME" which "$bin" 2>/dev/null \
        && echo "  ✅ $bin found" \
        || echo "  ❌ $bin MISSING on launchd's PATH — fix plist before reload"
done

# For Claude CLI specifically (the one that was broken):
env -i PATH="$PLIST_PATH" HOME="$HOME" claude --version 2>&1 | head -2
# Expect: a version string. "command not found" = PATH is wrong, don't reload.
```

If any of these fail, **do not reload the plist**. Fix the plist PATH first
(or fall back to absolute paths in the shell script).

---

## 3. Manual triggers

### 3.1 Manually run the hourly compose

```bash
# Option A: via launchctl (goes through plist, uses launchd's env — tests
# everything the scheduled path will touch)
launchctl start com.hsin.news-radar.compose

# Option B: directly (uses your shell env, so works even if plist PATH is
# still broken — useful as a diagnostic to split "script broken" vs
# "launchd env broken")
bash ~/bin/news_radar_compose.sh

# Watch live
tail -f $(ls -t ~/news_radar_snapshots/_compose_logs/*.log | head -1)
```

Expected end-of-log shape for a **healthy** run:

```
...
=== Stage: compose (--buffer-target 2) ===
 [Composer] Generating draft for news_id=xyz123...
 [DB] draft ABCDEF written (queue_status=queued)
=== pipeline exit code: 0 ===
📤 Push DB 回 state branch...
✅ state branch 已更新
```

Expected shape for a **silent-stall** run (see §6.1):

```
=== Stage: compose (--buffer-target 2) ===
 [Scorer] Gemini 429 — free tier exhausted
 [LlmBrain] shutil.which('claude') = None → skip
 [Hunter] process_item → skipped_no_llm
 ...repeated for 8 items...
=== pipeline exit code: 0 ===   ← exit 0 despite zero drafts
📤 Push DB 回 state branch...
✅ state branch 已更新           ← DB actually unchanged from last run
```

The difference is visible in the `[DB] draft … written` line — if absent,
the run produced nothing.

### 3.2 Manually push state branch (with verification)

Use `scripts/push_state.sh` when you've hand-edited the DB (e.g. promoted a
draft via SQL) and want to make sure it lands on `state` before the next
hourly cycle overwrites it.

```bash
cd ~/news_radar

# Dry-run first — lists what WILL be pushed, does not actually push
bash scripts/push_state.sh --dry-run

# Real push + sha256 post-condition (no SQL-level assert)
bash scripts/push_state.sh

# Real push + assert a specific draft_id exists on remote after push
bash scripts/push_state.sh --expect-draft 5a83ee9d97
```

Exit codes (scripted callers should check these):

| Code | Meaning | Action |
|---|---|---|
| 0 | Push succeeded AND post-condition passed | Safe to continue |
| 1 | Post-condition failed — remote state does not match expectation | DB did not land; do NOT treat as success; re-investigate |
| 2 | Arguments / cwd / missing DB etc. | Fix caller |
| 3 | Git op failure (network / auth / permissions) | Check `gh auth status`, retry |

### 3.3 Manually publish the queue (Cloud side)

Normally this runs on GitHub Actions hourly. To trigger manually:

1. On github.com → `Actions` → `News Radar Pipeline` → `Run workflow`.
2. Or `gh workflow run pipeline.yml` if you have `gh` CLI authenticated.

Do **not** run `run_publish_queue.py` locally against a production
`.env` — it will consume real Meta tokens and might double-post if the
state branch later gets a stale copy from a parallel run.

---

## 4. Workflow triggers — master index

| Name | Where | Schedule | Runs what | Logs |
|---|---|---|---|---|
| Hourly compose | Mac launchd | every 3600 s from load | `~/bin/news_radar_compose.sh` → `run_pipeline.py --compose-only --buffer-target 2` | `~/news_radar_snapshots/_compose_logs/*.log`, `/tmp/news-radar-compose.{out,err}.log` |
| Weekly snapshot | Mac launchd | Sun 10:30 local | `~/bin/news_radar_weekly_snapshot.sh` | `~/news_radar_snapshots/_logs/*.log` |
| Cloud publisher | GH Actions | `cron: 0 * * * *` | `pipeline.yml` → `run_publish_queue.py` | Actions run logs |
| Topic weights | GH Actions | `cron: 0 22 * * 0` | `reflect_topic.yml` | Actions run logs |
| Feed healthcheck | GH Actions | daily | `feed_healthcheck.yml` | Actions run logs, GH Issues on fail |

To temporarily pause any Mac agent: `launchctl unload <plist>`.
To temporarily pause any GH workflow: `Actions` → `<workflow>` → `···` → `Disable workflow`.

---

## 5. The Mac ↔ Cloud state-branch handshake

Three branches, two writers, one contract. Summarised here; full semantics in
`docs/System_Architecture.md` §5.

**Contract:**

- `state` branch always has `data/01_harvest/news_radar.db` at the repo root path.
- Writers produce **orphan** commits (`git init -b state` in a tmpdir). No history.
- Every writer stamps `LAST_RUN.txt` with kind, UTC timestamp, host. Read that
  file to know who last wrote.
- Force-push is normal, expected, and safe — nothing in `state` is cumulative
  across commits.

**Safe way to inspect without disturbing:**

```bash
cd /tmp && rm -rf state_peek && mkdir state_peek && cd state_peek
git init -q
git fetch --depth=1 https://github.com/HsinTiger/news-radar.git state --quiet
git show FETCH_HEAD:LAST_RUN.txt
git show FETCH_HEAD:data/01_harvest/news_radar.db > fetched.db
shasum -a 256 fetched.db
python3 -c "import sqlite3; c=sqlite3.connect('fetched.db'); print(list(c.execute('SELECT queue_status, COUNT(*) FROM drafts GROUP BY queue_status')))"
```

Do this before assuming "state is broken" — 90 % of the time state is fine
and the local clone is stale.

---

## 6. Recovery runbooks

Each subsection starts with a **symptom** and ends with a **verification
step** that must pass before declaring the recovery complete.

### 6.1 Silent pipeline stall (Gemini 429 + Claude CLI PATH trap)

**Symptom.**
- `launchctl list | grep news-radar-compose` shows last exit = 0.
- Logs in `_compose_logs/` exist for every hour.
- But `SELECT COUNT(*) FROM drafts WHERE generated_at > (now - 12h)` = 0.
- Log bodies contain `Gemini 429` + `shutil.which('claude') = None` (or equivalent skip).

**Cause.** `docs/System_Architecture.md` §7.1: Gemini free tier (20/day) hits
429, and launchd's plist PATH doesn't include `~/.local/bin` where `claude`
lives, so the 8.19 fallback never activates.

**Fix.**

```bash
# 1) Confirm Claude CLI is installed somewhere
which claude                        # in your interactive shell
ls -l ~/.local/bin/claude           # or wherever the native installer put it

# 2) Check the plist PATH
plutil -extract EnvironmentVariables.PATH raw -o - \
  ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist

# 3) If plist PATH does NOT include ~/.local/bin, reinstall the plist (§2.1).
#    The repo version (as of 2026-04-22) already has the fix.

# 4) Run the smoke test (§2.3) — it must pass before you reload launchctl.

# 5) Reload launchctl
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
launchctl load   ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist

# 6) Trigger immediately and watch
launchctl start com.hsin.news-radar.compose
tail -f $(ls -t ~/news_radar_snapshots/_compose_logs/*.log | head -1)
```

**Verification.** Next log should contain `[LlmBrain] claude CLI available`
(or equivalent) AND at least one `[DB] draft … written` line. If Gemini is
still 429'd, the log should pivot to Claude CLI instead of skipping.

If Gemini is NOT 429'd (e.g. daily quota reset), even without the plist
fix the pipeline will work — so to **really** verify the fix, look for an
hour where the log shows `Gemini 429` AND a new draft still got written
(via Claude). Until you see that combo, the fix is unverified.

### 6.2 Queue is empty but you need to publish today

**Symptom.** `SELECT * FROM drafts WHERE queue_status='queued'` returns 0
rows, but business need is to publish today.

**Decision tree.**

1. **Is it possible to compose a new draft right now?** If Gemini quota OK
   AND Claude CLI reachable → yes. Run `launchctl start com.hsin.news-radar.compose`
   and wait one cycle; if it produced something confidence ≥ 0.9, it's
   auto-queued.

2. **If compose can't produce one** (Gemini 429 + Claude CLI missing /
   rate-limited): you have to promote an existing draft by hand. Candidates
   are the `pending_review` rows (0.65 ≤ confidence < 0.9). **Do not** reopen
   `qs='failed'` drafts without re-running the guard check — they failed for
   reasons, and the reasons are still there.

3. **Manual promotion procedure** (use with care):

   ```bash
   cd ~/news_radar
   # Pick a candidate
   python3 -c "
   import sqlite3
   c = sqlite3.connect('data/01_harvest/news_radar.db')
   for r in c.execute(\"SELECT id, title, confidence, generated_at FROM drafts WHERE status='pending_review' AND queue_status IS NULL ORDER BY confidence DESC, generated_at DESC LIMIT 5\"):
       print(r)
   "

   # Read the full draft before promoting — don't publish something you haven't seen
   python3 -c "
   import sqlite3, json
   c = sqlite3.connect('data/01_harvest/news_radar.db')
   r = c.execute(\"SELECT fb_body, ig_caption, threads_body FROM drafts WHERE id LIKE ?\", ('<draft_id_prefix>%',)).fetchone()
   print(r)
   "

   # Promote (only after a human has read and OK'd it)
   python3 -c "
   import sqlite3
   c = sqlite3.connect('data/01_harvest/news_radar.db')
   c.execute(\"UPDATE drafts SET queue_status='queued', status='auto_approved' WHERE id LIKE ?\", ('<draft_id_prefix>%',))
   c.commit()
   print('updated rows:', c.total_changes)
   # Post-condition: confirm
   r = c.execute(\"SELECT id, status, queue_status FROM drafts WHERE id LIKE ?\", ('<draft_id_prefix>%',)).fetchone()
   print('after:', r)
   "

   # Push to state branch WITH verification
   bash scripts/push_state.sh --expect-draft <draft_id_prefix>
   # Must exit 0. If exit 1, DB did not land — do not trigger publish.
   ```

**Verification.** `push_state.sh` exit 0, then manually trigger the Cloud
publisher (GH Actions `pipeline.yml` → Run workflow), then check `drafts`
again — the promoted row should have `queue_status='published'` and a
non-empty `publish_log` JSON.

### 6.3 state branch drift (Mac DB ≠ remote DB)

**Symptom.** `shasum` of local `~/news_radar/data/01_harvest/news_radar.db`
doesn't match `origin/state:data/01_harvest/news_radar.db`.

**Common causes.**
- You edited the local DB by hand but didn't push.
- Cloud publisher wrote to `state` and Mac hasn't `fetch`'d yet.
- Two writers raced on the force-push (rare; see §4.3 of
  System_Architecture).

**Fix.** Decide who is authoritative first, then sync the other direction.
Never blindly `git pull` the `state` branch — orphan commits can't be
merged cleanly.

```bash
# Option A: remote is authoritative (Cloud just published and you want that)
cd ~/news_radar
git fetch origin state --quiet
git show origin/state:data/01_harvest/news_radar.db > data/01_harvest/news_radar.db
shasum -a 256 data/01_harvest/news_radar.db  # note this
# Verify
python3 -c "import sqlite3; c=sqlite3.connect('data/01_harvest/news_radar.db'); print(list(c.execute('SELECT queue_status, COUNT(*) FROM drafts GROUP BY queue_status')))"

# Option B: local is authoritative (you just promoted a draft by hand)
bash scripts/push_state.sh --expect-draft <draft_id>
# exit 0 → sha256 now matches; done
```

**Verification.** Re-run the two `shasum` commands from §0's "Is the Mac DB
in sync" row. They must match.

### 6.4 launchd stopped firing

**Symptom.** No new `_compose_logs/*.log` files in the last 2+ hours.

**Check.**

```bash
# Is the agent even loaded?
launchctl list | grep news-radar-compose
# If no output → agent unloaded. Reload (§2.1 step 3).

# If loaded, what was last exit status?
launchctl list com.hsin.news-radar.compose
# Look at "LastExitStatus" — 0 = success, non-zero = script error

# launchd's own stderr
cat /tmp/news-radar-compose.err.log

# Mac was asleep?
pmset -g log | grep -i sleep | tail -20
```

**Common fixes.**
- Mac was asleep: no recovery needed, next wake will fire the next cycle.
- plist corrupt: re-render from repo (§2.1).
- Script missing: `ls ~/bin/news_radar_compose.sh`; if gone, re-copy (§2.2).
- TCC permission error (`Operation not permitted` in stderr): check that
  `~/bin/` is not inside any CloudStorage path. It should be a plain local
  directory — see `scripts/INSTALL_COMPOSE_LAUNCHAGENT.md` §⚠️.

**Verification.** `launchctl start com.hsin.news-radar.compose` — new log
file appears in `_compose_logs/` within ~30 s.

### 6.5 Draft with wrong or offensive content landed in queue

**Symptom.** You're about to publish (or already did) something that
shouldn't ship.

**Immediate action (not yet published).**

```bash
# Pull it off the queue — do NOT leave it pending while you deliberate
cd ~/news_radar
python3 -c "
import sqlite3
c = sqlite3.connect('data/01_harvest/news_radar.db')
c.execute(\"UPDATE drafts SET queue_status='failed', guard_reason='manual_pull: <reason>' WHERE id LIKE ?\", ('<id>%',))
c.commit()
print('updated:', c.total_changes)
"
bash scripts/push_state.sh --expect-draft <id>
```

Now re-query `queue_status` for that id on the freshly-pushed state branch;
it must be `failed`, not `queued`.

**After-the-fact (already published).** This is a different problem — there's
no automated retract in the pipeline. Retract manually via the Meta-side UI,
then update `publish_log` with a note about the retract so future analytics
don't treat it as a healthy post.

---

## 7. Action items for user tomorrow (2026-04-22 overnight → next morning)

These are commands you should run once you read this:

```bash
# A. Reload launchd so the plist PATH fix (Claude CLI) goes live
launchctl unload ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
launchctl load   ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
launchctl list | grep news-radar-compose
# Expect: "- 0 com.hsin.news-radar.compose"

# B. Smoke-test Claude CLI visibility under the same PATH launchd will use
PLIST_PATH=$(plutil -extract EnvironmentVariables.PATH raw -o - \
  ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist)
env -i PATH="$PLIST_PATH" HOME="$HOME" which claude
env -i PATH="$PLIST_PATH" HOME="$HOME" claude --version
# Expect: path to claude + a version line.
# If "command not found" → the plist PATH is still wrong; re-check §2.1.

# C. Kick off one compose run manually and watch the tail
launchctl start com.hsin.news-radar.compose
sleep 5
tail -f $(ls -t ~/news_radar_snapshots/_compose_logs/*.log | head -1)
# Look for either a [DB] draft … written line (healthy) or a Claude CLI
# fallback activation line (healthy under Gemini 429).
```

Publish of "one real article today" is deliberately **not** automated as
part of this handoff — see `OVERNIGHT_REPORT_2026-04-22.md` §blocker.

---

## 8. Philosophy — why this runbook exists

Automation that prints ✅ but hasn't verified its own side-effects is worse
than manual work, because manual work at least forces a human to look at the
outcome. This system has already bitten us that way three times
(`docs/System_Architecture.md` §7.3 lists them). Every procedure above is
designed so that a human executing it, or a Claude session re-running it,
ends with a **verification step whose output is the success criterion** —
not a log line, not a "probably", not an "usually".

If you find yourself writing a new procedure here, keep that discipline:
end with a SELECT, a sha256 match, a `launchctl list` row, or a
`--expect-draft` assertion. Never end with "should be fine".
