# Gemini API 免費額度細節

## 本機使用

- Config keys: `gemini`（第一把 key）, `gemini2`（第二把 key）
- 主力模型: `gemini-2.5-flash`（1M context）→ 遷移中至 `gemini-3.5-flash`
- 使用方式: google-genai SDK (Python) + ccr proxy → LiteLLM (production)

## 免費 Tier 限制

| 指標 | 值 |
|------|-----|
| 免費 tokens | 輸入輸出皆免費 |
| 每天請求數 | **~20 req/day per project per model**（實測值, 非官方宣稱的 1,500） |
| RPM | 未公開（視用量動態調整） |
| Context window | 最高 1M tokens |
| Search grounding | 500 RPD free |
| Maps grounding | 500 RPD free |

## 免費模型 (14 個)

| Model | Context | 狀態 | 建議 |
|-------|---------|------|------|
| gemini-1.5-flash | 1M | 🟢 仍可用 | 舊世代, 性能較低 |
| gemini-1.5-pro | >200K | 🟢 仍可用 | 舊世代, 已非主力 |
| gemini-2.0-flash | 1M | 🔴 **DEPRECATED** — shutdown 2026-06-01 | 額度已歸零, 請移除 config |
| gemini-2.0-flash-lite | 1M | 🔴 **DEPRECATED** — shutdown 2026-06-01 | 2026-04 起免費額度歸零 |
| gemini-2.5-flash | 1M | 🟡 現行主力 — deprecation 2026-10 | 下半年請遷移至 3.5-flash |
| gemini-2.5-flash-lite | 1M | 🟡 現行 — deprecation 2026-10 | 輕量低成本, 短期可繼續用 |
| gemini-2.5-pro | >200K | 🟡 現行 — deprecation 2026-10 | 高品質, 低速率, 暫無直接替代 |
| gemini-3.0-flash | 1M | 🟢 現行 | 穩定可用, 過渡世代 |
| gemini-3.0-flash-lite | 1M | 🟢 現行 | 輕量版 |
| gemini-3.1-flash | 1M | 🟢 現行 | 2026 年初發佈, 良好的日常模型 |
| gemini-3.1-flash-lite | 1M | 🟢 現行 | 低成本選項, scorer 短任務適用 |
| gemini-3.2-flash | 1M | 🟢 現行 | 2026 中發佈 |
| gemini-3.2-flash-lite | 1M | 🟢 現行 | 輕量版, 低成本 |
| **gemini-3.5-flash** | 1M | 🌟 **推薦** | 最新世代, 質量最佳, 建議遷移目標 |

> 部分 1.5 世代模型可能已不再出現在官方文件但 API 仍可用。以上 14 個模型涵蓋所有已知可在免費 tier 存取的 Gemini 模型。

## 優化建議

- **每把 key 一個 Google Cloud project** → 多 key = 多配額
- 本機已有 **兩把 key**（`GEMINI_API_KEY` + `GEMINI_API_KEY_2`）
- 建議至少 **3-4 把 key**，在 `~/news_radar/.env` 用逗號分隔多把 key
- `_try_gemini()` 會在第一把撞 429/RESOURCE_EXHAUSTED 時自動換下一把

### 遷移路徑

1. **立即**: 從 config 移除 `gemini-2.0-flash` / `gemini-2.0-flash-lite`（已 shutdown）
2. **2026-10 前**: `gemini-2.5-flash` → `gemini-3.5-flash`
3. **短任務**: 不該吃 Gemini quota → 優先走 Groq `llama-3.1-8b-instant`（14,400 RPD）
4. **高品質需求**: `gemini-2.5-pro` 暫無直接替代, 可等到 deprecation 前再評估

## 金鑰策略詳解

Gemini 的免費配額是 **per-Google-Cloud-project 計算**:

| 資源 | 每 project 免費量 |
|------|-------------------|
| gemini-2.5-flash requests | ~20/day |
| gemini-3.5-flash requests | ~20/day |
| Search grounding | 500 RPD |
| Maps grounding | 500 RPD |

LiteLLM 自動輪換 + 撞 429 換 key 的設定:

```yaml
# ~/litellm-gateway/config.yaml
model_list:
  - model_name: gemini-flash
    litellm_params:
      model: gemini/gemini-3.5-flash
      api_key: os.environ/GEMINI_API_KEY_1
  - model_name: gemini-flash
    litellm_params:
      model: gemini/gemini-3.5-flash
      api_key: os.environ/GEMINI_API_KEY_2
```

## 參考連結

- [Gemini API Rate Limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Gemini Model Variants](https://ai.google.dev/gemini-api/docs/models)
