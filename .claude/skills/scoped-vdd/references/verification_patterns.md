# Verification patterns — by side-effect type

Concrete "how do I verify this side effect" snippets. Use as a template,
not as a constraint; adapt the variable names to your situation.

## SQL INSERT / UPDATE (SQLite)

### Python

```python
# Write
conn.execute("UPDATE drafts SET queue_status = ? WHERE id = ?",
             ("queued", draft_id))
conn.commit()

# Verify — the assertion runs BEFORE the success log
row = conn.execute(
    "SELECT id, queue_status FROM drafts WHERE id = ?",
    (draft_id,)
).fetchone()
if row is None:
    raise RuntimeError(f"draft {draft_id} missing after commit")
if row[1] != "queued":
    raise RuntimeError(f"draft {draft_id} queue_status = {row[1]}, expected queued")
print(f"✅ queued {draft_id}")
```

### Bash (one-liner)

```bash
python3 -c "
import sqlite3, sys
c = sqlite3.connect('$DB_PATH')
r = c.execute('SELECT queue_status FROM drafts WHERE id = ?', ('$DRAFT_ID',)).fetchone()
if r is None or r[0] != 'queued':
    sys.exit(1)
" && echo "✅ queued $DRAFT_ID" || { echo "❌ queue verification failed" >&2; exit 1; }
```

## File write

```bash
TARGET="$HOME/news_radar/data/01_harvest/news_radar.db"
# ... do the write ...
if [[ ! -s "$TARGET" ]]; then
    echo "❌ $TARGET missing or empty" >&2
    exit 1
fi
LOCAL_SHA="$(shasum -a 256 "$TARGET" | cut -d' ' -f1)"
echo "✅ wrote $TARGET  sha256=${LOCAL_SHA:0:16}…"
```

If reproducibility matters (you're copying a file and want to know the
copy is byte-identical), capture the source sha before the write and
compare after.

## State branch push

Always go through `scripts/push_state.sh`:

```bash
bash scripts/push_state.sh --expect-draft "$DRAFT_ID"
# Exit 0 = push AND re-fetch-and-sha256 AND SQL assert all passed
# Exit 1 = post-condition failed; don't trust the push
```

If you can't use `push_state.sh` (e.g. inside GitHub Actions), inline it:

```bash
# after the push:
TMPDIR=$(mktemp -d)
(
    cd "$TMPDIR"
    git init -q
    git fetch --depth=1 "$REPO_URL" state --quiet
    git show FETCH_HEAD:data/01_harvest/news_radar.db > fetched.db
)
REMOTE_SHA="$(shasum -a 256 "$TMPDIR/fetched.db" | cut -d' ' -f1)"
[[ "$REMOTE_SHA" == "$LOCAL_SHA" ]] || { echo "❌ sha mismatch" >&2; exit 1; }
echo "✅ state branch updated, remote sha=${REMOTE_SHA:0:16}…"
```

## Subprocess call

Don't trust log output. Check exit code + parse the output you care about.

```python
import subprocess, json
result = subprocess.run(
    ["claude", "--help"],
    capture_output=True, text=True, timeout=30
)
if result.returncode != 0:
    raise RuntimeError(f"claude --help exited {result.returncode}: {result.stderr}")
if "Usage:" not in result.stdout:
    raise RuntimeError(f"claude --help output looks wrong: {result.stdout[:200]}")
print("✅ claude CLI reachable")
```

## HTTP POST (Meta Graph API etc.)

```python
resp = requests.post(url, json=payload, timeout=30)
if not (200 <= resp.status_code < 300):
    raise RuntimeError(f"{url} returned {resp.status_code}: {resp.text[:300]}")
try:
    body = resp.json()
except ValueError:
    raise RuntimeError(f"{url} returned non-JSON: {resp.text[:300]}")
if "id" not in body:
    raise RuntimeError(f"{url} response missing id: {body}")
print(f"✅ posted to {url}, id={body['id']}")
```

## launchctl load

```bash
launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST" || { echo "❌ load failed" >&2; exit 1; }

# Verify scheduled
if ! launchctl list | grep -q "com.hsin.news-radar.compose"; then
    echo "❌ agent not listed after load" >&2
    exit 1
fi
echo "✅ agent loaded and scheduled"
```

## Pattern the assertions share

Every one of the above:

1. Runs a **read** after the **write**.
2. Checks specific shape / value, not just "something came back".
3. **Errors loudly** if the check fails (exit 1, raise, assert — not a
   log line and a continue).
4. Only prints ✅ when the check passes.

The shape is non-negotiable. Log lines that don't follow it are what
caused the bugs this skill exists to prevent.
