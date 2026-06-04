# FSDB × DeepSeek 波形 Debug — 研究與落地

讓**離線自架的 DeepSeek**（V4-Flash / V3.2 / R1）理解 **FSDB + VCS log + VCD**，
用於 RTL/晶片驗證 debug 與**自動產生時序圖**的可行性評估與分階段落地方案。

## 一句話結論

可行，但 **FSDB 是 Synopsys 專有二進位、LLM 不能直接看**——唯一路徑是
`FSDB --(Verdi fsdb2vcd/FsdbReader)--> 文字/結構化 --> 工具查詢 --> DeepSeek`。
VCD（IEEE 1364 ASCII）與 vcs.log（純文字）則可直接讀。
**建議走「Agent + 確定性波形查詢工具」，不要整檔硬塞**（抗幻覺 + 可驗證）。

## 文件

| 檔案 | 內容 |
|------|------|
| [`EVALUATION.md`](./EVALUATION.md) | **主報告**：格式澄清、工具鏈、DeepSeek 能力、先例、部署、驗證、架構、路線圖（逐條附引用與信心等級） |
| [`SKILL_DRAFT.md`](./SKILL_DRAFT.md) | 給 DeepSeek/agent 用的 skill 草稿（系統提示 + 工具規格 + 抗幻覺規則） |

## 分階段路線圖

1. **Phase 0 PoC** — DeepSeek 直接讀小 VCD 問答，證明「看得懂」。
2. **Phase 1 時序圖** — `fsdb2vcd` → 篩選 → `vcd2wavedrom` → LLM 精修 WaveJSON → `wavedrom` 渲染 SVG。最可驗證、先做。
3. **Phase 2 波形問答** — 把波形包成 typed tools（`get_signal`/`value_at`/`grep_log`），LLM 工具查詢。
4. **Phase 3 自動 debug** — vcs.log + 波形 → failing-signal 定位與根因解釋。

## 上線前硬限制

- ⚠️ **需 Verdi 授權**才能解 FSDB（VCD/log 不需）。
- ⚠️ DeepSeek 主線**無 vision**：時序圖是「生成文字再渲染」，不是「看圖」。
- ⚠️ 研究中數條 V4 規格來自次級來源（官方頁 403），上線前依 `EVALUATION.md` §10 覆核。
