# Research Brief 06 · 發文時段 & 節奏研究

## Paste-to-Gemini prompt

---

你是社群媒體數據分析師。請幫我做一份針對**台灣科技/商業類別受眾**的最佳
發文時段研究。

### 研究脈絡

News Radar 每小時由 cloud 端 `run_publish_queue.py` 檢查佇列，目前 cadence 規則：

```
- min_interval: 1 小時（同一平台）
- max_silence: 2 小時（fallback：即使沒有新稿也硬發一則舊的）
- 不區分時段：24/7 都可能發
```

這個策略的盲點：**台灣受眾睡覺時間（凌晨 2–6 點）發的貼文，reach 幾乎零，
還會影響當天平均 engagement 率**。

### 研究目標

1. **台灣 FB / IG / Threads 科技/商業類帳號的最佳發文時段**（2025–2026）：
   - Meta for Business、Hootsuite、Later、Buffer、Sprout Social 的最近
     報告有沒有 **台灣地區** 或 **GMT+8 泛東亞** 的時段數據？
   - 給出每平台 top 3 時段（含信心估計）
   - 如果找不到台灣專屬，列日本 / 韓國 / 新加坡 的作為替代

2. **主題類別 × 時段的交互作用**：
   - 科技新聞最佳時段 vs 股市新聞最佳時段有差嗎？
   - 例：台股新聞應該開盤前（08:00–09:00）還是收盤後（13:30–14:30）發？
     國際美股新聞應該前一晚還是隔天早上？

3. **頻率上限的實證**：
   - 一天發幾次開始邊際報酬為負？列每平台的「甜蜜點」
   - 同平台兩篇間隔 1 小時 vs 2 小時 vs 4 小時，engagement 差多少？
   - 連續發太密會觸發 Meta shadow ban 嗎？

4. **「假日 vs 平日」的差異**：
   - 台灣受眾週末的科技/商業內容消費模式？
   - 週一早上（Blue Monday 效應）值得增加 reach-driving post 嗎？
   - 大型事件（法說會季、CES、WWDC 等）期間是否該打破 cadence？

5. **News Radar 的具體建議**：基於上面數據，給我一個 7 × 24 小時的時段權重
   表（168 格），每格一個 0–1 的 score，告訴我「該小時發文的期望 engagement
   乘數」。格式：`"Mon_09": 1.2, "Mon_10": 1.0, ...`。
   - 以台北時間為準
   - 0 = 強制不發（深夜）
   - 1 = 基準
   - >1 = 黃金時段
   - 科技 vs 台股 vs 國際要能分開看，或者至少給不同類別的調整建議

### 輸出格式

```markdown
# 發文時段 2026 · Deep Research Report

## Section 1: 各平台最佳時段
### Facebook 台灣地區
- Top 1: <時段> — 信心：high/mid/low — 出處：...
### Instagram
...
### Threads
...

## Section 2: 主題 × 時段
| 主題 | 最佳時段 | 次佳 | 避開 |
|---|---|---|---|
| 台股 | ... | ... | ... |
...

## Section 3: 頻率甜蜜點
- FB: 一天 X 篇最佳、同平台間隔 Y 小時
- IG: ...
- Threads: ...

## Section 4: 平日 vs 假日
...

## Section 5: 7×24 時段權重表（台北時區）
```yaml
# 基礎權重表
Mon_00: 0.0
Mon_01: 0.0
...
Sun_23: 0.0
```

## Section 6: 主題特化建議
- tw_stocks 在 `Mon_08` 加 +0.3
- ai_model 在 `Wed_22` 加 +0.2（英文時區讀者）
...

## Section 7: 引用來源
...
```

---

## 用完 report 後 Claude 要做的事

1. Section 5 的權重表 → 寫進 `config/cadence_timing.yaml`
2. 修改 `run_publish_queue.py`：load 時段權重 → 當前時段 < 0.3 時跳過本輪
   （除非 stale 兜底）
3. Section 3 的間隔建議 → 調整 `config.yaml` 的 `min_interval_hours`

## 為什麼跑（優先順序 2）
Effort 小（一份 cadence yaml + 50 行程式碼改動），但效果立竿見影。
過去可能有 30–40% 的 post 發在垃圾時段，這調整後直接回收。
