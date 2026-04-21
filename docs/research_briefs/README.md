# News Radar · Research Briefs for Gemini Deep Research

這個資料夾存放 Cowork Claude 為各 pipeline 節點寫的「調研委託書」。
用法：Hsin 把整份 .md 貼進 [Gemini 網頁版](https://gemini.google.com/)
的 Deep Research 模式，Gemini 回報告後把報告交回給 Claude，Claude 再
根據調研把 pipeline 對應節點升級。

## 為什麼這樣分工

- Cowork Claude 的 sandbox 連不上 github.com / PyPI 以外的大部分網域，
  也沒有 web search。無法主動做 market research、抓真實 KOL 貼文去 reverse
  engineer 風格、也無法跑 Google Scholar 類的 meta-analysis。
- Hsin 在 Gemini 網頁版有免費 Deep Research 額度；Gemini 能跑真正的 web
  research + 吐結構化 report。
- 分工：Claude 寫「要問什麼」、Gemini 回「答案」、Claude 根據答案改 code。

## 使用建議

1. 一次只跑一篇 brief（Gemini Deep Research 每次大約 5–15 分鐘）。
2. Gemini 回完，把 report 放進 `docs/research_briefs/<brief_name>_report.md`
   再告訴 Claude「已經有 X 的 report 了」。
3. Claude 會讀 report，列出「可直接套用的修改清單」，等 Hsin 批准後才動 code。

## 現有 briefs（2026-04-21 overnight 寫）

| 檔名 | 研究對象 | 預估 Gemini Deep Research 時長 |
|------|---------|------|
| `01_composer_personae.md` | 強化三平台寫手人格（FB / IG / Threads） | 10–15 分鐘 |
| `02_scorer_heuristics.md` | 選題信心啟發式補強（有沒有更好的 feature？）| 8–12 分鐘 |
| `03_topic_keywords.md` | 關鍵字覆蓋率與邊界 case | 5–10 分鐘 |
| `04_content_quality_redflags.md` | 內容品質紅旗擴充（模型 hallucination / clickbait 的最新徵兆）| 10 分鐘 |
| `05_hashtag_strategy.md` | FB / IG / Threads 的 hashtag 最佳實踐 2025–2026 | 8 分鐘 |
| `06_cadence_timing.md` | 台灣科技圈受眾的最佳發文時段研究 | 6–10 分鐘 |

## 研究優先順序建議

如果 Gemini 額度有限，按「對 engagement 影響的直接程度」排序建議跑：

1. **`01_composer_personae.md`**（直接影響每篇文字的擊穿力，最高槓桿）
2. **`06_cadence_timing.md`**（發文時段研究，cheap 但效果立竿見影）
3. **`05_hashtag_strategy.md`**（可被拿來改 appendix.md，effort-to-gain 高）
4. `04_content_quality_redflags.md`（guard rails 擴充，長期保險）
5. `02_scorer_heuristics.md`（選題啟發式，需要樣本才看得出效果）
6. `03_topic_keywords.md`（比較是『覆蓋率 audit』而非『品質提升』）
