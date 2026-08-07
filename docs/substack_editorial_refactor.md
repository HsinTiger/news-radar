# Substack editorial refactor

開源方法的候選、固定版本、採用與拒絕理由另見
[`substack_open_source_editorial_methods.md`](substack_open_source_editorial_methods.md)。

## 1. Goal / non-goals

- `PROVEN`：Substack 只建立草稿，發布仍由 owner 在 Substack 內決定。
- `PROVEN`：2026-08-06 已移除舊 soul/Manny 堆疊、內文生圖與封面 prompt，並在檔案與遠端 API 邊界做 reader-ready 清理。
- `PROVEN`：2026-08-07 前的深度文仍只有一次寫稿呼叫；Podcast 搜尋最多 5 筆連結，但只讀 1–2 篇正文，搜尋失敗也會繼續出稿。這不能證明完成延伸調研。
- `PROVEN`：新 runtime 將 Podcast/公司文分成主來源消化、外部證據包、最終寫作三段；少於 5 個已讀取來源時 fail closed。
- `ASSUMED`：經營目標是穩定建立讀者信任，不是最大化每天的草稿數量。
- `UNKNOWN`：這台 Windows 排程實際載入的 commit 與下一次 12:00 timing；repo 內容不能單獨證明 live 排程。
- `BLOCKED`：本次寫手重構未產生一篇真實遠端 Substack 草稿 ID，因此不能宣稱生產发布鏈已驗證。

本次只重構 Substack 寫手、品質檢查與 editorial cadence。Meta、submission/D1 狀態機、自動發布權限不在範圍內。

## 2. Options / decision

1. 只加長單一 prompt：改動最小，但無法證明模型先理解 Podcast，也無法證明 5–10 個延伸來源真的被讀取。
2. 預搜連結後一次寫稿：比現況稍好，但搜尋問題仍由原始標題決定，不是由對談中的證據缺口決定。
3. **結構化兩階段寫手（採用）**：第一次 LLM 只消化主來源並定義研究問題；程式搜尋、讀取、去重與驗證 5–10 源；第二次 LLM 只從結構化摘要與證據包寫文。搜尋與寫作仍分開，所以可追蹤、可失敗、可重現。

## 3. Contracts / invariants

- Common voice：先說人話，再補必要術語；一段一件事；讓證據、推論與未知自然分開；不賣弄、不訓話、不假裝全知。
- First-person：以「我」表達消化後的理解、取捨與判斷；不得虛構親臨現場、採訪、見聞或情緒。
- Human/AI boundary：AI 整理素材、交叉證據並提出可檢驗的觀點；owner 判斷論點是否成立、是否值得發布。擬人語氣不得變成虛構經驗或假裝採訪。
- Daily：一篇只處理一個變化與一個判斷，1800–2800 個中文字，7–10 分鐘；2–4 個具名證據錨點。
- Podcast：4200–6500 個中文字，17–25 分鐘；先呈現引人入勝的對談摘要、追問與觀點，再以 5–10 源延伸為調查、論證或自我成長文。
- Company：3800–6000 個中文字，15–23 分鐘；財報事實先與公司敘事分開，再以 5–10 源檢驗賺錢機制、優勢、財務支撐與最強反方。
- Research evidence：主來源不計入 5–10 個延伸源；只有真正取得可讀正文的去重 URL 才計數，搜尋結果摘要不算證據。
- Cognitive load：5–10 源是作者研究投入，不是正文清單。每段須通過資訊價值閘門：新增證據、必要因果、最強反方、必要定義或讀者後果至少一項；同義來源合併，刪除後不影響論證的段落直接刪除。一節只推進一個子問題，一段一件事；三個以上專有名詞無法避免時，才加最多五條的註解段落。
- Deep source bundle、podcast、company 預設走 Weekly；morning/evening 預設走 Daily；CLI 可顯式覆寫。
- Writer 不產生 inline image marker、搜尋指令、chart prompt、footer 或訂閱 CTA。封面仍由既有 deterministic cover path 負責。
- Writer schema 僅有 `title / subtitle / body_markdown`；實際模型 provenance 由 pipeline 根據成功回應寫入，模型不能自行宣稱。
- `Article_Substack.md` 與遠端 `from_markdown()` 前都套用 reader-ready sanitizer；發現殘留製程標記即 fail closed。
- Reader-ready 草稿保留四項讀者價值：實際產文路線／模型、可點擊取材來源、底部訂閱 CTA，以及獨立的瑞瑞／達達 `cover.png`。
- Reader-ready 草稿不含「發布前刪此行」、內文圖片位置、Path B/C、搜尋詞、生圖 prompt、封面 prompt 或 editor 註解。
- Fast submission worker 必須先 fast-forward `origin/main`；無法同步時不使用舊 writer 產稿。
- 排程目標是每天 12:00 啟動一個 batch，依序完成兩篇不同 Podcast 延伸文；候選僅限最近 7 天。週日 09:00 在同一工作內先選公司再完成一篇 Weekly；owner submission immediate/hourly lanes 保持不變。
- 每次 Podcast 選稿前，超過 7 天、沒有 Substack 歷史證據、也未被社群 draft 引用的舊候選會移入 canonical DB quarantine 後退出 active `news_items`；歷史稿與跨平台引用不得刪除。
- 所有 scheduled editorial worker 必須共用 Release lease、pull/push 與 remote-draft evidence contract。

