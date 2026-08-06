# Substack editorial refactor

## 1. Goal / non-goals

- `PROVEN`：Substack 只建立草稿，發布仍由 owner 在 Substack 內決定。
- `PROVEN`：目前 runtime 把一份約 480 行的 soul、voice anchor，以及 company mode 的四份 Manny framework 全部注入寫稿 prompt；內部存在互相衝突的結尾、場景與判斷規則。
- `PROVEN`：2026-08-05 已移除內文生圖，但 writer prompt 仍要求輸出 2–3 組內文視覺標記與生圖 prompt。
- `ASSUMED`：經營目標是穩定建立讀者信任，不是最大化每天的草稿數量。
- `UNKNOWN`：Mac 上實際載入哪些 launchd agent；repo 內容不能證明 live 排程。
- `BLOCKED`：本機 Windows 無法證明 Mac launchd 與 Substack cookie/API 路徑。

本次只重構 Substack 寫手、品質檢查與 editorial cadence。Meta、submission/D1 狀態機、自動發布權限不在範圍內。

## 2. Options / decision

1. 只縮短既有 soul：改動小，但 Daily/Weekly 仍互相妥協，排程重疊不會消失。
2. **共同人格 + Daily/Weekly brief + deterministic audit（採用）**：寫手只接收當次真正需要的規則；程式負責可機械判斷的格式與語言問題。
3. 完全 agentic research writer：研究彈性高，但查證、寫稿與外部工具混成不可重現的長任務，目前不採用。

## 3. Contracts / invariants

- Common voice：先說人話，再補必要術語；一段一件事；讓證據、推論與未知自然分開；不賣弄、不訓話、不假裝全知。
- Human/AI boundary：AI 整理素材、交叉證據並提出可檢驗的觀點；owner 判斷論點是否成立、是否值得發布。擬人語氣不得變成虛構經驗或假裝採訪。
- Daily：一篇只處理一個變化與一個判斷，1400–2200 個中文字，6–8 分鐘；2–4 個具名證據錨點；結尾是一個與本文決策相關的具體回信問題。
- Weekly：2800–4200 個中文字，12–16 分鐘；至少兩種來源視角、最強反方、證據缺口與可觀測的後續訊號；company 文可使用公司拆解 lens，但不整包載入外部 skills。
- Deep source bundle、podcast、company 預設走 Weekly；morning/evening 預設走 Daily；CLI 可顯式覆寫。
- Writer 不產生 inline image marker、搜尋指令、chart prompt、footer 或訂閱 CTA。封面仍由既有 deterministic cover path 負責。
- Writer schema 僅有 `title / subtitle / body_markdown`；模型 provenance 只留在 metadata。
- `Article_Substack.md` 與遠端 `from_markdown()` 前都套用 reader-ready sanitizer；發現殘留製程標記即 fail closed。
- Reader-ready 草稿不含產文路線、內文圖片位置、Path B/C、生圖 prompt、封面 prompt 或 editor 註解。已產生並上傳的 `cover.png` 保留。
- Fast submission worker 必須先 fast-forward `origin/main`；無法同步時不使用舊 writer 產稿。
- 排程目標是每天 12:00 啟動一個 batch，依序完成兩篇不同 Podcast 延伸文；候選僅限最近 7 天。週日 09:00 在同一工作內先選公司再完成一篇 Weekly；owner submission immediate/hourly lanes 保持不變。
- 所有 scheduled editorial worker 必須共用 Release lease、pull/push 與 remote-draft evidence contract。

## 4. Verification matrix

| Requirement | Executable evidence | Remaining limitation |
|---|---|---|
| Profile routing | unit test for mode/bundle/override | does not prove source quality |
| Prompt separation | unit assertions for required/forbidden content | does not judge prose beauty |
| Profile word ranges | audit unit tests | character count is not editorial quality |
| No obsolete image instructions | prompt/schema/file-reference tests | cover visual quality remains separate |
| Reader-ready final payload | file-writer, pasted-draft, and API-boundary regression tests | remote private-draft readback still requires the Mac runtime |
| One noon two-draft batch + one weekly pick-and-compose schedule | static worker/plist/installer contract tests | macOS launchd not executed on Windows; completion before 13:00 needs a timed canary |
| Existing behavior preserved | full unit suite + compile/diff checks | no remote Substack draft without Mac credentials |

