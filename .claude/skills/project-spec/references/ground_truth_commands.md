# Ground-truth commands for News Radar

Cheap, read-only commands for re-verifying architectural claims against the
actual system. Lift from `docs/System_Architecture.md` Appendix for quick
reference; rerun any time you're about to assert something and want to be
sure.

## DB locations

```bash
# Every DB_PATH defined in the codebase
grep -rn "DB_PATH\s*=" ~/news_radar/src ~/news_radar/scripts ~/news_radar/run_*.py 2>/dev/null

# Every actual DB file on disk in the exec clone
find ~/news_radar/ -name "*.db" -not -path "*/.venv/*" -not -path "*/__pycache__/*" 2>/dev/null

# Size + mtime + sha256 of the canonical DB
ls -la ~/news_radar/data/01_harvest/news_radar.db
shasum -a 256 ~/news_radar/data/01_harvest/news_radar.db
```

## State branch inspection

```bash
# Non-invasive peek at origin/state (doesn't disturb your working copy)
cd /tmp && rm -rf state_peek && mkdir state_peek && cd state_peek
git init -q
git fetch --depth=1 https://github.com/HsinTiger/news-radar.git state --quiet
git show FETCH_HEAD:LAST_RUN.txt
git show FETCH_HEAD:data/01_harvest/news_radar.db > fetched.db
shasum -a 256 fetched.db
```

## Launchd reality check

```bash
# Is the agent scheduled?
launchctl list | grep news-radar

# What PATH is actually in the installed plist?
plutil -extract EnvironmentVariables.PATH raw -o - \
    ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist

# Does Claude CLI (or any dependency) actually resolve under that PATH?
env -i PATH=$(plutil -extract EnvironmentVariables.PATH raw -o - \
    ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist) \
    HOME="$HOME" which claude

# How does the installed plist differ from the repo version?
diff <(sed "s|HOME_DIR|$HOME|g" ~/news_radar/scripts/com.hsin.news-radar.compose.plist) \
     ~/Library/LaunchAgents/com.hsin.news-radar.compose.plist
# Empty diff = in sync. Non-empty = drift between repo and installed.
```

## DB state (drafts / queue)

```bash
# Queue status histogram
python3 -c "
import sqlite3
c = sqlite3.connect('$HOME/news_radar/data/01_harvest/news_radar.db')
print(list(c.execute('SELECT status, queue_status, COUNT(*) FROM drafts GROUP BY status, queue_status')))
"

# Most recent drafts
python3 -c "
import sqlite3
c = sqlite3.connect('$HOME/news_radar/data/01_harvest/news_radar.db')
for r in c.execute('SELECT id, status, queue_status, generated_at FROM drafts ORDER BY generated_at DESC LIMIT 5'):
    print(r)
"
```

## Compose logs

```bash
# Last 8 compose runs, by file
ls -lt ~/news_radar_snapshots/_compose_logs/*.log | head -8

# Did the last run actually write a draft?
tail -80 $(ls -t ~/news_radar_snapshots/_compose_logs/*.log | head -1) \
    | grep -E "(draft .* written|skipped_no_llm|Gemini 429|claude)"
```

## Rule of thumb

If you're about to write a claim into `System_Architecture.md` or act on it
in code, run the matching command above first. It's cheap. The cost of being
wrong is a day of debugging.
