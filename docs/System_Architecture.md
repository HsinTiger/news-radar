# System Architecture — legacy v1 reference

> **Superseded 2026-07-23.** This file preserves the April architecture and is
> not the current SSOT. In particular, the `state` branch and automatic Meta
> publisher described below are retired/paused. Current truth is
> [`README.md`](../README.md), [`OPERATIONS.md`](OPERATIONS.md), and
> [`REBUILD_CHECKPOINT_2026-07-23.md`](REBUILD_CHECKPOINT_2026-07-23.md).

> **Scope of this doc.** `docs/architecture.md` is the canonical data-flow picture
> (Mermaid graph, Opus 4.7 pass on 2026-04-19). This doc is the **operational-reality
> companion**: where things *actually live* on disk, *when* they fire, *who* runs them,
> and *how* the Mac and Cloud stay in sync. Any time an architectural claim is
> ambiguous, this file is the ground truth; if it contradicts code, fix the code or
> the doc — no third source wins.
>
> **Last ground-truth pass:** 2026-04-28, verified against code (`src/db.py`,
> `run_pipeline.py`, `run_publish_queue.py`, `scripts/compose_hourly.sh`,
> `scripts/news_radar_engagement.sh`, `src/engagement.py`,
> `scripts/com.hsin.news-radar.compose.plist`,
> `scripts/com.hsin.news-radar.engagement.plist`), the DB itself, all three
> installed launchd plists at `~/Library/LaunchAgents/`, and last 24 h of
> compose + engagement logs (case study §7.4 added). Phase 9 Item 3
> refactor (2026-04-28) folded `src/reflector_topic.py` into
> `src/reflector/topic.py` and resolved the `src/reflector.py` ↔
> `src/reflector/` import shadow by relocating legacy soul-rule
> reflection into `src/reflector/composer_rules.py` with a re-export
> from `src/reflector/__init__.py`.

---

## 1. Single local clone topology (2026-04-23 起)

There is **one local clone** of this repo on the user's machine, and GitHub holds
the canonical history. This is the standard DVCS pattern — no "dev vs exec"
split, no "which folder do I edit in" confusion.

| Location | Path | Role | DB? |
|---|---|---|---|
| **Local clone** | `/Users/hsin/news_radar` | Human edits + `git push` + launchd + manual compose all run against this one | Authoritative runtime DB |
| **GitHub `main`** | `https://github.com/HsinTiger/news-radar.git` | Off-site canonical backup of code/config/docs | — |
| **GitHub `state`** | same remote, `state` branch | Off-site orphan-push mirror of live DB | network-transported DB lives here |

**Rule.** Source-of-truth for code is GitHub `main`. Source-of-truth for the
**live DB** is GitHub `state` branch. The local clone is a *cache*. Specifically:

- `~/news_radar` → human edits → `git commit` + `git push origin main` (same sitting)
- `~/news_radar` launchd compose → `git fetch origin main && git merge --ff-only origin/main` → runs compose → orphan-push DB to `state` branch
- GitHub Actions (Cloud publisher) → clones fresh → reads DB from `state` → publishes to Meta → orphan-pushes updated `state`

**Golden rule:** edit → commit → push all in the same sitting. The hourly
`compose_hourly.sh` expects `--ff-only` to succeed; any local commit you leave
un-pushed will make the next cycle fail loudly (by design — §5.3).

### 1.1 Historical note: why this used to be three clones

Pre-2026-04-23 there was also an **OneDrive clone** at `~/Library/CloudStorage/OneDrive-.../news_radar`
for cross-device auto-sync of uncommitted work. It was retired because:
- GitHub is a better backup for code (full history, branches, rollback; OneDrive only mirrors latest state)
- OneDrive silently modifies file mtime causing phantom git diffs
- OneDrive occasionally produces `... (conflicted copy).py` sibling files
- The "commit + push in same sitting" habit makes OneDrive's cross-device sync niche unnecessary

If you ever need a cross-device editing workflow again, prefer `git clone` on the
second device over OneDrive.

---

## 2. Every DB instance on disk (derived from code)

All paths below were read from source, not recalled. If source changes, update
this table in the same commit.

