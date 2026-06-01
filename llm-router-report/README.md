# 🧠 LLM Router Report — CCR + 免費 LLM API 路由研究報告

> **報告日期**: 2026-05-31 ~ 2026-06-01
> **研究對象**: `ccr` (Claude Code Router) / `news_radar` / `litellm-gateway`  
> **本機路徑**: `~/news_radar/llm-router-report/`
> **Session 協作記錄**: [`reports/CHANGELOG.md`](./reports/CHANGELOG.md)

---

## 🏗 完整目錄結構

```
~/news_radar/llm-router-report/
├── README.md                    ← 入口（這份）
├── REPORT-CACHE.md              ← 🔥 一頁濃縮 + 所有表格
├── REPORT-FULL.md               ← 📖 完整報告 447 行
├── INSTALL.md                   ← 🔧 安裝維護指南
│
├── config/
│   └── router-scenarios.md      ← 7 種路由場景 + 快速對照表
│
├── scripts/
│   └── health-check.sh          ← 一鍵檢查所有服務
│
├── docs/
│   ├── ARCHITECTURE.md          ← 三層路由架構圖
│   ├── OPTIMIZATION.md          ← 🚀 優化方針 P0~P3
│   ├── PLATFORM-GEMINI.md       ← 14 個免費模型細節
│   ├── PLATFORM-GROQ.md         ← 16 個免費模型細節
│   ├── PLATFORM-CEREBRAS.md     ← 2 個免費 + 付費模型
│   └── PLATFORM-OPENCODE.md     ← 6 個限免模型 (Zen)
│
└── reports/
    └── CHANGELOG.md             ← 📝 完整協作記錄 (94行)
```

---

## 🏛 路由架構圖

```
┌─────────────────────────────────────────────────────────────────────┐
│  User                                                              │
│   │                                                                │
│   ├─ claude (手動) ──────────────────────────────────────┐         │
│   │                                                       │         │
│   ├─ claude -p (pipeline subprocess)                      │         │
│   │                                                       │         │
│   └─ Python call_for_json() ── llm_brain.py              │         │
│                                                           ▼         │
│                                              ANTHROPIC_BASE_URL     │
│                                              = http://127.0.0.1:3456│
│                                                           │         │
│                                              ┌────────────┴──────┐  │
│                                              │  CCR (:3456)      │  │
│                                              │  Node.js proxy    │  │
│                                              │  PID 75813        │  │
│                                              │  config.json      │  │
│                                              └────────┬──────────┘  │
│                                                       │             │
│                                         Router mode  │             │
│                                ┌──────────────────────┤             │
│                                │         │            │            │
│                    ┌───────────┴───┐ ┌──┴────────┐ ┌──┴────────┐   │
│                    │ default       │ │ background│ │ think     │   │
│                    │ longContext   │ │           │ │ webSearch │   │
│                    │ webSearch     │ │           │ │           │   │
│                    └───┬───────────┘ └─────┬─────┘ └─────┬─────┘   │
│                        │                   │             │         │
│                        └──────────┬────────┴──────┬──────┘         │
│                                   │               │                │
│                                   ▼               ▼                │
│                        ┌──────────────────────────────┐            │
│                        │  LiteLLM Gateway (:4000)     │            │
│                        │  Python uvicorn, PID 77586   │            │
│                        │  launchd KeepAlive auto-restart           │
│                        │  14 model groups, 13 fallbacks            │
│                        └──────┬──────┬──────┬──────┬──┘            │
│                               │      │      │      │               │
│                    ┌──────────┘      │      │      └──────────┐    │
│                    ▼                 ▼      ▼                 ▼    │
│              ┌──────────┐  ┌──────────┐  ┌──────┐  ┌──────────┐  │
│              │ Gemini   │  │ OpenCode │  │ Groq │  │ Cerebras │  │
│              │ 2 keys   │  │ 6 models │  │ 5 m. │  │ 2 models │  │
│              │ 1M ctx   │  │ 128K+ ctx│  │131K  │  │ ~65K ctx │  │
│              └──────────┘  └──────────┘  └──────┘  └──────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Fallback 鏈（純能力排序）

```
① gemini-flash       → Gemini 2.5-flash, 1M ctx, 2 keys      Tier S
② gemini-35-flash    → Gemini 3.5-flash, 1M ctx, 2 keys      Tier S
③ deepseek-v4-flash  → DeepSeek V4 Flash, 128K+ ctx          Tier A
④ qwen3.6-plus-free  → Qwen 3.6 Plus, 128K+ ctx              Tier A
⑤ big-pickle         → Stealth model, maker unknown          Tier A-
⑥ minimax-m2.5-free  → MiniMax M2.5, 128K+                   Tier A-
⑦ groq-70b           → Llama 3.3 70B, 131K                   Tier B
⑧ groq-oss           → GPT-OSS 120B, 131K                    Tier B
⑨ groq-qwen3         → Qwen3 32B, 131K, 60 RPM               Tier B
⑩ groq-scout         → Llama 4 Scout 17B MoE                 Tier B-
⑪ cerebras-glm       → GLM 4.7 355B, ~65K ctx                Tier B-
⑫ mimo-v2.5-free     → Unknown maker                         Tier C
⑬ nemotron-3-super   → NVIDIA trial, NOT production           Tier C
⑭ groq-8b            → Llama 3.1 8B, 14,400 RPD              Tier D
```

---

## 📝 Session 快速回顧（cache 版）

| 時間 | 事件 | 當時你走哪個模型 |
|------|------|----------------|
| 5/31 ~20:55 | Session 啟動 | **big-pickle** (think mode) |
| 5/31 ~22:00 | ccr + LLM 平台研究 | big-pickle |
| 5/31 ~23:00 | 產出第一版 ultrawork 報告 | big-pickle |
| 6/01 ~00:03 | Workflow 平行研究 5 平台 | big-pickle |
| 6/01 ~00:37 | 改名 ultrawork → llm-router-report | big-pickle → Gemini 2.5 快沒了 |
| 6/01 ~01:54 | 加入 OpenCode 6 個免費模型 | **Gemini 3.5-flash** (2.5 用完) |
| 6/01 ~02:04 | 修正 fallback 排序（你的指正） | Gemini 3.5-flash |
| 6/01 ~02:25 | 最終按純能力重新排名 + worklog | Gemini 3.5-flash |

---

## 🎯 核心結論（10 秒版）

| 日常主力 | Gemini 2.5-flash (1M ctx, 2 keys ~40 req/day) |
|---------|----------------------------------------------|
| 今天你實際在哪 | **Gemini 3.5-flash**（2.5 今天額度用完了） |
| 最脆弱點 | Gemini 每 key ~20 req/day |
| 最推薦 $0 升級 | 多申請 2-3 把 Gemini key |
| 最推薦 $10/mo | Cerebras Developer (500 RPM) |
| 總模型數 | **14 個模型 groups, 13 層 fallback** |

---

## ⚡ 系統狀態快照

| 系統 | PID | 狀態 |
|------|-----|------|
| `ccr` (:3456) | 75813 | ✅ Running |
| `LiteLLM` (:4000) | 77586 | ✅ Running (launchd auto-restart) |
| `claude CLI` | 2.1.158 | ✅ Installed |
| `news_radar venv` | Python 3.11.3 | ✅ Ready |
| `.env` files | — | ✅ Found |
