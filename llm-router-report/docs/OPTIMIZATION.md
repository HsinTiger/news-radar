# 🚀 優化方針 — CCR + 免費 LLM API 路由系統

> **本文件列出從 $0 到 $200/mo 的優化方案，依優先級排序。**
> **每條優化都有「預期效果」和「實作方式」。**

---

## P0: 立刻做（$0）

### ① 多申請 2-3 把 Gemini API Key

**為什麼**: 目前 Gemini 免費 tier 每把 key 每天只能 ~20 requests。兩把 key ~40/天。在 Claude Max 正常時夠用，但如果 Max 掛了，Gemini 是第一個備援 — 40/天對 ~250/天需求來說完全不夠。

⚠️ **注意**: `gemini-2.0-flash` / `gemini-2.0-flash-lite` 已於 2026-06-01 shutdown。config 中應移除這兩個模型，改用 `gemini-3.5-flash`（推薦）或 `gemini-3.1-flash-lite`（低成本選項）。

**方法**:

1. 用 2-3 個不同 Google 帳號登入 [Google AI Studio](https://aistudio.google.com/)
2. 每個帳號建立一個 Google Cloud project → 啟用 Generative Language API
3. 每個 project 產生一組 API Key
4. 加入 `~/news_radar/.env`：

```bash
GEMINI_API_KEY="AIza...,AIza...,AIza..." # 逗號分隔多把
```

5. 也可以在 LiteLLM config 用同樣方式管理：

```yaml
# ~/litellm-gateway/config.yaml
model_list:
  - model_name: gemini-flash
    litellm_params:
      model: gemini/gemini-2.5-flash
      api_key: os.environ/GEMINI_API_KEY_1
  - model_name: gemini-flash
    litellm_params:
      model: gemini/gemini-2.5-flash
      api_key: os.environ/GEMINI_API_KEY_2
  - model_name: gemini-flash
    litellm_params:
      model: gemini/gemini-2.5-flash
      api_key: os.environ/GEMINI_API_KEY_3
# LiteLLM 會自動 load-balance + 撞 429 換下一把
```

**成本**: $0  
**預期效果**: Gemini 可用配額從 ~40/day → ~80-100/day  
**難度**: ⭐ (5分鐘搞定)

---

### ② 優化 ccr background mode — 改用 llama-3.1-8b-instant

**為什麼**: 目前 background mode 走 `groq,llama-3.3-70b-versatile`（1,000 RPD）。scorer 這種短任務用 `llama-3.1-8b-instant`（14,400 RPD）**綽綽有餘**，且 RPD 高 14 倍。

另外 **qwen3-32b** 提供 60 RPM（其他 Groq 模型的 2 倍），適合高吞吐短任務。若需要更高即時吞吐（如突發大量 scorer 請求），可考慮將 qwen3-32b 設為 background mode 首選。

**方法**:

```json
// ~/.claude-code-router/config.json
"Router": {
    "background": "groq,llama-3.1-8b-instant",
    // ...其他不變
}
```

或者透過 LiteLLM 做更細的控制（把不同任務分到不同 Groq model）：

```yaml
router_settings:
  routing_strategy: usage-based  # 根據用量自動分配
  fallbacks: [
    { "gemini-flash": ["big-pickle", "groq-oss", "cerebras-glm"] }
  ]
```

**成本**: $0  
**預期效果**: background（scorer/classifier 等短任務）RPD 從 1,000 → 14,400  
**難度**: ⭐ (30秒編輯 config)

---

### ③ 建立 ccr + LiteLLM 互相 cover 的監控

**為什麼**: 如果 LiteLLM 掛掉，ccr 整個路由都指向 `litellm,*` → 全部失敗。ccr 本身不會自動退到直連 provider。

**方法**:

建立一個 cron job 每 5 分鐘檢查 LiteLLM 健康並修復：

```bash
# ~/news_radar/llm-router-report/scripts/health-check.sh
#!/bin/bash
# 檢查 LiteLLM 是否活著
if ! curl -sf http://127.0.0.1:4000/health/readiness > /dev/null 2>&1; then
    echo "[$(date)] LiteLLM down — 嘗試重啟..."
    launchctl start com.hsin.litellm-gateway
    sleep 3
    if curl -sf http://127.0.0.1:4000/health/readiness > /dev/null 2>&1; then
        echo "[$(date)] ✅ LiteLLM 已恢復"
    else
        echo "[$(date)] ❌ LiteLLM 無法恢復"
    fi
fi
```

**成本**: $0  
**預期效果**: LiteLLM 掛掉後自動修復，不需手動介入  
**難度**: ⭐⭐ (5分鐘)

---

## P1: 建議做（$0-$10/mo）

### ④ Cerebras Developer $10/mo

**為什麼**: 勘誤: Cerebras 免費 context 是 **~65K**（非先前認知的 8K），soul bundle (~17K) 可以正常放入。Developer tier ($10/mo) 主要解鎖 **500–1,000 RPM** 和完整 **131K context**，讓這個平台從「能用」變成「好用的備用」。免費層 context 已足夠，但 RPM 5 仍是瓶頸。

**方法**:

```bash
# 1. 去 https://cloud.cerebras.ai/ 註冊 Developer 方案
# 2. 取得新的 API key（paid tier key 跟 free key 不同）
# 3. 更新 LiteLLM config
```

```yaml
# ~/litellm-gateway/config.yaml 加入 paid tier
model_list:
  - model_name: cerebras-glm
    litellm_params:
      model: openai/cerebras
      api_key: os.environ/CEREBRAS_DEV_KEY  # paid tier 的 key
      api_base: https://api.cerebras.ai/v1
      rpm: 500
```

**成本**: $10/月  
**預期效果**: Cerebras 從「不能用」變成「可靠的長文備用」（65K context）  
**難度**: ⭐⭐ (10分鐘設定)

---

### ⑤ 建立 Groq 多組織輪換

**為什麼**: Groq 的 rate limits 是 per-organization。一個組織 = 1,000 RPD（llama-3.3-70b）。兩個組織 = 2,000 RPD。

**方法**:

1. 用另一個 email 註冊第二個 Groq 帳號
2. 取得第二把 API key
3. 在 LiteLLM 把兩把掛同一個 alias：

```yaml
# ~/litellm-gateway/config.yaml
model_list:
  - model_name: groq-oss
    litellm_params:
      model: openai/groq
      api_key: os.environ/GROQ_API_KEY_1
      api_base: https://api.groq.com/openai/v1
  - model_name: groq-oss
    litellm_params:
      model: openai/groq
      api_key: os.environ/GROQ_API_KEY_2
      api_base: https://api.groq.com/openai/v1
# LiteLLM 自動 load-balance → 撞限換 key
```

**成本**: $0（需多一個 email 帳號）  
**預期效果**: Groq 對特定模型的可用配額加倍  
**難度**: ⭐ (5分鐘)

---

### ⑥ 監控 big-pickle 是否轉付費

**為什麼**: OpenCode Zen 的 `big-pickle` 是 **真正的 stealth 模型**（maker unknown，先前推測為 GLM-4.6 未經證實）。它仍是「限免」— 隨時可能轉付費或關閉。如果它消失了，Claude + Gemini 同時掛掉時長文 composer 就沒有替代了。

**方法**: 建立健康檢查腳本，定時檢查 big-pickle 是否還在：

```bash
# ~/news_radar/llm-router-report/scripts/check-big-pickle.sh
#!/bin/bash
RESULT=$(curl -s https://opencode.ai/zen/v1/models | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    models = data.get('data', [])
    if any(m['id'] == 'big-pickle' for m in models if isinstance(m, dict)):
        print('available')
    else:
        print('missing')
except:
    print('error')
")
echo "[$(date)] big-pickle: $RESULT"
```

建議加入 launchd 每週一次，結果發到 log。

**成本**: $0  
**預期效果**: 在 big-pickle 消失前預警，有時間準備替代方案  
**難度**: ⭐

---

## P2: 可評估（$0-$5/mo）

### ⑦ 引入 GitHub Models 作為額外免費鏈

**為什麼**: GitHub 在 2026 年初推出 GitHub Models 的免費配額（透過 Azure AI），支援 GPT-4o-mini / Llama 3 / Mistral 等模型。可作為 Groq 的平行備用。

**方法**:

```yaml
# ~/litellm-gateway/config.yaml 加入
model_list:
  - model_name: github-models
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/GITHUB_TOKEN
      api_base: https://models.inference.ai.azure.com
```

然後在 fallback 鏈加入：

```yaml
router_settings:
  fallbacks: [
    { "gemini-flash": ["big-pickle", "groq-oss", "github-models", "cerebras-glm"] }
  ]
```

**成本**: $0（需 GitHub 帳號 + Azure free subscription）  
**預期效果**: 多一條免費的 4o-mini 作為額外備用  
**難度**: ⭐⭐⭐（需先確認 Azure 免費配額是否還有）

---

### ⑧ 把 scorer 短任務全部改用 Groq

**為什麼**: 目前 scorer（評分）也是走 `call_for_json` 預設鏈（claude_cli → gemini → ...）。但 scorer 的任務很短（一篇文評幾個分數），不需要 Claude 的強大能力。改用 Groq 的 llama-3.1-8b-instant 可以省下 Claude Max 的額度。

**方法**:

```python
# ~/news_radar/src/scorer.py
# 改成只走 groq
result = await call_for_json(
    system=...,
    prompt=...,
    response_model=...,
    backends=("groq",),  # 只走 Groq
)
```

**成本**: $0（Groq 免費）  
**預期效果**: Claude Max 用量減少 ~20-30%（scorer 呼叫佔比）  
**難度**: ⭐⭐（改一行 code）

---

## P3: 暫緩（$200+/mo）

### ⑨ Claude Enterprise 升級

**為什麼**: Claude Max 已 cover ~95% 用量。Enterprise 的優勢是更高的 rate limit 和專屬支援，但本機用量 ~250 calls/day 完全不需要。

**何時需要**: 當每日呼叫量超過 2,500+（目前 10 倍）時考慮。

---

## 優化總結

| 優先級 | 方案 | 成本 | 時間 | 預期效果 |
|--------|------|------|------|---------|
| P0 | 多申請 2-3 把 Gemini Key | $0 | 5 min | +40-60 Gemini calls/day |
| P0 | background → llama-3.1-8b 或 qwen3-32b | $0 | 30 sec | 背景任務 RPD 14x ↑ 或 RPM 2x ↑ |
| P0 | LiteLLM health monitor | $0 | 5 min | 預防 LiteLLM 掛掉 |
| P0 | **移除 gemini-2.0 系列** | $0 | 1 min | 已 shutdown, 改用 3.5-flash |
| P1 | Cerebras Developer ($10/mo 解鎖 RPM) | $10/mo | 10 min | 5→500 RPM, 免費 ~65K 已夠用 |
| P1 | Groq 多組織輪換 | $0 | 5 min | Groq RPD 翻倍 |
| P1 | big-pickle 監控 (stealth, maker unknown) | $0 | 5 min | 預警限免消失 |
| P2 | GitHub Models | $0 | 30 min | 多一條免費鏈 |
| P2 | Scorer 走 Groq | $0 | 5 min | Max 用量 -20~30% |
| P3 | Claude Enterprise | $200+/mo | — | 用量 10x 前不需要 |
