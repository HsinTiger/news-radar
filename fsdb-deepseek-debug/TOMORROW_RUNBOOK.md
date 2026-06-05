# 明天的 Runbook：用 Claude Code + 你的 FSDB 找出設計問題

> 環境：工網內 **Claude Code + tcsh + Linux**。目標：把你手上的 **FSDB + 設計**餵給
> agent，讓它確定性地指出「哪個訊號、在哪個 cycle、為什麼錯」。

---

## 0. 選實驗對象（很重要，先別挑最難的 bug）

**Day-1 請挑一個你「已經知道答案」的小 failing case**（你清楚根因的那種）。
理由：先驗證 agent + 工具給的結論跟你已知的一致，建立信任後，再拿它去咬未知的 bug。
挑選條件：
- 設計小（數百訊號內，對應你說的小檔案）。
- 有明確症狀（某訊號在某時間值不對 / log 有 UVM_ERROR / scoreboard mismatch）。
- 最好有一個 **C-model 中間節點 golden（如 phyUD 的 hex）** 可比對——這條最強。沒有也能做（走 log+查詢）。

---

## 1. 環境準備（tcsh）

```tcsh
# 設工具包路徑（把 rtl-waveform-debug 放到固定位置）
setenv RTLDBG /proj/eda/tools/rtl-waveform-debug

# 自我測試：確認確定性工具可跑（預期 ALL PASS）
sh $RTLDBG/tools/_selftest.sh

# 確認 Verdi 在（要把 FSDB 轉 VCD 才需要）
which fsdb2vcd
#   找不到就先設 Verdi：
#   setenv VERDI_HOME /tools/synopsys/verdi/<ver>
#   set path = ($VERDI_HOME/bin $path)
```

---

## 2. 把 FSDB 轉成 VCD（agent 看得懂的純文字）

```tcsh
cd <你的 sim 目錄>
sh $RTLDBG/tools/fsdb2vcd.sh your.fsdb your.vcd
```

> 小檔案整顆轉沒問題。若訊號很多，只轉你要看的 scope（`fsdb2vcd -h` 看篩選選項），避免 VCD 過大。

**先自己 sanity check 一下**（也順便找出 clk 的完整名字，等下要給 agent）：
```tcsh
python3 $RTLDBG/tools/vcd.py list your.vcd | head -50
python3 $RTLDBG/tools/vcd.py list your.vcd --match clk
```

---

## 3. 啟動 Claude Code 並掛上 skill

```tcsh
# 最省事：直接在工具包目錄啟動，skill 的相對工具路徑就能用
cd $RTLDBG
claude
```
（或永久安裝：`mkdir -p ~/.claude/skills/rtl-waveform-debug && cp skill/SKILL.md ~/.claude/skills/rtl-waveform-debug/ && cp -r tools ~/.claude/skills/rtl-waveform-debug/`）

---

## 4. 給 agent 的指令（兩種情境）

### 情境 A：你有 C-model golden（最強，建議 Day-1 用這個）
```
這個 testcase 失敗了，幫我找根因。用 rtl-waveform-debug skill。
  RTL:    /proj/x/rtl/phy_ud.sv
  VCD:    <你的 sim 目錄>/your.vcd      （clk = <第2步查到的 clk 全名>）
  log:    <你的 sim 目錄>/vcs.log
  golden: /proj/x/cmodel/phyUD.hex      （node = phyUD，要比對的 RTL 訊號 = tb...phyUD）
先用 compare.py 找出 RTL 對 golden 的第一個分歧，再對照 RTL 解釋為什麼、怎麼改。
```

### 情境 B：沒有 golden，只有 log + 波形
```
這個 testcase 失敗了，幫我找根因。用 rtl-waveform-debug skill。
  RTL:  /proj/x/rtl/phy_ud.sv
  VCD:  <你的 sim 目錄>/your.vcd        （clk = <clk 全名>）
  log:  <你的 sim 目錄>/vcs.log
先 grep log 找第一個 error 的時間與訊號，再用 vcd.py 查那個訊號與它的 driver
在出錯時間附近的值，定位是哪個 always/assign/FSM 出錯。
```

---

## 5. agent 會怎麼「看出問題」（它實際做的事）

**情境 A（有 golden）：**
1. `grep` vcs.log → 找到第一個 error 與時間 T。
2. `vcd.py list --match <node>` → 確認你給的訊號名真的存在（不存在就回報，不會亂編）。
3. `compare.py your.vcd --clk <clk> --sig <node> phyUD.hex --node phyUD --skip-x`
   → **工具確定性回報**：`first_divergence = {index, clk_time, golden=XX, actual=YY}`
   → 意思是「第 N 個 clk（時間 clk_time）RTL 算出 YY，但 C-model 黃金值是 XX」。
4. `vcd.py changes <node> <T-Δ> <T+Δ>` + 對相關 handshake/control 訊號 `vcd.py value`
   → 看那個 cycle 前後發生什麼。
5. 對照 `phy_ud.sv`：指出是哪個 always 區塊 / assign / FSM 狀態算錯、為什麼（例如少了一個 enable 條件、用了上一拍的值、reset 沒清乾淨）。
6. 提修法；你改完重跑，再 `compare.py` 應回 match（rc 0）。

**情境 B（無 golden）：** 把第 3 步換成「從 log 的 mismatch 訊息拿到預期值，用 `vcd.py value` 查實際值，逐一往訊號的 driver 上游追」。

**關鍵**：分歧點在哪、值是多少，都是 **`compare.py`/`vcd.py` 算的**；agent 只負責「解釋為什麼 + 怎麼改」。所以它不會幻覺出不存在的訊號或時序——這正是它比「貼截圖到 ChatWeb 求方向」可靠的地方。

---

## 6. 驗收（因為你挑的是已知答案的 case）

- agent 報的分歧 cycle / 訊號，跟你已知的根因**對得上** → 工具鏈可信，進下一個未知 case。
- 對不上 → 看是 (a) clk 名字 / golden 對齊（試 `--skip-x` 或 `--t0`），還是 (b) cmodel hex 格式沒被正確解析（跑 `python3 $RTLDBG/tools/cmodel_hex.py phyUD.hex --node phyUD` 檢查，必要時改 `cmodel_hex.py` 標記區塊）。

---

## 7. 今天先別期待的事

- 別期待它「看波形圖」debug——它讀的是**文字**（VCD/log/hex），圖是另外渲染給你看的。
- 別期待它一鍵改好流片級 RTL——Day-1 目標是**準確定位 + 合理解釋**，改碼你要 review。
- 別把 FSDB 直接丟給它——它讀不懂二進位，**一定要先 `fsdb2vcd` 轉成 VCD**。

---

## 一句話總結明天

> 1) `_selftest.sh` 確認工具 OK → 2) `fsdb2vcd.sh` 把你的 fsdb 轉 vcd → 3) `cd $RTLDBG; claude` →
> 4) 用情境 A/B 的模板貼上你的檔案路徑 → 5) 看 agent 用 `compare.py`/`vcd.py` 指出分歧 cycle 與 RTL 成因 →
> 6) 跟你已知答案對驗收。
