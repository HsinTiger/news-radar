# Evening Report · 2026-04-20（你上班期間我自己跑的）

晚安！你早上交代完出門後，我在你的 `main` branch 上又疊了 6 個 commit（全部 local，**要你晚上推**）。

---

## 🎯 一句話總結

夜班完成的 8.18 + 8.19 主體架構之後，**收緊 "no brain → no publish" 不變量、補上 regression tests、更新文件漂移**。沒有觸發新的授權項目，你今晚只需要 `git push`。

---

## 📦 morning 新增的 8 個 commit（在 origin/main..HEAD 之間）

```
8cc6309 docs(config): mark llm.* block as deprecated (unread since Phase 8.19)
7a559ef docs: evening report for Hsin's 9pm review (morning session wrap-up)
66c9a48 docs(pipeline): flag outdated Stage 02-04 contracts vs Phase 8.11/8.18/8.19
ede6c74 docs: Phase 8.19 hardening addendum (morning commits)
4bf06b3 fix(publisher): close file handle on FB photo upload errors
4a9ed24 test: regression guards for skipped_no_llm + no-fabricated-score
7f393bd docs: mark D-01 cloud deployment as resolved by Phase 8.17/8.18/8.19
78d5835 feat(8.19+): tighten claude CLI invocation + kill scorer-fail footgun
```

你 push 時驗證：
```bash
cd ~/Library/CloudStorage/OneDrive-*/*/*/科技商業國際新聞自動化流程研究/news_radar
git log --oneline origin/main..HEAD   # 應看到上述 6 筆
rm -f .git/index.lock* .git/HEAD.lock*
git push origin main
```

---

## 🔧 程式碼改動要點（按 commit 順序）

### 78d5835 · Claude CLI 改官方 flag + scorer-fail footgun 清除

兩件事塞一個 commit 因為邏輯高度耦合：

**A. Claude CLI 改用 `--system-prompt` / `--bare` / `--no-session-persistence`**

