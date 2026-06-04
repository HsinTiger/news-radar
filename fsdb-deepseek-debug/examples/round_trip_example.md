# 範例：白話文 ↔ WaveJSON ↔ RTL round-trip

具體走一遍主迴圈，讓「WaveJSON 當共同語言」是什麼意思變得可看。
情境：一個簡單的 req/ack handshake，輸出 `data` 在交握成功後送出。

---

## ① Agent 寫 RTL + 同步輸出「預期」WaveJSON

Agent 產的 RTL（節錄）：
```verilog
module handshake (
  input        clk, rst_n, req,
  output reg   ack,
  output reg [7:0] data
);
  always @(posedge clk or negedge rst_n)
    if (!rst_n)      begin ack <= 0; data <= 0; end
    else if (req)    begin ack <= 1; data <= 8'hA5; end
    else             begin ack <= 0; end
endmodule
```

Agent 同步給的「我打算讓它這樣動」WaveJSON：
```json5
{ signal: [
  { name: "clk",      wave: "p......" },
  { name: "req",      wave: "0.1..0." },
  { name: "ack",      wave: "0..1.0." },
  { name: "data[7:0]",wave: "x..=..x", data: ["A5"] }
]}
```
→ `npx wavedrom` 渲染成圖，工程師看到：req 拉高後**下一拍** ack 起、data 出 A5。

---

## ② 工程師看圖，③ 白話文回饋

> 「不對，我要的是 **ack 跟 data 要跟 req 同一拍**就出來，不要延遲一個 cycle。而且 data 在 ack 拉高的整段都要 hold 住。」

---

## ④ LLM：白話文 → 修正後 WaveJSON（readback 確認，先別改 RTL）

LLM 把白話文具體化成 WaveJSON，渲染後問「我理解成這樣，對嗎？」：
```json5
{ signal: [
  { name: "clk",      wave: "p......" },
  { name: "req",      wave: "0.1..0." },
  { name: "ack",      wave: "0.1..0." },
  { name: "data[7:0]",wave: "x.=..x.", data: ["A5"] }
]}
```
差異一眼可見：`ack`/`data` 從「req 後一拍」變成「與 req 同拍」，且 data hold 整段交握。

---

## ⑤ 工程師確認

> 「對，就是這樣。」

---

## ⑥ 對齊後的 WaveJSON 餵回 LLM（grounding）+ ⑦ 改 RTL

LLM 以那份**具體波形**為 spec，看出原 RTL 是「時序邏輯（posedge 後一拍才變）」造成延遲，改成**組合邏輯立即輸出**：
```verilog
module handshake (
  input        clk, rst_n, req,
  output       ack,
  output [7:0] data
);
  assign ack  = req;                 // 同拍
  assign data = req ? 8'hA5 : 8'h00; // 交握期間 hold A5
endmodule
```

---

## Phase 3：跑 VCS 後驗證（intended vs actual）

工程師 `vcs -debug_access+all` 跑模擬，testbench 用 native dump：
```verilog
initial begin $dumpfile("dump.vcd"); $dumpvars(0, tb); end
```
確定性工具把 `dump.vcd` 的同訊號同視窗轉成 actual WaveJSON，`diff_waves(intended, actual)`：

- 若 actual 與 ④ 的 intended 完全一致 → ✅ RTL 符合意圖。
- 若分歧（例如 data 晚一拍）→ 工具回報「第一個分歧 @ cycle N, signal=data」→ LLM 解釋成因 → 回主迴圈修。

**重點**：分歧點是**工具確定性算出來的**，LLM 只負責「解釋為什麼 + 怎麼改」——這就是抗幻覺的關鍵分工。

---

## 這個範例證明了什麼

1. **白話文的歧義**（「ack 要出來」沒講同拍/延遲）被 WaveJSON **具體化**後消失。
2. **同一份 WaveJSON** 給人看（圖）也給模型讀（規格），雙方鎖在同一個物件上。
3. **readback 確認**（④渲染回去問「對嗎」）擋掉「模型自以為懂、其實改錯」。
4. **事後 debug** 用確定性 `diff_waves` 定位、LLM 解釋——不讓模型自己「看」原始波形捏結論。
