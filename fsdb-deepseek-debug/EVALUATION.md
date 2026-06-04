# DeepSeek × FSDB/VCD/VCS 波形 Debug — 深度評估報告

> 目標：在**完全離線、氣隙工網內自架** DeepSeek（使用者所稱 "V4 Flash"）的前提下，讓模型理解
> **Synopsys FSDB + VCS 模擬 log + VCD 波形檔**，用於 RTL/晶片驗證 debug 與**自動產生時序圖**。
>
> 範圍假設（已與需求方確認）：部署＝氣隙自架；典型檔案＝小型（< 幾十 MB / 數百訊號）；目標＝**分階段**（時序圖 → 互動問答 → 自動 debug）。
>
> 研究方法：5 路並行 web 搜尋 → 來源去重抓取 → 對抗式查證 → 合併綜整。每條主張標注信心等級與來源。
> 報告日期 2026-06-04。

---

## 0. 給決策者的一頁結論（TL;DR）

1. **「IEEE 嚴謹格式、模型看得懂」這個前提只對一半。** 必須拆開看：
   - **VCD** ✅ 真的是 IEEE 1364 標準、純 ASCII 文字、LLM 可直接讀。
   - **vcs.log** ✅ 純文字（無正式格式），LLM 可直接讀。
   - **FSDB** ❌ **Synopsys 專有二進位格式，無任何公開/IEEE 規格，無獨立開源解析器。** LLM **不能**直接看 FSDB 位元組。
2. **可行，但唯一務實路徑是：FSDB →（用 Verdi 授權工具 `fsdb2vcd` / FsdbReader）→ 文字中間表示 → 餵 / 給工具查詢 → DeepSeek。** FSDB 這一步永遠繞不開 Synopsys 授權。
3. **「DeepSeek V4 Flash」是真的存在的模型**（2026-04 釋出的 V4 Preview 小變體，284B/13B MoE、open-weights、原生 ~1M context、**純文字無 vision**）。1M context 對「小檔案整段塞入」非常有利。
4. **強烈建議走「Agent + 確定性波形查詢工具」而非「整檔硬塞純文字」。** 業界與學界一致證據：直接塞大型結構化資料會讓 LLM 幻覺出不存在的訊號 / 誤判時序；NVIDIA、Cadence、Siemens 的方案都先把波形結構化或以工具查詢。
5. **分階段落地**：Phase 1 時序圖生成（最可驗證）→ Phase 2 互動波形問答（工具查詢）→ Phase 3 自動 root-cause（結合 vcs.log + 波形）。每階段都要有 ground-truth 驗證關卡。

---

## 1. 格式可行性與「IEEE 迷思」澄清

這是整個方案的地基，先把三種檔案的標準化狀態講清楚。

| 檔案 | 標準化狀態 | 內容型態 | LLM 能直接讀位元組？ |
|------|-----------|---------|---------------------|
| **VCD** | ✅ IEEE 1364（1995 四值版；2001 擴充 extended VCD） | 純 ASCII 文字 | **可以** |
| **vcs.log** | ❌ 無正式格式（VCS `-l` 產生的工具 log） | 純文字 | **可以** |
| **FSDB** | ❌ Synopsys/Novas **專有二進位**，無公開/IEEE 規格 | 壓縮二進位 | **不行** |

**主張 1.1（高信心）** — VCD 由 IEEE 1364 定義：四值 VCD 源於 IEEE Std 1364-1995，extended VCD（含訊號強度/方向）於 IEEE Std 1364-2001 加入。
來源：https://en.wikipedia.org/wiki/Value_change_dump

**主張 1.2（高信心）** — VCD 是人類可讀的 ASCII 文字格式（非二進位）。這是它最關鍵的性質——LLM 可直接從位元組解析。
來源：https://en.wikipedia.org/wiki/Value_change_dump

**主張 1.3（高信心）** — VCD 三段結構：(a) header（date / simulator version / `$timescale`）；(b) 變數定義段 `$scope` / `$var`，每個訊號配一個短識別碼；(c) 時間排序的值變化段，初值由 `$dumpvars` 引入。識別碼由可印 ASCII 字元 `!`–`~`（十進位 33–126）組成，慣例 1–2 字元。
來源：https://en.wikipedia.org/wiki/Value_change_dump ， http://www.pldworld.com/_hdl/2/_ref/se_html/manual_html/c_vcd.html

**主張 1.4（高信心）** — **FSDB 是專有二進位格式**，源自 Novas → SpringSoft → Synopsys（Verdi 平台背後的資料庫）。
來源：https://www.synopsys.com/verification/debug/verdi.html

