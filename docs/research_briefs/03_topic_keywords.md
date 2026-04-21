# Research Brief 03 · Topic Keywords 覆蓋率與邊界 case

## Paste-to-Gemini prompt

---

你是產業分類學家兼中文 NLP 工程師。我有一份分類用的關鍵字清單，需要你做一次
「覆蓋率與邊界 case」審核。

### 研究脈絡

News Radar 有 10 個穩定類別，每類別都有一份 keyword list 做 fast-path 分類：

```
ai_model / ai_agent / ai_application
supply_chain / earnings
tw_stocks / us_stocks
tech_product_launch
policy_geopolitics
other (兜底，不定義 keyword)
```

現有 keyword list 在 `config/topic_keywords.yaml`（我會另外貼給你，或你也可以
向我索取）。特色：**大部分繁體中文、英文只補了基礎幾個詞**。

### 已知問題

1. 今早測試 `"台積電 Q1 法說會：毛利率創新高"` 被分到 `supply_chain` 而非
   `earnings`——因為 `台積電` 在 supply_chain 裡、排序優先。這是該接受的
   editorial choice 還是 bug？
2. SemiAnalysis（英文深度半導體媒體）的貼文命中率似乎偏低——英文覆蓋不足。
3. `ai_agent` vs `ai_application` 邊界模糊（Cursor / Copilot 到底是
   agent 還是 application？）

### 研究目標

1. **2025–2026 年台灣科技/商業圈會出現但我 keyword list 沒覆蓋的新詞**：
   請列 30–50 個建議新增的中英文關鍵字，每個說：
   - 建議歸到哪一類
   - 為什麼（引用 2025 年以後出現這個詞的文章 URL）
   - 與現有關鍵字是否會衝突（同時命中多類）

2. **歧義詞處理建議**：
   - 列 10–15 個容易誤分的詞（例：「台積電」本身、「Meta」、「Apple」、「iPhone」）
   - 建議的 disambiguation 策略（關鍵字組合 / regex / 優先順序）

3. **`ai_agent` vs `ai_application` 的清晰定義**：
   - 用 2025 年業界寫作慣例寫一段 300 字的區分準則
   - 列 10 個產品，明確歸類（Cursor / Copilot / Devin / Claude Code /
     Perplexity / Notion AI / GPT-5 / Gemini Pro / Midjourney / Manus）
   - 講出你分類時用的判準

4. **英文覆蓋率補齊**：對 SemiAnalysis / Stratechery / The Information /
   Calculated Risk / Marginal Revolution 這五個英文媒體，抓每個媒體
   過去 30 天 10 篇代表作，驗證我的 keyword list 會不會命中。**miss 的
   請列出，並建議要加哪個英文詞**。

5. **排除關鍵字建議**（false positive guard）：例如「加州蘋果季」會命中
   `Apple`，但其實是農業新聞。建議 5–10 個「命中以下詞則排除」的 patterns。

### 輸出格式

```markdown
# Topic Keywords 覆蓋率審核 · Deep Research Report

## Section 1: 新增關鍵字建議（分類整理）
### ai_model 新增
- `<關鍵字>` — 出處：<URL>，理由：...
...
### supply_chain 新增
...

## Section 2: 歧義詞與 disambiguation
| 詞 | 現狀 | 建議策略 | 說明 |
|---|---|---|---|
...

## Section 3: ai_agent vs ai_application 區分準則
<300 字準則>
<10 個產品分類表>

## Section 4: 英文媒體覆蓋率審核
### SemiAnalysis
- Miss: <URL> — 標題 "..." — 建議加的英文詞：...
...

## Section 5: 排除關鍵字建議
- 若命中 `Apple` 但同時命中 `<排除詞>` → 不歸 tech_product_launch
...
```

---

## 用完 report 後 Claude 要做的事

1. Section 1 的建議新詞：挑信心高的，diff 更新 `config/topic_keywords.yaml`
2. Section 2 的歧義詞：若要做 disambiguation，擴充 `src/topic_classifier.py`
   的 keyword path 加條件組合邏輯
3. Section 3 的 ai_agent / ai_application 準則：寫進 `src/topic_taxonomy.py`
   的 docstring
4. Section 5 的排除規則：同樣改 classifier keyword path

## 為什麼跑（優先順序 6）
比起 KOL 風格研究或 hashtag 策略，這份是「覆蓋率 audit」——品質改進空間
較小，但該修的 edge case 沒修會持續誤分、污染 back-prop 學習。可以放到後面。
