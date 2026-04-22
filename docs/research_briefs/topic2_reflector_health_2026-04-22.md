# Topic 2 · Reflector Health Check · 2026-04-22

**決策上下文**：Hsin 2026-04-22 拍板 Topic 2 走 (c) 路徑（seed 不動、reflector 規則不動），做一輪唯讀健檢，結果若全綠則 close Topic 2、有離譜值則 follow-up 不立即動。

**模式**：唯讀（read-only）。本報告**不觸發任何 DB 寫入**。

---

## A. 靜態稽核（完成）

直接稽核 `src/reflector_topic.py` 與 `src/db.py`，不動 DB。

### A1. Clamp 邊界與 guard rails（`src/reflector_topic.py:57-65`）

| 常數 | 值 | 意義 |
|---|---:|---|
| `ETA` | 0.10 | 學習率（溫和） |
| `MAX_WEEKLY_DELTA` | 0.30 | 單週變動絕對值上限，超過即 clip |
| `MIN_SAMPLES_TOTAL` | 5 | 跨平台合計 < 5 → 該類整輪跳過 |
| `MIN_SAMPLES_PER_PLATFORM` | 3 | 某平台 < 3 → 該平台不進類別平均 |
| `GLOBAL_WEIGHT_FLOOR` | 0.30 | 全域地板（包含 other） |
| `GLOBAL_WEIGHT_CEIL` | 2.00 | 全域天花板 |
| `TREND_CONSECUTIVE_WEEKS` | 3 | 連續同方向 N 週才標 trend（僅 report，不改 math） |

**結論**：邊界與 guard rails 均對齊 2026-04-21 Hsin 拍板 spec。**靜態層面 clean**。

### A2. 合法 `update_reason` 集合

源自 `src/db.py:127`（seeder）+ `src/reflector_topic.py:399, 410`（back-prop writer）：

- `initial_seed`（由 `_seed_topic_weights` 冷啟動時寫入）
- `back_prop`（由 `_write_updates` 每週一 06:00 TW cron 寫入）
- `manual`（spec 允許的手動值，code 內目前**沒有寫入點**——只能人工執行 SQL UPDATE）

**結論**：合法集合 = `{initial_seed, back_prop, manual}`。Topic 2 的 diagnose 工具會把落在此集合外的值標紅。

### A3. Cron 與觸發路徑

| 項目 | 位置 | 狀態 |
|---|---|---|
| GitHub Actions 定期觸發 | `.github/workflows/reflect_topic.yml:5-14` | 週一 06:00 TW |
| CLI 手動觸發 | `python -m src.reflector_topic [--dry-run] [--lookback-days N]` | OK |
| dry-run 旗標 | `_write_updates` 只在 `not dry_run` 時執行 | OK |

### A4. Seed 失效風險（Topic 2 verdict B 的根因）

- `_seed_topic_weights` 對已存在 category_id **完全 skip**（`src/db.py:121-122`）
- runtime 讀權重走 `get_topic_weight`（`src/db.py:227-241`）純 SELECT DB
- 無任何 feature toggle 可切回走 seed

→ 這正是 Topic 2 的核心發現，(c) 方案的前提就是「接受這條規則、只做 health-check，不再碰 seed」。

---

## B. Runtime 查詢（deferred — bash sandbox down）

本輪 session 的 bash sandbox 持續回 `failed to mount /mnt/.virtiofs-root/shared/usr/local/bin` 錯誤，無法執行 SQLite 查詢。

**provisioned 方案**：`tools/diagnose_topic_weights.py` 已寫好並入倉（唯讀，無副作用）。Hsin 任何時候在原生 terminal 裡：

```bash
cd ~/news_radar
source .venv/bin/activate   # 或直接用系統 python 也行（純 stdlib）
python tools/diagnose_topic_weights.py --print
```

即可產出：

- `docs/research_briefs/topic2_reflector_health_<today>.md`（完整報告）
- stdout 同步（`--print`）

工具會自動做：

1. **Q1 極端權重**：近地板（≤ 0.35，預設）/ 近天花板（≥ 1.95，預設）名單
2. **Q2 從未 back-prop 類別**：`update_reason='initial_seed'` 的類別清單
3. **Q3 非法 update_reason**：不在 `{initial_seed, back_prop, manual}` 的 row
4. **近期 back-prop 活動**：`topic_weight_history` 最近 20 筆
5. **reflection_events**：最近 3 筆 cron 觸發紀錄
6. **Verdict**：自動判定 closed 或 follow-up

> 工具 200 行，純 stdlib + sqlite3，零 token 成本，零副作用。遵循 `tools/README.md` 的「讀多於寫、deterministic」公約。

---

## C. 預期結果與 follow-up 決策樹

### C-1. 全綠（理想情境）

若工具輸出最後一段 Verdict 為 ✅，則：

> Topic 2 closed。
> 下週一 cron 跑完後可再跑一次確認 back-prop 有正常推進。

### C-2. 有 initial_seed 大量殘留

若 Q2 列出 > 50% 類別仍為 `initial_seed`，**不代表 reflector 壞了**，可能原因：

- 該類別跨平台樣本合計 < 5（很可能是冷啟動早期，總發文量還不夠）
- 該類別分類準度不足，樣本被誤分到其他類

**行動**：不動 reflector 邏輯。改為跑 `tools/diagnose_harvest.py` + classifier accuracy 抽樣檢查，這屬 Topic 3 戰線。

### C-3. 有權重觸頂/觸底

若 Q1 列出近地板/天花板類別，**觀察而不動**：

- **近 2.00 天花板**：該類別 engagement 強，無法再往上獎勵——下一步是「拆子類」（Topic 3 戰線，非 Topic 2）
- **近 0.30 地板**：該類別長期低迷——考慮降級為 `other` 的 sub-tag（也屬 Topic 3 戰線）

**現階段不動**，下一輪健檢看是否持續。

### C-4. 非法 update_reason

若 Q3 非空 → **真問題**。代表有外部程式碼繞過 `_write_updates` 直接寫 `topic_weights`。

**立即行動**：`git log -p src/db.py src/reflector_topic.py` 看最近三週 diff 找寫入點，修補或回滾。這種情況才動 code。

---

## D. SSOT 掛點（與 `docs/System_Architecture.md` 的連動）

本報告與 §A3 Topic Classification（topic_weights schema + back-prop）直接對齊。

Session 尾聲會把本 health check 工具列入 §A3 的「診斷工具」區塊。

---

## E. 本輪 Verdict（Session-local）

| 項目 | 結果 |
|---|---|
| 靜態稽核（A1-A4） | ✅ clean |
| Runtime 唯讀查詢（B） | ⏸ deferred（bash sandbox down） |
| Diagnose 工具 provisioned | ✅ `tools/diagnose_topic_weights.py` |
| 本輪是否動 code | ❌ 不動 |
| Topic 2 狀態 | **可關但等 bash 恢復跑一次 runtime query 再 close** |

**Follow-up item（非 blocker）**：Hsin 下次開 terminal 時跑一次工具，報告放 `docs/research_briefs/topic2_reflector_health_<date>.md`，若全綠就正式 close Topic 2。

---

_報告產出：2026-04-22 overnight session · Cowork Claude · 對應 Topic 2 (c) 最小可行路徑_