**主張 1.5（中高信心；屬「不存在」的否定性主張）** — **FSDB 沒有公開或 IEEE 的二進位格式規格**；唯一存取途徑是 Synopsys 授權的 FsdbReader/ffrAPI。所有第三方專案都是包裝該授權 library，沒有從公開規格獨立實作的。
來源：https://github.com/nayiri-k/fsdb-parse （建在 Verdi FsdbReader 之上，需 Verdi libs）

**主張 1.6（高信心）** — **GTKWave 能開 FSDB，但只是依賴 Synopsys 工具**：configure 時需 `fsdb2vcd`/`fsdbdebug` 在 `$PATH`，或找得到專有 FsdbReader libs（nffr/nsys）。GTKWave 自身**沒有**獨立 FSDB 解碼器，且該格式在 GTKWave 4 計畫被移除。
來源：https://github.com/gtkwave/gtkwave/blob/master/docs/intro/formats.md

**主張 1.7（高信心）** — 開源 Rust 波形庫 `wellen`（Surfer/Vaporview 用）支援 VCD/FST/GHW，但**不支援 FSDB**。
來源：https://github.com/ekiwi/wellen

**主張 1.8（高信心）** — `vcs.log` 只是 Synopsys VCS 的純文字編譯/模擬 log（`-l vcs.log` 產生，`-a` 附加），**無正式格式**，檔名只是慣例預設。
來源：https://www.edaboard.com/threads/synopsys-vcs-log-file-creation.321128/

**主張 1.9（中高信心）** — VCD 對大型設計會非常肥大：ASCII、無內建壓縮、且**每一次值變化都記錄**。百萬閘級 / 長跑模擬常達多 GB（FSDB 的賣點正是「比 VCD 壓縮很多」）。具體 GB 數字屬示意而非硬基準。
來源：https://en.wikipedia.org/wiki/Value_change_dump

> **對需求方原話的直接回應：**
> 「聽說這些檔案格式都有嚴謹 IEEE 格式定義、應該看得懂、有被訓練過」——**這句話對 VCD 成立、對 vcs.log 部分成立、對 FSDB 不成立。**
> VCD 的 IEEE 1364 結構在 GitHub/教材/論壇大量出現，LLM 訓練語料中常見，模型確實「看得懂」VCD 語法。
> 但 **FSDB 是封閉二進位，沒有規格可被訓練、也無法從位元組解讀**——這是必須先用 `fsdb2vcd` 轉文字才談得下去的硬限制。

---

## 2. FSDB → 文字工具鏈（氣隙環境）

因為 FSDB 不能直接讀，前處理層的第一件事就是「在離線環境把 FSDB 轉成 LLM 可讀文字」。盤點如下。

**主張 2.1（高信心）** — `fsdb2vcd` / `fsdb2vcd64` 把 FSDB 轉 VCD；`vcd2fsdb` 反向。這些是 Synopsys 隨 Verdi 出貨的波形工具。
來源：https://www.synopsys.com/blogs/chip-design/verdi-waveform-utilities.html

**主張 2.2（高信心）** — `FsdbReader`（C/C++，`ffrAPI` 標頭，link `libnffr.a`/`libnsys.a`）是官方 FSDB 讀取 API，**以 Synopsys 授權協議出貨，不可自由轉散**。
來源：https://www.coursehero.com/file/219192537/FsdbReaderpdf/ ， fsdb2vcd 原始碼引用 libnffr/libnsys：https://codesearch.isocpp.org/actcd19/main/g/gtkwave/gtkwave_3.3.98-1/contrib/fsdb2vcd/fsdb2vcd_fast.cc

**主張 2.3（中高信心，否定性）** — **不存在完全獨立的開源 FSDB reader**。領頭的「開源」專案（`nayiri-k/fsdb-parse`）也是包裝 Verdi FsdbReader，需 `LD_LIBRARY_PATH` 指到 Verdi 安裝。
來源：https://github.com/nayiri-k/fsdb-parse

**結論（前處理層設計）**：
- 氣隙環境**必須有 Verdi 授權**才能解 FSDB——這在你們已用 VCS/Verdi 的工網裡通常成立。
- 兩條路：
  1. **`fsdb2vcd` 轉檔路線**（簡單）：批次轉成 VCD，再用純 Python 解析。小檔案首選。
  2. **FsdbReader API 路線**（精準）：用 C/C++ 或 Python binding 直接查詢指定訊號的跳變，**不落地整個 VCD**——這是 Phase 2/3「工具查詢」要走的路，避免 VCD 肥大。