## 4. Verification matrix

| Requirement | Executable evidence | Remaining limitation |
|---|---|---|
| Profile routing | unit test for mode/bundle/override | does not prove source quality |
| Prompt separation | unit assertions for required/forbidden content | does not judge prose beauty |
| Profile word ranges | audit unit tests | character count is not editorial quality |
| Primary-source digestion precedes research | research-brief prompt/schema tests | a schema-valid digest still needs owner editorial judgment |
| 5–10 readable extension sources | dedupe, page-read, snippet-rejection and fail-closed tests | URL availability does not prove every source is high quality |
| Investigation/argument/self-growth structures | parameterized final-prompt tests | prompt contract cannot score beauty or originality |
| First-person without fabricated experience | prompt + deterministic author-voice warning | warning cannot distinguish every quoted 「我」 |
| No obsolete image instructions | prompt/schema/file-reference tests | cover visual quality remains separate |
| Reader-ready final payload | file-writer, pasted-draft, and API-boundary regression tests | remote private-draft readback still requires the Mac runtime |
| One noon two-draft batch + one weekly pick-and-compose schedule | static worker/plist/installer contract tests | macOS launchd not executed on Windows; completion before 13:00 needs a timed canary |
| Existing behavior preserved | full unit suite + compile/diff checks | no remote Substack draft without Mac credentials |

## 5. Implementation slices

1. Add RED tests for source digestion, 5–10-source enforcement, source reading, article forms and cognitive-load guidance.
2. Add `EditorialResearchBrief` as the stage-one structured contract.
3. Add deterministic URL search/read/dedupe with a fail-closed evidence gate.
4. Route only the digest plus validated evidence pack into the final first-person writer.
5. Add Podcast/company-specific briefs, source-ledger presentation and dashboard metadata.
6. Update operator docs; run focused and full regression checks.

## 5A. Length and cognitive-load rationale (2026-08-07)

