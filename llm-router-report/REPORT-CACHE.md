# 📋 REPORT CACHE — CCR + 免費 LLM 平台研究總表
> **一頁濃縮版** — 需要的數字這裡都有。→ 深度細節見 [REPORT-FULL.md](./REPORT-FULL.md)

---

## 1. 系統架構（三層路由）

```
┌────────────────────────────────────────────────────────────┐
│  ① claude CLI (使用者輸入 / news_radar_pipeline)          │
│     cache="/Users/hsin/.claude/projects/-Users-hsin/memory/ │
│     settings=~/.claude-code-router/ccr-settings-*.json     │
│     ANTHROPIC_BASE_URL → http://127.0.0.1:3456             │
└────────────────────┬───────────────────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────────────────┐
│  ② ccr (Claude Code Router) @ port 3456                   │
│     /opt/homebrew/bin/ccr (Node.js, 2.7MB)                 │
│     PID: 29001, config: ~/.claude-code-router/config.json │
│                                                             │
│  Router 模式 → 決定走哪個 Provider:                         │
│   default=    litellm,* → gemini2-flash via LiteLLM        │
│   background= litellm,* → groq via LiteLLM                 │
│   think=      litellm,* → big-pickle via LiteLLM           │
│   longContext=litellm,* → gemini-flash via LiteLLM         │
│   webSearch=  litellm,* → gemini-flash via LiteLLM         │
└────────────────────┬───────────────────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────────────────┐
│  ③ LiteLLM Gateway @ 127.0.0.1:4000                       │
│     ~/litellm-gateway/ (Python 3.11 venv, litellm 1.86.2) │
│     launchd auto-start + KeepAlive (~6s recovery)          │
│                                                             │
│  功能: 多 key 自動輪換 + 配額錯誤切換 + fallback 鏈        │
│  Config: ~/litellm-gateway/config.yaml                     │
└─────┬──────────┬──────────┬──────────┬─────────────────────┘
      │          │          │          │
      ▼          ▼          ▼          ▼
  Gemini 1   Gemini 2   Groq      Cerebras/OpenCode
  (free)     (free)     (free)    (free)
```

---

## 2. 雙重 Fallback 鏈

### 2a. `llm_brain.py`（news_radar pipeline 內部）

```
順序    Provider          Model              Context    觸發條件
─────  ────────────────  ─────────────────  ─────────  ──────────────────────
  1️⃣   claude_cli (Max)  Opus via Claude    ~200K     主腦（Max 訂閱，無 API 費用）
  2️⃣   gemini            gemini-2.5-flash   1M tokens  失敗 / 429 時
  3️⃣   opencode          big-pickle(stealth, maker unknown) ~200K     限免，隨時會消失
  4️⃣   groq              openai/gpt-oss-120b 131K      僅短任務可用（8K TPM）
  5️⃣   cerebras          zai-glm-4.7        ~65K(免費)  長文 composer 可用
```

### 2b. `ccr Router`（Claude CLI HTTPS proxy 層）

```
ccr 模式         → Provider          → 實際模型
──────────────────────────────────────────────────
default          → litellm → gemini   → gemini-2.5-flash
background       → litellm → groq     → llama-3.3-70b-versatile
think            → litellm → opencode → big-pickle
longContext      → litellm → gemini   → gemini-2.5-flash
webSearch        → litellm → gemini   → gemini-2.5-flash
```

> ⚠️ **重要區別**: ccr 路由的是 `claude` CLI 命令本身 → 把 Anthropic API 呼叫轉到其他免費 providers。  
> `llm_brain.py` 是 Python 程式碼直接呼叫各 providers。**兩者是獨立運作的系統。**

---

## 3. 各平台免費額度比較

| 平台 | 免費每日請求 | 免費 RPM | Context | 本機使用的 Model | 可靠性 |
|------|------------|---------|---------|-----------------|--------|
| **Gemini 2.5 Flash** | ~20 req/day (per key) | 未公開 | **1M tokens** | gemini-2.5-flash | 🟡 低但 key 多可補 |
| **Gemini 3.5 Flash** 🌟 | ~20 req/day (per key) | 未公開 | **1M tokens** | 最新推薦 | 🟢 2026 新, 建議遷移 |
| **Gemini 3.1 Flash Lite** | ~20 req/day (per key) | 未公開 | **1M tokens** | 低成本選項 | 🟢 短任務適用 |
| **Groq** | 1,000 RPD | 30 RPM | 131K | llama-3.3-70b / gpt-oss-120b | 🟢 穩定 |
| **Cerebras** | 1M TPD | 5 RPM | **~65K** (免費) | zai-glm-4.7 / gpt-oss-120b | 🟢 長文可用 (RPM 瓶頸) |
| **OpenCode big-pickle** | 未公開 | 未公開 | ~200K (傳聞) | big-pickle (stealth, maker unknown) | 🟡 限免，隨時轉付費 |
| **Claude Max** (訂閱) | 5hrs rolling | N/A | ~200K | Claude Opus 4.8 | 🟢 主腦，穩定 |