- VCD 解析（Python，皆 MIT/開源、可離線打包）：
  - `vcdvcd`（讀取，random-access + streaming callback，附 `vcdcat`）— https://github.com/cirosantilli/vcdvcd
  - `pyDigitalWaveTools`（讀 IEEE 1364 VCD → JSON，也能寫）— https://github.com/Nic30/pyDigitalWaveTools
  - `pyvcd`（主要是**寫** VCD）— 配合上面兩者使用

---

## 3. DeepSeek 模型能力盤點

**主張 3.1（中高信心，多來源一致；官方頁 403 無法直接開）** — **「DeepSeek V4 Flash」是真實模型**：DeepSeek-V4 Preview 於 **2026-04-24** 開源釋出，兩個變體：
  - **V4-Pro**：1.6T 總 / 49B 激活（MoE）
  - **V4-Flash**：**284B 總 / 13B 激活（MoE）** ← 使用者指的就是這個
來源：https://simonwillison.net/2026/apr/24/deepseek-v4/ ， https://www.atlascloud.ai/blog/ai-updates/what-is-deepseek-v4 ， 官方（403 待直接確認）https://api-docs.deepseek.com/news/news260424

**主張 3.2（中信心）** — **V4（Pro/Flash）原生支援 ~1M token context**，靠 DeepSeek Sparse Attention（DSA）+ token 壓縮達成。對「小檔案整段塞入」極有利。
來源：https://simonwillison.net/2026/apr/24/deepseek-v4/

**主張 3.3（高信心）** — 對照組：V3/V3.1/V3.2(-Exp) 皆 **128K** context、671B/37B MoE；R1 系列亦 128K。V3 為 **MIT 授權**、open-weights、原生 FP8 safetensors。
來源：https://huggingface.co/deepseek-ai/DeepSeek-V3 ， https://www.bentoml.com/blog/the-complete-guide-to-deepseek-models-from-v3-to-r1-and-beyond

**主張 3.4（高信心）** — **DeepSeek 主線 chat/reason 模型（V3/V3.1/V3.2/R1，及一切跡象顯示 V4）都是純文字、不能讀圖。** 視覺要另外用 DeepSeek-VL2 或 Janus-Pro（獨立模型家族）。
來源：https://www.inferless.com/learn/the-ultimate-guide-to-deepseek-models ， DeepSeek-VL2 https://arxiv.org/pdf/2412.10302 ， Janus-Pro https://arxiv.org/pdf/2501.17811

> **對方案的關鍵涵義：**
> - **不能寄望「直接餵波形截圖讓 DeepSeek 看圖 debug」**——主線模型沒有 vision。要嘛把波形轉成**文字/結構化表示**，要嘛另接 DeepSeek-VL2/Janus（但這條較弱、且仍需另一套部署）。
> - **時序圖要走「生成 WaveDrom 文字 → 用 wavedrom 渲染 SVG」**，而不是「模型看圖」。模型負責產生 WaveJSON（文字），渲染交給確定性工具。
> - **1M context（V4）讓小檔案 (<幾十 MB) 的 VCD 子集可整段放入**，但仍建議先篩選（見 §5 KV cache 成本）。

**主張 3.5（中低信心，無基準來源）** — V4-Flash（284B/13B）比 V4-Pro 便宜很多，是「較省硬體」的 V4 選項，但仍是數百 GB 權重的大型 MoE。**確切 VRAM footprint 目前無公開基準，需自行實測。**
來源：https://www.atlascloud.ai/blog/ai-updates/what-is-deepseek-v4

---

## 4. LLM-for-waveform-debug 的業界/學界先例

這節直接回答「別人做過嗎、該用哪種架構」——證據壓倒性地指向**工具型 / 先結構化**，而非整檔硬塞。

### 學界
**主張 4.1（高信心存在；中信心數字）** — **NVIDIA FVDebug**：自動分析 formal 反例的根因，結合波形 + RTL + spec，三段式管線：因果圖合成（failure trace → DAG）→ Graph Scanner（批次 LLM「正反論證」prompting）→ agentic「Insight Rover」。**關鍵：它不把原始 trace 丟給 LLM，而是先結構化成圖。**
來源：https://arxiv.org/abs/2510.15906 ， https://research.nvidia.com/publication/2025-09_fvdebug-llm-driven-debugging-assistant-automated-root-cause-analysis-formal

