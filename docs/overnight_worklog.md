# Overnight Worklog · 2026-04-20 夜班

使用者就寢前託付：**5 小時內把 Phase 8.18 + 8.19 flow 修完、驗證完、deploy 上去**。
原則：**正確性 > 快速驗證性 > 節省 token**。動手前先做架構規劃，避免重工。

---

## 🎯 目標盤點（現況 → 目標）

**起點**（Phase 8.18 已完成，尚未 commit / push）：
- Mac 端要跑 `run_pipeline.py --compose-only`（用 Gemini 寫稿）→ queue → push state branch
- Cloud 端 `run_publish_queue.py` 從 queue 挑 freshness-first → 發 Meta → 更新 state
- **已知破口**：Mac Gemini 429 時觸發 `run_pipeline.py` 的 `emergency_v` 硬編模板 → 垃圾進 queue → Cloud 照發 → 品質崩盤

**目標**（Phase 8.19 + deploy）：
1. Mac 寫稿改成「Gemini primary → Claude CLI fallback → 兩個都失敗就 skip」
2. 徹底刪除 emergency template 這個隱患
3. 寫單元 + 整合測試驗證新路徑
4. commit + push 到 main；讓 GitHub Actions 接手（首次跑會看到 queue 空，安全 no-op）
5. 留一份精簡 morning checklist 給使用者

**明確不做**（避免過度擴張 + 降低風險）：
- 不動 `reflect.yml`（低頻、missable、目前 Gemini key 還在 secrets 裡可以跑；移到 Mac 是 Phase 8.20 tech debt）
- 不動 `analyst.py`（heartbeat 模式在 Phase 8.18 已實質棄用）
- 不動 `competitor_agent.py`（舊 Gemini SDK，非 compose 主路徑）
- 不實際呼叫 Meta API 測試（用 live token 有風險；dry-run 驗證即可）
- 不幫使用者安裝 Mac launchd（需要他們的 user context + `launchctl`）

---

## 🧱 模組規劃：`src/llm_brain.py`

**為什麼獨立一個模組**：
- scorer.py / composer.py 都各自呼叫 Gemini；現在要加 Claude CLI fallback → 複製邏輯兩份是反模式
- 未來若要再加第三條路（例如本機 llama.cpp），動一處而非散在各 caller
- 測試性：單一模組好 mock，單一 contract 好 assert

**對外 API（contract）**：
```python
async def call_for_json(
    *,
    system: str,
    prompt: str,
    response_model: Type[BaseModel],
    gemini_model: str = "gemini-2.0-flash-lite",
    temperature: float = 0.2,
    timeout_s: int = 180,
) -> LLMResult[T]:
    """回傳 LLMResult(data, provider, input_tokens, output_tokens, cost_usd)
    data 為 None 表示兩條路都失敗（呼叫端要 skip）。
    """
```

**決策樹（內部）**：
```
1. GEMINI_API_KEY 有設 → 試 Gemini（google-genai SDK，structured output）
   ├ 成功 → return LLMResult(provider="gemini", data=parsed, ...)
   └ 失敗（429 / 任何 Exception） → print 警告 → go to 2
2. `claude` CLI 可用（shutil.which("claude")) → 試 claude -p --output-format json
   ├ 成功 → 解 envelope JSON → 抽 result text → 抽內層 JSON → Pydantic validate → return LLMResult(provider="claude_cli", ...)
   └ 失敗 → print 警告 → go to 3
3. 兩條都失敗 → return LLMResult(data=None, provider="none")
```

**邊緣 case 清單（動手前列出，動手時對照）**：
- Claude CLI 輸出 envelope 結構跨版本不同 → 我用 defensive parsing：envelope 內找 `result`、找不到就把整個 stdout 當 text，對應 text 再抽 JSON
- Claude 輸出 JSON 被包在 markdown code fence (` ```json ... ``` `) → 用 regex 抽出
- Claude 輸出有前後閒聊 → 從第一個 `{` 到最後一個 `}` 抽出來試 parse
- Claude CLI timeout（180s 硬上限） → 視為失敗，go to 3
- Pydantic validation 失敗（欄位缺、型別錯） → 記錄 raw output 到 log、視為失敗

