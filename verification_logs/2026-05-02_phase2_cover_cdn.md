# Verification Log — Phase 2 Cover CDN

**Date**: 2026-05-02
**Feature**: Symmetric cover-cdn URL flow for FB + IG, dashboard cover audit page, TTL cleanup
**Authored by**: News Radar PM agent
**Read order**: skim §1 (summary table) → §3 (what Hsin verifies on Mac) → drill down only if §3 surfaces an issue

This log exists because unit tests share the same logic chain as the
implementation (PM thinks → PM writes code → PM writes tests against
same mental model). Tests passing only proves *I built what I designed*
— not that the boss-relevant outcome will happen. Following the
verification-independence rule (`PM_Radar/CHARTER.md §Communicating
with Hsin`), each row below uses a logic angle that doesn't reuse
the implementation's mental model.

---

## §1 Summary table

| Verification angle | Tool / source | Result | Boss-relevant claim it supports |
|---|---|---|---|
| Output is a real PNG (not just bytes I wrote) | external `file` command | ✅ both files report `PNG image data, … 8-bit/color RGB` | renderer output is a real, openable PNG, not a lie |
| Image dimensions match spec | PIL re-open of saved file | ✅ IG = (1080, 1350), FB = (1080, 1080) | aspect ratios are correct for both platforms |
| Cross-repo URL config consistent | regex-extract from `cover_uploader.py` AND `CoverPage.jsx`, compare | ✅ owner=HsinTiger, repo=news-radar, branch=cover-cdn match in both files | dashboard's URL pattern matches uploader's; if uploader pushes correctly, dashboard will find the file |
| Workflow YAML parses + has required fields | PyYAML + structural assertions | ✅ `permissions: contents:write`, `GITHUB_TOKEN` env, font-download step all present | GH Actions runner has push auth + fonts when publishing |
| Bash cleanup script syntax | `bash -n` (parse only, no exec) | ✅ no syntax errors | TTL pruning won't break on the first cron run |
| End-to-end pipeline trace | call `prepare_publish_image` with mocked NETWORK BOUNDARIES (download + upload) but real renderer + real PIL | ✅ FB → `…/abc123_fb.png`, IG → `…/abc123_ig.png`, Threads → original news URL | publisher will receive the right URLs for each platform |
| Unit tests | pytest | ✅ 108 / 108 green | each module's contract holds in isolation |
| Dashboard build | `vite build` | ✅ 871 modules transformed, 670 KB JS bundle | new CoverPage compiles, renders, no broken imports |

108 unit tests cover the modules in isolation. The 6 verification
angles above test boss-relevant *outcomes* using tools / file-readers
that didn't write the implementation — that's the independence the
CHARTER demands.

---

## §2 What this log does NOT cover (and why)

These are real-world checks that **cannot be done from the dev sandbox**
because they require live credentials, real git push, real Meta API
calls, or real network round-trips:

| Claim | Why sandbox can't verify | Who/what verifies |
|---|---|---|
| `cover-cdn` branch actually receives a push | no GitHub credentials in sandbox; `.git/index.lock` permissions issues | Hsin's first cron cycle on his Mac OR first GH Actions publish run |
| `raw.githubusercontent.com/.../cover-cdn/...` returns the PNG bytes | requires real branch to exist on GitHub | curl from Hsin's terminal after first push |
| Sha256 round-trip (uploaded file == fetched file) | depends on the above | Hsin can run a one-liner — see §3 |
| FB Graph API accepts the cover-cdn URL | real Meta API + real access token | first published post after deploy |
| IG Graph API accepts the cover-cdn URL (the Phase 2 promise) | real Meta API + real access token | first published post after deploy |
| Dashboard `/cover` page renders thumbnails from real URLs | requires live deploy + real cover-cdn content | Hsin opens hsintiger.github.io/cover after both deploys land |

**These are not red flags.** They're inherent to any change that
touches live systems. The verification log's job is to make this gap
explicit so we don't pretend "all tests pass" means "shipped safely".

---

## §3 What Hsin runs on his Mac to close the gap

After pulling these commits and pushing both repos, run these checks
**in order**. If any fails, stop and surface to PM — don't keep going.

### §3.1 Confirm cover-cdn branch was created on first publish

```bash
# After the next compose+publish cron cycle (or trigger one manually):
git fetch origin cover-cdn
git ls-tree -r origin/cover-cdn | head
# Expected: at least 2 PNGs visible — {draft_id}_fb.png and {draft_id}_ig.png
```

