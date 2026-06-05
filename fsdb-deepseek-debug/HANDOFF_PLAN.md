# 交接計畫：工網內 Claude Code / DeepSeek RTL 波形 Debug Agent

> 目的：把這整包丟進**氣隙工網**，讓 agent 拿到「RTL/UVM 原始碼路徑 + VCD + vcs.log + C phyUD hex dump」就能 debug、定位 RTL vs golden 分歧、並產時序圖。
> 本檔 = 計畫 + 詳細步驟 + 目錄結構 + 上線前檢查表。所有確定性工具已在開發環境**實測通過**（見 §6）。

---

## 1. 你會給 agent 的四種輸入 → 對應處理

| 輸入 | 性質 | agent 怎麼處理 |
|------|------|---------------|
| RTL checker / UVM source（路徑） | 純文字 | 直接讀 |
| VCD | IEEE 1364 ASCII | **只透過 `tools/vcd.py` 查**（list/value/changes/wavejson） |
| vcs.log | 純文字 | `grep` 第一個 error + 時間/訊號 |
| **C phyUD dump（hex）** | 純文字、**golden 參考** | `tools/cmodel_hex.py` 載入；`tools/compare.py` 找 RTL vs golden 第一個分歧 |

**核心 debug 邏輯**：log 定位嫌疑時間/訊號 → `compare.py` 用 golden 算出**確定性分歧點** → agent 對照 RTL 解釋成因、提修法。分歧點由工具決定，不由模型「看波形」決定 → 抗幻覺。

---

## 2. 目錄結構（整包搬進工網）

```
fsdb-deepseek-debug/
├── README.md                      # 總覽、兩條迴圈
├── HANDOFF_PLAN.md                # 本檔：部署計畫與步驟
├── EVALUATION.md                  # 可行性研究（引用 + 信心等級）
├── VALUE_FOR_DESIGNERS.md         # 對 RTL designer 的價值與導入策略
├── skill/
│   └── SKILL.md                   # 給 agent 的 skill（frontmatter + 流程 + 抗幻覺規則）
├── tools/                         # 確定性工具（純 Python stdlib，離線）
│   ├── vcd.py                     # VCD 解析/查詢/轉 WaveJSON  ✅ 已測
│   ├── cmodel_hex.py              # C phyUD hex 載入器（含格式 adapter）✅ 已測
│   ├── compare.py                 # RTL vs golden 第一個分歧  ✅ 已測
│   ├── fsdb2vcd.sh                # Verdi FSDB→VCD 包裝
│   └── render_wavedrom.sh         # WaveJSON→SVG 包裝（npx wavedrom-cli）
└── examples/
    └── round_trip_example.md      # 白話文↔WaveJSON↔RTL 走一遍
```

---

## 3. 上線前要 stage 進氣隙網路的東西（Claude Code 版）

> 目標環境＝**工網內 Claude Code + tcsh + Linux**。Claude Code 本身就是 agent，
> **不需要自架模型、不需要 vLLM/SGLang、不需要搬權重**——它已有檔案讀取、bash、tool-use。

| 項目 | 怎麼準備 | 備註 |
|------|---------|------|
| 本目錄 `fsdb-deepseek-debug/` | git/打包搬入工網 | 工具是 Python stdlib，無 pip 相依 |
| Python 3.8+ | 工網內通常已有 | `tools/` 只用標準庫 |
| Claude Code | 工網內既有 | agent 本體 |
| Verdi | 工網內既有 EDA | 只有要解 FSDB 才需要；純 VCD 不需要 |
| wavedrom-cli | 連網機 `npm pack wavedrom-cli` 搬入後 `npm i -g` | 只有要產 SVG 圖才需要；查詢/debug 不需要 |

> **最小可跑集合**：Python + 本目錄 + Claude Code。要把 FSDB 轉成 VCD 才需要 Verdi；要產時序圖才需要 wavedrom。

---

## 4. Bring-up 步驟（Claude Code + tcsh）

```tcsh
# (1) 取得這包，放到一個固定路徑
setenv RTLDBG /proj/eda/tools/fsdb-deepseek-debug
cd $RTLDBG

# (2) 自我測試：確認確定性工具在本機可跑（預期 ALL PASS，見 §6）
sh tools/_selftest.sh

# (3) 安裝 skill 給 Claude Code（兩種擇一）
#  A. 永久安裝：把 skill 放進 Claude Code 的 skills 目錄，工具也一起
mkdir -p ~/.claude/skills/rtl-waveform-debug
cp skill/SKILL.md ~/.claude/skills/rtl-waveform-debug/
cp -r tools       ~/.claude/skills/rtl-waveform-debug/
#  B. 當次使用：直接在 $RTLDBG 目錄裡啟動 claude，CWD 即 package 根，
#     SKILL.md 內的相對路徑 tools/xxx 就能用（day-1 最省事，建議先用這個）
cd $RTLDBG && claude
```