## 5. Implementation slices

1. Add RED tests for profile routing, prompt boundaries, audit ranges, and schedule topology.
2. Replace runtime prompt assembly with concise common/Daily/Weekly briefs.
3. Remove obsolete chart/inline-image metadata and Manny runtime injection.
4. Add the lease-backed editorial worker and converge installed editorial schedules.
5. Update operator docs; run focused and full regression checks.

## 8. 2026-08-06 reader-ready correction

The earlier refactor shortened the writer prompt but did not control the final
payload. That was insufficient: queued/legacy output could still carry two
inline image instruction blocks, a cover-generation prompt, provenance, and an
editor-only deletion note into a real Substack draft.

The corrected invariant is simpler: a draft body is a reader product, not a
manufacturing worksheet. Cover art is a separate deterministic artifact. The
writer spends tokens only on title, subtitle, and article body; provenance is
operational metadata; deterministic sanitization runs before writing and again
immediately before the remote draft mutation.

Local closure evidence:

- Focused reader-ready/schema/API/scheduler regressions: `18 passed`.
- Full repository suite: `798 passed`.
- `python -X utf8 -m compileall -q substack_radar src scripts`: pass.
- `git diff --check`: pass.
- Both owner-supplied contaminated draft samples pass deterministic cleanup
  without any forbidden production marker; this is local payload evidence, not
  a remote private-draft readback.

## 6. Risks / owner gates

- A shorter prompt gives the current model more judgment; poor source material can still produce a weak article.
- 兩篇 Podcast 在同一 lease 與 local lock 內依序執行，不會互相搶 state；代價是任一篇過慢都會拉長整個 batch，因此 13:00 前完成仍需 Mac 實機計時 canary。
- Repo schedule changes do not alter the Mac until the tracked scripts/plists are copied and loaded by the owner.
- A visible remote draft plus canonical IDs is the production proof gate; local tests are not that proof.

## 7. Closure evidence / decision changes

- `PROVEN`：the initial editorial refactor had causal RED failures for the absent profile, prompt, removal, and schedule behavior. The final schedule-convergence RED command `python -X utf8 -m pytest tests/unit/test_substack_editorial_contract.py tests/unit/test_substack_source_routing.py -q --basetemp <appdata-temp>` failed 2 tests (the single noon batch plist was absent; an 8-day-old interview was still selectable), then passed 12/12 after the production change.
- `PROVEN`：all local tests pass with a unique AppData temp root: `python -X utf8 -m pytest tests -q --basetemp <appdata-temp>` → 791 passed.
- `PROVEN`：`python -X utf8 -m compileall -q substack_radar scripts` and `git diff --check` pass.
- `PROVEN`：the two active editorial plists parse with Python `plistlib` and expose the expected labels, schedules, and worker profiles.
- `PROVEN`：compared with the baseline files at `31253af`, runtime editorial guidance fell from 18,910 to 856 characters for Daily (95.5% reduction), and from 28,802 to 965 for Weekly/company (96.6% reduction). This measures injected guidance files, not source material or the full API request.
- Decision change：owner prefers ideas extended from top international Podcast conversations. The scheduled 08:00 Daily was therefore replaced by one 12:00 two-draft Podcast batch using only the last 7 days of candidates. Company selection now runs inside the Sunday 09:00 compose job. The public cadence promise remains「每天兩篇對談延伸 · 每週一篇公司深拆」.
- `BLOCKED`：this Windows host has no Bash runtime, so `bash -n`, macOS `plutil`, launchd load/readback, and a remote Substack draft canary remain unverified.
