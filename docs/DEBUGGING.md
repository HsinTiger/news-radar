# News Radar · Debugging Playbook

> 「壞掉了怎麼辦」分層排查手冊。每一條都從**症狀**出發。
>
> 先跑 `make diag`（= diagnose_harvest + diagnose_feeds），再對照本表。

---

## 症狀 1：`run_harvest.py` 跑完，`items_new = 0`

### 可能原因

| # | 原因 | 排查 |
|---|---|---|
| A | 所有 feed 都是舊的（`max_age_hours` 過嚴） | `diagnose_harvest.py` 看最新 item 的 `published_at`；調整 `filters.max_age_hours` |
| B | Feed 全部掛了 | `diagnose_feeds.py` 看 verdict 分布 |
| C | Feed 都抓到了，但 cleaner 全部 drop | 看 `logs/execution_log.jsonl` 最新一筆的 `drop_reasons` |
| D | DB 鎖死 | `fuser data/01_harvest/news_radar.db` 確認沒有 zombie 程序 |

---

## 症狀 2：`drop_reasons` 幾乎都是 `too_short:XX<100`

### 可能原因 + 解法

1. **feed 是 summary-only RSS**（TechCrunch / Reddit 常見）
   → `replay_item.py <id> --refetch` 強制重抓文章頁，看 trafilatura 能不能解。
   → 解不到就換 feed URL 找 full-content 版本。
2. **網站有 bot 牆**（Bloomberg / OpenAI /index/）
   → `diagnose_feeds.py` 會標 `ALL_ARTICLES_BLOCKED`，從 config 下架。
3. **trafilatura 參數太保守**
   → 改 `src/cleaner.py:extract_markdown` 的 `no_fallback=False` / `include_tables=True` 再測。

---

## 症狀 3：`drop_reasons` 幾乎都是 `no_keyword_match`

→ `config.yaml` 的 `keywords.must_include_any` 太窄或打錯字。

快速驗證：

```bash
# 對某個 item 做 replay，看 title / clean_markdown 的實際內容
python tools/replay_item.py <id>
```

接著把真實內容中的關鍵字補進 whitelist。

---

## 症狀 4：某一類 item 的 `clean_markdown` 開頭是 `YouTube Interview Description`

這是 `src/fetcher.py` 的**已知設計**：YouTube RSS 只會拿到 video description，
沒有完整逐字稿。fetcher 會用 RSS `summary` 直接填 `clean_markdown`，繞過 trafilatura。

**後果**：`word_count` 通常 < 100。以前單一門檻 100 時會**全部被 drop**（extract 成功 → 字數不夠）。

### ✅ Phase 8.9 已修復（tiered min_word_count）

`config.yaml` 的 `filters.min_word_count` 已改成 **dict 分級**，各 `source_type` 走自己的門檻：

```yaml
filters:
  min_word_count:
    default: 100
    article: 200   # 傳統 blog / 媒體稿
    social:  40    # Reddit / X / Threads UGC
    video:   30    # YouTube description
    forum:   60    # HN / Lobsters
```

YouTube feeds 在 `config.yaml` 標為 `source_type: video`，cleaner 自動套用 30 字門檻。
`resolve_min_word_count` 對舊版 int config **向後相容**；drop_reason 也改成
`too_short[video]:X<30` 格式，`diagnose_harvest` 可分層看 drop 率。

### 進階改善（若要更完整的 YouTube 訊號）

- **中期**：接 `youtube-transcript-api` 抓字幕 → 真正的逐字稿 → 改走 article 門檻
- **長期**：YouTube feed 單獨走一個 pipeline，`composer` 對 video 型別套不同 prompt（訪談摘要 vs. 新聞解讀）

---

## 症狀 5：`composer.py` 生出的貼文全部長得一樣

→ 訊號源多樣性不夠、或 `composer.py` 的 prompt 太制式。

排查：

```bash
# 看近 7 天發文
python scripts/list_recent_posts.py  # TODO

# 看有多少 item 的 clean_markdown 少於 500 字
sqlite3 data/01_harvest/news_radar.db \
  "SELECT feed_name, COUNT(*) FROM news_items
    WHERE word_count < 500 AND status != 'dropped'
    GROUP BY feed_name ORDER BY 2 DESC;"
```

短文佔比 > 60% 就是**訊號匱乏**，先修上游再動 composer。

---

## 症狀 6：Meta Graph API 發文失敗

→ 先看 `logs/publisher_errors.log` 的 `error_code`。

常見對照：

| code | 含義 | 解法 |
|---|---|---|
| 190 | Access token 過期 | 重跑 `docs/META_API_SETUP.md` token 流程 |
| 200 | 沒有權限 | 檢查 App Review 是否需要重審 |
| 368 | 被平台限流 | 等 24 小時，降低 `schedule.max_posts_per_day` |
| 100 | 參數錯誤 | grep 錯誤訊息，多半是 `attach_link` URL 格式 |

---

## 症狀 7.5：第三方 RSS 橋接服務整條路由回 404（Phase 8.10 教訓）

**情境**：原官方沒提供 RSS（例如 X/Twitter、Threads、LinkedIn），你掛上社群維護的橋接服務（`rsshub.app`、`rss.app` 公開實例、`nitter.*` 等），結果 harvest 整批 404。

**診斷特徵**：
- `diagnose_feeds.py` 標 `DEAD_FEED`（HTTP 404 在 feed 層）
- log 訊息長這樣：`Client error '404 Not Found' for url 'https://google.com/404'`（注意 URL 被 301 轉走了）
- **同一 host 的所有路由都死，不是單一帳號**

**解法**：

