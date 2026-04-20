# Morning Checklist · 2026-04-21（Phase 8.20 overnight 交接）

早安。睡前你指派的三件事都完成並 commit，**還沒 push**（沙箱 proxy 擋 GitHub，一條指令的事）。

> 原話：「希望一早起來看到的是符合我剛提點的品質的文章」「記得用系統設計的思維來做 避免重工或者 spaghetti code」「如果你心有餘力 回去增添訊號源的豐富度」

---

## 🎯 一句話總結

**品質守門員 + 主題分類權重 + 訊號源擴充** 三件套全部落地。「【系統代班速報】」從今起不可能再流到 Meta；新文進來會自動被歸到 10 類主題並按權重排序；另外新增 6 個 KOL 風格 feed（蕭上農／游庭皓／IEO 對齊）。

---

## 📦 本次新增 commit（3 個，在 `main` 上面）

```
a1f11d1 feat(feeds): Phase 8.20 signal enrichment — 6 new feeds + audit tool
1621333 feat(topics): Phase 8.20 Step 2+3 — classifier + weighted ordering
a2f3da8 feat(quality-guard): block 【系統代班速報】 assembly-line fallbacks
```

確認：

```bash
cd ~/Library/CloudStorage/OneDrive-*/*/*/科技商業國際新聞自動化流程研究/news_radar
git log --oneline -5
```

---

## 🛡️ 第 1 件：品質守門員（Task #46 衍生 #51–#54）

**為什麼做**：2026-04-19 那篇「【系統代班速報】」之所以流出，是因為舊 queue 裡還殘留 Phase 8.19 之前產的 draft — 即使程式碼已經把 emergency template 移除，已經進 `queue_status='queued'` 的垃圾照樣會發。

**怎麼防**：純函式 checker + 雙整合點（compose-time、publish-time），共用同一份規則。

- `src/content_quality_guard.py` — 純 checker，零副作用。規則涵蓋：
  - 開頭字串 `【系統代班速報】`
  - 「結構性位移」「護城河的定義已從產品轉向生態數據」「數據密度高的決策」等組合招牌句
  - 英文 title + 中文 body（翻譯漏譯症狀）
  - generic hashtag bundle（`#科技戰略 + #商業洞察 + #數據驅動`）
- `src/local_notify.py` — macOS `osascript display notification`，被攔下時彈本機通知。非 Darwin 自動 no-op。
- `run_pipeline.py` 整合點：composer 輸出後、落 DB 前，命中就丟 `dropped_quality_block`。
- `run_publish_queue.py` 整合點：發 Meta 前最後一關，命中就 `mark_queue_failed(reason=...)` + 通知。

**測試**：`tests/unit/test_content_quality_guard.py`（7 條）+ `tests/unit/test_publish_queue_quality_guard.py`（2 條整合）。golden trap 直接塞 2026-04-19 那篇全文確認會被攔下。

**手動驗證**：

```bash
# 用 2026-04-19 那篇測 checker 是否會攔
python3 -c "
from src.content_quality_guard import check_quality, has_blocking_issues, format_issues
text = '''【系統代班速報】...（貼上當天那篇）'''
issues = check_quality(text, '')
print(f'blocked: {has_blocking_issues(issues)}')
print(format_issues(issues))
"
```

---

## 🏷️ 第 2 件：主題分類 + 權重排序（Phase 8.20 Step 2+3，Task #48 #49）

**為什麼做**：你 2026-04-21 00:55 拍板的 10 類 taxonomy + 權重表（AI 拆三類、AI 再高、`other` 保留）現在有實際 runtime 效果了 — 先前只是 schema 坐在 DB 裡。

- `src/topic_classifier.py` — keyword fast-path（免 LLM、conf=0.6）+ LLM fallback（走 llm_brain.call_for_json） + orchestrator（永遠落地到 `other`，絕不丟例外）。pydantic 缺失時 keyword path 仍能跑（給 backfill 用）。
- `config/topic_keywords.yaml` — 9 類代表性關鍵字（`other` 不需要規則，是兜底）。
- `run_pipeline.py` 整合：`score → classify → weighted_score = score × weight`（clip 0..2.0），寫進 `news_items.topic_category / topic_confidence / weighted_score`。
- `src/db.py`：
  - `get_pending_items` ORDER BY 改 `COALESCE(weighted_score, 0) DESC, published_at DESC`
  - `pick_fallback_any_approved` ORDER BY 同上（2h lower-bound fallback 會優先挑高權重）
  - `pick_freshest_queued` **刻意不改** — Phase 8.18 freshness-first 契約保留，weight 只在 queue 空或處理 pending 時發聲

**測試**：`tests/unit/test_topic_classifier.py`（12 條）+ `tests/unit/test_pick_fallback_weighted.py`（3 條）。

**回填舊資料**（手動，隨時跑；冪等）：

```bash
# keyword-only（免 LLM、快、免費）
python3 -m scripts.backfill_topic_classifier

# 強制全部重跑（含 LLM path）
python3 -m scripts.backfill_topic_classifier --force --llm
```

---

## 📡 第 3 件：訊號源擴充 + audit 工具（Task #55）

**為什麼做**：你原話「訊號沒有趕上像我提及的那三位 KOL」。

新增 6 個 feed（全部標 `# UNVERIFIED 2026-04-21`，還沒實際打過 HTTP）：

