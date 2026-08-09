<!-- 由 manny-li-pro-kb 自動同步，請勿在此編輯。
     來源：HsinTiger/manny-li-pro-kb  commit dd47ad4
     要修改框架請改上游 skills/README.md 後重跑 sync-skills.sh -->

# skills/ — 給下游寫手的分析框架層

這個目錄是把 `notes/` 92 篇筆記裡**可重複套用的分析框架**蒸餾出來的指令檔，
供下游 repo 的自動寫手在組稿時直接注入 prompt。

## 為什麼要有這層

`notes/` 是「知識」——按篇、按公司組織，適合人翻閱與檢索。
`skills/` 是「方法」——按**分析動作**組織，寫手拿了就能照著做。

同一套框架有兩個消費端，所以框架必須放在這裡當 SSOT，而不是各自複製到下游：

| 消費端 | 用途 |
|---|---|
| `HsinTiger/news-radar` | Substack 每週美股公司分析（`compose.py company`） |
| `HsinTiger/mstr-btc-bottom-report` | BTC / MSTR 行情分析的迭代與觀點更新 |

下游只讀不改。要改框架，改這裡，兩邊同步。

## 檔案

| 檔案 | 回答什麼問題 | 主要消費端 |
|---|---|---|
| `company-teardown.md` | 一家公司要從哪四段拆到底？ | news-radar |
| `capital-allocation-engine.md` | 這是真的複利引擎，還是槓桿幻覺？ | 兩邊（MSTR 核心） |
| `cycle-and-capital-flow.md` | 這波是結構性轉折還是週期波動？錢最後流去哪？ | mstr-btc |
| `counter-case-construction.md` | 反面怎麼寫才可被觸發、才能轉成證偽條件？ | 兩邊 |
| `de-ai-prose.md` | 破折號、冒號、對仗句這些 AI 手癖怎麼清？（減法） | 兩邊 |
| `human-editorial-layer.md` | 意思都對卻不像人說話，怎麼重建思考路徑？（加法） | 兩邊 |
| `title-engine.md` | 標題副標怎麼下才像人話、才有張力？ | 兩邊 |

## 使用規則（重要）

1. **框架是透鏡，不是內容。** 這裡寫的是「怎麼看」，不是「看到什麼」。
   寫手要用當期的真實數據填充，不可以把框架的舉例當成本期事實寫出去。

2. **引用要標源。** 每個框架都標了它蒸餾自哪幾篇 `notes/`。寫手若在文中
   引用曼報觀點，必須標明來源與原文連結（`notes/` 每篇頂端都有 URL）。
   不可整段照抄筆記內容當原創。

3. **反面必寫。** 每個框架都有「什麼情況下這個框架會失效」一節。
   分析若只走完正面推論、沒有處理反面，視為不完整。

4. **框架可以判定「不適用」。** 如果標的不符合框架的前提，正確做法是
   明說不適用並換框架，而不是硬套。

## 同步

下游 repo 各自持有一份唯讀副本，由 `sync-skills.sh` 從本 repo 拉取。
副本頂端會標記來源 commit，用來判斷是否過期。