**主張 4.2（中高信心）** — **FIXME**：宣稱首個端到端、多模型、開源的 LLM 硬體功能驗證 benchmark（180 任務 / 3 難度 / 6 子領域）；對波形相關任務，它把 **VCD 同時轉成 (a) 文字時間序列 與 (b) 波形截圖** 餵模型。可直接借鏡為驗證資料集設計。
來源：https://arxiv.org/html/2507.04276v1

**主張 4.3（中高信心）** — **TD-Interpreter**：多模態 LLM（微調 LLaVA-7B）**解讀**時序圖的 VQA 工具，宣稱大幅勝過未微調 GPT-4o。注意這是「讀圖」非「生成 WaveDrom」。
來源：https://arxiv.org/abs/2507.16844

**主張 4.4（中高信心）** — RTL debug 既有系統：**MEIC**（迭代修 RTL，宣稱語法 93%/功能 78% 修復率）、**RTLFixer**、**AssertLLM**、**HDLdebugger**、**「Towards LLM-based Root Cause Analysis of Hardware Design Failures」**（34 個 buggy 情境，o3-mini pass@5 達 100%，+RAG 多數 >90%）。數字皆來自 snippet，引用前應核對 PDF。
來源：https://arxiv.org/abs/2405.06840 ， https://arxiv.org/abs/2507.06512

### 工具/開源
**主張 4.5（中高信心）** — **存在一個 VCD Waveform Analyzer MCP server**，其 `get-signal` 工具把**指定訊號**的變化送進 context——正因為大型 VCD 塞不進 context。這是「工具查詢取代整檔硬塞」最對點的真實案例。
來源：https://skywork.ai/skypage/en/vcd-waveform-analyzer-hardware-debugging/1981598825536614400

**主張 4.6（高信心）** — **MCP4EDA**（論文+repo）把 Verilog 合成、模擬、ASIC flow、波形分析（含 GTKWave VCD）以統一 MCP 工具介面暴露給 LLM。
來源：https://github.com/NellyW8/MCP4EDA

### 商用 EDA AI debug
**主張 4.7（高信心，廠商行銷口徑）** — **Cadence Verisium WaveMiner**：跨**多次 run** 的波形用 AI 判斷「哪些訊號、在哪些時間點」最可能是失敗根因；Verisium Debug 以 pass/fail 自動比對做 root cause。
來源：https://www.cadence.com/en_US/home/tools/system-design-and-verification/ai-driven-verification/verisium-debug.html

**主張 4.8（高信心，廠商口徑）** — **Siemens Questa One Agentic Toolkit**（約 2026-02）含 Debug Agent：吃模擬波形、log、assertion、coverage，關聯後追可疑訊號跳變、提出失敗機制、產生 debug 情境供工程師審。**建在 MCP 之上**——與本案 Phase 2/3 架構同型。
來源：https://news.siemens.com/en-us/questa-one-agentic-ai-toolkit/

**主張 4.9（中高信心）** — Synopsys.ai Copilot（2025-09 擴充）重點在 assertion 生成、RTL 生成、workflow assistant（宣稱 60% 更快、某客戶 35% formal 生產力），**公開材料較少強調波形/log root-cause**——波形 debug 的較強商用先例是 Cadence 與 Siemens。
來源：https://news.synopsys.com/2025-09-03-Synopsys-Announces-Expanding-AI-Capabilities-for-its-Leading-EDA-Solutions

### 幻覺風險（為何不要整檔硬塞）
**主張 4.10（高信心，定性）** — 把表格/結構化資料序列化成文字餵 LLM，會模糊行列邊界、對行列順序敏感、並出現「捏造儲存格、誤判欄位關係、為填空編值」等幻覺。
來源：https://arxiv.org/pdf/2402.17944 ， https://tryolabs.com/blog/why-llms-struggle-with-your-spreadsheet-data

**主張 4.11（中信心）** — 在表格擾動下，即使頂級模型在 distorted vs clean 表格的準確率掉 ≥22%（具體模型名疑為 snippet artifact，待核）。結論（穩健度退化）成立。
來源：https://arxiv.org/pdf/2601.05009

**主張 4.12（高信心，通則）** — tool-use 文獻明確建議把 LLM 呼叫變成「**帶嚴格 JSON schema 的 typed function call**」以縮小錯誤空間（機率生成 → 確定性驗證），並以檢索/結構化輸出取代自由文本攝入來降幻覺。
來源：https://arxiv.org/pdf/2412.04141 ， https://aws.amazon.com/blogs/machine-learning/reducing-hallucinations-in-llm-agents-with-a-verified-semantic-cache-using-amazon-bedrock-knowledge-bases/

