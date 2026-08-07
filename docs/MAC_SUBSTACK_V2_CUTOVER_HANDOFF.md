# Mac 停止 Substack 寫手、Windows 接管交接

> 給 Mac 端執行 Agent：這次不是把 Mac 升級成新版寫手，而是停止 Mac 的 Substack 選題、排程與 AI 寫稿。Windows 是唯一 writer。

## Owner 目標

- Mac 不再執行 Substack 選題、Podcast／公司排程或任何 AI compose。
- 不影響 Meta 三平台 worker。
- 不修改或重新授權 GitHub token、Substack cookie、Keychain、瀏覽器登入與 `.env`。
- 舊本機污染稿採可復原 quarantine，不永久刪除。
- 已存在的污染遠端 draft 標記 `DO NOT PUBLISH`，不自動刪除。
- Windows 每日 12:00 依序寫兩篇最近 7 天 Podcast 延伸文；週日 09:00 先選公司再寫一篇。

## 證據邊界

| 狀態 | 內容 |
|---|---|
| `PROVEN` | Windows repo 已具備 `scripts/windows_substack_editorial_worker.py`，模型順序為 `gpt-latest` → `claude-latest`。 |
| `PROVEN` | Windows 寫手強制 `SUBSTACK_AUTO_DRAFT=0`；只產生 local/OneDrive artifact 與 canonical state。 |
| `PROVEN` | 新稿保留真實產文路線／模型、可點擊來源、訂閱 CTA、瑞瑞／達達 `cover.png`；移除所有生圖與封面 prompt。 |
| `UNKNOWN` | Mac 現在實際載入的 launchd labels、compose PID 與本機污染稿數量。 |
| `BLOCKED` | Windows 尚無 Substack credential，不能宣稱已建立遠端 draft；Windows 到 Mac 的 SSH readback 也尚未成功。 |

## Phase 0：保護 owner 狀態

```bash
set -euo pipefail
REPO="$HOME/news_radar"
cd "$REPO"

git status --short --branch
git remote get-url origin
[ -z "$(git status --porcelain)" ] || {
  echo 'worktree is dirty; preserve owner changes and stop'
  exit 11
}
```

禁止執行：

- 不讀出或貼出 `.env`、cookie、token 的值。
- 不執行 `gh auth logout/login`。
- 不清除 Keychain、瀏覽器資料或 Substack session。
- 不停止 Meta workers。
- 不刪除遠端 Substack drafts。

## Phase 1：留下停止前證據

```bash
CUTOVER_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CUTOVER_DIR="$HOME/news-radar-cutover/$CUTOVER_STAMP"
mkdir -p "$CUTOVER_DIR"

git rev-parse HEAD | tee "$CUTOVER_DIR/before-head.txt"
launchctl list | grep -Ei 'news.?radar.*(substack|company)' \
  | tee "$CUTOVER_DIR/before-substack-launchctl.txt" || true
pgrep -fl 'substack_radar/compose.py|substack_editorial_worker' \
  | tee "$CUTOVER_DIR/before-substack-processes.txt" || true
```

若發現 Substack compose PID，先以 `ps` 與 `lsof -a -p <PID> -d cwd -Fn` 確認屬於本 repo；只對已確認的 PID 送 `TERM`。禁止使用 `pkill python`。

## Phase 2：卸載 Mac 的 Substack 寫稿排程

```bash
cd "$REPO"
bash scripts/install_substack_daily_agents.sh --uninstall
```

再逐項 readback。至少包含下列 labels，並把 Phase 1 發現的其他 Substack compose label 一併加入：

```bash
labels=(
  com.hsin.news-radar.substack-podcast-noon
  com.hsin.news-radar.company-compose
  com.hsin.news-radar.substack-podcast-noon-1
  com.hsin.news-radar.substack-podcast-noon-2
  com.hsin.news-radar.company-pick
  com.hsin.news-radar.substack-daily
  com.hsin.news-radar.substack-morning
  com.hsin.news-radar.substack-evening
  com.newsradar.substack_morning
  com.newsradar.substack_evening
  com.newsradar.substack_podcast
  com.newsradar.substack_podcast2
  com.newsradar.substack_podcast3
  com.newsradar.company_pick
  com.newsradar.substack_company
)

found=0
for label in "${labels[@]}"; do
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    echo "STILL LOADED: $label"
    found=1
  fi
  if [ -e "$HOME/Library/LaunchAgents/$label.plist" ]; then
    echo "PLIST STILL EXISTS: $label.plist"
    found=1
  fi
done
[ "$found" -eq 0 ] || exit 30
echo 'Mac Substack editorial launchd state: CLEAN'
```