### 詳細平台限制

| 平台 | Model | RPM | RPD | TPM | TPD | Context |
|------|-------|-----|-----|-----|-----|---------|
| **Gemini - gemini-2.5-flash** | 主要 | 未公開 | ~20/日/key | — | — | 1M |
| **Gemini - gemini-3.5-flash** 🌟 | 推薦 | 未公開 | ~20/日/key | — | — | 1M |
| **Gemini - gemini-3.1-flash-lite** | 低成本 | 未公開 | ~20/日/key | — | — | 1M |
| **Gemini - gemini-2.0-flash** | 🔴 DEPRECATED | — | — | — | — | shutdown 06-01 |
| **Gemini - gemini-2.0-flash-lite** | 🔴 DEPRECATED | — | — | — | — | shutdown 06-01 |
| **Groq - llama-3.3-70b** | 主要 | 30 | 1,000 | 12K | 100K | 131K |
| **Groq - gpt-oss-120b** | 次要 | 30 | 1,000 | 8K | 200K | 131K |
| **Groq - llama-3.1-8b** | 備用 | 30 | 14,400 | 6K | 500K | 131K |
| **Groq - qwen3-32b** | 備用 | **60** | 1,000 | 6K | 500K | 131K |
| **Groq - llama-4-scout-17b** | 中長任務 | 30 | 1,000 | 30K | 500K | 131K |
| **Cerebras - gpt-oss-120b** | 主要 | 5 | n/a | 30K/分 | 1M/日 | ~65K (免費) |
| **Cerebras - zai-glm-4.7** | 主要 | 5 | n/a | 30K/分 | 1M/日 | ~65K (免費) |
| **OpenCode big-pickle** | 唯一(stealth) | 未公開 | 未公開 | 未公開 | 未公開 | 未公開(~200k) |

---

## 4. 用量估算 — 夠不夠用？

### news_radar 每小時用量
```
每小時: 2 篇 compose × 各 ~4 LLM calls + 1~2 次 scoring = 約 8-12 calls/hour
每天 ~24h 運行 → 約 200-300 calls/day
```

### 各平台可承載量

| 場景 | 可承載 | 說明 |
|------|--------|------|
| **Claude Max 完全正常** | ✅ 無壓力 | Max 主腦處理全部，~200 calls/day < 5hr rolling 上限 |
| **Max 掛 + Gemini × 2 keys** | 🟡 僅 ~40 calls | 20+20=40/day → 缺口 ~160-260/day |
| **Max 掛 + Gemini + Groq** | 🟢 ~1,040 calls | 40 + 1,000 = 夠 cover |
| **全免費鏈** | 🟢 理論 1,000+ calls | Gemini 40 + Groq 1,000 + Cerebras 1M TPD |
| **長文 composer 退到 Cerebras** | 🟢 可用 | ~65K context 夠 soul bundle (~17K) |

### 結論
**✅ 日常運行: 非常夠用** — Claude Max 訂閱處理 ~95% 的呼叫。  
**⚠️ 當 Max 掛掉: Gemini + Groq 勉強夠，但 Gemini 每天只有 ~40 calls** → 建議多申請 1-2 把 Gemini key。  
**✅ Cerebras 免費層對長文可用** — context ~65K 可裝 soul bundle (~17K)，但 RPM 5 仍是瓶頸。

---

## 5. 優化建議優先級

| 優先級 | 方案 | 成本 | 預期效果 |
|--------|------|------|---------|
| 🔴 P0 | 多申請 2-3 把 Gemini API key | $0 | +40-60 free Gemini calls/day |
| 🔴 P0 | 移除 config 中 gemini-2.0-flash / lite | $0 | 已 06-01 shutdown，改用 3.5-flash |
| 🟡 P1 | Cerebras Developer $10/mo (解鎖 RPM) | $10/mo | 5→500 RPM；免費 ~65K context 已夠用 |
| 🟡 P1 | 在 LiteLLM 設定 Groq 多組織輪換 | $0 | 多開幾個 Groq 帳號，RPD 累加 |
| 🟡 P1 | 監控 big-pickle (stealth, maker unknown) | $0 | 非 GLM-4.6，真 stealth 模型，限免隨時消失 |
| 🟢 P2 | 引入 GitHub Models (Azure free) | $0 | 多一條免費模型鏈 |
| 🟢 P2 | background mode → qwen3-32b (60 RPM) | $0 | 高吞吐短任務用 |
| ⭐ P3 | Claude Max → Claude Enterprise | $200+/mo | 更高 rate limit，但用量 10x 前不需要 |