> **prior-art 的真空地帶（也是你們的機會點）：**
> (a) 沒有論文專門量測 LLM 在 VCD/FSDB 上**幻覺訊號名 / 誤判時脈邊緣**；
> (b) 沒有 peer-reviewed 的 **LLM 生成 WaveDrom** 工作（只有讀圖的 TD-Interpreter 與商用 wavedrom-llm.com）；
> (c)「確定性波形查詢 API 抗幻覺」此模式被工具示範但尚未被正式命名/消融——你們可定位為「填補未形式化的 prior art」。

---

## 5. 時序圖生成：WaveDrom 路線（Phase 1 核心）

**主張 5.1（高信心）** — **WaveDrom 是 MIT 授權的開源引擎**，把 **WaveJSON 文字描述** 轉成 SVG。
來源：https://github.com/wavedrom/wavedrom

**主張 5.2（高信心）** — WaveJSON 是 JSON/JSON5；signal lane 有 `name` 與 `wave`（`wave` 必填，每字元 = 一個 cycle）。字元語意：`p/n` 時脈、`0/1` 高低、`h/l`、`H/L`（在作用邊緣加 marker）、`=`/`2`–`9` 帶資料值、`x` 未定、`z` 高阻、`.` 延續前一 cycle、`|` 延續且畫 gap。`data` 陣列依序填 `=`/數字槽的標籤。
來源：https://github.com/wavedrom/schema/blob/master/WaveJSON.md ， https://wavedrom.com/tutorial.html

**主張 5.3（高信心）** — 最小完整範例（極為精簡，~5 行 / <200 字元）：
```json5
{ signal: [
  { name: "clk",  wave: "P......" },
  { name: "bus",  wave: "x.==.=x", data: ["head", "body", "tail", "data"] },
  { name: "wire", wave: "0.1..0." }
]}
```
來源：https://wavedrom.com/tutorial.html

**主張 5.4（高信心）** — **VCD → WaveDrom 自動轉換器已存在**（Python、MIT）：`Toroid-io/vcd2wavedrom`、`nanamake/vcd2json`。
來源：https://github.com/Toroid-io/vcd2wavedrom ， https://github.com/nanamake/vcd2json

**主張 5.5（高信心）** — `wavedrom` npm CLI 是**渲染器**（WaveJSON → SVG），**不吃 VCD**：`npm i -g wavedrom; wavedrom --input s.json5 > out.svg`。
來源：https://github.com/wavedrom/wavedrom ， https://www.npmjs.com/package/wavedrom

**主張 5.6（中高信心，屬推論）** — WaveJSON 夠精簡，LLM 為少數訊號 / 短視窗生成它是可行的；主要失敗模式是 **`data` 陣列對齊**（`=`/數字槽數量要對得上標籤順序）與 `.` held-value 用法。這是「推論而非實測成功率」，需用 §7 驗證。

> **Phase 1 推薦資料流**：
> `FSDB --fsdb2vcd--> VCD --(篩選訊號+時間視窗, vcd2wavedrom 或自寫)--> 候選 WaveJSON`
> → DeepSeek 做「**精修/標註/解釋**」而非從零生成（降低幻覺）→ `wavedrom` CLI 渲染 SVG。
> 也就是：**確定性工具先把骨架做出來，LLM 只做它擅長的語意層**。

其他文字時序格式（備案）：`tikz-timing`（LaTeX）、`schemdraw`（Python，接受 WaveJSON-like）、`asciiwave`（ASCII）。皆不如 WaveJSON 順手。

---

## 6. 部署（氣隙自架）

**主張 6.1（高信心）** — **SGLang 是 DeepSeek 官方 Day-0 推薦引擎**；**vLLM 自 v0.6.6 起官方支援 DeepSeek-V3/R1**（FP8/BF16，NVIDIA+AMD）。
來源：https://github.com/sgl-project/sglang/blob/main/docs/basic_usage/deepseek_v3.md ， https://docs.vllm.ai/projects/recipes/en/latest/DeepSeek/DeepSeek-V3.html

**主張 6.2（高信心）** — 671B 級硬體：full 671B FP8 ≈ **685 GB 權重，放不進 8×H100（640 GB）**，需 **8×H200（~1.1 TB）單節點**或 2 節點 H100/BF16。SGLang 官方檔位：FP8→8×H200/8×B200/8×MI300X 或 2×(8×H100)；BF16→更多。
來源：https://github.com/sgl-project/sglang/blob/main/docs/basic_usage/deepseek_v3.md ， https://company.hpc-ai.com/blog/deepseek-r1-671b-deployment-how-to-maximize-performance

