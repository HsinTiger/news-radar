# Groq API 免費額度細節

## 本機使用

- Config key: `groq`
- 使用模型: `llama-3.3-70b-versatile`（default）, `openai/gpt-oss-120b`（次要）
- 使用方式: OpenAI-compatible HTTP API (ccr → LiteLLM)

## 免費 Tier 限制

| 指標 | 值 |
|------|-----|
| 費用 | **$0**（不需信用卡） |
| 可用模型 | **16 個**（含 text / TTS / STT） |
| 配額計算 | Per-organization |
| 多組織輪換 | 每個 Groq 帳號 = 獨立的 RPD/RPM 配額 |

## 全部 16 個免費模型配額

| Model | RPM | RPD | TPM | TPD | Context | 類型 |
|-------|-----|-----|-----|-----|---------|------|
| **llama-4-scout-17b** | 30 | 1,000 | 30K | 500K | 131K | text |
| **llama-4-maverick-17b-128k** | 30 | 1,000 | 30K | 500K | 128K | text |
| **llama-3.3-70b-versatile** | 30 | 1,000 | 12K | 100K | 131K | text |
| **llama-3.1-8b-instant** | 30 | **14,400** | 6K | 500K | 131K | text |
| **qwen3-32b** | **60** | 1,000 | 6K | 500K | 131K | text |
| **openai/gpt-oss-120b** | 30 | 1,000 | 8K | 200K | 131K | text |
| **groq/compound** | 30 | 250 | **70K** | 500K | — | text |
| **groq/compound-mini** | 30 | 250 | **70K** | 500K | — | text |
| deepseek-r1-distill-llama-70b | 30 | 1,000 | 12K | 500K | 131K | text |
| deepseek-r1-distill-qwen-32b | 30 | 1,000 | 12K | 500K | 131K | text |
| gemma-2-9b-it | 30 | 1,000 | 15K | 500K | 8K | text |
| gemma-2-27b-it | 30 | 1,000 | 15K | 500K | 8K | text |
| llama-3.2-11b-vision-preview | 30 | 1,000 | 15K | 500K | 128K | vision |
| llama-3.2-90b-vision-preview | 30 | 1,000 | 15K | 500K | 128K | vision |
| whisper-large-v3 | — | — | — | — | — | STT |
| whisper-large-v3-turbo | — | — | — | — | — | STT |

> whisper 系列為語音轉文字 (STT)，配額獨立計算。

## 本機主要使用模型的詳細限制

| Model | RPM | RPD | TPM | TPD | Context | 用途 |
|-------|-----|-----|-----|-----|---------|------|
| llama-3.3-70b-versatile | 30 | 1,000 | 12K | 100K | 131K | default mode 主力 |
| openai/gpt-oss-120b | 30 | 1,000 | 8K | 200K | 131K | 次要、短任務備用 |
| llama-3.1-8b-instant | 30 | **14,400** | 6K | 500K | 131K | 批量短任務 (scorer) |
| qwen3-32b | **60** | 1,000 | 6K | 500K | 131K | **最高 RPM**, scorer 神器 |
| llama-4-scout-17b | 30 | 1,000 | 30K | 500K | 131K | 稍長任務, 高 TPM |

## 已移除模型

以下模型曾存在於 Groq 免費 tier 但已移除或停止支援:

| Model | 狀態 |
|-------|------|
| Mixtral-8x7b | 🔴 已移除 |
| openai/gpt-4o-scaled | 🔴 已移除 (短命實驗) |
| LLaMA 3.2 1B/3B | 🔴 已移除 |

## 優化建議

1. **background mode 建議改 `llama-3.1-8b-instant`**: 14,400 RPD 是 llama-3.3-70b 的 14 倍, scorer 這類短任務完全勝任
2. **scorer 專用 `qwen3-32b`**: 60 RPM 是其他模型的 2 倍, 高吞吐場景首選
3. **多組織輪換**: 用不同 email 註冊多個 Groq 帳號, LiteLLM 可掛同 alias 自動 load-balance
4. **Llama-4-Scout 適合中等長度任務**: 30K TPM 是 llama-3.3-70b 的 2.5 倍
5. **Groq Compound 適合一次性大量 token**: 70K TPM 但 RPD 僅 250, 適合批次處理

## 參考連結

- [Groq Rate Limits](https://console.groq.com/docs/rate-limits)
- [Groq Models](https://console.groq.com/docs/models)
