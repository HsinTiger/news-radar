# Research Brief 02 · 選題信心啟發式補強

## Paste-to-Gemini prompt

---

你是新聞摘要與內容策展研究者。請幫我做一份深度調研，目的是強化一個
自動化新聞選題器的「信心啟發式」。

### 研究脈絡

這是一個針對**台灣科技/商業讀者**的自動新聞評論帳號。每個 news_item 進來後
會被 scorer 打一個 0.0–1.0 的 confidence_score，代表「這則值得寫的可能性」。
目前 scorer 的 feature 清單（都是 deterministic，零 LLM）：

```
- word_count（清洗後字數）
- feed_tier（primary / secondary）
- has_numerical_anchor（含金額 / 百分比 / 日期）
- has_named_entity_density（每 100 字的實體名稱數）
- is_recency_fresh（< 24h for primary, < 48h for secondary）
- keyword_match_count（命中 config/filters.keywords 的次數）
- duplicate_with_recent_30_titles（過去 30 則是否有語意重複）
```

LLM scorer 在 deterministic feature 之外，會額外 call LLM 給：
- `framework_fit`（0–1，新聞結構是否適合寫 hook / framework / insight 三段）
- `viewer_relevance`（0–1，對台灣科技圈讀者的直接相關度）
- `contrarian_potential`（0–1，有沒有 angle 跟主流敘事反著走）

### 研究目標

1. **新聞價值判斷的既有文獻 / 業界經驗**：
   - 華爾街日報、Axios、The Information 這類商業新聞媒體的編輯手冊
     （editorial guidelines）有被公開嗎？它們怎麼判斷一則新聞該不該做？
   - 2023–2025 年有沒有學術論文討論 "newsworthiness model" 可以
     作 signal？請列 3–5 篇重要 paper。
   - 爆紅商業 newsletter（Stratechery、The Information、Axios Pro Rata）
     在選題時公開講過哪些啟發式？

2. **超出現有 feature 的新訊號建議**：請根據上面找到的文獻與業界經驗，提
   出 5–10 個**現在 scorer 還沒有**但值得加的 feature。每個 feature 要說：
   - 定義（怎麼從 news_item 的欄位計算）
   - 預期與 engagement 的相關性方向（+ / −）
   - 實作難度（low / mid / high）
   - 是否需要 LLM 才能估，還是有純規則可算

3. **反例案例庫**：找 5 則「表面看起來 newsworthy 但實際 engagement 低」
   的經典失敗案例（以台灣市場為主）。分析：
   - 為什麼表面高分？（含數字、知名公司、時效性？）
   - 為什麼實際低分？（太 niche / 已被搶先報 / 讀者不關心？）
   - 現有 scorer 的哪個 feature 應該抓到但沒抓到？

4. **weighted_score 的 topic_weight 應該如何校準**：目前我的 10 類主題權重
   是拍腦袋定的（ai_model=1.7, supply_chain=1.4, tw_stocks=1.0...）。
   - 2024–2026 年台灣科技圈 engagement 數據有公開嗎？（例如 INSIDE、
     數位時代、科技新報的主題流量分佈）
   - 這些分佈能否支持或反駁我的權重？

### 輸出格式

```markdown
# Scorer 啟發式補強 · Deep Research Report

## Section 1: 新聞價值判斷文獻回顧
- 引用 3–5 篇 paper + 2–3 份編輯手冊...

## Section 2: 新 feature 建議（5–10 個）
### Feature A: <名稱>
- 定義：...
- 預期相關性：...
- 實作：low / mid / high / LLM-required
- 出處：...
...

## Section 3: 反例案例庫（5 則）
### 案例 1: <title + link>
- 表面高分因：...
- 實際低分因：...
- 現有 feature 缺口：...

## Section 4: 主題權重校準建議
- 若數據支持，列出建議的權重修正方向與幅度...

## Section 5: 引用來源
...
```

### 注意事項
- 請以**2024–2026 年**的觀察為主，2020–2023 的文獻只在沒有近期資料時補充。
- 盡量引用有流量數據的來源；不接受「我覺得」類型的主觀論述。
- 繁體中文。

---

## 用完 report 後 Claude 要做的事

1. 把 Section 2 的可行 feature 中「實作難度 low + 不需 LLM」的優先加進
   `src/scorer.py`，寫成獨立 heuristic function + unit test
2. 把 Section 3 的反例寫進 `tests/integration/test_scorer_false_positives.py`
   當回歸護欄
3. 若 Section 4 建議權重修正 + 給足證據，update `src/db.py` 的 `_seed_topic_weights`

## 為什麼值得跑（優先順序 2）
scorer 現在每天的判斷是 pipeline 的第一道漏斗。多抓對一則好的、少選到一則
clickbait，整日 engagement 都會改變。值得投入一次認真調研。