**主張 6.3（高信心）** — **若只有單機**：Ollama 跑 **distilled R1**（1.5b/7b/8b/14b/32b/70b，預設 Q4_K_M）。32b ≈ 20–24 GB（可上 RTX 4090 24 GB）、70b ≈ 40+ GB（需 48 GB 或雙卡）。**注意這是蒸餾近似，非真 R1/V4。** 記憶體法則：權重 VRAM ≈ 參數 × bytes/參數（FP16=2、FP8/INT8=1、Q4≈0.5），再 ×~1.2 留 KV/overhead。
來源：https://ollama.com/library/deepseek-r1 ， https://huggingface.co/deepseek-ai/DeepSeek-R1/discussions/19

**主張 6.4（高信心）** — 氣隙下載流程：在連網機 `snapshot_download(repo_id=...)`（或 `hf download`）→ 搬 `~/.cache/huggingface` → 設 `HF_HUB_OFFLINE=1` `TRANSFORMERS_OFFLINE=1` → **拔網實測載入**（已知某些下游庫在 offline 仍會繞過 cache，務必實測）→ 關 vLLM telemetry（`VLLM_NO_USAGE_STATS=1` 等）。
來源：https://huggingface.co/docs/transformers/main/installation ， https://huggingface.co/docs/hub/en/models-downloading ， https://github.com/qdrant/fastembed/issues/615

**主張 6.5（高信心）** — vLLM/SGLang 都提供 **OpenAI 相容 API**（`/v1/chat/completions`），內部腳本指 `localhost` 即可。兩者都有 **DeepSeek 專用 tool-call parser**（vLLM `--enable-auto-tool-choice --tool-call-parser deepseek_v3`；SGLang `--tool-call-parser deepseekv3`）+ reasoning parser——這正是 Phase 2/3「工具查詢」的基礎。
來源：https://docs.vllm.ai/en/latest/features/tool_calling/ ， https://docs.sglang.ai/advanced_features/function_calling.html

**主張 6.6（高信心）** — **context 長度成本**：KV cache 隨 序列長度 × batch 線性成長，是長 context 的主要 VRAM 瓶頸。DeepSeek 的 **MLA（Multi-head Latent Attention）把 KV 壓到 512 維 latent**，比同級 dense MHA 小很多，讓長 context 實際可用；再配 PagedAttention（KV 浪費 60–80%→<4%）與 FP8 KV cache（再省一半）。
來源：https://arxiv.org/pdf/2412.19437 ， https://arxiv.org/pdf/2309.06180 ， https://docs.vllm.ai/en/latest/features/quantization/quantized_kvcache/

> **部署選型建議（依硬體）：**
> - **有 8×H200 級節點** → 自架 **V4-Flash 或 V3.2**（SGLang 優先），開 OpenAI API + tool-call parser，享 1M/128K context。
> - **只有單張 24–48 GB 卡** → 先用 **Ollama distilled R1 32B** 做 PoC 驗證流程，硬體到位再換真模型。架構（前處理 + 工具 + 渲染）兩者通用。
> - 不論哪種，**前處理腳本 + Verdi reader + wavedrom CLI 都要一起打包進氣隙映像**，並把所有相依 vendoring（pip wheel、npm pack、apt 離線包）。

---

## 7. 驗證方法（避免幻覺、客觀評分）

驗證是這套東西能不能上線的關鍵閘門。原則：**任何 LLM 輸出都要能對到確定性 ground-truth。**

**7.1 建 ground-truth 測試集**
- 借鏡 **FIXME**（主張 4.2）：每個 testcase 同時備 (a) VCD、(b) 由確定性解析器算出的「標準答案」（某訊號在某時間的值、某事件的時間戳、failing signal 名）。
- 規模對齊你們的「小檔案」：數百訊號、已知 bug 的 RTL testbench 各 N 個。

**7.2 分階段指標**
| 階段 | 指標 | ground-truth 來源 |
|------|------|------------------|
| Phase 1 時序圖 | WaveJSON 還原正確率（訊號值/邊緣序列 == VCD 解析） | `vcdvcd` 解析比對 |
| Phase 2 問答 | 訊號值/時間查詢命中率、**幻覺率**（回答不存在的訊號比例） | 確定性查詢 API |
| Phase 3 debug | failing-signal 命中率（Top-1/Top-k）、root-cause 概念正確率 | 已知注入 bug |

