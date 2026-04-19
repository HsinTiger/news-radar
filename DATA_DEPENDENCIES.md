# News Radar Metadata Dependencies Registry

> 為了確保專案在日後龐大的迭代中保持穩健 (Robust)，這張表列出了「改變某個核心 Feature 或資料結構時，需要同步連帶修改的工作點」。所有未來接手的 AI Agent，在更動以下節點前，必須查閱此表。

## 1. 資料庫層 (Database Flow)
如果在 `db/schema.sql` 做了任何 table 欄位新增或刪除：
- **上游 (Upstream)**：必須查看資料產生地 `src/schema.py` 內對應的 Pydantic Model 欄位。
- **寫入層 (Writers)**：必須對應地更新 `src/db.py` 中 `upsert_*` 函式內的 `INSERT` 或 `UPDATE` 語句，並確保 Tuple 長度相符。
- **搬運層 (Pipelines)**：若為平台變體表 (`platform_drafts`) 改動，須檢查 `run_pipeline.py` 是否有漏送參數。

## 2. 階段性資料夾架構 (Data Stages Architecture)
本專案現已導入嚴謹的 **5 階段 Agent 快照模式** (`data/01_harvest` 至 `data/05_feedback`)。
若要變更任何暫存備份路徑（如將 `drafts` 資料夾換位）：
- **寫入點 (Write Points)**：
  - `run_pipeline.py` (`_save_md_draft`, `save_archive_md`) 定義了 Compose (`pending_drafts`) 與 Publish (`archive`) 的實體文件位置。
  - `src/db.py` 中定義的 `DB_PATH` 指向 `data/01_harvest/news_radar.db`。
- **文檔同步 (Docs)**：
  - 放棄硬寫路徑，任何調整必須回頭更新 `docs/architecture.md` 的節點定義。

## 3. 作家風格設定 (Persona & Soul)
如果在 `config/news_radar_soul.md` 對寫作調性做調整（例如：從「第一人稱評論」轉成「第三人稱同理心敘事」）：
- **防呆審核 (Composer Check)**：
  - `src/composer.py` 的 System Instructions 中可能帶有「硬編碼」的禁忌單詞（例如禁止輸出「我的反思」），需同步更新以防 Prompt 衝突。
- **緊急備援模版 (Emergency Fallback)**：
  - `run_pipeline.py` 內有一組 `emergency_v` 的 fallback 字串。當模型超載 (429) 切換時，該段落的語氣必須與最新的 `news_radar_soul.md` 一致。

## 4. 平台欄位字數限制 (Platform Limits)
如果 Facebook / Threads / IG 的發文字數限制更改：
- **審核點 (Enforcement)**：`src/composer.py` 頂部的 `PLATFORM_LIMITS` 字典必須更新。
- **檢查點 (Validation)**：`run_pipeline.py` 呼叫 `_squeeze_to_limit()` 函數時，如果演算法無法容納新限制，可能會陷入死結。

---
*此 Registry 作為可行版本 V1 釋出，請後續 Agent 持續擴充*