---

## ✅ 實作順序 + 驗證 gate（每一步做完才走下一步）

| # | 步驟 | 驗證 gate | 預估時間 |
|---|------|-----------|----------|
| 1 | 寫本規劃文件（你正在讀） | 存檔即可 | 10 min |
| 2 | 寫 `src/llm_brain.py` | AST 通過 + contract 對照 | 25 min |
| 3 | 寫 `tests/unit/test_llm_brain.py`（mock subprocess + mock gemini） | 全綠通過 | 20 min |
| 4 | refactor `src/scorer.py` 用 llm_brain | AST 通過 + 既有 NewsScore schema 不動 | 10 min |
| 5 | refactor `src/composer.py` 用 llm_brain | AST 通過 + 既有 MultiPlatformDraft schema 不動 | 15 min |
| 6 | `run_pipeline.py` 刪 emergency template，改成 skip+log | AST 通過 + 函式簽章回傳值新增 `"skip"` | 10 min |
| 7 | 整合測試：seed 3 新聞 + mock 兩個 LLM + 跑 compose-only + 檢查 queue | 佇列 1 筆 queued、status=auto_approved | 25 min |
| 8 | 整合測試：seed 3 queued drafts + 跑 `run_publish_queue.py --dry-run` | 挑到最新 + 印出要發的內容 | 15 min |
| 9 | 補 Phase 8.19 架構文件 addendum | 檔案 +80 行 | 15 min |
| 10 | commit 分三批：8.18 infra / 8.19 brain / tests+docs | 每個 commit 都能獨立 bisect | 10 min |
| 11 | push origin main | GitHub Actions workflow syntax 無誤（push 會自動檢查） | 5 min |
| 12 | 寫 `docs/MORNING_CHECKLIST.md` | 10 步內完成 Mac launchd install | 10 min |

**總計：~2h45m**。留 2h15m 給：不可預期的 debug、如果 Claude CLI envelope 格式和我預想不同、或某步驟我誤判複雜度。

---

## 🛡️ 安全 / 避免踩雷

1. **不要 force push / amend 任何東西**——每一次修改都是新 commit，出事才能 bisect
2. **不碰 `.env`**——token 在 GitHub Secrets 已經有，本地 `.env` 使用者自己管
3. **測試用 tmpfile DB，不動 real DB**——`tests/` 下的整合測試都用 `tempfile.NamedTemporaryFile`
4. **沙箱無法 `pip install` 外部套件**（proxy 擋 PyPI） → 整合測試得繞開 pydantic / google-genai 的 import。策略：用 `unittest.mock.patch` 攔住 `llm_brain.call_for_json` 整個函式，避免觸發 LLM SDK 的 import
5. **sandbox 無 `claude` CLI** → 不能實際測 subprocess；單元測試靠 `subprocess.run` mock；真實驗證得等使用者明早 Mac 上跑
6. **不 touch `config/soul.md` / persona 檔案**——那是使用者的創作領域
7. **commit message 明確標 Phase**（讓使用者明早 `git log` 一目了然）

---

## 📋 Morning checklist 要回答的問題（寫文件時參考）

使用者醒來會想知道：
1. 我做了什麼？（一句話）
2. 我驗證了什麼？（測試結果）
3. 他還需要做什麼才能讓系統真的跑起來？（launchd 安裝 → 設 `.env` → 驗證第一次 compose）
4. 出問題怎麼 debug？（看哪個 log、跑哪個命令）
5. token 花了多少？

---

**開工時間**：預計 22:00（台北）
**預計完工**：01:00 前（留 2h 給突發狀況）