**7.3 抗幻覺硬規則**
- **訊號名白名單校驗**：任何 LLM 提到的訊號名，事後用解析器查存在性；不存在即標記/駁回。
- **時間戳回查**：LLM 宣稱「t=X 時 sig=V」一律用 API 覆核，矛盾即降信任。
- **「不知道」優於編造**：prompt 與評分都獎勵 LLM 在工具查不到時回 unknown。

**7.4 迴歸**：每次換模型/prompt/前處理版本，跑整個 ground-truth 集，記錄指標變化（沿用本 repo 既有的 verification log 習慣）。

---

## 8. 推薦架構：Agent + 確定性波形查詢工具（而非整檔硬塞）

綜合 §4 全部證據（NVIDIA 先結構化、Siemens/MCP4EDA/VCD-MCP 用工具查詢、tool-use 文獻、表格幻覺風險），**強烈建議走工具型**：

```
┌─────────────────────────────────────────────────────────────┐
│  EDA flow:  VCS sim ──► vcs.log + FSDB                        │
└───────────────┬─────────────────────────────────────────────┘
                │ (Verdi 授權)
        ┌───────▼────────┐   fsdb2vcd / FsdbReader API
        │ 前處理層        │── 訊號索引、時間視窗、訊號篩選
        │ (確定性, Python)│── VCD 解析 (vcdvcd/pyDigitalWaveTools)
        └───────┬────────┘
                │  暴露為 typed tools (JSON schema)
        ┌───────▼─────────────────────────────────────┐
        │ 確定性波形查詢 API / MCP server               │
        │  • list_signals(scope)                       │
        │  • get_signal(name, t0, t1)  ← 只回該訊號跳變 │
        │  • find_edges(name, edge)                     │
        │  • value_at(name, t)                          │
        │  • grep_log(pattern)  ← vcs.log 錯誤定位       │
        │  • render_wavedrom(wavejson) → SVG            │
        └───────┬─────────────────────────────────────┘
                │ OpenAI-compatible tool-calling
        ┌───────▼────────┐
        │ DeepSeek V4-Flash│  純文字、~1M ctx、tool-call parser
        │ (vLLM/SGLang)    │  只做語意推理，事實一律查工具
        └──────────────────┘
```

**為何不整檔硬塞**：①大檔塞不進 context；②表格序列化會幻覺（主張 4.10/4.11）；③工具查詢可被確定性覆核（§7.3）。**小檔案雖然「塞得進」1M context，仍建議工具查詢**——因為驗證性與抗幻覺遠比省一次工具呼叫重要。

---

## 9. 分階段落地路線圖

| Phase | 目標 | 交付 | 驗證關卡 | 主要相依 |
|-------|------|------|---------|---------|
| **0 PoC** | 證明 DeepSeek 讀得懂 VCD 文字 | 小 VCD 直接問答 demo | 人工抽查 | Ollama R1-32B 或自架 V4-Flash |
| **1 時序圖** | FSDB→VCD→WaveJSON→SVG | `fsdb2vcd`+`vcd2wavedrom`+LLM 精修+`wavedrom` 渲染 | WaveJSON 還原率 | Verdi、wavedrom CLI |
| **2 波形問答** | 工具查詢式互動 | MCP/OpenAI tools：`get_signal`/`value_at`/`grep_log` | 命中率 + 幻覺率 | tool-call parser |
| **3 自動 debug** | vcs.log+波形 root-cause | failing-signal 定位 + 解釋 | Top-k 命中率 | 注入 bug 測試集 |

**先做 Phase 1**：它最可驗證、價值即時、且把整條 FSDB→文字→渲染管線打通，為 2/3 鋪路。

---

## 10. 風險與待確認清單（上線前）

**硬限制（不可繞過）**
- ⚠️ **FSDB 必須有 Verdi 授權**才能解；無 Verdi 則整個方案在 FSDB 這端不成立（VCD/log 仍可）。
- ⚠️ DeepSeek 主線**無 vision**——時序圖只能「生成文字再渲染」，不能「看圖 debug」。

