# News Radar · Dashboard

> 基於 sql.js 的瀏覽器端 SQLite 資料庫閱讀器，用於監控 News Radar 系統狀態、發布佇列、互動數據等。

## 架構

完全前端的靜態網站，不需要後端伺服器：

1. **DB 來源**：從 `state` branch 的 `data/01_harvest/news_radar.db`（raw.githubusercontent.com CDN）載入
2. **瀏覽器 SQLite**：使用 [sql.js](https://sql.js.org/)（Emscripten 編譯的 SQLite）直接在瀏覽器中開資料庫
3. **部署**：GitHub Pages（`dashboard-deploy.yml` 自動部署）

## 頁面

| 頁面 | 說明 |
|---|---|
| 🏠 **首頁** | 系統概覽、上次發布、累計統計、最近 10 條發布 + 素材 |
| 📋 **發布佇列** | Queue 狀態管理（等待/已發/失敗/過期），可按狀態篩選 |
| 📚 **歷史存檔** | 已發布貼文卡片列表，附各平台互動數據 |
| 🗑️ **被擋掉的** | 未通過篩選的素材，可按原因篩選 |
| 🎭 **寫作風格** | 從 GitHub raw 載入三平台風格指南 |
| ⚙️ **設定** | 主題權重、Token 用量、Reflection 事件 |

## 部署

每次 push 到 `main` 且 `dashboard/` 目錄有變更時，`dashboard-deploy.yml` 自動部署到 GitHub Pages。

**啟用方式**：
1. 在 repo Settings → Pages → Source 選「GitHub Actions」
2. 首次可手動觸發 `Dashboard · Deploy to GitHub Pages` workflow

## 本機開發

```bash
cd news_radar/dashboard
python3 -m http.server 8080
# 開瀏覽器 http://localhost:8080
```

需先讓 `state` branch 有 DB 資料。

## 資料延遲

DB 由 `full_pipeline.yml` 每 2 小時更新一次並推送至 `state` branch。Dashboard 會自動每隔 5 分鐘重新載入 DB。
