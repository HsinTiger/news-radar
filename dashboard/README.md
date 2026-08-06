# HsinTiger 營運駕駛艙

`dashboard/` 是 News Radar 的單一 owner 操作介面，整合 Substack 編輯營運、Facebook／Instagram／Threads 數據健康、GitHub workflow 訊號與人工投稿。

## 五個視圖

1. **總覽**：runtime、owner attention、內容節奏、最近草稿與三平台快照。
2. **Substack**：每日兩篇 Podcast 延伸文、週日公司深度文、寫手契約與遠端草稿 metadata。
3. **Meta 三平台**：各平台原生指標、30 日趨勢、近期貼文與最新 engagement 回讀。
4. **資料健康**：collector／sync／cadence、預期 scheduler tick、watchdog lineage 與 GitHub Actions。
5. **新增投稿**：Substack 優先草稿、Meta queue／publish-now 與投稿進度。

舊 `/substack-submit/` 保留為同網域相容入口，直接前往 `/dashboard/?view=submit`。

## 授權不變條件

- Storage key 固定為 `hsintiger_social_ops_owner_token`。
- 讀取順序為 `localStorage`、`sessionStorage`。
- 401、網路失敗或 page load 不會刪除 token。
- 只有 owner 明確按下「鎖定此裝置」才會清除目前裝置的值。
- Token 只送往既有 Cloudflare Worker，不送往 GitHub API、URL、DOM、log 或 D1。

## 資料來源

- 公開：Worker `/health`、GitHub Actions 公開 REST API。
- Owner protected：Worker `/api/dashboard` 與 `/api/submissions`。
- `scripts/sync_social_ops.py` 同步版本化 editorial contract、Substack 草稿 metadata、Meta analytics 與 data health 到 D1。
- 儀表板不接收文章全文、Substack cookie 或平台 credential。

缺值必須顯示 `未知`、`STALE` 或 `NOT CONNECTED`；不得自動補成零。

## 本機開發

從 repository root 啟動靜態 server：

```bash
python -m http.server 8765 --bind 127.0.0.1
```

開啟 `http://127.0.0.1:8765/dashboard/`。

## 驗證

```bash
node --check dashboard/app.js
node --check dashboard/ops-core.mjs
node --test tests/js/dashboard_ops.test.mjs
python -m pytest tests -q
```

單元測試不等於 production proof。正式上線需依序完成 D1 migration、Worker deploy、operational sync、Pages deploy，再以實際 owner 授權回讀 protected payload，並驗證桌機與手機版畫面。

## 部署

`.github/workflows/pages-deploy.yml` 會同時組裝根目錄 dashboard、`/dashboard/` 與所有相容 submit 頁。不要單獨部署其中一個資料夾，避免 GitHub Pages 被另一個 artifact 覆蓋。
