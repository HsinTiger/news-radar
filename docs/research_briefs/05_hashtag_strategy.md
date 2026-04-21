# Research Brief 05 · Hashtag 策略

## Paste-to-Gemini prompt

---

你是社群媒體演算法研究員兼繁體中文內容策略師。請幫我做一份深度調研，對象是
**台灣科技/商業類別在 FB / IG / Threads 的 hashtag 最佳實踐**。

### 研究脈絡

News Radar 是一個自動化新聞評論帳號，每則 draft 會展開成三個平台變體，每個
平台各帶一組 hashtag。目前 hashtag 生成邏輯寫在 `src/composer.py`：

```
FB：3–5 個，中英文皆可，前兩個由 LLM 生成、後面 2–3 個塞 appendix 固定 tag
IG：10–15 個，LLM 生成 6–8 個、appendix 固定 4–7 個
Threads：0–3 個，只用最精準的 1–2 個，或不帶
```

我目前沒有數據支持這些數量選擇——都是 2024 年初從幾篇 blog post 看來的經驗
法則。想知道 2025–2026 是否有實證更新。

### 研究目標

1. **2025–2026 年 FB / IG / Threads 各自的 hashtag 演算法最新狀態**：
   - Meta 在過去 12 個月有沒有公開或洩漏 hashtag weighting 邏輯？
   - 社群行銷行業有沒有 A/B test 大樣本報告（HubSpot / Hootsuite / Later /
     Buffer 年度報告）？
   - 列 5–8 篇 2025–2026 年的來源，提煉「每平台 hashtag 數量 × reach」
     的具體數字

2. **台灣科技/商業受眾實際被 follow 的 hashtag**：
   - Threads 台灣區 tech/startup/stock 最熱 hashtag top 30
   - IG 同上 top 30
   - FB 上 hashtag 效果有限（FB 演算法對 hashtag 權重低），但仍列 top 15
   - 對每個 hashtag 給 2025 年粗估使用次數

3. **中文 vs 英文 hashtag 的選擇**：
   - 同一話題用 `#半導體` vs `#semiconductor`，哪一個 reach 高？有沒有公開
     觀察？
   - 混用會不會被降權？
   - 對台灣中文主流受眾，建議的比例是多少？

4. **「brand hashtag」的必要性**：我的帳號有沒有值得創建一個自己的
   `#news_radar_` 系列 brand hashtag？2025–2026 年業界對小型媒體帳號建立
   brand hashtag 的看法是？

5. **hashtag 的 red flags**（會降權的）：
   - 過度重複使用同一組 hashtag → 被判 spam
   - 過大 hashtag（`#love #instagood`）→ 演算法判不精準
   - 敏感字 hashtag → shadow ban
   - 列 10–15 個紅旗並附來源

### 輸出格式

```markdown
# Hashtag 策略 2026 · Deep Research Report

## Section 1: 演算法狀態
### Facebook
- 2025–2026 觀察：...
### Instagram
...
### Threads
...

## Section 2: 台灣科技/商業熱門 hashtag
### Threads Top 30
| rank | hashtag | 估計使用次數 | 類別 |
|---|---|---|---|
...
### Instagram Top 30
...
### Facebook Top 15
...

## Section 3: 中英 hashtag 選擇建議
- 實證數字 + 建議比例

## Section 4: Brand hashtag 建議
...

## Section 5: Hashtag red flags
| red flag | 出處 | 降權幅度估計 |
...

## Section 6: 針對 News Radar 的具體建議
- 我目前的 config 應該怎麼改？給具體數量範圍 + 固定 tag 清單
```

---

## 用完 report 後 Claude 要做的事

1. 根據 Section 2 的 top hashtag 更新三個 platform appendix:
   - `config/platform_appendix_fb.md`
   - `config/platform_appendix_ig.md`
   - `config/platform_appendix_threads.md`
2. 根據 Section 1 的每平台數量共識，微調 composer 的 hashtag 數量 target
3. 根據 Section 5 的紅旗擴充 `src/content_quality_guard.py` 的 hashtag 檢查

## 為什麼跑（優先順序 3）
Hashtag 改動 effort 最小（只改 appendix markdown 和幾個 config 數字），但
對 IG/Threads 的 discoverability 影響直接。一份 10 分鐘的 Gemini 調研可以
救回來過去半年的猜測。
