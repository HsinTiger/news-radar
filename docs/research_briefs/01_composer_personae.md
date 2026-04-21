# Research Brief 01 · 強化三平台寫手人格

## Paste-to-Gemini prompt（從下一行開始複製）

---

你是資深社群媒體策略師兼華語科技寫手研究者。請幫我做一份深度調研，對象是
**台灣科技/商業類別在三個平台上的高 engagement 寫作模式**。

### 研究脈絡

我正在經營一個自動化的科技/商業新聞評論帳號，風格取自三位對標 KOL：

- **蕭上農**（INSIDE 創辦人、對科技商業有犀利第一手分析）
- **游庭皓**（台股+國際總經評論，擅長把複雜市場議題講白）
- **IEO**（INSIDE Everywhere Official？或 Index Events Online 類的頻道，請
  查證正確身分。定位為深度科技觀察）

我的帳號同時發 Facebook、Instagram、Threads。每個平台的最佳寫法不同：

- **Facebook**：700–1000 字，完整論述、可上圖可不上，hashtag 3–5 個
- **Instagram**：圖先字後，caption 建議 150–250 字、hashtag 10–15 個
- **Threads**：300–500 字，conversation-first、短段落、hashtag 0–3 個

### 研究目標

請用 Google / Threads 搜尋，幫我找出：

1. **蕭上農 / 游庭皓 / IEO 2025–2026 年代表性貼文** 各 5 則，列出：
   - 平台（FB / IG / Threads / X / Medium）
   - 標題 + 前三句開場
   - 該貼文的 engagement 估計（like / comment / share 概數）
   - **你的分析**：這篇為什麼擊穿？用了什麼修辭結構（hook / framework /
     numerical anchor / contrarian claim / rhetorical question）？

2. **三平台寫作差異的 15 條量化觀察**。例如：
   - 「FB 上 `但是` 這種轉折詞的出現頻率 vs IG」
   - 「Threads 的平均段落長度 vs FB」
   - 「IG 貼文前三秒（caption 第一行）必放的元素類型」
   - 這些觀察要給數字或樣本，不要空泛的「要更口語化」

3. **可以直接塞進 system prompt 的三段 persona**（FB / IG / Threads），
   要求：
   - 長度各 250–400 字
   - 開頭一句用「你是……」定義身分
   - 至少 5 條寫作規則（正反都要：「要這樣」「不要這樣」）
   - 一個「不做清單」列 5 個雷區（從蕭/游/IEO 的反面 case 推）
   - 結尾給一段 80 字 sample output 示範聲音

4. **2026 年寫作風格的新趨勢**（過去 12 個月有沒有出現新打法？例如
   Claude 4.6 / Gemini 3 等工具讓某種 meta-analysis 寫法爆紅？）。列 3–5
   個 trend 並各給 2 個 sample 連結。

### 輸出格式

請用 **繁體中文**，以下面的 Markdown 結構回覆：

```markdown
# 三平台寫手人格強化 · Deep Research Report

## Section 1: 對標 KOL 代表性貼文（15 則）
### 蕭上農
1. [平台] · 標題
   - 開場：...
   - 互動：... likes / ... comments
   - 擊穿分析：...
...

## Section 2: 三平台差異的量化觀察（15 條）
1. ...
...

## Section 3: System prompt personae
### Facebook persona
...
### Instagram persona
...
### Threads persona
...

## Section 4: 2026 趨勢（3–5 個）
...

## Section 5: 引用來源
- 每一個結論最少一條 URL，若是從貼文截圖推斷請標註 "visual observation"
```

嚴禁編造 engagement 數字；不確定就寫 "<visual estimate>" 或 "尚未查到"。

---

## 用完 report 後 Claude 要做的事

1. 把 Section 3 的三段 persona 做成 `config/personae_candidates.md`，人工
   比對現行 `soul.md` 差異
2. 把 Section 2 的 15 條觀察做成 `config/platform_voice_rules.yaml`，注入
   composer 的 system prompt
3. 把 Section 4 的趨勢寫成 `docs/BACKLOG.md` 新條目

## 為什麼這份 brief 值得優先跑

composer 寫得像不像 KOL 直接決定 engagement 上限。現在 composer 的 system
prompt 是 Hsin 年初靠手感寫的，沒有量化觀察做靠山。這份調研可以把「像不像」
從主觀感受變成 15 條可以驗證、可以 A/B test 的規則。
