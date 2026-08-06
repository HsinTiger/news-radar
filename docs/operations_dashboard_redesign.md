# HsinTiger 營運儀表板重設計

## 1. Goal / non-goals

- `PROVEN`：owner 需要從一個入口在 2 分鐘內回答：今天系統是否正常、Substack 正在怎麼寫與排程、Meta 三平台各自表現如何、哪些資料沒接上、下一個需要人判斷的是什麼。
- `PROVEN`：現行 `/substack-submit/` 以投稿表單為主，營運資訊在另一個 `/dashboard/`；兩頁共用 `hsintiger_social_ops_owner_token`，但資訊架構分裂。
- `PROVEN`：2026-08-06 live health 顯示 automation=`recovery`、submission processor=`live`、Meta publish-now ready、Substack auto-publish=false。
- `PROVEN`：最新 operational sync 已送入 D1：312 platform posts、261 engagement snapshots、3 quality snapshots、22 recovery experiments、500 knowledge metadata、32 proposals、11 health signals；latest audience monitor 另送入三個平台 snapshot。
- `PROVEN`：上述 ingestion 成功不代表每一項資料都新鮮、完整或在 UI 正確呈現。
- `UNKNOWN`：未持有 owner token 的此工作階段無法讀取 protected `/api/dashboard` live payload；不能把 workflow count 當成實際畫面資料完整度。
- `UNKNOWN`：新版 Mac launchd 尚未載入，因此 repo 內的 Substack 排程設計不等於 live schedule proof。

本次範圍：重做 Pages 儀表板資訊架構與互動；補齊可稽核的 Substack 草稿 metadata、寫手／排程契約與 Meta 原生分析呈現；保留既有投稿 API。

非目標：不改發布政策、不啟用 Substack 自動發布、不改 Meta secrets、不重發舊內容、不清除或搬移任何瀏覽器 token。

## 2. Options / decision

### A. 保留兩頁，只重畫 CSS

改動小，但投稿與營運仍分裂，Substack 排程／寫手策略沒有可維護的真相來源，資料健康仍被埋在頁尾。拒絕。

### B. 單一營運駕駛艙，舊入口相容（採用）

`/dashboard/` 成為唯一 app；`/substack-submit/` 保留並同網域導向 `../dashboard/?view=submit`。第一屏先回答 owner attention，之後提供 Overview、Substack、Meta、Data health、投稿五個視圖。授權 key 與 API 不變。

### C. 新增需要 GitHub OAuth 的 server-side dashboard

可做更強的權限與即時查詢，但會迫使所有裝置重新授權，也擴大 secrets 與維運面。與 owner 的明確限制衝突，拒絕。

## 3. Contracts / invariants

### Authorization

- storage key 永遠是 `hsintiger_social_ops_owner_token`。
- 讀取順序維持 `localStorage` → `sessionStorage`。
- page load、401、network error 都不得自動刪除 token；只有 owner 明確按「鎖定此裝置」才清除目前裝置的值。
- owner token 只送到既有 Cloudflare Worker `Authorization: Bearer`；不得送往 GitHub API、analytics、log、DOM、URL、測試 snapshot 或 D1。
- `/substack-submit/` 與 `/dashboard/` 保持 `https://hsintiger.github.io` 同 origin；redirect 不讀寫 storage。

### Information architecture

1. `Overview`：automation、attention queue、Substack／Meta 今日狀態、資料新鮮度。
2. `Substack`：repo 設計與 live proof 分開；12:00 two-draft Podcast batch、7-day candidates、Sun 09:00 company pick-and-compose、Weekly writer contract、recent remote drafts、submission backlog。
3. `Meta`：Facebook／Instagram／Threads 分開顯示 followers、7d delta、平台原生 median、品質 cohort、最後發布與最新資料時間；可切換 actions／views／reach／clicks 趨勢。
4. `Data health`：每一條 collector/sync/cadence 的 status、captured_at、age 與 detail；缺失是 UNKNOWN，不是 0。
5. `Submit`：保留 URL／text／YouTube、Substack draft-priority、Meta queue/publish-now、platform selection 與 idempotency。

### Data contracts

- public `/health` 與 public GitHub workflow status 可在未解鎖時呈現；protected D1 analytics 只在 owner token 驗證後載入。
- operational sync 的 `automation.detail` 改為 versioned JSON，包含 tracked repo 可證明的 editorial schedule/writer contract；API 回傳為 `editorial_contract`。
- 新增 `substack_drafts` metadata table，只同步 source/draft identity、type、title、URL、remote draft id、written/drafted timestamps；永不傳 article body、cookies 或 credentials。
- `recent_posts` 必須帶對應最新 engagement snapshot，讓「近期內容」不再只有發布標題。
- 所有 stale/absent data 顯示 UNKNOWN/STALE/NOT CONNECTED；不得以 0 補洞。

## 4. Verification matrix

| Requirement | Executable evidence | PASS limitation |
|---|---|---|
| token 相容且不自動清除 | JS unit tests + static HTML/app contract | 無法讀另一台電腦的 storage |
| Substack schedule/writer contract | Python unit tests parse tracked plist/briefs into versioned detail | repo contract 不等於 Mac launchd loaded |
| scheduled/owner Substack draft metadata | Python builder tests + Worker migration/static API contract | D1 migration/deploy/sync 後才有 live rows |
| Meta analytics normalization | JS pure-function tests for platform KPIs, trend and missing-data semantics | mock data 不等於 platform API readback |
| UI navigation/form | local browser desktop/mobile DOM and interaction smoke | local PASS 不等於 Pages deployment |
| no layout overflow / console error | local browser geometry + console checks | public CDN/font/network may differ |
| regression | focused tests + complete `pytest tests` | does not exercise macOS launchd/Substack API |

## 5. Implementation slices

1. Pure operations core + RED unit tests: token contract, attention derivation, platform metrics, editorial contract.
2. Sync/API slice: versioned editorial detail, `substack_drafts`, recent post metrics.
3. Unified responsive cockpit and submission form; compatibility redirect.
4. Browser QA at desktop and 375px mobile; regression and static security checks.

## 6. Risks / owner gates

- Cloudflare migration and Worker deployment are separate production gates; Pages UI must tolerate old API payloads during rollout.
- GitHub Actions success is delivery-process evidence, not proof that Meta published or Substack accepted a draft.
- Full Cloud Pipeline currently fails when no Threads attempt/readback exists in the verification window; UI should surface the failure without calling the analytics collectors broken.
- No commit, push, Worker migration, Worker deploy or Pages deploy is implied by local implementation evidence.

## 7. Decision changes

- Initial idea of expanding `/substack-submit/` in place was rejected because it would duplicate the dashboard API/render/auth code. Same-origin redirect preserves bookmarks and authorization while producing one maintainable app.