---

## 6. 替代免費平台評估

| 平台 | 免費 Tier | Context | 適合本專案？ | 理由 |
|------|----------|---------|------------|------|
| **OpenCode Zen (big-pickle)** | 全免費 (限免) | ~200K | 🟡 主要備用 | 真 stealth 模型(maker unknown, 非 GLM-4.6)，限免隨時消失 |
| **DeepSeek API** | 付費（極低價） | 1M | ❌ 已無免費 tier | 2025 年底取消免費 |
| **Together AI** | 一次性 $25 贈金 | 各種 | ❌ 用完就沒 | 非永久免費 |
| **Mistral AI (Le Chat)** | Web 免費，API 付費 | 32K | ❌ API 不自免費 | 只有 Chat UI 免費 |
| **GitHub Models** | Azure 付費，但有試用配額 | 各種 | 🟡 可當短期備用 | 2026 年有免費配額但需綁 Azure |
| **HuggingFace Inference** | 免費但有速率限制 | 各種 | 🟡 可當備選 | 模型多但速度慢 |
| **Perplexity API** | 每月 $5 額度 | 各種 | 🟡 低價選項 | 每月 $5 可用 pplx-7b-online |
| **Fireworks AI** | 免費 tier 有 | 32K+ | 🟡 可評估 | 2026 年有免費額度 |

**總結**: 目前沒有其他 $0 且穩定的免費平台值得大改加入現有鏈。big-pickle 作為 stealth 模型雖然珍貴，但不應作為長期架構的基石。

---

## 7. 已安裝檔案位置總表

| 檔案 | 路徑 | 大小 | 說明 |
|------|------|------|------|
| ccr binary | `/opt/homebrew/bin/ccr` | 2.7 MB | Node.js bundle（brew install） |
| ccr config | `~/.claude-code-router/config.json` | 2.1 KB | 5 個 providers + Router 設定 |
| ccr PID | `~/.claude-code-router/.claude-code-router.pid` | 5 B | 進程 ID = 29001 |
| ccr logs | `~/.claude-code-router/logs/` | - | 空白，LOG=false 時不輸出 |
| claude settings (global) | `~/.claude/settings.json` | 230 B | 設 model=opus, effort=high |
| claude settings (local) | `~/.claude/settings.local.json` | 2.5 KB | permissions allow list |
| ccr 臨時 settings | `/var/folders/.../ccr-settings-*.json` | 161 B | ANTHROPIC_BASE_URL → :3456 |
| LiteLLM gateway | `~/litellm-gateway/` | 4 dirs | Python 3.11 venv, litellm 1.86.2 |
| LiteLLM config | `~/litellm-gateway/config.yaml` | 2 KB | providers + fallback 設定 |
| LiteLLM log | `~/litellm-gateway/proxy.log` | 464 KB | 運行日誌 |
| LiteLLM start | `~/litellm-gateway/start.sh` | 405 B | 啟動腳本 |
| LiteLLM env | `~/litellm-gateway/.env` | 351 B | API keys（重疊但不完全同 ccr） |
| news_radar repo | `~/news_radar/` | - | Python pipeline |
| news_radar soul | `~/news_radar/config/news_radar_soul.md` | - | 寫作規範（~17KB） |
| news_radar env | `~/news_radar/.env` | 4.2 KB | GEMINI_API_KEY 等 |
| llm_brain.py | `~/news_radar/src/llm_brain.py` | 867 lines | 5 條 fallback 鏈實作 |
| compose script | `~/bin/news_radar_compose.sh` | 6.2 KB | launchd 觸發的 compose pipeline |
| launchd plist | `~/Library/LaunchAgents/com.hsin.litellm-gateway.plist` | - | LiteLLM 開機自啟 |

---

> **⏩ 想深入?** 完整報告見 [REPORT-FULL.md](./REPORT-FULL.md)  
> **🔧 想安裝/維護?** 見 [INSTALL.md](./INSTALL.md)  
> **🚀 想優化?** 見 [docs/OPTIMIZATION.md](./docs/OPTIMIZATION.md)