> tcsh 注意：`tools/*.sh` 都是 `#!/bin/sh`，用 `sh tools/x.sh` 呼叫即可，不受登入 shell 是 tcsh 影響。

---

## 5. Agent 的典型一次 debug（你給它的指令長相）

```
這個 testcase 失敗了，幫我找根因：
  RTL:    /proj/x/rtl/phy_ud.sv
  UVM:    /proj/x/tb/phy_ud_checker.sv
  VCD:    /proj/x/sim/run123/dump.vcd        (clk = tb.phy_ud.clk)
  log:    /proj/x/sim/run123/vcs.log
  golden: /proj/x/cmodel/phyUD.hex           (node = phyUD)
```
agent 依 `SKILL.md`：grep log → `vcd.py list` 驗證訊號 → `compare.py ... --skip-x` 取得第一個分歧 `{index, clk_time, golden, actual}` → `vcd.py changes/value` 看周邊控制訊號 → 對照 RTL 指出哪個 always/assign/FSM 出錯 → 提修法。

---

## 6. 已驗證的工具行為（開發環境實測）

以一個 req/ack handshake 的 VCD（data 在 t=15 變 `8'hA5`）測試：

| 指令 | 預期 | 實測 |
|------|------|------|
| `vcd.py list dump.vcd` | 列出 4 訊號 | ✅ tb.handshake.{ack,clk,data[7:0],req} |
| `vcd.py value dump.vcd tb.handshake.data 15` | `10100101` | ✅ |
| `vcd.py value dump.vcd ack 12` | `0` | ✅ |
| `vcd.py value dump.vcd nosuchsig 10` | 乾淨 ERROR、rc 2 | ✅（不丟 traceback） |
| `vcd.py wavejson ... --clk clk --sig req ack data` | clk=`p..`,data=`==.`+`[xxxxxxxx,A5]` | ✅ |
| `compare.py ... golden_ok.hex --skip-x` | match=true、rc 0 | ✅ |
| `compare.py ... golden_bad.hex --skip-x` | 分歧 idx1 @t=25 golden FF/actual A5、rc 3 | ✅ |
| `render_wavedrom.sh wave.json5 wave.svg` | 產生合法 SVG | ✅（42 KB SVG） |

> 建議在工網內 stage 完，照這張表重跑一次當 smoke test。

---

## 7. 分階段導入（先建立信任，再擴大）

1. **Phase 1（先做）— golden-driven debug（迴圈 A）**：有 C-model 當 ground-truth，`compare.py` 確定性定位，最容易讓 designer 信任、最快見效。
2. **Phase 2 — 互動波形問答**：把 `vcd.py` 查詢包成 agent 工具，回答「X 訊號在 T 做什麼」。
3. **Phase 3 — 設計協同（迴圈 B）**：白話文↔WaveJSON↔RTL，定位「時序對齊副駕 + 文件自動化」，不是自動寫 RTL。

每階段用 `EVALUATION.md` §7 的指標量測（訊號名幻覺率、分歧命中率、波形還原率），拿數字對部門報告。

---

## 8. 上線前檢查表

- [ ] `python3 tools/vcd.py list <你的真實 dump.vcd>` 能列出訊號（確認你的 VCD 版本相容）。
- [ ] 用你真實的 `phyUD.hex` 跑 `cmodel_hex.py`，確認格式被正確解析；不對就改 `cmodel_hex.py` 標記區塊。
- [ ] `compare.py` 在一個**已知答案**的 testcase 上回報正確分歧點（建你的 ground-truth 集）。
- [ ] 模型在**拔網**狀態下能載入並回應（`HF_HUB_OFFLINE=1` 實測）。
- [ ] tool-call parser 正常：agent 真的會呼叫 `vcd.py`/`compare.py` 而非自己編值。
- [ ] （要產圖才需）wavedrom-cli tarball 已 vendoring、`render_wavedrom.sh` 可離線跑。
- [ ] （來源是 FSDB 才需）`which fsdb2vcd` 找得到、`fsdb2vcd.sh` 可轉。
- [ ] 確認 agent 在「訊號查無」時會回報錯誤、不幻覺（拿一個錯訊號名測它）。

---

## 9. 兩個沒變的硬限制

- ⚠️ **FSDB 要 Verdi 授權**才能轉；但你既有 VCS/Verdi flow 通常成立。只要 VCD/log/hex 就完全不需要 Verdi。
- ⚠️ **模型純文字、無 vision**：時序圖是「生成 WaveJSON → 渲染」，不是「看圖」。debug 靠 `compare.py` 的確定性數字，不是讓模型看波形。
