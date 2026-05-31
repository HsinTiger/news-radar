# 📖 完整研究報告 — CCR + 免費 LLM API 路由系統

> **報告日期**: 2026-05-31  
> **研究範圍**: Claude Code Router (`ccr`) × news_radar pipeline × LiteLLM Gateway  
> **核心問題**: 三層路由如何協作？免費平台夠用嗎？如何優化？

---

## 1. 背景：什麼是 CCR？

**ccr (Claude Code Router)** 是一個開源 CLI 工具，由 [@Musistudio](https://github.com/musistudio) 開發，透過 Homebrew 安裝:

```bash
# 安裝方式
brew install musistudio/tap/claude-code-router
```

本機的 `ccr` 版本是 bundled Node.js executable（2.7MB），運行 PID 29001，listen 在 `127.0.0.1:3456`。

### 核心功能

ccr 攔截 `claude` CLI 的 API 請求（透過設定 `ANTHROPIC_BASE_URL=http://127.0.0.1:3456`），然後根據不同的 **Router mode** 把請求轉發到不同的 LLM provider（Gemini / Groq / Cerebras / OpenCode）。使用者完全無感 — claude CLI 以為自己在跟 Anthropic API 講話。

### 本機啟動方式

```bash
# 從 terminal 啟動 ccr
ccr code

# 它會自動:
# 1. 讀取 ~/.claude-code-router/config.json
# 2. 啟動一個 local proxy server 在 :3456
# 3. 開一個子行程 claude --settings <temp_settings_file>
#     其中 temp_settings 含 ANTHROPIC_BASE_URL=http://127.0.0.1:3456
```

---

## 2. 完整架構 — 不是一層，是三層

本機實際上有 **三個獨立的路由系統** 疊在一起運作:

```
Layer 1: claude CLI
───────────────────
  單一入口。不論是使用者手動 `claude` 還是 pipeline 裡的 `subprocess claude -p`，
  都會打到 ANTHROPIC_BASE_URL。

Layer 2: ccr (Claude Code Router)
───────────────────
  攔截 :3456，根據 Router mode（default/background/think/longContext/webSearch）
  決定走哪個 provider。2026-05-31 的 config 把所有 mode 指向 litellm,*。

Layer 3: LiteLLM Gateway
─────────────────────────
  真正的智慧路由器。用 config.yaml 定義:
  - model_list: 兩把 Gemini 掛同 alias「gemini-flash」（自動 load-balance）
  - router_settings.fallbacks: 整組 gemini-flash 用盡時跳 big-pickle → groq → cerebras
  - 由 launchd 管理（KeepAlive）→ 死掉自動重啟
  - 監聽 :4000，ccr 指向它完成串接

Layer 4: 各 Provider API
─────────────────────────
  Gemini / Groq / Cerebras / OpenCode — 真正送出 HTTP 請求的地方。
```

### 為什麼要三層？

| 層 | 負責 | 不負責 |
|----|------|--------|
| claude CLI | 提供一致的使用者體驗、skill runtime | provider 選擇、key 管理 |
| ccr | 依 mode 分流（background cheap / think strong） | key 輪換、fallback 重試 |
| LiteLLM | key 輪換、429 重試、複雜 fallback 邏輯 | mode 路由 |

**歷史沿革**:
1. 最早只有 `llm_brain.py`（Python 層）負責 fallback
2. 然後引入 ccr 讓所有 `claude` CLI 呼叫都有免費備用
3. 2026-05-31 加入 LiteLLM gateway 解決多 key 管理問題

---

## 3. 雙重 Fallback 鏈（完整對照）

這是最容易混淆的地方。本機**有兩條獨立運作的 fallback 鏈**。

### 鏈 A: `llm_brain.py`（Python code fallback）

位於 `~/news_radar/src/llm_brain.py`，用在 `scorer.py` 和 `composer.py` 裡。

```python
# 預設鏈（call_for_json 的 allowed tuple）
allowed = ("claude_cli", "gemini", "opencode", "groq", "cerebras")
```

| 順序 | Provider | 實作方式 | Context | 限制 | 用途 |
|------|----------|---------|---------|------|------|
| 1 | **claude_cli** | `subprocess claude -p --output-format json` | ~200K | Max 訂閱 5hrs rolling | 主腦（95%+ traffic） |
| 2 | **gemini** | google-genai SDK `response_schema` | **1M tokens** | 每 key ~20 req/day | 大 context 備援 |
| 3 | **opencode** | HTTP POST OpenAI-compatible | ~200K (未確認) | 限免，隨時消失 | 長文備用 |
| 4 | **groq** | HTTP POST OpenAI-compatible | 131K | 6K TPM / 1K RPD | 短任務備用 |
| 5 | **cerebras** | HTTP POST OpenAI-compatible | **~65K (免費)** | 5 RPM / 1M TPD | 短∼中任務 |

### 鏈 B: `ccr Router mode`（CLI proxy fallback）

ccr 的 config 定義了每種 mode 要走哪個 provider。**ccr 不做 retry** — 它只路由，失敗就直接報錯給 claude CLI。

```
Router mode     →  目標 Provider (via LiteLLM)
────────────────────────────────────────────
default         →  litellm → gemini2/gemini-2.5-flash
background      →  litellm → groq/llama-3.3-70b-versatile
think           →  litellm → opencode/big-pickle
longContext     →  litellm → gemini/gemini-2.5-flash
webSearch       →  litellm → gemini/gemini-2.5-flash
```

### 什麼時候哪條鏈被觸發？

| 場景 | 哪個系統路由 | 使用鏈 |
|------|------------|--------|
| Claude Code 互動 (`claude <prompt>`) | ccr → LiteLLM | 鏈 B |
| Claude CLI `-p` (`claude -p "xxx"`) | ccr → LiteLLM | 鏈 B |
| `llm_brain.py` 直接呼叫 | llm_brain.py | 鏈 A |
| news_radar `composer.py` 產文 | llm_brain.py | 鏈 A（通常只用到 `claude_cli`） |
| news_radar `scorer.py` 評分 | llm_brain.py | 鏈 A（可能用到 groq 做短任務） |

### 重要：兩鏈不互相 cover

**如果 ccr 無法連到 LiteLLM** → 所有 claude CLI 呼叫失敗（ccr 不會 fallback 到直連 provider）。  
**如果 `llm_brain.py` 所有 backend 都失敗** → 返回 `LLMResult(data=None)`，呼叫端自己處理 skip。

---

## 4. 各平台免費額度深度分析

### 4.1 Gemini API

**模型**: gemini-2.5-flash（1M context）、gemini-2.5-pro（>200K）、gemini-3.5-flash（1M）、gemini-3.1-flash-lite（1M）等 14 個免費模型  
**官方頁面**: [ai.google.dev](https://ai.google.dev/gemini-api/docs/rate-limits)

| 指標 | 值 |
|------|-----|
| 免費 tier 命名 | Free tier（需 Google 帳號，no credit card） |
| **實際每天請求數** | **~20 req/day per project per model** |
| RPM | 未公開（內部限制，無法在文件查到） |
| 免費 tokens | 輸入輸出皆免費 |
| Context window | 最高 1M tokens（大部分模型） |
| Search grounding | 500 RPD free |
| Maps grounding | 500 RPD free |

**已棄用模型**:
- gemini-2.0-flash — **DEPRECATED**, shutdown 2026-06-01（額度已歸零）
- gemini-2.0-flash-lite — **DEPRECATED**, shutdown 2026-06-01（2026-04 起額度歸零）
- gemini-2.5 系列 — deprecation **2026-10**，建議遷移至 gemini-3.5-flash

**新模型追加**:
- **gemini-3.5-flash** (2026 新) — 1M context, 推薦取代 2.5-flash
- **gemini-3.1-flash-lite** (2026 新) — 1M context, 低成本選項, 適合短任務

**Key 策略**:
- Gemini 的配額是 **per-project 計算**
- 每把 API key 屬於一個 Google Cloud project
- 多一把 key = 多一個 project = 多一份 ~20 req/day
- 本機已有 **兩把 key**（`GEMINI_API_KEY` + `GEMINI_API_KEY_2`）
- `_try_gemini()` 會在第一把撞 429 時自動換第二把
- **推薦再多申請 2-3 把 key**（用不同 Google 帳號開 project）

### 4.2 Groq

**模型**: 16 個免費模型  
**官方頁面**: [console.groq.com/docs/rate-limits](https://console.groq.com/docs/rate-limits)

| 本機使用模型 | RPM | RPD | TPM | TPD | Context |
|-------------|-----|-----|-----|-----|---------|
| llama-3.3-70b-versatile | 30 | 1,000 | 12K | 100K | 131K |
| openai/gpt-oss-120b | 30 | 1,000 | 8K | 200K | 131K |

**其他可用模型**:
| Model | RPM | RPD | TPM | TPD | 特點 |
|-------|-----|-----|-----|-----|------|
| llama-3.1-8b-instant | 30 | **14,400** | 6K | 500K | 最高 RPD，適合批量短任務 |
| qwen3-32b | **60** | 1,000 | 6K | 500K | 最高 RPM，scorer 神器 |
| llama-4-scout-17b | 30 | 1,000 | 30K | 500K | 高 TPM，長一點的任務 |
| groq/compound | 30 | 250 | **70K** | — | 最高 TPM，多模型複合 |
| groq/compound-mini | 30 | 250 | **70K** | — | 同上（mini 版）|

**注意**: Mixtral 已被從 Groq 免費模型中移除。

**評語**: Groq 是目前最穩定、最慷慨的免費平台。llama-3.1-8b 有 14,400 req/day，足夠 cover 大規模短任務（如 scorer）。RPM 30 對本機每小時 ~8-12 calls 來說太低 — 但這是指「突發峰值」，日常幾乎不會撞。

### 4.3 Cerebras

**模型**: gpt-oss-120b、zai-glm-4.7  
**官方頁面**: [docs.cerebras.ai](https://docs.cerebras.ai/)

| 指標 | 值 |
|------|-----|
| RPM | **5**（極低 — 註冊後每小時才跑幾次所以還好） |
| TPM | 30,000 |
| TPH | 1,000,000 |
| TPD | 1,000,000 |
| **免費 context** | **~65K tokens**（全 context 131K 的一半，勘誤: 先前標為 8K） |
| Developer tier | $10/month → **131K context**、**500-1,000 RPM** |

**更新**: 勘誤 — 免費 context 是 **~65K** 而非先前認知的 8K。soul bundle (~17K) 可以正常放入。免費 tier 的 **RPM 5** 才是真正瓶頸。

**問題**: Cerebras 免費層的 RPM 5 極低，突發多請求時一定撞限。但 context (~65K) 已足夠裝 soul bundle + prompt。

**建議**: 如果只是需要長文 context，免費 ~65K 已夠用。但如果需要更高吞吐（scorer 批量、composer 即時），建議升級 $10/mo Developer tier 解鎖 500–1,000 RPM 和完整 131K context。

### 4.4 OpenCode (big-pickle)

**官方頁面**: [opencode.ai](https://opencode.ai/docs)

| 指標 | 值 |
|------|-----|
| 模型 ID | `big-pickle` |
| 真實身份 | **真正的 stealth 模型** — maker unknown；先前推測為智譜 GLM-4.6，但**未經證實** |
| 價格 | $0（input/output/cached 全免） |
| Rate limits | **未公開**（完全沒有文件） |
| Context | 未公開，社群傳聞 ~200K |
| 有效期限 | **限免**（「for a limited time」） |
| Data retention | 可能會用你的資料訓練模型 |

**風險最高**: OpenCode Zen 的條款直接說「free for a limited time」— 隨時可能轉付費或關閉。**不要把長期架構押在它身上**。maker 不明的 stealth 模型使其風險更難評估。

**目前價值**: 作為 Claude + Gemini 同時掛掉時的長文兜底（~200K context 是 Groq/Cerebras 無法取代的）。但一收費就要立刻換掉。

---

## 5. 用量估算 — 夠不夠？

### 基準: news_radar 每小時運行

```
每小時 compose cycle:
  - 2 篇 × 4 LLM calls each (composer + scorer + classifier + refiner) = 8 calls
  - 1~2 次額外 scoring = 1-2 calls
  - overhead (verification / reflect) = 1-2 calls
  ─────────────────────────────────────
  總計: ~10-12 calls/hour

  每天 24h 運行（含 launchd 定時）:
  ~250-300 calls/day
```

### 各場景承載分析

#### 場景 1: 💚 正常運行（Claude Max 主腦）

| 平台 | 每天可承載 | 實際分配 | 餘量 |
|------|-----------|---------|------|
| claude_cli (Max) | ~200-300 | 230-280 | ✅ 足夠 |
| gemini (2 keys) | ~40 | 0-10 (僅備援) | ✅ 幾乎不用 |

→ **完全無壓力**。Max 訂閱處理 ~95%+ traffic。

#### 場景 2: 🟡 Max 暫時掛掉（fallback 模式）

| 平台 | 每天可承載 | 實際分配 | 餘量 |
|------|-----------|---------|------|
| gemini (2 keys) | ~40 | ~40 | 吃滿 |
| groq | ~1,000 | ~100 | ✅ 充足 |
| opencode | 未公開 | ~50 (長文用) | ❓ 未知 |
| cerebras | 1M TPD | ~少量 | 🟡 context ~65K 夠但 RPM 5 慢 |

→ **勉強夠用但 Gemini 會被吃乾抹淨**。缺點:
- Gemini 40/天 的配額會被短時間消耗完
- Groq 的 1K RPD 用於 scorer（短任務）很夠，但 composer（長文）會撞 TPM
- OpenCode 的 big-pickle 是長文唯一可用的免費備用

#### 場景 3: 🔴 全免費鏈（無 Claude Max）

| 平台 | 每天可承載 | 缺口 |
|------|-----------|------|
| gemini × 2 | ~40 | -210 |
| groq | ~1,000 (短任務) | 🟢 夠 |
| cerebras | 1M TPD | 🟡 ~65K 夠但 RPM 5 極慢 |
| → **短任務夠；長文最多 40 篇/天** (via Gemini + big-pickle) |

### 最終結論

**✅ 日常 100% 夠用**。Max 不會撞限制（本機用量很小 — ~250 calls/day）。

**⚠️ 唯一脆弱點**: Gemini 免費 key 每天 ~20 req 太低。**最便宜的加固**: 多開 2-3 個 Google 帳號申請 Gemini key → 免費 +40-60 calls/day。

**✅ Cerebras 免費層對長文可用**（勘誤: context 是 ~65K 非 8K），但 RPM 5 是瓶頸。Developer tier ($10/mo) 主要解鎖 RPM。

---

## 6. 替代平台評估

### 其他免費 / 低價 LLM API（截至 2026-05）

| 平台 | 免費程度 | Context | 適合？ | 說明 |
|------|---------|---------|--------|------|
| **OpenCode Zen (big-pickle)** | 🆓 全免費（限免） | ~200K | 🟡 主要備用 | 真 stealth 模型, maker unknown（非 GLM-4.6），限免隨時消失 |
| **DeepSeek** | ❌ $0 已取消 | 1M | ❌ | 2025 年底取消免費 API，現在是極低 pay-per-use |
| **Together AI** | 💵 $25 一次性贈金 | 32K+ | ⚠️ 用完就沒 | 不是永久免費，不適合做 fallback |
| **Mistral** | ❌ API 付費 | 32K | ❌ | Le Chat app 免費但 API 要錢 |
| **GitHub Models** | 🆕 有限免費配額（綁 Azure） | 各種 | 🟡 可評估 | 2026 年推出 Azure AI 免費配額，但需要 Azure 帳號 |
| **HuggingFace Inference** | 🆓 免費但有速率限制 | 各種 | 🟡 備選 | 速度慢但模型選擇多 |
| **Perplexity** | 💵 $5/mo 額度 | 各種 | 🟡 低價備選 | 每月 $5 可用 pplx-7b-online |
| **Fireworks AI** | 🆓 有限免費 | 32K+ | 🟡 可評估 | 2026 年有免費額度但細節不明 |
| **Cohere** | ❌ 免費已取消 | — | ❌ | 2025 年結束免費 tier |
| **Anyscale** | ❌ 關閉 | — | ❌ | 已停止服務 |

**最佳選擇**: 目前已有 Gemini + Groq + Cerebras + OpenCode Zen 四條免費鏈，沒有值得大改架構的新 $0 平台。如果有支付意願，推薦順序:
1. `Cerebras Developer $10/mo` — 解鎖 500+ RPM（免費 ~65K context 已夠），讓 Cerebras 從「慢」變「快」
2. `Groq Developer plan` — 解鎖更高 RPM，但目前 1K RPD 已夠

---

## 7. 優化方針

### P0: 立刻做（$0）

#### 7.1 移除已棄用的 Gemini 2.0 + 多申請 Key

⚠️ **gemini-2.0-flash / gemini-2.0-flash-lite 已於 2026-06-01 shutdown**。請從所有 config 中移除，改用 `gemini-3.5-flash`（推薦）或 `gemini-3.1-flash-lite`。

```bash
# config 更新:
# ~/.claude-code-router/config.json → default: "litellm,gemini-3.5-flash"
# ~/litellm-gateway/config.yaml → model: gemini/gemini-3.5-flash
```

同時多申請 Gemini API key:
```bash
# 需要做的事:
# 1. 用 2-3 個不同 Google 帳號登入 https://aistudio.google.com/
# 2. 每個帳號開一個 Google Cloud project
# 3. 每個 project 啟用 Generative Language API
# 4. 每個 project 產生一組 API key
# 5. 加入 ~/news_radar/.env 的 GEMINI_API_KEY（逗號分隔）
```

效果: +40-60 free calls/day via Gemini → **消除 Gemini 瓶頸**

#### 7.2 調整 ccr 的 background mode

目前 background 走 `groq,llama-3.3-70b-versatile`。短任務可選:

**選項 A** `llama-3.1-8b-instant`（14,400 RPD — RPD 最高）:
```json
// ccr config.json
"background": "groq,llama-3.1-8b-instant",
```

**選項 B** `qwen3-32b`（60 RPM — RPM 最高）:
```json
"background": "groq,qwen3-32b",
```

#### 7.3 在 ccr config 保留直連 provider 備用

目前 ccr 把 Router 全指向 `litellm,*`。建議保留直連作為**手動備選**:

```json
"Router": {
    "default": "litellm,gemini-3.5-flash",
    "background": "litellm,qwen3-32b",
    "think": "litellm,big-pickle",
    "longContext": "litellm,gemini-3.5-flash",
    "webSearch": "litellm,gemini-3.5-flash"
}
```

如果 LiteLLM 掛掉但 ccr 還在，手動 `ccr /model` 切到直連 provider 應急。

### P1: 建議做（$0-$10/mo）

#### 7.4 Cerebras Developer $10/mo

⚠️ **勘誤**: 免費 context 是 **~65K** 非 8K，soul bundle 可正常放入。Developer tier 主要價值在 **RPM 提升**。

```yaml
# 在 LiteLLM config.yaml 加入 paid tier
# 目前 free tier: context ~65K (長文已可用), RPM 5 (瓶頸)
# Developer $10/mo: RPM 500+ → 真正可用的備用
model_list:
  - model_name: cerebras-glm
    litellm_params:
      model: openai/cerebras
      api_key: os.environ/CEREBRAS_API_KEY
      api_base: https://api.cerebras.ai/v1
      rpm: 500  # Developer tier
```

#### 7.5 建立 Groq 多組織輪換

Groq 的配額是 **per-organization**。多註冊一個 Groq 帳號 = 多 1,000 RPD：

```
架構: LiteLLM 把多把 Groq key 掛同一個 alias「groq-oss」
效果: 1000 RPD → N × 1000 RPD
```

### P2: 可評估（$0）

#### 7.6 引入 GitHub Models 作為額外免費鏈

GitHub 2026 年推出 Azure AI 免費配額。如果可行，可直接在 LiteLLM 加上：

```yaml
model_list:
  - model_name: github-models
    litellm_params:
      model: openai/github-models
      api_key: os.environ/GITHUB_TOKEN
      api_base: https://models.inference.ai/azure.com
```

#### 7.7 追蹤 OpenCode Zen big-pickle 狀態

big-pickle 是 **真 stealth 模型**（maker unknown，非經證實的 GLM-4.6）。加上限免隨時可能結束，建議每週執行一次 health check：

```bash
curl -s https://opencode.ai/zen/v1/models | json_pp
# 檢查 big-pickle 是否還在列表中
```

### P3: 暫緩（$200+/mo）

#### 7.8 Claude Enterprise

Claude Max 已經 cover ~95% 用量。到用量翻 10 倍（~2,500 calls/day）之前不需要升級。

---

## 8. 本機耗用資源總結

| 資源 | 用量 | 備註 |
|------|------|------|
| ccr port :3456 | ~0.5% CPU, ~17MB RAM | Node.js 後台服務 |
| LiteLLM :4000 | ~2% CPU, ~100MB RAM | Python uvicorn，需 KeepAlive |
| claude CLI (when running) | ~4% CPU, ~332MB RAM | 大但每次呼叫時才啟動 |
| news_radar cron/launchd | <0.1% CPU | 每小時跑一次 |
| **總計** | **~6-7% CPU, ~450MB RAM** | 對 M 系列 Mac 可忽略 |

---

## 9. 術語對照表

| 縮寫 | 全名 | 本機用途 |
|------|------|---------|
| ccr | Claude Code Router | Claude CLI → 免費 LLM 的 proxy 路由器 |
| LiteLLM | Lite LLM Gateway | 多 key 輪換 + fallback 的 proxy |
| llm_brain | LLM Brain module | news_radar 內部 LLM 呼叫層 |
| launchd | macOS 常駐服務管理器 | 確保 LiteLLM 開機自啟 + 自動恢復 |
| 429 | HTTP 429 Too Many Requests | API 配額用盡訊號 → 觸發 key 輪換 |
| RPD | Requests Per Day | 每天可發送的請求上限 |
| RPM | Requests Per Minute | 每分鐘可發送的請求上限 |
| TPM | Tokens Per Minute | 每分鐘可處理 token 上限 |
| TPD | Tokens Per Day | 每天可處理 token 上限 |
| soul bundle | 寫作規範 + 角色定義 | ~17KB system prompt，包含 brand/soul/style |

---

## 10. 參考資料

- [ccr GitHub 原始碼](https://github.com/musistudio/claude-code-router)
- [Gemini API Rate Limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Groq Rate Limits](https://console.groq.com/docs/rate-limits)
- [Cerebras API Docs](https://docs.cerebras.ai/)
- [OpenCode.ai Docs](https://opencode.ai/docs)
- [LiteLLM Documentation](https://docs.litellm.ai/)
- [Claude Code CLI Reference](https://code.claude.com/docs/en/cli-reference)
