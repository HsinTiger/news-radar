# News Radar · Diagnostic Tools

這個資料夾放**不會產生 token 成本**的診斷腳本。核心設計原則：

1. **每個工具只回答一個問題**，不要混合職責。
2. **讀多於寫**：除非明確 `--commit`，都是 dry-run，不動 DB。
3. **輸出永遠是 Markdown + stdout**，方便貼到 issue / 對話繼續迭代。
4. **純 stdlib + 既有依賴**（httpx / feedparser / trafilatura），不引入新套件。

---

## 什麼時候用哪個工具？

| 症狀 | 你在問的問題 | 該跑哪個 |
|---|---|---|
| 今天又沒東西進 DB | 「到底有多少進來？怎麼分布？」 | `diagnose_harvest.py` |
| 某個 feed 似乎死了 | 「是網路掛了，還是被擋？」 | `diagnose_feeds.py` |
| 某篇該通過卻被 Drop | 「這一篇到底卡在哪一層？」 | `replay_item.py <id>` |
| 剛改完 cleaner | 「對過去那些失敗篇重跑有救嗎？」 | `replay_item.py <id> --commit` |

---

## 1. `diagnose_harvest.py` · Harvest DB 健康報告

**唯讀 SQLite**，產出一份 Markdown，回答：

- 每個 feed 的通過率、平均字數
- Drop 原因長尾分布
- 字數直方圖（primary vs secondary）
- **YouTube 短路 item 清單**（fetcher.py bug 證據）
- 近 7 天趨勢

```bash
# 最簡用法
python tools/diagnose_harvest.py

# 指定輸出位置
python tools/diagnose_harvest.py --out data/01_harvest/diag_2026_04_19.md

# 同時印到 stdout
python tools/diagnose_harvest.py --print
```

**預設輸出**：`data/01_harvest/diagnostic_report.md`

---

## 2. `diagnose_feeds.py` · Feeds 即時存活探測

**會打網路**（~30-90 秒）。逐一：

1. GET 每個 feed URL 看回應碼
2. 解析 RSS，拿前 N 篇文章的 URL
3. GET 每個文章 URL 測抓取成功率
4. 給出 verdict：`HEALTHY` / `PARTIALLY_BLOCKED` / `ALL_ARTICLES_BLOCKED` / `DEAD_FEED` / `EMPTY_FEED`

```bash
# 探測所有 feed，每個 sample 3 篇
python tools/diagnose_feeds.py

# 只探測名稱含 "OpenAI" 的 feed
python tools/diagnose_feeds.py --feed OpenAI

# 增加 sample 數 + 放寬 timeout
python tools/diagnose_feeds.py --samples 5 --timeout 20
```

**預設輸出**：`data/01_harvest/feeds_health.md`

---

## 3. `replay_item.py` · 單篇清洗 Replay

給一個 `news_item.id` 前綴或完整 URL，重跑清洗 pipeline，印出：

- Step A：HTML 來源（DB cache / 即時 GET / YouTube 短路）
- Step B：trafilatura 萃取結果 + markdown 前 400 字
- Step C：og:image
- Step D：`clean_and_filter` 的 pass/drop 判定與理由
- Step E：是否寫回 DB（預設 dry-run）

```bash
# 用 id 前綴
python tools/replay_item.py ab12cd

# 用完整 URL
python tools/replay_item.py https://openai.com/blog/...

# 強制重抓 HTML，忽略 DB 快取
python tools/replay_item.py ab12cd --refetch

# 修完 bug 要把這篇的結論覆寫回 DB
python tools/replay_item.py ab12cd --commit
```

---

## 工程原則（給以後的自己）

1. **新增工具時**：檔名以 `<verb>_<target>.py` 命名（如 `diagnose_harvest.py`、`replay_item.py`）。
2. **Dry-run first**：所有會寫 DB / 打外部 API 的動作都要 `--commit` 才真的執行。
3. **輸出 path 可覆蓋**：`--out` 必須是可選參數，預設落在 `data/01_harvest/` 方便歸檔。
4. **每個工具開頭的 docstring 就是 README 條目**：`README.md` 只需要摘要表 + 連到 docstring。
5. **禁呼叫 LLM**：這裡的工具一律 deterministic。任何需要 LLM 的分析寫成 `scripts/` 或 `src/` 的模組。
