# Windows 撰稿主機：永久退役（2026-08-08）

**決定：不復工。** 這不是暫時停機，是撤除。owner 於 2026-08-08 決定不再把
撰稿任務交給公司的 Windows 電腦，全部由 Mac 承接。

## 現況

`config/windows_writer_mode` = `paused`，且**不應被改回 `live`**。

閘門有兩個消費端，兩條線都擋住：

| 檔案 | 擋什麼 |
|---|---|
| `scripts/windows_substack_editorial_worker.py` | 寫稿。讀到非 `live` 就 return 0，不取租約、不組稿 |
| `scripts/windows_substack_browser_handoff.py` | 推草稿。讀到非 `live` 就 raise `HandoffError` |

兩者都在 `git fetch` + `git merge --ff-only origin/main` **之後**讀值，
所以 push 就會生效，不需要登入那台機器——這也是當初選 repo 檔案而非
環境變數的原因（owner 當時不在機器旁）。

`WINDOWS_WRITER_MODE` 環境變數優先於此檔，僅供測試與現場緊急處置使用。
`tests/unit/test_windows_substack_browser_handoff.py` 用 autouse fixture
設 `live`，所以營運狀態不會讓測試失敗。

## 為什麼撤除

- **憑證面**：Windows 沒有 Substack credential，而且被釘死在
  `SUBSTACK_AUTO_DRAFT=0`，就算補上 cookie 也不會建立遠端草稿。
  cookie 內的 `cf_clearance` 由 Cloudflare 綁 IP 與瀏覽器指紋，
  不能從 Mac 複製，必須在該機器自行登入並自行輪替。
- **控制面**：那是公司的電腦。一台會自動對外發布內容、卻只能靠實體接觸
  才能叫停的機器，本身就是風險。閘門是補救，不是解法。
- **品質面**：兩邊模型鏈不同（Windows 是 `codex_cli,claude_cli`，
  Mac 走 `claude_cli → agy → litellm`），同一個 pipeline 產出的文章
  風格與品質會不一致。

## Mac 這邊怎麼接手

排程 plist 都在 repo，尚未載入。要啟用：

```bash
bash scripts/install_substack_daily_agents.sh
```

會掛上兩個 agent：

- `com.hsin.news-radar.substack-podcast-noon` — 每日 12:00，兩篇 podcast 延伸
- `com.hsin.news-radar.company-compose` — 週日 09:00，一篇公司拆解

撰稿模型順序見 `.env` 的 `SUBSTACK_COMPOSER_BACKEND`：
`claude_cli(opus+high) → antigravity_cli(AGY_MODEL_CHAIN) → litellm(開源)`。

## 給接手的 agent

看到 `windows_writer_mode = paused` 不要「順手修好它」。這是刻意的終局狀態，
不是待辦事項。要復工必須有 owner 明確指示，並且先處理上面「為什麼撤除」
列的三件事。

相關紀錄：`MAC_SUBSTACK_V2_CUTOVER_HANDOFF.md`（原始交接）、
`MAC_SUBSTACK_V2_CUTOVER_REPORT.md`（執行回報）。
