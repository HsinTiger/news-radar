# Cerebras API 免費額度細節

## 本機使用

- Config key: `cerebras`
- 使用模型: `zai-glm-4.7`, `openai/gpt-oss-120b`
- 使用方式: OpenAI-compatible HTTP API (ccr → LiteLLM)

## 免費 Tier 限制

| 指標 | 值 |
|------|-----|
| RPM | **5**（極低 — 註冊後每小時才跑幾次所以還好） |
| TPM | 30,000 |
| TPH | 1,000,000 |
| TPD | 1,000,000 |
| **免費 context** | **~65K tokens** (完整 context 131K 的一半) |
| Developer tier ($10/mo) context | **65K tokens** (與免費相同, 差異在 RPM) |

> ⚠️ **勘誤**: 先前文件標示免費 context 為 8K — 實測為 **~65K**。soul bundle (~17K) 可以正常放入。

## 可用模型（免費）

Cerebras 免費 tier 提供 **2 個模型**:

| Model | 參數量 | Full Context | 免費 Context | 架構 |
|-------|--------|-------------|-------------|------|
| gpt-oss-120b | 120B | 131K | ~65K | Dense |
| zai-glm-4.7 | 355B/32B MoE | 131K | ~64K | MoE |

## Developer Tier ($10/mo)

| 指標 | 免費 | Developer ($10/mo) |
|------|------|-------------------|
| RPM | 5 | **500–1,000** |
| Context | ~65K | **131K (完整)** |
| 模型 | 2 個 | **全部模型** |

> Developer tier 的主要價值是 **RPM 提升 100-200 倍** 和 **完整 131K context**。免費層的 context 已有 ~65K，對 soul bundle (~17K) 已足夠，但 RPM 5 仍是顯著瓶頸。

## 付費模型清單（Developer tier 可存取）

| Model | 參數量 | Context | 說明 |
|-------|--------|---------|------|
| gpt-oss-120b | 120B | 131K | 旗艦模型 |
| zai-glm-4.7 | 355B/32B MoE | 131K | 多專家混合 |
| cerebras-nano | — | 8K | 極輕量 |
| cerebras-pico | — | 8K | 最輕量 |
| cerebras-femto | — | 8K | 最小推理用 |

> 部分命名可能隨 Cerebras 產品更新而變動，請以官方文件為準。

## 當前限制

- ❌ **免費 RPM 5 極低** — 突發多請求時一定撞限
- 🟢 **免費 context ~65K** — 足夠裝 soul bundle (~17K) + prompt (~3-8K)
- 🟢 **zai-glm-4.7**（355B MoE）理論品質很高，context 限制解除後更實用
- **Developer tier $10/mo** 主要解鎖 RPM, 次要解鎖完整 131K context

## 建議

- **如只需長文 context**: 免費層 ~65K 對 soul bundle 已足夠, 不需升級
- **如需更高吞吐**: Developer tier ($10/mo) → 500+ RPM, 解決 RPM 5 瓶頸
- **在 ccr/LiteLLM 中**: Cerebras 目前是 fallback 鏈的最後一環，適合：
  - 短任務 (scorer/classifier) — 但 RPM 5 會拖慢
  - 長文 composer — 免費 context ~65K 夠用，RPM 5 非即時需求可接受

## 參考連結

- [Cerebras API Docs](https://docs.cerebras.ai/)
- [Cerebras Cloud Console](https://cloud.cerebras.ai/)
