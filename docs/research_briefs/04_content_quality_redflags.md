# Research Brief 04 · 內容品質紅旗擴充

## Paste-to-Gemini prompt

---

你是同時熟悉 LLM hallucination 研究與社群媒體內容政策的 trust & safety 專家。
請幫我做一份「內容品質紅旗清單」的擴充調研。

### 研究脈絡

News Radar 有一個 pure function `src/content_quality_guard.py`，會在 composer
產出 draft 之後、publisher 發文之前兩個時點各檢查一次。任何一條紅旗命中就
丟回 composer 重寫、重寫失敗就 skip 整則。目前檢查的項目：

```
1. word_count 是否符合平台下限（FB ≥ 300，IG ≥ 120，Threads ≥ 180）
2. hashtag 數量是否在平台範圍（FB 3–5，IG 10–15，Threads 0–3）
3. 是否含未填入的範本字串（"{{", "TODO", "TBA", "[範本]"）
4. 是否含 AI 常見口水詞（"身處", "在...的時代裡", "不容忽視", "值得我們深思"）
5. 是否含對標 KOL 名字（避免假冒人設）
6. 是否含純造假統計數字（無出處、無時間錨點但寫「X% 成長」）
7. 是否出現 Claude / Gemini / ChatGPT 等工具名（除非原文引用）
```

### 研究目標

1. **2024–2026 年 LLM hallucination 的最新徵兆**：
   - 最近 12 個月有哪些論文或公開報告討論 LLM 在**繁體中文財經/科技**情境
     下特別容易 hallucinate 的模式？請列 5 篇最新、最相關的。
   - 有哪些 hallucination patterns 是 2023–2024 年沒被討論、2025–2026 才
     浮現的？（例如 multimodal hallucination、code 虛構 API、agent
     task 捏造）

2. **可以轉成 detection regex / string-contains 的紅旗樣板**：
   根據上面文獻，列 15–25 個**具體字串 pattern** 可以加進 guard。每個 pattern：
   - 具體字串或 regex（繁體中文 / 英文都要涵蓋）
   - 為什麼是 hallucination 徵兆（引用文獻）
   - False positive 機率估計（high / mid / low）
   - 建議動作（reject / warn / rewrite）

3. **新興 clickbait / engagement farming pattern**：
   - 2025–2026 年社群媒體圈有沒有被平台演算法打壓的新 clickbait 樣板？
     （比如 "你絕對想不到..."、"XX 背後的真相..."、過度 rhetorical question
     疊字）
   - 列 10 個 pattern 可以加進我的紅旗清單

4. **AI-written detection 的反向觀察**：哪些短語/句法結構**越來越被 Google**
   或**LinkedIn 的 AI-content detector** 標為「由 AI 生成」？避免我們寫出
   被平台自動降權的風格。

5. **Meta 平台（FB / IG / Threads）2025–2026 年內容政策更新重點**：
   - Meta 有沒有在過去 12 個月公布新的「不鼓勵內容類型」？例如嚴打
     政治分享、反對 engagement bait、金融內容特殊規範？
   - 請列 5–10 條，並各給 Meta 官方 source URL

### 輸出格式

```markdown
# 內容品質紅旗擴充 · Deep Research Report

## Section 1: 2024–2026 年 LLM hallucination 文獻
- 5 篇 paper + 關鍵發現...

## Section 2: 新紅旗 pattern（15–25 個）
### Pattern A: <名稱>
- Regex / 字串：...
- 徵兆類型：hallucination / clickbait / AI-tone / policy-violation
- 出處：...
- FP 機率：...
- 建議動作：...
...

## Section 3: 新興 clickbait pattern（10 個）
...

## Section 4: AI-detection 避雷句法
...

## Section 5: Meta 平台 2025–2026 政策更新
...

## Section 6: 引用來源
...
```

---

## 用完 report 後 Claude 要做的事

1. 把 Section 2 + 3 的新 pattern 編成 `config/quality_redflags_v2.yaml`，
   讓 `content_quality_guard.py` 動態載入（不硬編碼）
2. Section 5 的政策更新 → 寫進 `docs/BACKLOG.md` 當監控條目
3. 每條紅旗加進 `tests/unit/test_content_quality_guard.py`

## 為什麼跑（優先順序 4）
Guard 擴充是長期保險——會防止 30 天後某個 hallucination 模式出現、我們要手動
逐一下架。比起 composer / cadence 這種直接影響 engagement 的，效果是「不
會崩」而非「更好」，但重要。
