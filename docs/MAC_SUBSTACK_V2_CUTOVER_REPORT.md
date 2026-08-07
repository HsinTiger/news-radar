# Mac Substack Cutover — 執行回報（2026-08-08）

依 `MAC_SUBSTACK_V2_CUTOVER_HANDOFF.md` 執行。含一項 owner 授權的例外，見最後一節。

```text
Done
- Mac SHA: 48e0d8f
- unloaded Substack labels:
    com.hsin.news-radar.substack-morning
    com.hsin.news-radar.substack-evening
    com.hsin.news-radar.substack-podcast1
    com.hsin.news-radar.substack-podcast2
    com.hsin.news-radar.substack-podcast3
    com.hsin.news-radar.company-pick
    com.hsin.news-radar.company-compose
    com.hsin.news-radar.substack-fast      ← 見「判斷一」
- remaining Substack compose PID: none
- quarantined local draft count: 353（active 由 359 降為 6）
- Meta workers changed: no
- GitHub/Substack authorization changed: no

Blocked
- 無

Owner watch
- DO NOT PUBLISH remote draft IDs still present:
    210173280 / 210170756 / 210149380 / 210047942 / 210085118
    （未刪除，依交接不授權）
- dirty owner files preserved: 無（Phase 0 時 worktree 已 clean）
- transport credential retained but not used: GitHub token、Substack cookie 均未讀出、未變更
```

證據留存於 `~/news-radar-cutover/<UTC 時間戳>/`：`before/after-substack-launchctl.txt`、
`before/after-substack-processes.txt`、`legacy-local-drafts.txt`、`quarantine/`。

## 判斷一：多卸載了 `com.hsin.news-radar.substack-fast`

它不在 `install_substack_daily_agents.sh` 的 `AGENTS` 或 `LEGACY_AGENTS` 清單裡，
但交接文件第 106 行要求「另有會呼叫 `substack_radar/compose.py` 或 LLM 的
Substack fast/hourly agent，也必須保持 unloaded」。

已驗證它符合該條件：

```
~/bin/news_radar_substack_fast.sh:70  → scripts/drain_substack.py --only-current-control
scripts/drain_substack.py:36          → COMPOSE = substack_radar/compose.py
```

卸載時它正在執行（PID 82548）。依 Phase 1 規定先以 `lsof -a -p 82548 -d cwd -Fn`
確認工作目錄為 `/Users/hsin/news_radar` 才處理，未使用 `pkill`。plist 保留未刪除，
可隨時 `launchctl bootstrap` 復原。

**這與 `install_substack_daily_agents.sh` 結尾印出的
「owner submission immediate/hourly workers are unchanged」相衝突。**
本次以交接文件為準（較新且較明確）。若 owner 要讓投稿路徑繼續在 Mac 運作，
復原成本是一行指令——但那條路徑會恢復在 Mac 上呼叫 AI compose。

## 判斷二：隔離比例 353/359 是預期值，不是誤判

污染 pattern 含「封面圖 Prompt」，而舊管線對**每一篇**都會附加該區塊
（`append_cover_prompt_block`），因此幾乎全部歷史稿件命中。已逐項確認路徑
落在 `data/substack_drafts/` 內才搬移，全部為 `mv`，可完整復原。

交接文件要求保留的狀態檔均未被移動，仍在原位：
`.substack_used.json`、`.substack_submissions.json`、`.company_done` 等 10 項。

## ⚠️ Owner 授權的例外：cutover 後在 Mac 執行了一次 AI compose

交接文件寫「Mac 不再執行…任何 AI compose」。**但 owner 於 2026-08-08 明確要求
立即產出一篇 MARA Holdings 財報分析並送進 Substack**，且說明為急件。

之所以只能由 Mac 執行，是交接文件自己記載的兩個事實：

- `BLOCKED`：Windows 尚無 Substack credential。
- `PROVEN`：Windows 寫手強制 `SUBSTACK_AUTO_DRAFT=0`，只產 local/OneDrive artifact。

所以**即使 Windows 拿到 cookie，現階段也不會建立遠端 draft**——這是設計選擇，
不只是憑證缺口。Mac 是目前唯一能把草稿送進 Substack 的機器。

執行方式與邊界：

- 走 repo 既有管線 `compose.py company --ticker MARA`，未繞過任何既有校驗。
- **手動單次觸發，未重新載入任何 launchd agent**；cutover 後的排程狀態不變。
- 完成後 Mac 仍維持「無 Substack 排程」狀態。

給接手 agent：若你在 `data/substack_drafts/` 看到一篇 cutover 之後產生的
MARA 稿，那是這次授權的例外，不是交接失敗，也不代表 Mac 排程已復活。
用 `launchctl list | grep -Ei 'news.?radar.*(substack|company)'` 驗證即可——
應為空。

## 建議的下一步（未執行）

要讓 Windows 真正接手遠端草稿，需要兩件事，缺一不可：

1. **在 Windows 上自行登入 Substack 取得 cookie。** 不可從 Mac 複製——
   cookie 內的 `cf_clearance` 由 Cloudflare 綁 IP 與瀏覽器指紋發出，換機即失效。
2. **調整 `SUBSTACK_AUTO_DRAFT=0`。** 這是 Windows 寫手目前不推遠端草稿的直接原因。

Substack 無公開的建立草稿 API，`python-substack` 是以 session cookie 冒充瀏覽器操作
（`push_pasted_draft.py:114`），因此憑證本質是「登入狀態」——易取得但會過期，
需要輪替機制，這點在規劃 Windows 接手時應一併考慮。
