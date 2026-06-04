# RTL ↔ 波形 協同 + DeepSeek Debug

讓**離線自架的 DeepSeek**，在 RTL designer 寫 code 的同時用**時序圖（WaveDrom）**跟人對齊意圖、
並在 VCS 模擬失敗時做事後 debug。波形是「人看的圖」也是「餵回模型的精確規格」。

## 兩條迴圈

**① 設計協同迴圈（主軸，純文字、不碰 FSDB）**
工程師白話文 → LLM 轉成 WaveJSON → 渲染給人確認「我理解成這樣對嗎」→ 同一份餵回模型 → 改 RTL。
WaveJSON 當共同語言，把白話文的歧義收斂掉。詳見 [`examples/round_trip_example.md`](./examples/round_trip_example.md)。

**② 事後 debug 迴圈（連 VCS 模擬驗證）**
VCS 跑掛 → VCD（native `$dumpvars`，或 FSDB 經 `fsdb2vcd`）+ vcs.log → 確定性工具把實際波形轉 WaveJSON →
`diff_waves(intended, actual)` 定位第一個分歧 → LLM 解釋成因、改 RTL。

## 關鍵事實（澄清「IEEE 都看得懂」的迷思）

- **VCD** ✅ IEEE 1364 純 ASCII，LLM 直接讀。
- **vcs.log** ✅ 純文字。
- **FSDB** ❌ Synopsys 專有二進位、無公開規格、無獨立開源 reader——**LLM 不能直接看**，要先 `fsdb2vcd`（Verdi 授權）。
- **「要 VCD 給工程師看」其實不必碰 FSDB**：Verilog 內建 `$dumpvars` 直接從模擬產 VCD，零 Verdi。
- **DeepSeek 主線純文字無 vision**：時序圖是「生成 WaveJSON 文字 → 渲染」，不是「看圖」。

## 文件

| 檔案 | 內容 |
|------|------|
| [`EVALUATION.md`](./EVALUATION.md) | 主研究報告（格式、工具鏈、DeepSeek 能力、業界先例、部署、驗證），逐條附引用與信心等級 |
| [`SKILL_DRAFT.md`](./SKILL_DRAFT.md) | **核心 skill**：白話文↔WaveJSON↔RTL 協同迴圈 + 工具規格 + 抗幻覺規則 |
| [`examples/round_trip_example.md`](./examples/round_trip_example.md) | 具體走一遍 round-trip（含 RTL、WaveJSON、白話文修改、diff 驗證） |

## 工具問題速答

- 轉 VCD 的指令是 **`fsdb2vcd`（Verdi 工具，不是 vcs）**；用 `which fsdb2vcd` 確認你環境有沒有裝。
- 但多數情況**直接 `$dumpvars` 產 VCD 更簡單**，FSDB 只在你們已習慣 dump FSDB 時才需要轉。

## 上線前硬限制

- ⚠️ 解 FSDB 需 **Verdi 授權**（VCD/log 不需）。
- ⚠️ DeepSeek 無 vision：時序圖＝生成文字再渲染。
- ⚠️ 數條 V4 規格來自次級來源（官方頁 403），上線前依 `EVALUATION.md` §10 覆核。