| 對齊 KOL | Feed | Tier | 理由 |
|---|---|---|---|
| 蕭上農 | SemiAnalysis (Dylan Patel) | primary | AI 晶片 / HBM / 代工深度研究 |
| 蕭上農 | TechNews 科技新報 | primary | 台灣半導體中文第一線 |
| 游庭皓 | Calculated Risk | secondary | 宏觀數據長期追蹤 |
| 游庭皓 | The Diff (Byrne Hobart) | primary | 戰略級財務分析 |
| IEO | Marginal Revolution | secondary | Tyler Cowen 每日經濟觀察 |
| IEO | Not Boring (Packy McCormick) | secondary | VC / 戰略 |

**配套新工具** `scripts/audit_feeds.py`：

```bash
# 健檢所有 feed（含舊的 17 條 + 新的 6 條）
python3 -m scripts.audit_feeds --urls-only

# 看主題分佈 + 權重表 + 各 feed 近 30 天貢獻貼文數
python3 -m scripts.audit_feeds --db-only

# 兩段都跑
python3 -m scripts.audit_feeds
```

**你要做的**：跑一次 `--urls-only`，把 `✅ 200` 的把註解改成 `# VERIFIED 2026-04-21`；失敗的整條移除或換路徑。

---

## 🔴 你要做的事（照順序）

1. **Push**（一分鐘）
   ```bash
   git push origin main
   ```
   驗證：https://github.com/HsinTiger/news-radar/commits/main 看到 3 個新 commit。

2. **驗證新 feed URL**（兩分鐘）
   ```bash
   python3 -m scripts.audit_feeds --urls-only
   ```
   把通過的改註解 `# VERIFIED 2026-04-21`，失敗的移除。commit 訊息 `chore(feeds): verify Phase 8.20 new feeds`。

3. **回填主題分類**（若要立刻看到既有 drafts 也帶權重）
   ```bash
   python3 -m scripts.backfill_topic_classifier
   ```

4. **看一次 DB audit**（確認 topic 分佈健康）
   ```bash
   python3 -m scripts.audit_feeds --db-only
   ```
   期望：權重前 3 位應該是 `ai_model / ai_agent / ai_application`；`other` 不應該佔超過 30%（若超過，代表 keyword 規則漏太多，該加關鍵字到 `config/topic_keywords.yaml`）。

5. **手動清除殘留的壞 queue**（如果還有）
   ```bash
   sqlite3 data/news_radar.db "
     SELECT d.id, d.queue_status,
            substr(p.post_text,1,40)
       FROM drafts d JOIN platform_drafts p ON p.draft_id = d.id
      WHERE d.queue_status = 'queued'
        AND (p.post_text LIKE '【系統代班速報】%'
             OR p.post_text LIKE '%結構性位移%');
   "
   # 有的話：
   #   sqlite3 data/news_radar.db "UPDATE drafts SET queue_status='stale_legacy' WHERE id='xxx';"
   ```
   守門員會在 publish-time 擋住這些，但先讓它們不參與 queue picking 更乾淨。

---

## ✅ 本次驗證通過的測試（別再跑一次浪費時間）

| 檔案 | 條數 | 狀態 |
|------|------|------|
| `test_content_quality_guard.py` | 7 | ✅ |
| `test_publish_queue_quality_guard.py` | 2（整合） | ✅ |
| `test_topic_classifier.py` | 12 | ✅ |
| `test_pick_fallback_weighted.py` | 3 | ✅ |
| `test_topic_taxonomy.py` | 8 | ✅ |

**沙箱跑不動**：pydantic、pytest、httpx 都被 proxy 擋住 pip install。單元測試是用 plain python 跑（`spec.loader.exec_module` + 呼叫 `test_*`）。Mac 上你有 pydantic / pytest，跑 `pytest tests/unit -q` 應該全綠。

---

## 🧭 系統設計後記（避免 spaghetti）

這次刻意守了幾條邊界：

1. **scorer 的純度**：scorer 不知道 topic / weight / quality_guard 的存在。它只做「這是不是高品質 signal」。分類+加權+過濾全部在 `run_pipeline.process_item` 這個 orchestrator 裡做。
2. **publisher 不 LLM**：Phase 8.18 契約保留 — publisher 只做 freshness-first + cadence + Meta API。quality guard 是純字串檢查，沒有 LLM 呼叫。
3. **單一事實來源**：category_id 在 `src/topic_taxonomy.py`，關鍵字在 `config/topic_keywords.yaml`，權重在 DB `topic_weights` 表。三者不重複。
4. **防線深度 (defense-in-depth)**：quality guard 在 compose-time 擋一次、publish-time 擋第二次，共用同一個純函式。避免「改了一邊忘了另一邊」。
5. **Phase 8.18 契約**：`pick_freshest_queued` 刻意不加權，freshness-first 不動搖。weight 只影響『queue 空』或『處理 pending』兩個路徑。

---

## ⏳ 還沒做的（明確推遲）

- **Phase 8.20 Step 4：週一 back-prop reflector**（Task #50）
  延後 2 週，等累積每類 ≥ 5 篇真實發文的 engagement 才有意義。在那之前權重保持 seed 值。

- **launchd 排程新 audit / backfill**
  我沒動 `~/Library/LaunchAgents/`。若想讓 audit 每週一自動跑，你自己加一個 plist 或我下次處理。

—— 2026-04-21 overnight, Cowork Claude