| Symbol | Resolves to (on Hsin's Mac) | Source | Notes |
|---|---|---|---|
| `src/db.py: DB_PATH` | `/Users/hsin/news_radar/data/01_harvest/news_radar.db` | `src/db.py:19-20` — `_BASE = Path(__file__).resolve().parent.parent; DB_PATH = _BASE/"data"/"01_harvest"/"news_radar.db"` | **Anchored to code location**, immune to cwd. This is the "real" runtime DB for compose/scorer/composer/publisher. |
| `src/db.py: SCHEMA_PATH` | `/Users/hsin/news_radar/data/01_harvest/schema.sql` | `src/db.py:21` | Init-db reads this. |
| `scripts/*.py: _DB_PATH` | same as `DB_PATH` | `morning_report.py:36`, `classify_dryrun.py:44`, `queue_inspect.py:33`, `flush_legacy_drafts.py:62` all use `_ROOT/"data"/"01_harvest"/"news_radar.db"` with `_ROOT` resolved from `__file__` | Consistent with `src/db.py`. |
| `src/export_drafts.py: DB_PATH` | `/Users/hsin/news_radar/db/news_radar.db` ⚠️ **wrong path, file does not exist** | `src/export_drafts.py:11` | Bug, not currently a live footgun (script apparently unused). Park it. |
| GitHub `state` branch blob | `origin/state:data/01_harvest/news_radar.db` | `scripts/compose_hourly.sh` does `git show origin/state:data/01_harvest/news_radar.db > data/01_harvest/news_radar.db` at start of each run | This is the network-transported DB. Size as of 2026-04-22 04:30 UTC: 1,658,880 bytes. |

On disk today there is exactly **one** `news_radar.db` file under the local
clone (verified via `find ~/news_radar -name "*.db" -not -path "*/.venv/*"`).

---

## 3. Entry-point cwd contracts

Every entry point has an implicit cwd expectation. When it holds, paths resolve
correctly; when it doesn't, something usually still "runs" but writes to the
wrong place. `src/db.py` uses `__file__`-anchored paths so it is cwd-independent,
but some sibling scripts resolve files via `pathlib.Path(...)` relative to cwd.

| Entry point | Where it's called from | Required cwd | Consequence if wrong |
|---|---|---|---|
| `run_pipeline.py` | `scripts/compose_hourly.sh` at `~/news_radar/` | `~/news_radar/` | Reads `.env` from cwd, reads `config/*`, writes `logs/`, writes `data/03_compose/pending_drafts/`. Wrong cwd → `.env` missing → Gemini key not loaded. |
| `run_publish_queue.py` | GitHub Actions `pipeline.yml` step | repo root (actions checkout) | Same concerns as above; CI sets cwd correctly. |
| `scripts/compose_one.py` | Manual: `python -m scripts.compose_one ...` | `~/news_radar/` | `compose_one.py:42-44` prepends `ROOT = Path(__file__).resolve().parents[1]` to sys.path, so import works regardless. But it still uses the `.env` in cwd. |
| `scripts/morning_report.py` | GH Actions daily; also manual | repo root | cwd-independent for DB path (via `__file__`), but other outputs depend on cwd. |
| `scripts/push_state.sh` (v1, 2026-04-22) | Manual | repo root (auto-detects by walking up for `.git`) | Auto-recovers if called from subdir; fails loudly if no repo found. |

**Pragma:** if you add a new entry point, anchor paths with `Path(__file__).resolve().parent(s)` — never raw relative paths — and put the expectation here.

---

## 4. Workflow triggers & concurrency

### 4.1 Mac end (launchd)

| Agent | Schedule | Real script at | Logs |
|---|---|---|---|
| `com.hsin.news-radar.compose` | `StartInterval 3600` (every 1 h from load time) | `~/bin/news_radar_compose.sh` (source: `scripts/compose_hourly.sh`) | `~/news_radar_snapshots/_compose_logs/YYYYMMDD_HHMMSS.log` + `/tmp/news-radar-compose.{out,err}.log` |
| `com.hsin.news-radar.engagement` | `StartInterval 3600` (every 1 h, log-scale bucket dispatch within ±15min of [1h, 24h, 168h] post-age tolerance windows) | `~/bin/news_radar_engagement.sh` (source: `scripts/news_radar_engagement.sh`) | `~/news_radar_snapshots/_engagement_logs/YYYYMMDD_HHMMSS.log` + `/tmp/news-radar-engagement.{out,err}.log` |
| `com.hsin.news-radar.snapshot` | Sunday 10:30 local | `~/bin/news_radar_weekly_snapshot.sh` | `~/news_radar_snapshots/_logs/YYYYMMDD_HHMMSS.log` |

The plists at `~/Library/LaunchAgents/com.hsin.news-radar.*.plist` are produced
from the repo's `scripts/com.hsin.news-radar.*.plist` by the `sed "s|HOME_DIR|$HOME|g"`
step in `scripts/INSTALL_COMPOSE_LAUNCHAGENT.md`. The **repo version contains
`HOME_DIR`**, the **installed version contains `/Users/hsin`**. Both are valid
plists; if they drift otherwise that's a bug.

**Important: launchd's PATH is not inherited from the login shell.** It takes
exactly what `EnvironmentVariables.PATH` says in the plist. As of 2026-04-22 that
is `HOME_DIR/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin` (see
§7.2 case study for why `HOME_DIR/.local/bin` is required).

### 4.2 Cloud end (GitHub Actions)

| Workflow | Schedule | What it does |
|---|---|---|
| `pipeline.yml` | `cron: 0 * * * *` (hourly on :00) | Clone main → restore DB from `state` → `python run_publish_queue.py` → push `state` |
| `reflect_topic.yml` | `cron: 0 22 * * 0` (Sun 22:00 UTC ≈ Mon 06:00 Taipei) | Weekly topic-weight back-prop |
| `reflect_harvest.yml` | `cron: 0 3 * * *` (daily 03:00 UTC ≈ 11:00 Taipei) | Daily harvest analyzer (Phase 9 Item 4) — feed-yield evaluation, proposal-only sunset/investigation entries |
| `reflect_scorer.yml` | `cron: 0 4 * * *` (daily 04:00 UTC ≈ 12:00 Taipei) | Daily scorer analyzer (Phase 9 Item 6) — per-platform AUTO_PUBLISH threshold optimization, proposal-only |
| `feed_healthcheck.yml` | daily | Pings feed URLs, opens GH Issue on failure |

### 4.3 Concurrency & race windows

Both the hourly compose (Mac, :30) and the hourly publish (Cloud, :00)
force-push `state` branch. They deliberately stagger by ~30 min. If their
timings cross, the later force-push overwrites the earlier — this is
**acceptable** because compose only writes new drafts and publisher only writes
updated `queue_status` / `publish_log`, and they work on disjoint rows (publish
touches drafts that compose has already finalized in previous cycles).

All Actions workflows use the same concurrency group `news-radar-pipeline` to
serialize themselves with each other. The Mac side has no such guard — it
trusts the force-push race window to be wide enough.

---

## 5. Mac ↔ Cloud sync contract

Three branches carry three kinds of state:

| Branch | Contains | Who writes | Who reads |
|---|---|---|---|
| `main` | Code, config, docs, tests | Humans (via local clone `~/news_radar` → push) | Everyone fetches this |
| `state` | `data/01_harvest/news_radar.db`, `state/last_harvest.txt`, `archive/`, `LAST_RUN.txt` | Mac `compose_hourly.sh`, Mac `news_radar_engagement.sh` (via `push_state.sh` since 2026-04-26 fix), Mac `push_state.sh`, Cloud `pipeline.yml`, Cloud `reflect_topic.yml` | Mac compose, Mac engagement worker, Cloud publisher — **force-pushed orphan commits, no history retention** |
| `gh-pages` or none for docs | — | — | — |

### 5.1 The orphan-commit pattern

Every writer of `state` branch creates a fresh orphan commit (`git init -b state`
in a tmpdir, copy files in, commit once, force-push). This means `state` has no
git history and its commits can be overwritten freely. The writer's identity is
recorded in `LAST_RUN.txt` (kind, UTC timestamp, host, optionally DB hash).

**Why orphan:** history would balloon the repo (DB is ~1.6 MB, changes every
hour, so 24×365 = 8.7k commits/year). Orphan keeps repo size flat.

### 5.2 What `push_state.sh` adds (new, 2026-04-22)

`scripts/push_state.sh` is a manual-triggered variant of the state-branch push
logic in `compose_hourly.sh`, with two additions that close the 2026-04-20 hole:

1. **sha256 post-condition.** After push, it re-fetches `origin/state`, extracts
   the DB, and asserts the remote blob's sha256 matches local. If not, exit 1.
2. **Optional `--expect-draft <id>`.** Pre-push, SQL-assert the id exists locally;
   post-push, SQL-assert the id exists in the re-fetched DB. This is the
   "I pushed a queued draft and need to confirm it landed" workflow.

Anything that writes a draft and then claims "it's queued" should pipe through
`push_state.sh --expect-draft <new_id>` or equivalent — log lines alone are not
evidence.

### 5.3 Hybrid 兩方同步視覺圖（Mac × Cloud × GitHub）

§5 的表格用 prose 講完了誰讀誰寫，這裡補一張可以 30 秒看懂的視覺版。
兩個本地／遠端實體、兩條分支、七條 recurring 路徑。

```mermaid
graph LR
    subgraph MAC["💻 Mac (本機，單一 clone)"]
        LC["~/news_radar<br/>(人類改 code<br/>+ launchd 跑 compose)"]
    end

    subgraph GH["☁️ GitHub origin"]
        MAIN[("main branch<br/>code / config / docs")]
        STATE[("state branch<br/>news_radar.db +<br/>LAST_RUN.txt<br/>**orphan commits**")]
    end

    subgraph CLOUD["🌐 GitHub Actions runner"]
        PL["pipeline.yml<br/>cron: 每整點"]
    end

    META[("📱 Meta API<br/>FB / IG / Threads")]

    LC -- "①  git push<br/>(code 改動)" --> MAIN
    MAIN -- "②  git fetch<br/>+ merge --ff-only<br/>(每小時 compose 前)" --> LC
    STATE -- "③  restore DB<br/>git show origin/state:...db" --> LC
    LC -- "④  orphan push<br/>(compose 完把 DB 推回)" --> STATE
    MAIN -- "⑤a clone main" --> PL
    STATE -- "⑤b restore DB" --> PL
    PL -- "⑥  publish" --> META
    PL -- "⑦  orphan push<br/>(更新 queue_status)" --> STATE

    classDef code fill:#dbeafe,stroke:#2563eb,color:#000
    classDef data fill:#fef3c7,stroke:#d97706,color:#000
    class MAIN code
    class STATE data
```

**分工規則（記兩條就夠）**：

1. **`main` 分支 = 程式碼／設定／文件**：只有人類會寫（`~/news_radar` → `git push`）。launchd 自動流程只讀 main，不寫。
2. **`state` 分支 = 執行狀態（DB + LAST_RUN.txt）**：Mac 的 launchd compose 跟 Cloud runner 都會寫，都是 **orphan force-push**（無歷史，只保留最新一份）。

**黃金法則**：edit → commit → push 一次坐下做完。不要留 uncommitted / unpushed 的改動過夜——下次 launchd compose 跑 `git merge --ff-only` 時會碰到「本地有新 commit 但沒 push」而卡住（這是刻意設計的，見下）。

**為什麼是 `--ff-only`（Phase 8.20 後）而不是 `git reset --hard`**：
`--ff-only` 只允許「純粹向前」的更新；如果本地意外有了未 push 的 commit（例如 debug 手癢），merge 會拒絕並報錯，讓你人工決定是要 push 還是放棄。`reset --hard` 則會無聲蓋掉——曾經發生過一次本地 hotfix 被清掉的事故。這是從「黃金法則違規」恢復的安全網。

---

## 6. Queue state machine

Reading `drafts` table columns `status` + `queue_status`:

| `status` | `queue_status` | Meaning |
|---|---|---|
| `pending_review` | `NULL` | Scorer said ≥0.65 but <0.9 — composed, not auto-publishable, not queued |
| `auto_approved` | `NULL` | Just composed, waiting to be enqueued (transient; compose should set queue_status in the same transaction) |
| `auto_approved` | `queued` | Ready for Cloud publisher to pick |
| `auto_approved` | `published` | Cloud publisher succeeded on ≥1 platform |
| `auto_approved` | `failed` | Guard blocked / Cloud publisher all-platforms-failed |
| `auto_approved` | `stale` | An older queued draft superseded by a fresher one |
| `published` | `published` | Legacy / post-hoc consistency state |

**Current DB snapshot (2026-04-22 06:00 UTC):**

```
status='auto_approved'  qs='failed'      n=18
status='pending_review' qs='failed'      n=12
status='pending_review' qs=NULL          n=3    ← 2026-04-20 15:59–16:07 drafts
status='published'      qs='published'   n=1
(zero queued)
```

The three `qs=NULL` rows from 2026-04-20 are the **last real composer outputs
on this system**. Everything since then is no-ops due to §7.1. Nothing is
eligible for today's publish without either new compose or manual promotion.

### 6.2 Substrate views (Phase 9 Item 1, 2026-04-27; Item 1.5 fold-in 2026-04-27; Item 1.6 extension 2026-04-28)

Phase 9's unified reflector reads from a thin SQL-view layer rather than
issuing direct queries against base tables. Views live in
`data/01_harvest/views.sql` and are sourced by `src/db.py::init_db` **after**
`schema.sql` + all column migrations (Phase 8.18 `queue_status`, Phase 8.20
`topic_category` / `weighted_score`, Phase 8.16 video columns). Idempotent
via `CREATE VIEW IF NOT EXISTS`; cheap to re-run on every init.

| View | Purpose | Downstream Phase 9 consumer |
|---|---|---|
| `v_post_engagement_aggregated` | **Foundation.** One row per published draft (`drafts.queue_status='published' OR drafts.status='published'`) joined to `news_items`, with the latest per-platform engagement snapshot pulled from `engagement_stats` via correlated subqueries (NULL when a platform has no row). **Item 1.5 (2026-04-27):** added `confidence_score` column (from `drafts.confidence_score`) so downstream analyzers can correlate composer's pre-publish self-rating against realized engagement. | Item 5 composer analyzer + Item 6 scorer analyzer; also feeds the three derived views below. |
| `v_drafts_with_outcome` | Adds `engagement_quartile` via `NTILE(4) OVER (PARTITION BY topic_category)` over the trailing 14-day window. | Item 5 composer analyzer's top-Q vs bot-Q sibling sampler (canonical §8.3 row 4). |
| `v_feed_yield_7d` | Per-feed 7-day publish/fetch counts, average weighted_score, and `engagement_yield_ratio` (share of fetched items that ended up published with non-zero engagement). | Item 4 harvest analyzer's feed sunset/boost proposals (canonical §8.3 row 1). |
| `v_topic_engagement_x_platform` | Per-topic × platform engagement averages over the trailing 30-day window, plus `sample_count` for the §1.2 sample-size gate. **Item 1.6 (2026-04-28)** extends the view with the full per-platform AVG metric set so the Hsin-pinned engagement formula has the substrate it needs without re-deriving off base tables: FB now exposes `fb_avg_likes_30d`/`fb_avg_comments_30d`/`fb_avg_shares_30d`/`fb_avg_reach_30d`; IG exposes `ig_avg_likes_30d`/`ig_avg_comments_30d`/`ig_avg_shares_30d`/`ig_avg_saves_30d`/`ig_avg_reach_30d`; Threads exposes `th_avg_likes_30d`/`th_avg_replies_30d`/`th_avg_reposts_30d`/`th_avg_quotes_30d`/`th_avg_views_30d`. Existing AVG columns + `sample_count` + GROUP BY untouched; the new view definition uses `DROP VIEW IF EXISTS` so older DBs pick up the new columns on next `init_db`. The per-row columns on `v_post_engagement_aggregated` were already complete since Item 1 (FB: likes/comments/shares/views/reach; IG: likes/comments/saves/shares/reach; Threads: likes/replies/reposts/quotes/views) — Item 1.6 verified this and only changed the topic-AVG view. | **Consumed by Item 3 reflector topic analyzer (`src/reflector/topic.py`, 2026-04-27)** — supplies per-platform aggregates that flow into proposal evidence metrics + sample_count cross-check. Math inputs (median+normalize on per-row scores) still come from base tables (Item 3's dual-source math is parked as future tech debt per Hsin's Cowork ruling 2026-04-28; see `audits/2026-04-28_phase9_item1_6_substrate_extension.md` and `audits/2026-04-28_phase9_item3_reflector_topic_refactor.md`). |
| `v_draft_hook_by_platform` (Item 1.5, 2026-04-27) | Per-platform-per-draft "hook" extraction joined (LEFT JOIN) to engagement metadata. Hook rule: facebook = first 100 chars of `platform_drafts.full_text`; instagram = substring before first newline (or full text if none); threads = first 30 chars. Engagement columns are NULL for drafts not yet published. | Item 5 composer analyzer (hook-pattern correlation) + Item 6 scorer analyzer (per-platform first-impression scoring). |

`v_post_engagement_aggregated` is the foundation; `v_drafts_with_outcome` /
`v_feed_yield_7d` / `v_topic_engagement_x_platform` are derived layers that
read from it (or from the same base tables in the same join shape, for
consistency). No materialization, no indexes added — view evaluation is
SELECT-time. If analyzer query patterns later surface a hot path, indexes
get added against the base tables (out of scope for Item 1, see spec).

State-branch implication: views are evaluated against whichever DB the
consumer reads from. The state branch carries the base tables only —
each consumer's local `init_db` re-creates the views on top of the
state-restored schema. No view DDL is committed to the state branch.

### 6.3 Reflector proposal substrate (Phase 9 Item 2, 2026-04-27)

Phase 9 analyzers (Items 3-7) record every proposal-firing in two
storage layers, kept in lockstep by `src/reflector/proposals.py`:

| Layer | Path / object | Role |
|---|---|---|
| **JSONL (canonical)** | `data/05_reflect/proposals/YYYY-WW.jsonl` | Append-only, human-readable, git-friendly audit trail. One file per ISO week, one JSON object per line. Schema per spec §3 Item 2 lines 134-156 of `PM_Radar/specs/phase_9_implementation_plan.md`. |
| **SQLite (mirror)** | `reflector_proposal_lineage` table | Queryable mirror for cross-analyzer SQL (recent proposals from analyzer X, pending decisions, approved-but-not-deployed). Schema in `data/01_harvest/schema.sql §9` + migration `2026-04-27_phase9_proposal_lineage.sql`. Index on `(analyzer, fire_at)` for the cooldown-window hot query. |

**Why both:** the JSONL is the durable, easy-to-grep, easy-to-PR-review
record (git diff of `data/05_reflect/proposals/2026-W18.jsonl` shows a
boss exactly what an analyzer fired this week). The sqlite mirror lets
analyzers and the (future) review UI run set queries without parsing
N week-files.

**Write-path API:** `news_radar/src/reflector/proposals.py`. Public
functions: `write_proposal(proposal) -> fire_id`, `read_proposals(week=None)`,
`update_decision(fire_id, decision, comment)`. Validation rejects
malformed proposals (missing fields, out-of-enum analyzer/platform/
proposal_type/target_config/confidence) BEFORE any side effect.
`write_proposal` appends jsonl first, inserts the lineage row second,
and rolls back the jsonl line by truncation on lineage failure.
`update_decision` rewrites the entire week-file via tmp + `os.replace`
(atomic on POSIX) and updates the lineage row in the same call;
in-place jsonl line edits are deliberately avoided because line
length is variable.

**Cadence:** there is **no standalone proposal-substrate cron**. Items
3-7 each fire on their own schedule (Item 3 weekly Mon 06:00 TW,
Item 4 daily, etc.) and call `write_proposal` directly. Hsin's
review cadence is weekly — Items 3-7 set `boss_attention_required`
appropriately, Item 2 just records.

**State-branch propagation:** wired by Item 3 (2026-04-27).
`src/reflector/topic.py::_maybe_push_state_branch` invokes
`scripts/push_state.sh` (extended in Amendment B `708ed93` to include
`data/05_reflect/proposals/`) when ALL of these hold:
  1. The run wasn't dry-run.
  2. ≥1 category crossed the sample gate this run (i.e. produced a
     proposal entry, regardless of branch).
  3. The `PUSH_STATE` env var is set to `1`/`true`/`yes`.

The env-var gate keeps local dev runs from accidentally pushing to the
state branch. The cron workflow `.github/workflows/reflect_topic.yml`
runs its own state-branch push step (the existing "Persist state" step,
unchanged by Item 3) which already includes the proposals dir via the
extended push_state.sh contract — the in-process trigger is the
fallback for non-CI cron contexts (e.g. launchd-only deployments).
Items 4-7 will reuse this same trigger pattern.

### 6.4 reflect_harvest analyzer (Phase 9 Item 4, 2026-04-28)

`src/reflector/harvest.py` is the second Phase 9 sub-analyzer. **Daily**
cron (`.github/workflows/reflect_harvest.yml`, 03:00 UTC ≈ 11:00 TW),
non-overlapping with `reflect_topic.yml` (Sun 22:00 UTC weekly). Reads
`v_feed_yield_7d` (Item 1, no Item 1.6 dependency) and writes per-feed
proposals to `data/05_reflect/proposals/YYYY-WW.jsonl` via
`src.reflector.proposals.write_proposal` — same Item 2 substrate as
Item 3.

**Two proposal lanes, both PROPOSAL-ONLY** (`boss_attention_required=True`):

| Lane | Trigger | `evidence.metrics.signal` | `action.proposed_value` |
|---|---|---|---|
| **sunset_feed** | `engagement_yield_ratio < 0.05` AND `feed_age_days ≥ grace_days` (cadence-aware: 28d standard / 56d for `source_tier: official` or unknown cadence) AND `publish_count_7d ≥ 3` AND not boss-pinned | `low_yield_sunset` | `sunset` |
| **investigation** (modeled as `proposal_type: sunset_feed`) | `publish_count_7d == 0` AND feed has historical `status='published'` rows older than 7 days | `zero_publish_with_history` | `investigate` |

**Skip reasons** (no proposal written, surfaced in per-run report):
`skip:null_score` (NULL `avg_score_7d` per Cowork 2026-04-27 ruling —
DEBUG-log emitted, never silent), `skip:samples`, `skip:grace`,
`skip:pinned`, `skip:unconfigured` (feed in view but absent from
`config/config.yaml`), `skip:ok` (yield healthy), `skip:zero_no_history`
(brand-new feed with no historical publishes).

**Cadence signal source:** the feed cadence is read from
`config/config.yaml`'s `feeds:` block. There is no explicit
`expected_cadence_per_week` field today — cadence is *derived* from
`source_tier: official` / `source_class: official` (low-cadence, ~1/week)
with all other tiers treated as high-cadence (≥ 4/week). Unknown cadence
defaults to **8-week grace** (conservative). The single point of update
is `derive_expected_cadence()` in `harvest.py`.

**`feed_added_at` source:** parsed from `config/config.yaml` per-feed
ISO-8601 string. Pre-Phase-8.24 feeds (most of the list) do not carry
`feed_added_at` — those feeds get `skip:unconfigured` rather than a
sunset proposal, since we cannot defend an age claim without the field.
This intentionally narrows Item 4's blast radius to the 6 boss-driven
international-official feeds added 2026-04-26 (and any future feeds with
explicit `feed_added_at`).

**Boss-pinned feed gate:** currently a forward-compat stub
(`_is_feed_boss_pinned`) — Item 8 will introduce the real signal
(either a `boss_pinned: true` field per feed in `config/config.yaml`
OR a `feeds.boss_pinned` column). Until then no feed is pinned in
production. The defensive PRAGMA-based check picks up either path
without code change. Same pattern as Item 3's `_is_boss_pinned`.

**No auto-deploy:** harvest analyzer never modifies `feeds.yml`
directly. Sunset is a destructive operation that always requires Hsin
sign-off. Therefore `mark_deployed()` is never called from this module
(unlike `topic.py`'s auto-deploy lane).

**Per-run markdown report (Task C absorption):** every non-dry-run
cycle writes `reports/harvest_<YYYY-MM-DD>.md` with feeds_evaluated /
sunset_count / investigation_count / per-feed verdict + reason. This
subsumes the audit-flagged gap that `tools/dryrun_official_feeds.py`
didn't write reports to disk — that tool's role is satisfied by this
analyzer's natural output. Workflow commits the markdown to `main`
(same shape as `reflect_topic.yml`'s `docs/topic_weight_log/` commit
step).

**State-branch propagation:** `_maybe_push_state_branch()` mirrors
`topic.py`'s env-var-gated subprocess pattern. Same trigger conditions:
non-dry-run AND ≥ 1 proposal written AND `PUSH_STATE` env var set.
The cron workflow's own "Persist state" step is the production
propagation path; the in-process trigger is the launchd-fallback.

### 6.5 reflect_scorer analyzer (Phase 9 Item 6, 2026-04-28)

`src/reflector/scorer.py` is the third Phase 9 sub-analyzer. **Daily**
cron (`.github/workflows/reflect_scorer.yml`, 04:00 UTC ≈ 12:00 TW),
deliberately staggered one hour after Item 4's `reflect_harvest.yml`
(03:00 UTC) to avoid same-time CI contention on the
`news-radar-pipeline` concurrency group. Reads
`v_post_engagement_aggregated` (Item 1, Item 1.6's full per-platform
column extension required) and writes per-platform AUTO_PUBLISH
threshold proposals to `data/05_reflect/proposals/YYYY-WW.jsonl` via
`src.reflector.proposals.write_proposal`.

**Per-platform analysis** (FB / IG / Threads, independent):

1. Pull every published draft from the last **30 days** for this
   platform via the substrate view.
2. Drop rows where the platform's engagement columns are ALL NULL —
   the engagement worker hasn't polled them yet, so they don't carry
   a curve-fitting signal. A separate "polled with 0 engagement" row
   IS included (any non-NULL column = polled).
3. **Sample-size gate:** < 30 polled-published rows → SKIP this
   platform. Write a **lineage-skip row** (no jsonl entry) tagged
   with `evidence.reason="insufficient_samples"`.
4. Bucket each row's `weighted_score` to nearest 0.05 via Python
   `round()` (banker's rounding — half-to-even — chosen over half-up to
   avoid systematic upward bias on bucket boundaries).
5. Compute `engagement_weight` per row using the **Hsin-pinned formulas**
   (Phase 8.20 verbatim, codified in `src/reflector/_engagement.py`):
   - FB:      `likes + 2*comments + 3*shares + 0.01*reach`
   - IG:      `likes + 2*comments + 3*shares + 1.5*saves + 0.01*reach`
   - Threads: `likes + 2*replies  + 3*reposts + 1.5*quotes + 0.005*views`
6. Grid-search threshold T over [0.30, 0.95] in 0.05 steps, scoring
   `mean(engagement_weight | weighted_score >= T)` (mean-of-tail =
   per-published-post engagement). Sub-threshold tails (< 5 rows)
   score 0 — guards against an optimum on a sparse top-bucket.
7. **Sanity-bound clamp** to `[0.50, 0.95]`. If unconstrained T < 0.50
   → propose 0.50 with `evidence.metrics.bound_hit="lower"`. If > 0.95
   → 0.95 with `bound_hit="upper"`.
8. **Noise floor:** `|delta| < 0.02` → no jsonl proposal, lineage-skip
   row with `evidence.reason="below_noise_floor"`.
9. **Calibration override** (Phase 9 §8.4): every actionable proposal
   carries `boss_attention_required=True` regardless of |delta|.
   `mark_deployed()` is never called from this module — Item 6 in
   calibration mode is strictly proposal-only. Phase 9 graduation
   flips this gate; until then auto-deploy is disabled.

**Proposal payload shape:**
- `analyzer="scorer"`, `proposal_type="tune_threshold"`,
  `platform=<facebook|instagram|threads>`.
- `action.target_config="thresholds.yml"`,
  `action.field="per_platform.<fb|ig|threads>.AUTO_PUBLISH"`.
- `evidence.confidence`: HIGH if `sample_count ≥ 60` AND `bound_hit is
  None`; MED otherwise (clamping demotes confidence — the data wanted
  to go further than our sanity rails).
- `evidence.sample_ids`: empty (curve-level proposal, not draft-level).
- `evidence.metrics`: `sample_count`, `excluded_unpolled`, current /
  proposed thresholds, delta, `engagement_per_post_at_*` for both
  thresholds, `total_engagement_lift_estimate`, `bound_hit`,
  `window_days`.

**Spec interpretation note** (audit-flagged for PM ratification, see
`PM_Radar/audits/2026-04-28_phase9_item6_scorer_analyzer.md`): the
spec's literal objective `mean(...) × (count_at_or_above_T /
total_count)` is algebraically equivalent to `sum_in_tail / N`, which
is monotonically non-increasing in T for non-negative weights — the
unconstrained optimum is always GRID_LO and the formula does NOT
"balance per-post quality with publish volume" as labeled. This module
implements the **mean-of-tail** interpretation (matches the spec's
named metric `engagement_per_published_post`) plus a
`MIN_TAIL_FOR_FIT=5` sparse-tail guard. Documented for PM sign-off
before Item 7 lands.

**`thresholds.yml` schema** (introduced by Item 6 — file did not
exist prior; production `AUTO_PUBLISH` lives as a Python constant
in `run_pipeline.py` until graduation deploys catch up). Resolution
order for any consumer:
1. `per_platform.<plat>.AUTO_PUBLISH` if file + key exist.
2. Top-level `AUTO_PUBLISH` as global default.
3. Hard-coded `0.70` (matches today's `run_pipeline.py` constant).

The reader helper is `src.reflector.scorer.read_current_threshold()`.
Existing constants in `run_pipeline.py` and `tools/emergency_oneshot.py`
are NOT yet refactored to read this file — the cutover is parked as a
Phase 9 follow-up (calibration phase makes everything proposal-only;
no auto-deploy reads thresholds.yml today). See the Item 6 audit for
the full parked-consumers list.

**Per-run markdown report (Task C absorption):** every non-dry-run
cycle writes `reports/scorer_<YYYY-MM-DD>.md` with one section per
platform: sample_count, current/proposed thresholds, delta, bound_hit,
confidence, engagement_per_post for both thresholds, and a per-bucket
histogram. Workflow commits the markdown to `main` (same shape as Item
3's `docs/topic_weight_log/` and Item 4's `reports/harvest_*.md`).

**State-branch propagation:** `_maybe_push_state_branch()` mirrors
`topic.py` / `harvest.py`'s env-var-gated subprocess pattern. Trigger
conditions: non-dry-run AND ≥ 1 fire_id produced (actionable OR skip
lineage row) AND `PUSH_STATE` env var set. The cron workflow's own
"Persist state" step is the production propagation path.

**Engagement-weight helper placement:** `src/reflector/_engagement.py`
(new leaf module, zero dependencies on `proposals.py`/`db.py`). The
formulas are pure and Hsin-pinned, so they're shared across analyzers
that correlate signal with engagement (Items 5/6/7). Imported by Item
6 today; Items 5 / 7 will pick it up when they ship.

### 6.6 EDITORIAL_MODE 分發層（Phase 5「一個聲音、四個出口」，2026-06-27）

`EDITORIAL_MODE`（`src/slot_routing.py:33-35::editorial_mode()`，預設關）原本只 gating
選題／搜集／查證／總編閘（Phases 0-4，見 `git log`）。Phase 5 把它延伸到**分發層**，
讓 FB／IG／Threads／Substack 從同一篇稿長出彼此呼應的四個出口。本次（2026-06-27）落地兩條：

| 出口 | 行為（只在 EDITORIAL_MODE 開時） | gate 點 |
|---|---|---|
| **IG 金句卡**（已撤回） | 原規劃把 carousel 第一張封面換成「全篇最利一句」；**信哥 2026-06-27 拍板「別改 carousel」，已 revert（`5987083`）**——封面恆維持原標題 hook，此功能不在 live。 | （無 live gate；死碼已移除） |
| **FB 導流** | FB body 末尾 append 一句含可點連結的 Substack CTA（`https://hsin73.substack.com`），驅動到深度版。 | `src/composer.py::finalize_variant`（只在 `platform=="fb"`、壓字數前 append，CTA 算進 1000 上限、以 URL 判重達成冪等）；CTA 句池在 `src/cta_pool.py::fb_funnel_cta`（Threads CTA pool 的 FB 版） |

兩條都 fail-safe：旗標關／任一步出錯 → 完全沿用舊行為（IG 用標題 hook、FB 無 CTA、絕不 raise／絕不擋發文）。
Threads 的 Substack CTA 早在 Phase 2（`src/cta_pool.py::decide_cta`，走 LLM、反指紋不寫 URL）就有；Substack 本身即深度版。
驗證：off-path FB `full_text` 兩次 finalize byte-identical 且無 substack（FB 導流冪等、fail-safe）。
（IG 金句卡已 revert，封面恆為原標題 hook，不再有相關 gate。）

**SSOT 覆蓋缺口（open item）**：本檔「Last ground-truth pass」仍停在 2026-04-28；
Phases 0-5 的 EDITORIAL_MODE 行為（slot 路由、gather、editor_desk、本分發層）尚未做完整
ground-truth pass 併進 §1–§5。下次動 EDITORIAL_MODE 相關路徑時應補齊。


### 6.7 Substack 審稿流程：對帳式（2026-11-16）

目標（信哥 2026-11-16）：維持品質的前提下，讓第一稿盡量一次就過。入口 `substack_radar/quality_loop.py::run_review_loop`，
預設走 `_run_review_loop_ledger`；`SUBSTACK_REVIEW_MODE=legacy` 切回舊的「請模型挑毛病、整篇重寫」。

| 關卡 | 誰做 | 做什麼 | 位置 |
|---|---|---|---|
| 程式檢查 | 程式 | 出處（E1）、數字定位（E3）、過期（E4）、開場（A1/A1b）、段落（A2）、時間錨點（A3）、數字量級、中文數字 | `quality_loop.py::evaluate` → `evidence_gate.py`、`aeo_gate.py` |
| 篇幅／文風 | 程式 | 太短、AI 套話、沒翻譯的英文、查證用語漏進正文、每句冠節目名、名詞清單 | `quality_loop.py::length_feedback`、`style_gate.py::style_feedback` |
| 對帳 | 兩個不同家的模型同時跑（都跟寫手不同家） | 只找「草稿跟素材矛盾」的句子，每條必附照抄的素材原文 | `fact_audit.py::run`（預設 `glm-5.3-flash,gemini-2.5-flash`，信哥 2026-11-16 同意加 gemini；有人被排除或都沒回時 `gemini-3.5-flash` 補位） |
| 驗證對帳意見 | 程式 | 原句真的在草稿、素材原文真的在素材、不是「素材沒有」類的說法，才算數 | `fact_audit.py::verify` |
| 小修 | 寫手（同第一稿的順序，Haiku 優先） | 只交「原句→改成」，程式換進去；新句子帶進素材沒有的數字或查證用語就不換 | `quality_loop.py::_patch_round`、`apply_patches` |
| 整篇重寫 | 寫手 | 只在結構問題（太短、開場太長、名詞清單）或小修一句都沒換到時 | `quality_loop.py::_run_review_loop_ledger` |

要點：
- 先結構、後對帳：第一稿太短或開場太長時先整篇重寫，重寫完才對帳，一篇只對一次。小修後不再請模型重看（沒動的句子已對過，動過的由程式擋）。
- 動過筆的人（第一稿寫手、重寫的人）同一家的模型不能當查核員；閘道逾時自動換上的模型若跟寫手同家，那份回答不算（`fact_audit.py::pick_models`、`run` 內的 `accept`）。
- 給模型的素材上限用 token 算（`SUBSTACK_EVIDENCE_TOKENS`，預設四萬）；英文逐字稿通常整份給。超過才挑段落（`select_evidence`）。
- 用完次數仍沒修掉的矛盾放在 `LoopReport.unresolved`，compose 轉成 `[跟素材不符，還沒修]` 警告進通知信，不擋稿；程式檢查違規才擋自動發文。
- 帳本：`logs/writing/rejections.csv` 記每輪每條原因（`source`＝程式／審稿／篇幅／文風／影子）；`articles.csv` 的 `rewrites` 自 2026-11-16 起只算整篇重寫，`patches` 是小修句數，兩者皆 0＝一次就過。
- `substack_radar/lessons.py` 讀帳本，把同專欄最近第一稿最常被退的原因（≤4 條）放進寫手 prompt 的「交稿前自查」。
- 開場太長／貼標籤（A1／A1b）只重寫第一個小標之前那一段（`quality_loop.py::_lead_round`），其他段落不動；整篇重寫只留給太短、名詞清單、還在轉述原素材（2026-11-17）。
- 對帳意見若只是英文名字的拼法之爭、且草稿的寫法在素材別處（含影片標題）有出處，視為語音辨識聽錯，不算矛盾（`fact_audit.py::_attested_spelling`、`misheard_name`）。
- Hsin選觀點（`SUBSTACK_COLUMN=hsin`）也跑延伸調研：依主編的角度規劃查詢、不查原影片本身（`composer.py::_build_research_brief_prompt` 的 `hsin` 分支、`compose.py` 的 Hsin 調研區塊）；查不到不擋稿，退回只用素材寫。有 3 份以上延伸資料時量「轉述句比例」，超過 15% 整篇重寫（`style_gate.py::retelling_ratio`）。`SUBSTACK_HSIN_RESEARCH=0` 可關。
- 中文標題的影片先抓中文字幕（`scripts/enrich_youtube_sources.py::_transcript(prefer_zh=)`），不再拿英文翻譯字幕。
- 影子檢查（只記帳）：草稿裡在素材找不到的英文專名與數字（`_shadow_grounding`）。觀察誤報率後再決定是否升成正式檢查。
- 通知信主旨的 ⚠️ 只在需要主編動手時出現（`src/notify.py::needs_editor`）；體檢警告改對定稿重算（`compose.py`，寫檔前）。

為什麼改：翻 32 篇記錄，28 篇把重寫次數用到上限——舊審稿每輪重審全文、固定挑出約 7 條，從不收斂；它自己提的 499 條意見約四成是
「素材裡沒有」（其實是它只看得到素材開頭），約兩成五是背景知識上的挑剔。毒化測試（定稿埋 5 個矛盾）：glm-5.3-flash 59–123 秒、
gemini-2.5-flash 54 秒、gemini-3.5-flash 20 秒、mistral-large-4 754 秒、step-5-preview 1,351 秒都 5/5；
deepseek-v4.1-flash 1,833 秒後回空白，mistral 用真的第一稿再測一次也是 1,529 秒後回空白。
NIM 上來得及用的開源模型只有 glm-5.3-flash。實測（2026-11-16）：Haiku 第一稿 3,957 字，整條審稿 233 秒、零整篇重寫；
同日舊流程兩篇各 108、143 分鐘，新流程第一篇（寫手退到 glm）14 分鐘。

---

### 6.8 寫作口吻：把思考過程寫出來（2026-11-20）

主編拿一篇他認為的好範本對照，說四個專欄「沒有溫度、很知識化、看不到人的思考過程」。量出來的差距
（範本 vs 11/17–11/19 三篇）：查證口吻每千字 0 vs 4–5 處、平均段長 70 vs 96–108 字、舉例情境 4 vs 0–3 個、
小標問句 2/5 vs 0/7。根因是我們給寫手的規則，不是模型：

- 規則要寫手在正文記帳：「沒有的證據就寫查不到」（框架硬規則）、「單一來源要說明限制」、「不確定就說不確定」，
  加上 `counter-case-construction` 要反方寫成「名字：條件→機制→觀測指標」。
- 提示詞互相打架：共同文風說收尾每篇換，深度稿提示又寫死「最後留一個回信問題」；公司標題要「主體：鉤子」又禁冒號；
  財報檢查清單連 podcast／Hsin選觀點都收到。
- 公司文一次收到五份分析框架（約 2.4 萬字），寫出逐格填表的檢查表文章。
- `src/locale_tw.to_traditional` 對繁體稿也跑 s2tw：「只」→「隻」、「台」→「臺」（11/11「隻買第一」、11/19「不是隻在談」）。

改法（刪多於加）：
- `editorial_voice.md` 改寫成七個思考習慣：讀者的問題起手、主角是做選擇的人、每一步先說出「可是」、
  關鍵主張配情境、數字少而有用、不確定用條件句、結尾交出一把尺；查證留在幕後。四個專欄 md 各自改成對應的走法。
- 寫手不再收到 `counter-case-construction`；公司文框架只留 `company-teardown`、`priced-in-or-not`。
  上游副本裡叫寫手「明寫查不到」的句子在載入時改寫（`composer._FRAMEWORK_OVERRIDES`），對標來源名稱也在載入時拿掉。
- `style_gate`：查證口吻超過 2 處就逐句列出（小修只換那幾句）；一句塞 4 個以上數字（年份不算）也列出。都不擋稿。
- `apply_patches` 不收「把錯句改成『尚未核實』」的修正；30 秒看重點丟掉查證紀錄式的條目。
- 標題台：小標連讀要像論證（約三分之一問句），擋「最強反方／我的判斷／查不到／接下來盯」章節名與「那、所以」開頭。
- `to_traditional`：原文以繁體為主時，「只」「台」保留原字；簡體稿照常全轉。
- 測試：`tests/unit/test_human_voice.py`。


### 6.9 讀者數據迴圈：選題與標題用數據判斷（2026-11-20）

主編問「是不是題材、YouTube 來源不夠爆，Substack 也不推我們」。數據的答案是：主因在分發，不在題目熱度。
- 11 月每篇從信箱以外來的瀏覽平均 0.6 次（3 月 12.6）。
- 有人分享、轉貼、留言或帶進訂閱的文章，從寫稿管線上線前的 48% 掉到 6%。
- 熱門題目也沒有比較多人開：〈黃仁勳辭職〉28 人，〈誰在乎巴黎評論？〉38 人。

研究（Robertson 2023、Berger & Milkman 2012、LOLA 2025）加上 116 篇去趨勢回歸，決定了三件事：

1. **選題加「台灣讀者有沒有感」關卡**：`compose._editor_pick`。
   - 評審每集要寫出 `tw_question`（台灣上班族會怎麼問），並打 `tw_score`。
   - 兩項都 ≥ `TW_GATE_MIN`（5）才算過；從過關的裡面挑「台灣＋帶走」平均最高的。
   - 沒有一集過關時，退回只看「帶走」（≥6），不會害整天沒稿。結果記在 `source.tw_gate`。
   - 評審手上有兩份數據，都在 `reader_signals.py`：
     - `taiwan_buzz()`：Google 搜尋趨勢台灣 RSS、PTT 股票／科技工作／房屋板、新聞庫的台灣媒體標題。一天抓一次，存在 `data/substack_stats/tw_buzz/`。
     - `calibration_block()`：我們讀者實際多打開、少打開的文章。
   - 過關那集的 `tw_question` 會交給調研（多查一條台灣的樣子）與寫手（從這個問題起手），並寫進 `source.reader_question`。
2. **標題張力交給實驗**（主編選的）：`headline_desk.polish`。
   - 寫 6 個候選：3 個有張力、3 個中性。`reader_signals.title_arm` 用湯普森抽樣決定這篇用哪一組；同一篇重跑會抽到同一組。
   - 明顯的標題黨（震驚、必看、99% 的人……）不管哪組都擋。
   - 實際上線的分組記在 `source.title_style`。
3. **每週報表算去趨勢成績**：`scripts/substack_stats_report.py`，每週一 08:15。
   - 每篇的成績是「不重複開信人數 ÷ 前後各 7 篇中位數 − 1」，未滿 3 天的不算。
   - 同時寫出兩個檔：`reader_calibration.json`（給選題評審）、`title_arms.json`（給標題抽樣）。迴圈就這樣接起來。
   - 開信率只當參考：它會被名單大小和 Apple 自動開信扭曲。

兩條限制：
- 樣本小。一週約 7 篇，兩組標題要幾週才分得出差 5–10% 的效果，所以抽樣器沒資料時兩組各半。
- 116 篇裡看了 11 個標題特徵，只有負面字（+4.5%）和冒號（−7.3%）落在 90% 區間外。10 月那次「冒號開信率較高」的結論，是早期名單小、開信率本來就高造成的錯覺。

**同日晚間補強**（主編：「用剩下的額度把頻道全面優化」）：
- **賺錢有道也從台灣讀者切入**：
  - `pick_company_candidate.py` 的熱度原本只用英文全名去數（「Apple Inc.」數到 0）。現在加上台灣媒體用中文名提到的次數，權重 ×3。中文名表在 `company_financials._TITLE_ALIASES`，補了美光、應材、科林等台灣供應鏈相關的美股。
  - 加了 `--dry-run`，只印出會挑哪家、不寫檔。
  - 公司文會帶 `source.reader_question`（「台灣讀者為什麼要在意蘋果？」）。調研多查一條這家公司跟台灣的關係，寫手照這個方向開場。
- **拿掉 `[缺少具體回信問題]` 警告**：它要求文末一定要有問句，跟新口吻的結尾規則（交出一個讀者用得上的判斷方法）矛盾。
- **Notes 不再出現 AI 套話**：`notes_desk.clean_note` 把「我的判斷是：」改成「我目前傾向認為，」，整句拿掉「什麼條件下我會改這個判斷？」這類固定反問。標題台的 Notes 提示也禁止這兩種寫法。
- **FB 點名檢查放寬**：`fb_follow.show_named` 允許省略中間名縮寫，以及 Podcast／Show 這類通用字。原本節目「Peter H. Diamandis」、貼文寫「Peter Diamandis」就被擋，整篇 FB 沒發。
- **對帳審稿**：問題寫成「素材沒說 X，而是說 Y」時算矛盾、保留，不再當成「素材沒有」丟掉（`fact_audit._CONTRADICTION`）。
- **讀者數據每天更新**：`daily_publish.refresh_reader_data` 發完文就重算一次 `reader_calibration.json`、`title_arms.json`，標題實驗不用等週一。
- **選題看得到最近一週寫過什麼**：`compose._recent_block` 附上取材節目的原標題。試跑時關卡挑了第三集黃仁勳，同一件事除非有全新問題，score 給 4 分以下；讀者問題也不准寫成個股買賣建議。
- **小標只換有毛病的**：`apply_subheads` 不再重寫原本合格的小標。試跑時「他說中國在表演，卻把中國當頭號對手」被改成「他前後的說法並不一致」，跟內文矛盾。

**2026-11-21 融入已驗證的方法論**（主編要求參考台灣創作者、媒體實務與公開論文）。完整對照表在報告頁。
- **標題用常見字**（Shulman 等 2024, Science Advances：華郵與 Upworthy 三萬多組實驗）：
  - `reader_signals.title_complexity` 計分：抽象詞、英文字（AI 除外）、引號、超過 13 字。
  - 標題台在抽到的那一組裡挑最簡單的。
  - 我們 116 篇方向一致（+4.5%），但跟冒號標題重疊、分不開，所以不設成硬門檻。
- **選題多評「讀完會想轉給誰」**（Berger & Milkman 2012；STEPPS）：
  - 評審要寫出 `share_to`（一種具體的人），寫不出來 `share_score` 就算 0。
  - 過關的題目看「台灣有感、能帶走、會被轉傳」三項的平均。
- **守住角度**（M觀點、科技島讀的經驗）：`score` 改成看能不能用「它怎麼運作、錢流到誰手上」講出別人沒講的東西。
- **做不到或待主編決定的**：
  - 主旨放收件人姓名（Sahni 等 2018）：Substack 不支援。
  - 推薦互換：主編之後自己做。
  - 作者人脈分發：跟「只做 Substack 站內」衝突，待決定。

**2026-11-21 凌晨可靠度修正**。賺錢有道最近四個週末天只發出一篇，11/12 智取未來也沒發，原因與修法：
- **數字換算誤判**（11/15 Pfizer）：`evidence_gate._scaled_hit` 把來源裡的「數字＋規模單位」換成實際數值再比，容許 0.6% 四捨五入，「63,625M」對得到「636.25 億」。事實表也照樣換算。
- **量級檢查錯配**（同一篇）：「同業合計營收 6,210.87 億」剛好約等於輝瑞營收的 10 倍。現在數字出自外部來源就不報量級錯誤（`quality_loop.evaluate`）。
- **開場句數**（11/12）：`aeo_gate` 改成前 12 句有作者表態（`_LEANING`）就放寬到 16 句。新口吻先寫讀者問題、再讓主角登場，開場會變長。
- **調研不足整天沒稿**（11/14 寶僑只找到 3 個來源）：
  - 改用 exit `EXIT_RESEARCH_SHORT`（11），跟「草稿已建好、紀錄待對帳」的 exit 6 分開。
  - 週末排程遇到 11 會換一家公司、只重試一次，仍然一天只寫一篇。
  - `~/bin` 的排程腳本是從 `scripts/substack_editorial_worker.sh` 複製的，改完要重新複製。
- **說明信不會被發出去**（11/12〈7,307 字逐字稿沒送到我停筆〉）：
  - `style_gate.refusal_signals` 抓「寫不出來、沒送到我手上、停筆」這類句型，兩種以上就算。
  - 定稿時命中就標成「品質迴圈未通過」，每日發文挑稿時也再檢查一次。
- **單元測試全綠**（1114）：
  - Windows CLI 路徑改用 `PureWindowsPath`。
  - 額度測試固定 `LITELLM_MODEL`。原本長期 4 個紅燈，新問題容易混在裡面看不到。

### 6.10 糾錯台：發稿前自動核對「跟素材不符」（2026-11-21）

觸發：`daily_publish` 選中的稿子帶 `[跟素材不符，還沒修]`（審稿迴圈用完次數還沒修掉的矛盾）。
程式 `substack_radar/fix_desk.py`：

1. 重抓文中引用的原始網頁；表格一列一行、儲存格用「｜」隔開（蘋果那篇審稿把 FY2008 表頭跟 FY2026 數字對錯欄，誤報兩條）。
2. 糾錯模型（跟寫手同一順序）逐條判定：真錯（給改法）／誤報（附原文）／查不到（只拿掉有疑問的那段）。
3. 程式驗證，模型只提議：
   - 原句要在稿子裡，引用的原文要在網頁上。
   - 誤報要拿得出同一個數字。
   - 新數字要在原文找得到（億↔B 換算）。
   - 出處連結不能被拿掉，改動不能大到像重寫。
   - 沒過的條目把原因退回，重做一輪。
4. 改 Substack 草稿：在單一文字節點找最短、唯一的片段替換，寫回後讀回確認；本機稿件同步改。`metadata.audit_warnings` 換成 `[已糾錯]`／`[審稿誤報，已核對原文]`，過程記在 `fix_log.json`。
5. 有任何一條沒處理乾淨，這篇就不發，改選下一篇候選。不管結果如何，都把工作紀錄寄給信哥（`src.notify._dispatch`）。

驗收：用蘋果那篇修改前的版本跑真實模型，四條全部判對：一條誤報、兩條同句併成一處、一條查不到改寫。結果跟人工核對一致。

### 6.11 週末旗艦第 1 步：文件組、來源分級、隱藏抽查（2026-11-21）

依據：信哥看完硅谷101〈Anthropic崛起史〉後定案。仍天天寄；週末賺錢有道升級成旗艦專題，上下篇共用一份調研，一篇 3000–4000 字。

- **文件組** `substack_radar/filings_desk.py`（只處理美國公司）：
  - 程式從 SEC 抓最新 10-K、10-Q 和財報 8-K 的新聞稿，切出業務說明、風險因素、管理層討論三章。
  - 免費的閘道 fast 模型逐段交回證據卡（主張、原文引句、數字、用途）。程式驗證引句在原文、數字在引句裡，沒過的退回。
  - XBRL 年度數字（營收、毛利、營業利益、淨利、研發費用、營業現金流、資本支出）直接用程式拿，不經過模型。
  - 結果依申報文件編號快取在 `data/filings_cache/`，週日下篇直接重用。
  - 研究包最前面放 SEC 文件；寫手只看證據卡，最多 36 張，各用途輪流挑。
  - AAPL 實測：7.5 分鐘、49 張卡、退回 3 張，零付費 token。
- **來源分級** `source_tier.py`：研究包每個來源都標 A/B/C/D 級。D 級整理站只能當線索，關鍵數字要出自 A 或 B 級。
- **隱藏抽查** `probes.py`，只在公司稿跑，規則不寫進寫手的指令：
  - `truth_probe`：比對年度總額和 SEC XBRL，分部營收和季度數字不判。
  - `citation_probe`：依日期當種子，隨機抽 4 個「帶連結又帶數字」的分句，重抓網頁，確認數字在上面；D 級整理站優先抽。
  - 沒過的標成 `[跟素材不符，還沒修]`，交給發稿前的糾錯台（6.10）。
  - 實測：修正後的蘋果稿零誤報；修正前的稿子抓到「49% 掛錯來源」。
- **模型分工**（11-21 晚，信哥：Gemini 系優先、依任務適配）：每日寫手走 agy，鏈為 Gemini 4 Argon (High)（Pro 級，寫長文）→ Neon → Hydrogen → 3.8 Flash → Claude Sonnet 5.5，`AGY_AUTO_LATEST=0`。週末旗艦 `SUBSTACK_FLAGSHIP_AGY_MODEL`＝Gemini 4 Neon (XHigh)。讀原文交證據卡 `SUBSTACK_EXTRACT_AGY_MODEL`＝Gemini 3.8 Flash (Medium)，閘道 fast 當後備。agy 寫程式工單用 Gemini 4 Hydrogen（執行者角色）。
- **上下篇規格**：上篇寫光環（錢怎麼流、轉折點），下篇寫另一面（依賴、風險、獲利品質、評價、台灣），貫穿一個核心矛盾。詳見 `config/editorial_company.md`。
- 還沒做（第 2、3 步）：聲音組、反方組、時間線組；每週從失敗案例學習；平日三個專欄改成 2000–3000 字並套用同一套做法。

## 7. Case studies — failures that forced doc updates

### 7.1 Gemini 429 × Claude CLI not-in-PATH = silent pipeline stall

**Symptom (2026-04-20 evening onward):** Hourly compose appears to run. Every log
ends with `pipeline exit code: 0` and `state branch 已更新`. But `drafts` table
stops receiving new rows after 2026-04-20T16:07. Publisher queue goes empty and
stays empty.

**Root cause** (verified 2026-04-22 from `~/news_radar_snapshots/_compose_logs/20260422_*.log`):

1. Gemini free tier quota is 20 req/day on `gemini-3-flash` (as of April 2026).
   User's `.env` key burned through daily by hourly composes + morning_report +
   reflect_topic. Every hour from somewhere around daily reset, scorer hits 429.
2. `src/llm_brain.py:146-150` defines `_claude_cli_available()` as
   `shutil.which(CLAUDE_CLI_BIN) is not None`.
3. launchd's plist PATH was `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`.
   Claude CLI is installed via the native installer at
   `/Users/hsin/.local/bin/claude` (symlink → `/Users/hsin/.local/share/claude/versions/<v>`),
   which is **not** in that PATH. So `which` returned None, Claude fallback path
   never activated.
4. Scorer falls through to `return None` → `process_item` returns
   `"skipped_no_llm"` → no draft written → commit is empty → state branch
   push happens but the DB is unchanged (LAST_RUN.txt bumps prevent "no commit"
   skip, so the push appears to succeed).
5. Hunter loop scans 8 items, all skip, loop terminates cleanly — the
   `pipeline exit code: 0` comes from a well-behaved skip, not from successful
   work.

This is also the origin of the "compose_one reported qs=queued but DB empty"
handoff report on the morning of 2026-04-22: the prior agent read "queued" in
the log out of context, but `process_item` almost certainly returned
`skipped_no_llm`. Final verification would require the terminal scrollback
from that run, which has been lost; hypothesis stands.

**Fix (applied 2026-04-22):** Update plist PATH to include `HOME_DIR/.local/bin`
(see `scripts/com.hsin.news-radar.compose.plist`; installed version at
`~/Library/LaunchAgents/` same but with `/Users/hsin/`). After user runs
`launchctl unload/load`, the next hourly compose will have `claude` on PATH
and the Phase 8.19 fallback will activate when Gemini 429s.

**Detection going forward:** `scripts/morning_report.py` should include a
"LLM path health" section — count of `skipped_no_llm` returns in the last 24 h.
If every cycle skipped → page. (Noted as follow-up; not implemented in this
session.)

### 7.2 Launchd minimal PATH trap

The generalization of §7.1: **launchd does not inherit the login shell's
PATH**. If a binary is installed under `~/.local/bin`, `~/.npm-global/bin`,
`~/.volta/bin`, nvm, mise, conda, or any other user-level prefix, it must be
explicitly listed in `EnvironmentVariables.PATH` inside the plist (or the
script must hard-code the absolute path). Don't assume that "works in my
terminal" means "works under launchd".

**Smoke test before deploying any plist change:**

```bash
# Simulate launchd's PATH, check the binary you depend on
env -i PATH='<same string as plist>' your_binary --version
```

If that fails, launchd will fail too.

### 7.4 Engagement worker writes clobbered by compose's state-restore (2026-04-26)

**Symptom (2026-04-25 → 2026-04-26):** State-branch DB's `engagement_stats`
table sits 17.5 h stale despite the new hourly engagement worker
(`com.hsin.news-radar.engagement`, added 2026-04-25) firing every hour and
its log saying `OK=N committed`. Local DB equally stale. Specifically: only
3 rows from `2026-04-26T07:02 UTC` survived in DB; runs at 11:02 / 13:02 UTC
each claimed to commit ≥1 row but those rows were nowhere on disk hours later.

**Root cause** (verified 2026-04-26 by correlating engagement worker logs
in `~/news_radar_snapshots/_engagement_logs/` with compose logs in
`~/news_radar_snapshots/_compose_logs/`):

1. Engagement worker writes to `data/01_harvest/news_radar.db` and commits.
2. ~3-25 minutes later, `compose_hourly.sh` line 76 runs
   `git show origin/state:data/01_harvest/news_radar.db > data/01_harvest/news_radar.db`
   to "restore" DB from state branch. This is a **whole-file overwrite** —
   any local engagement_stats writes since the last state-push are lost.
3. Compose then runs the pipeline and pushes the resulting (engagement-free)
   DB back to state. State-branch engagement_stats stops advancing.
4. The only engagement rows that survive are ones written in the narrow
   window between compose's restore step and compose's push step (typically
   <3 minutes per cycle). E.g. the 2026-04-26T07:02:50 rows survived because
   compose at 15:02:11 CST started its restore, then engagement at 15:02:50
   CST won the race against compose's push at ~15:05.

The original `news_radar_engagement.sh` comment explicitly said "不 push_state，
等下一次 compose_hourly.sh 順手帶上去" — that assumption was wrong: compose's
restore is destructive, not additive.

**Fix (2026-04-26):** `news_radar_engagement.sh` now compares
`MAX(fetched_at)` from `engagement_stats` before vs after `python -m
src.engagement`. If new rows were written, it calls `scripts/push_state.sh`
immediately to orphan-push the DB to state. Concurrent-writer race with
compose is acceptable because (a) state-branch already documents
disjoint-table force-push semantics in §4.3, (b) engagement_stats and
drafts/news_items are disjoint, so compose's next-cycle restore picks up
whatever engagement most-recently-pushed and includes those rows in its
own push, (c) push_state.sh has sha256 + fetch-back post-condition, so a
silent-loss like §7.3 cannot happen on the engagement push.

**Detection going forward:** any future "DB X is stale on state branch
despite worker logs saying it ran" case should immediately check whether
the worker pushes state itself OR has a downstream that does so before the
next state-restore clobber. Local-only writes between two state-restorers
are a known anti-pattern now.

### 7.3 "Log says ✅ so it worked" — post-condition mandate

Multiple recent bugs (2026-04-20 DB confusion, §7.1 silent stall, prior
emergency-template pollution) share a shape: a shell / Python pipeline prints
a success line that is not backed by a SELECT / assert on the actual state
change. From 2026-04-22 on:

- **Every DB write that is intended to be persistent** must be followed by a
  SELECT that asserts the expected row exists, and the assertion result (not
  a log line about the intent) is what determines success.
- **Every state-branch push** must be followed by a fetch + sha256 compare
  (see `push_state.sh` §5.2). `compose_hourly.sh` still uses the log-line
  pattern for now — that's acceptable because the force-push semantics make
  silent loss cheap to recover from, but anything manual or high-stakes
  should use `push_state.sh`.
- **Compose_one's own result log** (`[ComposeOne] ✅ DB 最新 draft: …`) already
  does this right — it queries back after `conn.commit()` and only prints ✅ if
  the new row exists. Pattern to copy.

---

## 8. What the skills enforce

Three user-level skills (installed under `news_radar/.claude/skills/` per repo
decision, 2026-04-22) bind the above disciplines into the agent workflow:

- **`project-spec`** — forces any Claude session on this repo to read this
  file (§ System Architecture) before making architectural claims, and to
  write back any newly-discovered ground truth into this file before closing
  out.
- **`scoped-vdd`** — before editing any module, the agent must write a scope
  declaration + explicit edge cases + explicit post-condition, *then* edit.
  No "read-code-and-edit-at-same-time" fluency; the post-condition clause is
  what links log output to reality (§7.3).
- **`cto`** — meta-process rules: log cadence (one checkpoint per completed
  unit), single-clone workflow protocol (edit → commit → push in one sitting;
  never leave unpushed commits overnight), red-line definitions (`git push`,
  `rm` of data, large API bursts, anything touching PII).

See `news_radar/.claude/skills/<name>/SKILL.md` for the full definitions.

---

## 9. Open items not yet in code

- `scripts/morning_report.py` should count `skipped_no_llm` returns in last
  24 h and flag if every compose cycle skipped (§7.1 detection clause).
- `src/export_drafts.py:11` still has the wrong DB path (`db/news_radar.db`).
  Not currently breaking anything — script unused. Park until first use.
- `compose_hourly.sh` prints `✅ state branch 已更新` without fetching back to
  verify. Low-priority since force-push semantics make silent loss cheap to
  recover from, but would be a good hardening.
- The Gemini free-tier cap (20/day) is structurally insufficient for the
  current workload. Either upgrade the plan, or accept the cap and tune
  compose frequency down. Either way, once §7.1's fix is live, Claude CLI
  fallback should keep the pipeline running through Gemini outages.
- EDITORIAL_MODE（Phases 0-5）整體尚未做完整 ground-truth pass 併進 §1–§5：
  slot 路由、`src/gather.py`、`src/editor_desk.py`、Phase 5 分發層（§6.6）目前只在各模組 docstring
  與本檔 §6.6 有記載。下次動到 EDITORIAL_MODE 路徑時，補一輪 §1–§5 並 bump「Last ground-truth pass」。

---

## Appendix — Ground-truth commands

If in doubt, re-run these (they are cheap and read-only):

```bash
# Where is every DB_PATH defined?
grep -rn "DB_PATH\s*=" ~/news_radar/src ~/news_radar/scripts ~/news_radar/run_*.py

# What DB files actually exist?
find ~/news_radar/ -name "*.db" -not -path "*/.venv/*" -not -path "*/__pycache__/*"

# What's on the state branch right now?
cd ~/news_radar && git fetch origin state && git show origin/state:LAST_RUN.txt

# Launchd PATH vs reality
plutil -extract EnvironmentVariables.PATH xml1 -o - \
    ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
env -i PATH=$(plutil -extract EnvironmentVariables.PATH raw -o - \
    ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist) which claude

# Are any drafts actually queued right now?
python3 -c "import sqlite3; c=sqlite3.connect('$HOME/news_radar/data/01_harvest/news_radar.db'); \
print(list(c.execute(\"SELECT queue_status, COUNT(*) FROM drafts GROUP BY queue_status\")))"
```

Any disagreement between these outputs and this document means the document is
stale and should be updated *in the same commit* that makes the code change.