如果另有會呼叫 `substack_radar/compose.py` 或 LLM 的 Substack fast/hourly agent，也必須保持 unloaded；但不要動到純 Meta worker。若無法判斷，列為 `UNKNOWN` 並停止，不要猜。

## Phase 3：可復原隔離舊本機污染稿

先建立 manifest，不立即搬動：

```bash
ACTIVE="$REPO/data/substack_drafts"
QUARANTINE="$CUTOVER_DIR/quarantine/substack_drafts"
MANIFEST="$CUTOVER_DIR/legacy-local-drafts.txt"
PATTERN='視覺位置|Path B|Path C|生圖[[:space:]]*[Pp]rompt|封面圖[[:space:]]*[Pp]rompt|substack-editor|cover_image_prompt|發布前刪'

: > "$MANIFEST"
if [ -d "$ACTIVE" ]; then
  find "$ACTIVE" -type f -name Article_Substack.md -print0 \
    | while IFS= read -r -d '' article; do
        grep -Eq "$PATTERN" "$article" && dirname "$article" || true
      done | sort -u | tee "$MANIFEST"
fi
```

逐項確認後，才移到 quarantine：

```bash
mkdir -p "$QUARANTINE"
while IFS= read -r draft_dir; do
  [ -n "$draft_dir" ] || continue
  resolved="$(cd "$draft_dir" && pwd -P)"
  case "$resolved" in "$ACTIVE"/*) ;; *) echo "REFUSE: $resolved"; exit 40;; esac
  rel="${resolved#"$ACTIVE"/}"
  dest="$QUARANTINE/$rel"
  [ ! -e "$dest" ] || { echo "destination exists: $dest"; exit 41; }
  mkdir -p "$(dirname "$dest")"
  mv "$resolved" "$dest"
done < "$MANIFEST"
```

不得移除去重、receipt 或 owner submission 狀態：

- `.substack_used.json`
- `.substack_remote_receipts.json`
- `.substack_submissions.json`
- `.company_done`
- canonical SQLite state

## Phase 4：遠端污染稿只標記、不刪除

目前至少列為 `DO NOT PUBLISH`：

- `210173280` 模型越聰明算力反而越貴？
- `210170756` 兆美元 AI 巨頭真的還會出現嗎？
- `210149380` 暴跌 18% 掀開的成長遮羞布
- `210047942` 開源模型的千億估值遮羞布
- `210085118` 央行暗中啃光了誰的未來？

本交接不授權刪除。若 owner 日後要刪，另列 ID 並在 Substack UI 人工確認。

## Phase 5：完成 readback

```bash
launchctl list | grep -Ei 'news.?radar.*(substack|company)' \
  | tee "$CUTOVER_DIR/after-substack-launchctl.txt" || true
pgrep -fl 'substack_radar/compose.py|substack_editorial_worker' \
  | tee "$CUTOVER_DIR/after-substack-processes.txt" || true
git status --short --branch | tee "$CUTOVER_DIR/final-git-status.txt"
```

完成標準：Mac 沒有 Substack 選題／AI compose label 或 PID；Meta workers 未被修改；credential/token 仍保留；quarantine manifest 存在。這份交接不建立 canary draft。

## 回報格式

```text
Done
- Mac SHA:
- unloaded Substack labels:
- remaining Substack compose PID: none / list
- quarantined local draft count:
- Meta workers changed: no
- GitHub/Substack authorization changed: no

Blocked
- exact non-secret error:
- unresolved label or PID:

Owner watch
- DO NOT PUBLISH remote draft IDs still present:
- dirty owner files preserved:
- transport credential retained but not used:
```
