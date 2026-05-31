# 🏗 路由架構 — CCR × LiteLLM × llm_brain.py

> 本機 Mac（Apple Silicon）上的三層 LLM API 路由設計。

---

## 系統流程圖

```
使用者 handoff
     │
     ├─ claude CLI (手動) ───────────── ccr(:3456) ── LiteLLM(:4000) ── Provider API
     │                                    │
     │                                    │ ANTHROPIC_BASE_URL=http://127.0.0.1:3456
     │                                    │ (從 temp ccr-settings-*.json 注入)
     │
     ├─ news_radar compose (launchd 定時)
     │    │
     │    └─ run_pipeline.py
     │         │
     │         ├─ harvest (RSS/Playwright)
     │         ├─ scorer ── llm_brain.call_for_json() ── 鏈 A fallback
     │         │    │         claude_cli → gemini → opencode → groq → cerebras
     │         │    └─ 如果所有 backend 失敗: return data=None
     │         │
     │         └─ composer ── llm_brain.call_for_json() ── 同上鏈 A
     │              │
     │              └─ publish (Substack API / Facebook / Threads)
     │
     └─ claude CLI (-p mode, 由 news_radar subprocess 呼叫)
          │
          └─ ANTHROPIC_BASE_URL 已是 :3456 → ccr 介入
               │
               └─ ccr Router mode (default) → LiteLLM → Gemini
```

## 關鍵檔案間的呼叫關係

```
claude CLI
  │ settings=ccr-settings-*.json (ANTHROPIC_BASE_URL=http://127.0.0.1:3456)
  ↓
ccr (Node.js proxy at :3456)
  │ config=~/.claude-code-router/config.json
  │ Router: default/background/think/longContext/webSearch → provider
  ↓ (大部分路由指向 litellm,*)
LiteLLM (Python proxy at :4000)
  │ config=~/litellm-gateway/config.yaml
  │ 多 key load-balance + fallback
  ↓
Gemini / Groq / Cerebras / OpenCode API
```

## news_radar 內部的 LLM 呼叫關係

```
run_pipeline.py
  │
  ├─ call_for_json(system, prompt, response_model=NewsScore)
  │    └─ 走 llm_brain.py 的 5 條 fallback 鏈
  │         1. claude_cli → subprocess `claude -p` (Max 主腦)
  │         2. gemini → google-genai SDK (1M context, 備援)
  │         3. opencode → HTTP POST (big-pickle, ~200K)
  │         4. groq → HTTP POST (llama-3.3-70b, 131K)
  │         5. cerebras → HTTP POST (zai-glm-4.7, ~65K)
  │
  └─ call_for_json(system, prompt, response_model=MultiPlatformDraft)
       └─ 同上鏈（但 composer 通常只用到 claude_cli）
```

## 重要設計決策

1. **為什麼不直接走一條鏈**：ccr 和 llm_brain.py 服務不同的場景 — ccr 服務所有 claude CLI 呼叫（含使用者互動和 skill），llm_brain 只服務 news_radar pipeline 的構造化 JSON 產出。

2. **LiteLLM 的核心價值**：多 key 自動輪換。ccr 本身不支援同一 provider 多 key 輪換，但 Gemini 免費 tier 每 key ~20 req/day 的配額需要輪換。LiteLLM 補上這個缺口。

3. **為什麼不把 llm_brain 的邏輯搬進 ccr**：因為 llm_brain 需要 Pydantic schema 驗證（response_model）和 structured output — 這些是 Python 層的邏輯，不是 proxy 層該管的。
