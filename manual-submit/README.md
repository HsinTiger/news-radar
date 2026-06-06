# News Radar · 手動提交前端

> 一個輕量級的前端 + API 後端，讓你從瀏覽器（手機／電腦）手動提交感興趣的文章、文字、YouTube 影片、或截圖到 News Radar 管線。

## 架構

```
瀏覽器（manual-submit/index.html）
    ↕ HTTP (CORS)
FastAPI Server（scripts/manual_submit_server.py）port 8765
    ↕
SQLite DB（data/01_harvest/news_radar.db，經 submit_source.py）
    ↕
Pipeline（run_pipeline.py）→ 三平台發布
```

## 快速開始

### 1. 啟動 API Server

```bash
# 開發模式
cd ~/news_radar
.venv/bin/uvicorn scripts.manual_submit_server:app --host 127.0.0.1 --port 8765 --reload
```

### 2. 啟動前端（開發模式）

```bash
cd ~/news_radar/manual-submit
python3 -m http.server 8080
# 開瀏覽器 http://localhost:8080
```

### 3. 安裝為 launchd 服務（自動啟動）

```bash
bash install_launchd.sh
```

之後可以從手機開 browser 連 `http://你的Mac區域IP:8765` 或從桌面開 `http://localhost:8765/manual-submit/`。

## 四種提交方式

| 類型 | 說明 | 處理方式 |
|------|------|----------|
| 🔗 網址 | 貼上文章 URL（支援多行） | `trafilatura` 抓取全文 → `submit_source.py` |
| 📝 純文字 | 直接貼上文章全文 | 直接寫入 DB → pipeline |
| ▶️ YouTube | 貼上 YouTube 影片 URL | `youtube-transcript-api` 抓字幕 → DB |
| 🖼️ 截圖/圖片 | 上傳 JPG/PNG/HEIC | Base64 → 存檔 → DB（目前以圖片 URL + caption 方式） |

## 排程選項

- **⏳ 下一輪 pipeline**：加入 publish queue，等下次 cron 排程發布
- **🚀 立即發布**：直接觸發 `publish_now.py`（僅支援 URL / YouTube 類型）

## 部署到 GitHub Pages

```bash
# 1. Push 到 GitHub repo
git add manual-submit/
git commit -m "feat: manual submit frontend"
git push

# 2. GitHub Actions 會自動部署到 Pages
# 見 .github/workflows/manual-submit-deploy.yml
```

部署後的前端會連到 `localhost:8765` 的 API Server。

如果你要連到遠端 API，在前端 console 執行：
```js
localStorage.setItem('news_radar_api_base', 'http://你的MacIP:8765');
```
或直接在 app.js 改 `API_BASE` 常數。

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | 系統健康檢查 + DB 狀態 |
| POST | `/api/submit` | 提交任何類型的來源（JSON body） |
| POST | `/api/submit/image` | 圖片上傳（multipart form） |
| GET | `/api/history` | 最近的提交紀錄 |
| GET | `/api/health` | Minimal health check |

### POST `/api/submit` 範例

```json
{
  "type": "url",
  "content": "https://example.com/article",
  "platforms": ["fb", "ig", "threads"],
  "note": "這篇關於 AI 監管的文章很棒",
  "schedule": "next"
}
```
