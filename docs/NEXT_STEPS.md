# ✅ 你現在要做的事（按順序）

## 🟢 步驟 1：本地環境準備（5 分鐘）

打開終端機：

```bash
cd "/Users/hsin/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/文件/antigravity_workspace/substack/科技商業國際新聞自動化流程研究/news_radar"

# 建議用 venv 隔離（不汙染你 system Python）
python3 -m venv .venv
source .venv/bin/activate

# 安裝套件
pip install -r requirements.txt

# 初始化資料庫
python src/db.py
```

✅ 跑完應該看到：
```
[DB] 初始化 news_radar.db
[DB]  ↳ schema 套用完成
```

---

## 🟢 步驟 2：跑第一次採集（2 分鐘）

```bash
python run_harvest.py
```

✅ 預期看到：
- `[Module 1] 抓取 Feed → ...` 重複 10 次
- 一份 Harvest Report，告訴你抓到幾筆、Drop 幾筆、原因分布
- `logs/execution_log.jsonl` 出現一行新紀錄

如果某些 feed 拒絕連線（FT、The Information 有時要會員），那是正常的，跳過繼續。

---

## 🟢 步驟 3：檢查抓到的素材品質（10 分鐘）

```bash
# 看資料庫有幾筆
python3 -c "
import sqlite3
conn = sqlite3.connect('db/news_radar.db')
print('總筆數    :', conn.execute('SELECT COUNT(*) FROM news_items').fetchone()[0])
print('已通過    :', conn.execute(\"SELECT COUNT(*) FROM news_items WHERE status='fetched'\").fetchone()[0])
print('被 drop  :', conn.execute(\"SELECT COUNT(*) FROM news_items WHERE status='dropped'\").fetchone()[0])
print()
print('=== 通過的前 5 筆標題 ===')
for r in conn.execute(\"SELECT title, feed_name, word_count FROM news_items WHERE status='fetched' LIMIT 5\"):
    print(f'  [{r[1]:18s}] {r[2]:4d}字  {r[0]}')
"
```

🤔 **這時候你要做的判斷**：抓回來的東西品質怎麼樣？
- 太多雜訊 → 把 `config/config.yaml` 裡的 `must_include_any` 加更嚴的關鍵字
- 太少 → 加更多 `feeds`，或放寬 `min_word_count`
- 漏掉某個你關心的領域 → 把該領域的官方 RSS 加到 `feeds`

---

## 🟡 步驟 4：申請 Meta API（30–60 分鐘）

跟著 [`META_API_SETUP.md`](./META_API_SETUP.md) 一步步做。

完成後 `.env` 應該有 6 個值都填好：

```env
META_APP_ID=...
META_APP_SECRET=...
FB_PAGE_ID=...
FB_PAGE_ACCESS_TOKEN=...
THREADS_USER_ID=...
THREADS_ACCESS_TOKEN=...
```

⏰ **這步可以跟步驟 1-3 並行做**，互不影響。

---

## 🟡 步驟 5：申請 Gemini API Key（5 分鐘・免費）

1. 開 [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. 用你的 Google 帳號登入
3. 點「Create API Key」→ 選擇任一專案
4. 複製出來的 key 寫進 `.env` 的 `GEMINI_API_KEY=`

**Free tier 額度**：Gemini 2.0 Flash 每分鐘 15 次 / 每天 100 萬 token。
我們的用量（每天 3 篇 × 1500 token = 4500 token）只用到免費額度的 0.45%。

---

## 🔴 等我（Milestone 2）

完成上面 5 步後告訴我，我會接著建：

- `src/scorer.py` — AI 信心評分
- `src/composer.py` — AI 短文撰寫（用你的 `news_radar_soul.md` persona）
- `src/reviewer_ui.py` — `localhost:8000` 本地審核面板
- `src/publisher.py` — Meta API 發文器
- `src/verify_meta_tokens.py` — 驗證 6 個 token 都能正常用
- launchd plist — 排程每 4 小時自動跑採集

---

## 🆘 出事故時

- DB 想重來：刪掉 `db/news_radar.db` 重跑 `python src/db.py`
- 想看 log：`tail -f logs/execution_log.jsonl | jq .`
- 想暫停採集：把 `config.yaml` 裡某個 feed 改 url 為空字串就好（不用改程式碼）

---

## 📊 預期 Token 成本（以每天 3 篇估算）

| 項目 | Token 量 | 月費（USD） |
|---|---|---|
| Gemini Flash 評分+撰寫 | 5800/天 ≈ 174K/月 | **$0**（免費額度內）|
| Anthropic Claude 週迭代 | 5K/週 ≈ 20K/月 | ~$0.30 |
| **合計** | | **< $1/月** |

對比直接「叫 LLM 爬蟲」的方式（每篇可能要 50K+ token），便宜 30 倍。