- Substack does not publish one official universal ideal word count. A third-party
  analysis of 94,391 Substack posts reports 1,000–2,000 English words as the
  workhorse range, with raw reactions rising through roughly 3,500 words and
  2,000+ word posts averaging more reactions than sub-500-word posts. This is
  useful directional evidence, not an editorial law: reactions are not reading
  completion or retention, and the authors do not establish causal control.
  Source: [I Analyzed 94,391 Substack Posts](https://writebuildscale.substack.com/p/i-analyzed-94391-substack-posts-heres).
- Nielsen Norman Group's cognitive-load guidance supports reducing what the user
  must hold in working memory and using progressive disclosure for dense material.
  We apply that editorially by giving one section one question, defining jargon
  at first use, and moving the complete source ledger after the article rather
  than opening with ten links. Sources:
  [Minimize Cognitive Load](https://www.nngroup.com/articles/minimize-cognitive-load/),
  [4 Principles to Reduce Cognitive Load](https://www.nngroup.com/articles/4-principles-reduce-cognitive-load/).
- Decision: use Chinese-character ranges rather than pretending English word
  counts convert directly. Length is a guardrail. A paragraph, section, or whole
  article may grow only when it adds evidence, a causal step, a real countercase,
  a necessary definition, or a falsifiable implication.

## 8. 2026-08-06 reader-ready correction

The earlier refactor shortened the writer prompt but did not control the final
payload. That was insufficient: queued/legacy output could still carry two
inline image instruction blocks, a cover-generation prompt, provenance, and an
editor-only deletion note into a real Substack draft.

The corrected invariant is simpler: a draft body is a reader product, not a
manufacturing worksheet. Cover art is a separate deterministic artifact. The
writer spends tokens only on title, subtitle, and article body; the pipeline
adds truthful route/model provenance, public sources, and the subscription CTA.
Deterministic sanitization runs before writing and again immediately before the
remote draft mutation.

Local closure evidence:

- Focused reader-ready/schema/API/scheduler regressions: `18 passed`.
- Full repository suite: `798 passed`.
- `python -X utf8 -m compileall -q substack_radar src scripts`: pass.
- `git diff --check`: pass.
- Both owner-supplied contaminated draft samples pass deterministic cleanup
  without any forbidden production marker; this is local payload evidence, not
  a remote private-draft readback.

## 6. Risks / owner gates

- Two LLM calls increase elapsed time and tokens. The noon two-article batch needs a Windows timing canary before claiming both are visible by 13:00.
- Search availability and readable-page extraction can fail. That is an explicit draft failure, not permission to fall back to a thin article.
- Five readable URLs are a minimum evidence-volume gate, not proof that every source is authoritative or that the final argument is correct; owner review remains required.
- 兩篇 Podcast 在同一 lease 與 local lock 內依序執行，不會互相搶 state；代價是任一篇過慢都會拉長整個 batch，因此 13:00 前完成仍需這台 Windows 寫作主機的實機計時 canary。
- Repo schedule changes do not prove the Windows scheduled task has loaded the new commit; require task/runtime readback.
- A visible remote draft plus canonical IDs is the production proof gate; local tests are not that proof.

## 7. Closure evidence / decision changes

- `PROVEN`：the initial editorial refactor had causal RED failures for the absent profile, prompt, removal, and schedule behavior. The final schedule-convergence RED command `python -X utf8 -m pytest tests/unit/test_substack_editorial_contract.py tests/unit/test_substack_source_routing.py -q --basetemp <appdata-temp>` failed 2 tests (the single noon batch plist was absent; an 8-day-old interview was still selectable), then passed 12/12 after the production change.
- `PROVEN`：all local tests pass with a unique AppData temp root: `python -X utf8 -m pytest tests -q --basetemp <appdata-temp>` → 791 passed.
- `PROVEN`：`python -X utf8 -m compileall -q substack_radar scripts` and `git diff --check` pass.
- `PROVEN`：the two active editorial plists parse with Python `plistlib` and expose the expected labels, schedules, and worker profiles.
- `PROVEN`：compared with the baseline files at `31253af`, runtime editorial guidance fell from 18,910 to 856 characters for Daily (95.5% reduction), and from 28,802 to 965 for Weekly/company (96.6% reduction). This measures injected guidance files, not source material or the full API request.
- Decision change：owner prefers ideas extended from top international Podcast conversations. The scheduled 08:00 Daily was therefore replaced by one 12:00 two-draft Podcast batch using only the last 7 days of candidates. Company selection now runs inside the Sunday 09:00 compose job. The public cadence promise remains「每天兩篇對談延伸 · 每週一篇公司深拆」.
- `BLOCKED`：this Windows host has no Bash runtime, so `bash -n`, macOS `plutil`, launchd load/readback, and a remote Substack draft canary remain unverified.