1. **先 `curl` 試一個 handle**，5 秒內確認 route 是否還活著：
   ```bash
   curl -sI https://rsshub.app/twitter/user/sama | head -5
   ```
   返回 `HTTP/2 404` 就是 route 已 deprecated，不要硬掛。
2. **public bridge 不是長期方案**：社群免費實例遲早會被來源站封 / 限流 / 整個 route 拔掉。不要把核心訊號源押在這上面。
3. **要用 X/Twitter 的正規路徑**（成本由低到高）：
   - rss.app SaaS：$5-10/月，5 分鐘設定
   - 自架 RSSHub in Docker：~$5/月 VPS，1-2 小時架
   - X API Basic tier：$200/月（不建議，CP 值太低）
4. **優先考慮「不靠 X」的替代源**：很多 KOL 的 X 發言其實是他們 blog / podcast / newsletter 的片段。直接打源頭通常**訊號密度更高**。例子：Howard Marks 的 Memo（art19 podcast RSS）比他的 X 推文豐富 10 倍。

**連動案例**：Phase 8.10 把 17 個 X-via-rsshub.app feed 全撤，改用 Howard Marks Memos（art19）+ Peter Zeihan（WordPress feed）。詳情見 `AGENT_WORKLOG.md:Phase 8.10`。

---

## 症狀 7.6：`diag-feeds` 判 HEALTHY，但 `make harvest` 對同一 URL 回 403（Phase 8.10-b 教訓）

**情境**：`diagnose_feeds.py` 探測某 feed 回 200 + entries 正常，但同一輪執行裡 `run_harvest.py` 對**完全同一個 URL** 回 403。

**根因（90% 是這個）**：`src/fetcher.py` 的 `fetch_feed` 跟 `tools/diagnose_feeds.py` 用**不同 User-Agent**。Cloudflare / Akamai 等站層 WAF 會對 `python-httpx/x.y` 這種裸 client UA 直接 403，但對 Safari/Chrome spoof UA 放行。

**快速驗證**：
```bash
# 用預設 UA 打，可能 403
curl -I "https://zeihan.com/feed/"

# 帶 Safari UA 打，應該 200
curl -I -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15" "https://zeihan.com/feed/"
```

**解法**：`fetch_feed` / `fetch_html` / `diagnose_feeds.py` 三邊用**同一組** browser UA header。Phase 8.10-b 在 `src/fetcher.py` 定義 `_BROWSER_HEADERS` 常數讓兩個 fetch 函式共用，若新增任何新的 HTTP client 路徑，也要一併改套。

**原則**：訊號不一致（diag 綠但 harvest 紅）比整體失敗更危險 —— 會讓未來的人花幾小時懷疑「是不是 feed URL 錯了？是不是 DNS 問題？」之類的地方。**保持 fetch 路徑的 header 表面一致**，就算流量加倍也值得。

**變體 B（同一症狀的另一個 flavor）**：harvest log 印 `RSS entry 數：0`，但 diag 印 `entries=77`。不是 4xx，是 **200 OK + feedparser 解不到 entry**。Phase 8.10-c 在 Howard Marks (art19) 撞到：原因是 `fetch_feed` 送了 `Accept: application/rss+xml, ...` 這種窄版 Accept header，art19 的 podcast CDN 會做 content-negotiation，對窄版 Accept 回 fallback 空體。解法：**只送 User-Agent，不送 Accept**，讓 httpx 用預設 `*/*`。某些 CDN 對 `*/*` 才會送正常內容。

---

## 症狀 8：某個 feed 一夕之間從 HEALTHY 變 ALL_ARTICLES_BLOCKED

**情境**：昨天還抓得到、今天 `diagnose_feeds.py` 顯示文章層全 403 / 401。

**常見原因**：
- **Cloudflare/站層 bot 牆升級**（OpenAI Blog 就是這樣）：feed 本身回 200（RSS CDN 沒管），但點進文章 URL 要過 JS challenge。
- **Paywall 強化**（WSJ / Bloomberg）：免費閱讀額度被縮緊，User-Agent 偽裝已經不夠。
- **A/B 實驗**：網站在特定 IP / 地區測試反爬，過幾天可能自己恢復。

**決策樹**：
- 一次性 → 等 2-3 天再 `make diag` 看有沒有自己好。
- 持續 → 從 config 下架；若這是重要訊號源，評估是否值得花時間套 Playwright / paid scraping service。
- Config 註解寫清楚**什麼時候撤、為什麼**，未來想補回來時有跡可循。

---

## 症狀 7：SQLite `database is locked`

→ 有另一個 process 開著同一個 DB。

```bash
# 找凶手
lsof data/01_harvest/news_radar.db

# 若確認沒有必要的 process，砍掉
kill -9 <pid>
```

**絕不手動刪 `.db-journal` 檔**，讓 SQLite 自己處理 WAL。

---

## 通用排查流程

```
┌────────────────────────┐
│ 1. 跑 make diag        │
└──────────┬─────────────┘
           ▼
┌────────────────────────┐
│ 2. 看 diagnostic_report│──── 有 DEAD_FEED  → 改 config
│    + feeds_health.md    │──── 全是 dropped  → 看 drop_reasons
└──────────┬─────────────┘
           ▼
┌────────────────────────┐
│ 3. 挑 1 篇 replay_item │──── 定位哪一層出事
└──────────┬─────────────┘
           ▼
┌────────────────────────┐
│ 4. 補 pytest 重現      │
│    改程式               │
│    pytest 綠            │
└──────────┬─────────────┘
           ▼
┌────────────────────────┐
│ 5. 再跑 make diag 驗證 │
└────────────────────────┘
```