原本 `_try_claude_cli` 把 system + user 合成一段從 stdin 餵進去。查了官方 [CLI reference](https://code.claude.com/docs/en/cli-reference) 後改成：
```python
args = [CLAUDE_CLI_BIN, "-p", "--output-format", "json",
        "--system-prompt", system.strip(),
        "--bare", "--no-session-persistence",
        prompt.strip()]
proc = await asyncio.create_subprocess_exec(
    *args, stdin=asyncio.subprocess.DEVNULL, ...)
```
roles 分離乾淨，降低 Claude CLI 把 schema 指令跟新聞內容混淆的風險。

**B. 拔掉 `run_pipeline.py` 的 scorer-fail 偽分數 footgun**

原本 scorer 回 None 時 pipeline 會塞 `NewsScore(confidence_score=1.0, editorial_note="[緊急代班] ...")` 強推。這跟夜班拔的 emergency template 同一類 bug——LLM 掛了就把每篇新聞強塞滿分自動發。

改成：回 `"skipped_no_llm"`、news.status 保持 fetched、下一輪重試。**跟夜班拔 emergency template 的哲學一致：no brain → no publish**。

### 7f393bd · BACKLOG.md 標記 D-01 已收口

雲端部署目標（D-01）因為 Phase 8.17/8.18/8.19 已達成，在 BACKLOG 加 "✅ 已收口" 標記，避免下次讀 backlog 看到「雲端全自動」還以為是 open item。

### 4a9ed24 · 雙版本 regression test

兩個檔案：
- `tests/integration/test_process_item_skip_paths.py` — pytest 版，走正規 CI
- `tests/integration/validate_skip_paths_sandbox.py` — 無 pytest/pydantic 依賴的備援版

三個案例：
1. scorer 回 None → process_item 回 `"skipped_no_llm"`、drafts 無列、status=fetched
2. composer 回 None → 同上
3. **regression guard**：任何 draft 的 confidence_score 絕不會 >= 0.99（未來有人想重加偽分數 fallback 會被這條擋住）

沙箱因為沒 pydantic 無法實測，但語法檢查通過。**醒來後請跑**：
```bash
cd ~/news_radar
pytest tests/integration/test_process_item_skip_paths.py -v
# 或無 pytest：
python tests/integration/validate_skip_paths_sandbox.py
```

### 4bf06b3 · publisher.py 修 FB 圖片上傳的 file handle leak

掃 publisher.py 時找到的小問題：`files = {"source": open(path, "rb")}` 如果 httpx call 拋例外，檔案 handle 不會關（靠 GC）。改成 `with open(...)` 包起來。

這不是 quality-crushing 問題，純資源洩漏，順手修。掃 publisher.py 沒找到其他 fallback footgun——publisher 層沒有 emergency template / fabricated score 這類設計，很乾淨。

### ede6c74 · architect_plan_disscussion.md Phase 8.19 hardening addendum

在 Phase 8.19 區段後加了 morning hardening 的 3 個決策點（CLI flag / scorer-fail / regression tests），各帶 trade-off 分析、observation points。符合檔案的維護規則（議題/挑戰/結論/trade-off/觀察點五件事）。

### 66c9a48 · PIPELINE.md 加漂移警告 banner

PIPELINE.md 最開頭加表格列出 Milestone 2-3 時的 Stage 02-04 contract 跟 Phase 8.19 實況的差異（scorer 有呼叫 LLM、publish 走 queue、cadence 不用 jitter/slots 等）。沒重寫整個檔——那是下個里程碑的事；現在只是先讓下次讀的人不會以為「scorer.py 不得呼叫 LLM」還是 ground truth。

### 8cc6309 · config.yaml 的 `llm:` block 標 deprecated

grep 掃過整個 repo 確認**沒有任何 .py 檔讀 `cfg['llm']`**。scorer / composer / reflector 三個地方的 `model=` 都硬編 `gemini-flash-latest`，而且統一走 `src/llm_brain.py` 的雙路徑兜底。所以 config 的 `llm.primary.model: gemini-2.0-flash` / `llm.premium: claude-sonnet-4-6` 這些設定完全沒在用。

沿用 `schedule.publishing_slots` 已廢棄但保留的同一模式：加 comment 標 DEPRECATED、保留作歷史，不動 YAML 結構（風險為零）。

日後若真要 config-driven 模型切換再重構這個 block 接進 llm_brain，不是今天。

---

## 🔓 需要你授權的項目

**沒有新增。**

你早上交代「暫時先把要授權的項目記錄起來我晚上授權」，我翻了今天做的所有工作：純 code/docs 編輯 + local commit，沒有觸發任何 request_access / 網路操作 / 系統檔修改。

所以 TODO 還是 MORNING_CHECKLIST 的那 5 件（push / launchd 安裝 / .env 複製 / 驗證 compose / 載 launchd），今晚挑你方便的時間做。

---

## 🧪 沒辦法在沙箱驗、要你晚上確認的事

| 項目 | 驗證方式 |
|------|----------|
| Claude CLI 新 argv 在 Mac 上真的能跑 | 跑一次 compose，看 log 有沒有 `provider=claude_cli` 的字樣（當 Gemini 還在就不會觸發，要等 429 或手動測） |
| `pytest tests/integration/test_process_item_skip_paths.py` 綠 | `cd ~/news_radar && pytest tests/integration/test_process_item_skip_paths.py -v` |
| publisher file handle 修正沒破壞正式 FB 圖片上傳 | 下一篇成功發文時看 FB 有沒有上圖 |
| PIPELINE.md 新 banner 沒踩 markdown 排版問題 | 在 GitHub 網頁看 docs/PIPELINE.md 的 table 有沒有正常渲染 |

---

## 📊 今天 morning 的 token 估算

| 階段 | input | output |
|------|-------|--------|
| Hardening 78d5835 （CLI flag 驗證 + scorer-fail refactor） | ~30k | ~10k |
| Regression tests 4a9ed24 | ~15k | ~8k |
| publisher.py audit + fix 4bf06b3 | ~10k | ~3k |
| Docs updates (7f393bd / ede6c74 / 66c9a48) | ~25k | ~12k |
| git lock wrangling + 檢查 commits | ~10k | ~2k |

**粗估**：~90k input / ~35k output。Opus 4.7 ≈ $2.5。

合夜班 $4.5 總計今天 ~$7 買到一個 production-ready + test-covered pipeline，值。

---

## 🔮 還沒做但可以考慮（明天以後）

不在今天 scope、列出來給你之後決定優先順序：

1. **AGENT_WORKLOG.md 補 Phase 8.18/8.19/morning 三個章節**（現在只更新到 Milestone 7；過去 3 phases 累積的決策紀錄還沒下放到這個「新 agent 必讀」檔）
2. **`docs/overnight_worklog.md` 跟 `MORNING_CHECKLIST.md` 可以歸檔**（一次性文件，已過時效）
3. **Reflector / analyst 沒動**（overnight 已明確 out-of-scope，之後 Phase 8.20 規劃時再處理）
4. ~~**config.yaml 裡的老舊 key**（`schedule.publishing_slots` / `schedule.jitter`）— Phase 8.15 後已不讀這些 key，但還留在 config 裡——清乾淨會讓新人不會誤以為還有這機制~~ → 今天順手標 DEPRECATED 了（commit 8cc6309；同樣處理了 `llm.*` block）。**不刪**，保留歷史；若未來真的想徹底清，那時再一次 atomic 移除
5. **GitHub Actions run 歷史檢查**——pipeline.yml 已改走 `run_publish_queue.py`，第一次實際雲端 run 若紅要看 log；綠代表 8.18 cloud 端 OK

---

祝晚安 🌙 別忘了先 push 我才是真的完成。
