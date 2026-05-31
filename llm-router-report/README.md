# 🧠 LLM Router Report — CCR + 免費 LLM API 路由研究報告

> **報告日期**: 2026-05-31
> **研究對象**: `ccr` (Claude Code Router) / `news_radar` / `litellm-gateway`  
> **本機路徑**: `~/news_radar/llm-router-report/`

---

## 📋 目錄（快取導向 — 可分段閱讀）

| # | 文件 | 內容 | 適合閱讀 |
|---|------|------|----------|
| 1 | **[REPORT-CACHE.md](./REPORT-CACHE.md)** | 🔥 **全部濃縮在一頁**，附導覽連結 | 快速回顧 / 找關鍵數字 |
| 2 | **[REPORT-FULL.md](./REPORT-FULL.md)** | 📖 完整報告 — 架構、鏈路、限制、優化建議 | 深度理解 |
| 3 | **[PLATFORM-GEMINI.md](./docs/PLATFORM-GEMINI.md)** | Gemini API 免費額度、模型、金鑰策略 | 平台細節 |
| 4 | **[PLATFORM-GROQ.md](./docs/PLATFORM-GROQ.md)** | Groq API 免費額度、模型 | 平台細節 |
| 5 | **[PLATFORM-CEREBRAS.md](./docs/PLATFORM-CEREBRAS.md)** | Cerebras API 免費額度、模型 | 平台細節 |
| 6 | **[PLATFORM-OPENCODE.md](./docs/PLATFORM-OPENCODE.md)** | OpenCode.ai 免費額度、big-pickle 模型 | 平台細節 |
| 7 | **[ARCHITECTURE.md](./docs/ARCHITECTURE.md)** | CCR → LiteLLM → Provider 三層路由架構圖 | 架構理解 |
| 8 | **[SETUP.md](./INSTALL.md)** | 安裝指南、已安裝檔案位置、launchd 設定 | 首次部署 / 維護 |
| 9 | **[OPTIMIZATION.md](./docs/OPTIMIZATION.md)** | 🚀 優化方針 — 更多用量、更強模型、付費評估 | 進階調校 |

---

## 🏗 專案結構

```
~/news_radar/llm-router-report/
├── README.md              ← 你正在看這份
├── REPORT-CACHE.md         ← 一頁濃縮 + 所有關鍵表格
├── REPORT-FULL.md          ← 完整報告正文
├── INSTALL.md              ← 安裝指南、已安裝檔案、launchd
├── config/
│   └── router-scenarios.md ← 各種路由場景的 config 範例
├── scripts/
│   └── health-check.sh     ← 檢查各 LLM 服務狀態
├── docs/
│   ├── PLATFORM-GEMINI.md
│   ├── PLATFORM-GROQ.md
│   ├── PLATFORM-CEREBRAS.md
│   ├── PLATFORM-OPENCODE.md
│   ├── ARCHITECTURE.md
│   └── OPTIMIZATION.md
└── reports/
    └── *.md                ← 歷史報告/版本對照
```

---

## 🎯 核心結論（30 秒版）

**目前的平台組合在 $0 成本下已經夠用，但有兩瓶頸：**

1. **Gemini 免費每天僅 ~20 requests**（非官方宣稱的 1,500）→ 靠兩把 key 輪換勉強過關
2. **Cerebras 免費 RPM 僅 5**（context ~65K 勘誤：足夠 soul bundle）→ 吞吐瓶頸

✅ **最關鍵的脆弱點**: 沒有真正的付費 backup。如果 Claude Max 出問題、Gemini 兩把 key 都撞 429，只剩 `big-pickle`（限免隨時消失、stealth 模型 maker unknown）和 `groq`（低 RPD）撐場。

🎯 **最推薦的 $0 升級**: 多申請幾把 Gemini API key（每把 +20 req/day），搭配 LiteLLM 自動輪換。
🎯 **最推薦的 $10/月升級**: Cerebras Developer tier（500 RPM），消滅 RPM 5 瓶頸（免費 ~65K context 已夠用）。

---

## ⚡ 系統快照

| 系統 | 狀態 | 路徑 |
|------|------|------|
| `ccr` (Claude Code Router) | ✅ 運行中 | `/opt/homebrew/bin/ccr` (2.7MB Node.js bundle) |
| LiteLLM gateway | ✅ 運行中 | `~/litellm-gateway/` (launchd auto-start) |
| news_radar pipeline | ✅ 有設定 | `~/news_radar/` + `~/bin/news_radar_compose.sh` |
| Gemini key 1 | ✅ 啟用 | 在 `~/.claude-code-router/config.json` |
| Gemini key 2 | ✅ 啟用 | 同上 + `~/.litellm-gateway/.env` |
| Groq API | ✅ 啟用 | 同上 |
| Cerebras API | ✅ 啟用 | 同上 |
| OpenCode API | ✅ 啟用 | 同上 |
