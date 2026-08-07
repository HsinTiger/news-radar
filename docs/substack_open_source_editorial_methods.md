# Substack 開源寫作方法整合紀錄

## 1. Goal / non-goals

- `PROVEN`：`skills-radar` 的中立樣本顯示，`writing-content` 多集中在生成與轉換，驗證只佔少數；這只能用來找候選，不能證明某個 Skill 有效。
- `PROVEN`：本次已讀取候選的實際上游 `SKILL.md`、固定 commit、license 與 repository 狀態。
- 目標：補強研究角度、主張與證據的映射，以及最後的資訊減法。
- 非目標：不安裝第三方 Skill bundle、不新增第三次模型呼叫、不改 GitHub／Substack token、排程與發布權限。

## 2. Options / decision

1. 完整載入熱門寫作 Skill：規則最多，但 prompt 膨脹、授權與英文文法會污染繁中作者聲音。
2. **採用 compact method compiler（決定）**：讀上游方法後，只把與現有流程互補的契約改寫成本 repo 的繁中規則。
3. 新增獨立 source-triage LLM：可輸出完整引用計畫，但增加時間、token 與 13:00 前完成兩篇 Podcast 的風險，暫不採用。

## 3. Adopted methods

| Upstream | Pin / license / snapshot | 採用 | 明確不採用 |
|---|---|---|---|
| [Firecrawl Deep Research](https://github.com/firecrawl/web-agent/blob/f023adf1cd1f731e27fdc844af62996f6c2a41c4/agent-templates/library/agent-core/src/skills/definitions/deep-research/SKILL.md) | `f023adf1` · MIT · 1,175 stars · non-archived · checked 2026-08-07 | 不同研究角度、官方／實證／獨立分析／反方查詢、按子問題而非來源組織 | 只按來源數量給 high/medium/low confidence；來源數不等於證據品質 |
| [OpenSquilla Citation Planner](https://github.com/opensquilla/opensquilla/blob/ad73e288df47ec97afb8100aa24e84a3dc0e60be/src/opensquilla/skills/bundled/paper-citation-planner/SKILL.md) | `ad73e288` · Apache-2.0 · 6,569 stars · non-archived · checked 2026-08-07 | 寫前把主張映射到來源與證據角色；引用附著於主張，不附著於填充句 | 「有 20 筆就至少引用 20 筆」與論文章節模板；會造成 citation stuffing |
| [MoAI Claim Check](https://github.com/modu-ai/moai-adk/blob/5dbf84baa579e7c013a68eb0aa61d6224327f274/.claude/skills/moai-workflow-docs-claim-check/SKILL.md) | `5dbf84ba` · Apache-2.0 · 1,161 stars · non-archived · checked 2026-08-07 | 複合主張拆成單一可查證斷言；無證據、證據不足與衝突不得冒充已證明 | 文件稽核專用輸出表格與 no-command 邊界；Substack 寫手不需要照搬 |

## 4. Rejected runtime candidates

- [NeoLabHQ `write-concisely`](https://github.com/NeoLabHQ/context-engineering-kit/blob/99808f7865d87e5810d992f0bfccddbb8dbf986f/plugins/docs/skills/write-concisely/SKILL.md)：GPL-3.0、72,587 字元，且大部分是英文文法與完整書籍內容。只保留 owner 已獨立定義的資訊價值閘門，不載入或複製此 Skill。
- [K-Dense Scholar Evaluation](https://github.com/K-Dense-AI/claude-scientific-writer/blob/43aaecd6a24bb949b5c5c5b7e7105963e1abd53e/scientific_writer/.claude/skills/scholar-evaluation/SKILL.md)：MIT、證據追溯清楚，但主要服務學術評鑑與 rubric；完整整合會把電子報寫作變成評分流程，暫不採用。

## 5. Runtime contracts

1. 階段一從主來源的未知與反方提出 3–5 個不同角度；至少包含第一手、實證、獨立分析與最強反方，不得只是換同義詞搜尋。
2. 系統仍只把真正讀到正文的 5–10 個去重來源送進寫手。
3. 寫手先在內部建立主張—證據圖：每個外部可查證斷言只能對到已編號來源；無證據就刪除或降為假說／未知。
4. 單一來源的說法具名歸屬；衝突證據呈現分歧；不以來源數量假裝確定。
5. 正文按子問題組織，再通過資訊價值閘門；來源包不是正文配額。

## 6. Verification matrix

| Requirement | Executable evidence | Limitation after PASS |
|---|---|---|
| 不同研究角度 | research-brief prompt contract test | 模型仍可能提出品質不佳的查詢 |
| 主張—證據圖 | final-writer prompt contract test | 未產生可外部稽核的 hidden plan |
| Dashboard 誠實揭露方法 | Python + JS contract tests | UI 顯示不等於實際文章好讀 |
| 無第三方 runtime dependency | requirements/diff review | 上游方法仍需定期人工重審 |
| 文章品質 | 真實 Podcast 與公司草稿 owner review | 在遠端草稿驗收前保持 `UNKNOWN` |

## 7. Risks / owner gates

- Prompt contract 不能證明模型真的做完內部主張—證據圖；首批真實草稿要逐段檢查來源、重複與認知負擔。
- Firecrawl Skill 的 repository push 時間早於其他兩個候選；固定 pin 能重現本次採用，但不代表長期維護成熟。
- 若兩篇 Podcast 無法在 13:00 前完成，再評估是否需要獨立 source-triage call；目前先維持兩次 LLM 呼叫。

## 8. Decision changes

- 2026-08-07：採三個上游的互補方法，但不安裝 Skill、不複製完整 prompt；明確排除 citation quota、來源數量信心分數與學術評分模板。