**需自行實測/核對的數字（研究中信心較低項）**
- V4-Pro/Flash 確切參數與 1M context（官方頁 403，待直接開瀏覽器確認）— 主張 3.1/3.2。
- V4-Flash 實際 VRAM footprint（無公開基準）— 主張 3.5。
- vLLM/SGLang 是否已在你要氣隙的版本支援 V4/DSA（引擎支援常落後權重）。
- 14b/32b/70b Ollama 體積、SGLang 45%→100% function-call 數字（次級來源）。
- 各論文量化數字（MEIC 93/78%、root-cause 100%@pass5 等）引用前核對 PDF。
- `HF_HUB_OFFLINE` 在你實際技術棧是否真離線載入（拔網實測）。

---

## 附錄：來源彙整

**格式**
- VCD/IEEE 1364：https://en.wikipedia.org/wiki/Value_change_dump ， http://www.pldworld.com/_hdl/2/_ref/se_html/manual_html/c_vcd.html
- GTKWave 格式支援：https://github.com/gtkwave/gtkwave/blob/master/docs/intro/formats.md
- wellen：https://github.com/ekiwi/wellen
- vcs.log：https://www.edaboard.com/threads/synopsys-vcs-log-file-creation.321128/

**FSDB 工具**
- Verdi 波形工具：https://www.synopsys.com/blogs/chip-design/verdi-waveform-utilities.html
- Verdi 平台：https://www.synopsys.com/verification/debug/verdi.html
- fsdb-parse（包裝 FsdbReader）：https://github.com/nayiri-k/fsdb-parse
- fsdb2vcd 原始碼：https://codesearch.isocpp.org/actcd19/main/g/gtkwave/gtkwave_3.3.98-1/contrib/fsdb2vcd/fsdb2vcd_fast.cc

**DeepSeek**
- V4 Preview：https://simonwillison.net/2026/apr/24/deepseek-v4/ ， https://www.atlascloud.ai/blog/ai-updates/what-is-deepseek-v4 ， https://api-docs.deepseek.com/news/news260424
- V3 卡：https://huggingface.co/deepseek-ai/DeepSeek-V3
- 模型總覽：https://www.bentoml.com/blog/the-complete-guide-to-deepseek-models-from-v3-to-r1-and-beyond
- VL2 / Janus：https://arxiv.org/pdf/2412.10302 ， https://arxiv.org/pdf/2501.17811

**部署**
- SGLang DeepSeek：https://github.com/sgl-project/sglang/blob/main/docs/basic_usage/deepseek_v3.md
- vLLM recipe：https://docs.vllm.ai/projects/recipes/en/latest/DeepSeek/DeepSeek-V3.html
- vLLM tool calling：https://docs.vllm.ai/en/latest/features/tool_calling/
- SGLang function calling：https://docs.sglang.ai/advanced_features/function_calling.html
- Ollama R1：https://ollama.com/library/deepseek-r1
- HF offline：https://huggingface.co/docs/transformers/main/installation ， https://huggingface.co/docs/hub/en/models-downloading
- MLA/PagedAttention：https://arxiv.org/pdf/2412.19437 ， https://arxiv.org/pdf/2309.06180

**WaveDrom**
- 引擎/schema：https://github.com/wavedrom/wavedrom ， https://github.com/wavedrom/schema/blob/master/WaveJSON.md ， https://wavedrom.com/tutorial.html
- VCD→WaveDrom：https://github.com/Toroid-io/vcd2wavedrom ， https://github.com/nanamake/vcd2json
- VCD 解析：https://github.com/cirosantilli/vcdvcd ， https://github.com/Nic30/pyDigitalWaveTools

**LLM-debug 先例**
- FVDebug：https://arxiv.org/abs/2510.15906
- FIXME：https://arxiv.org/html/2507.04276v1
- TD-Interpreter：https://arxiv.org/abs/2507.16844
- MEIC：https://arxiv.org/abs/2405.06840
- Root-cause：https://arxiv.org/abs/2507.06512
- VCD MCP server：https://skywork.ai/skypage/en/vcd-waveform-analyzer-hardware-debugging/1981598825536614400
- MCP4EDA：https://github.com/NellyW8/MCP4EDA
- Cadence Verisium：https://www.cadence.com/en_US/home/tools/system-design-and-verification/ai-driven-verification/verisium-debug.html
- Siemens Questa Agentic：https://news.siemens.com/en-us/questa-one-agentic-ai-toolkit/
- 表格幻覺：https://arxiv.org/pdf/2402.17944
- tool-use 抗幻覺：https://arxiv.org/pdf/2412.04141

---

*本報告由 5 路並行 web 研究 + 對抗式查證綜整。信心等級已逐條標注；標「中/低信心」或「待核 PDF」者，上線決策前請依 §10 清單覆核。*