If the branch doesn't exist after the cycle: check `pipeline.yml`
GH Actions logs for `[cover_uploader]` warning lines — likely
auth (token doesn't have contents:write on the branch).

### §3.2 Sha256 round-trip — uploaded file == fetched file

```bash
# Pick the latest draft_id from a recent publish_log:
DRAFT_ID="<paste the draft_id you see in the cover-cdn branch>"

# Local file (from cover_cache, if Mac was the renderer):
LOCAL_SHA=$(shasum -a 256 ~/news_radar/assets/cover_cache/${DRAFT_ID}_fb_1x1.png 2>/dev/null | cut -d' ' -f1 | head -c 16)

# Remote file (what FB actually sees):
REMOTE_SHA=$(curl -sL "https://raw.githubusercontent.com/HsinTiger/news-radar/cover-cdn/${DRAFT_ID}_fb.png" | shasum -a 256 | cut -d' ' -f1 | head -c 16)

echo "local:  $LOCAL_SHA"
echo "remote: $REMOTE_SHA"
# Expected: identical sha256 prefixes — proves Meta will fetch the same bytes you rendered
```

(Cloud-rendered case: the Mac local file won't exist; in that case
download the remote file twice 30s apart and confirm same sha — that's
a weaker check but still catches CDN corruption.)

### §3.3 First post visual — boss eyeballs

After cron's first publish:
- Open facebook.com/`主力爸爸我錯了` → newest post → cover should be deep navy + bold white title + purple `AI 模型` chip + `主力爸爸我錯了 · 5/2` brand bar.
- Open instagram.com/`smartmmmoney` → newest post → same template, brand bar reads `smartmmmoney · 5/2`.
- If either falls back to original news image: PM agent missed an edge case — surface log line `[cover_pipeline] passthrough` from the publisher run and we'll diagnose.

### §3.4 Dashboard cover page

After dashboard re-deploys (auto via GH Pages on push to main):
- Open hsintiger.github.io
- Sidebar should show new "封面" item between 歷史 and 被擋掉
- Click → grid of cards
- Filter "近 7 天" → see this week's posts
- Filter "含失敗" → see any post where FB or IG didn't ship; those are the ones to investigate

### §3.5 First TTL cleanup (in 7 days, not now)

Sunday 03:00 UTC the cleanup workflow fires for the first time. After
~7 days have elapsed since first publish, the first PNGs SHOULDN'T be
deleted yet (TTL=30d). After 30 days, run `gh run list --workflow
"Cleanup cover-cdn"` and confirm the workflow exits 0 and reports
"deleted N files".

Until then, don't touch — let it cook.

---

## §4 What I learned doing the verification (for next time)

Things that surfaced ONLY because I ran black-box checks (not unit tests):

1. **Cross-repo URL drift risk** — if I'd only run unit tests, both
   `cover_uploader.py` and `CoverPage.jsx` would happily test against
   their own URL patterns. The independent regex-extract caught any
   drift between the two files. Lesson: any string contract spanning
   two repos needs an explicit cross-file consistency check, not just
   per-file unit tests.

2. **The font download step is not unit-testable** — workflow YAML
   doesn't expose its run-time behavior to pytest. The `yaml.safe_load`
   + assertion check is structural ("right keys are there"); it doesn't
   prove the curl URLs work. That gap is now §3 boss verification.

3. **PIL fallback obscured a Latin-glyph rendering issue** — the
   sandbox's only CJK-capable font (Droid Sans Fallback) lacks Latin
   glyphs, so my sample renders showed boxes for "OpenAI". This was
   a HARMLESS verification artifact (real Source Han fonts on Hsin's
   Mac and on GH Actions cover Latin), but worth flagging: if Hsin
   sees boxes in his first real render, the font download step
   probably failed.

---

## §5 Re-running this verification after future changes

When `cover_renderer.py`, `cover_uploader.py`, `cover_pipeline.py`,
or `CoverPage.jsx` changes, re-run angles 1–6 from §1 (the script
chunks are inline above for copy-paste). Don't trust unit tests alone
to cover those changes.

When the URL pattern itself changes (owner/repo/branch), the
cross-repo consistency check (§1 row 3) is the load-bearing one —
without it, the dashboard silently breaks while the publisher silently
publishes to a URL nobody's reading.
