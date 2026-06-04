# 核心 Skill：RTL ↔ 波形 白話文協同迴圈

> 這是本工具的**主軸**（不是事後 FSDB 分析）。設計目標：讓 RTL designer 用**白話文**跟 agent 溝通，
> 用 **WaveJSON 當「人看的圖」+「餵回模型的精確規格」雙用 grounding artifact**，把意圖歧義收斂掉。

## 為什麼用 WaveJSON 當中介（核心洞見）

- 白話文有歧義（「output 拉高」——第幾拍？持續幾拍？哪個 clk edge？）。
- WaveJSON 是**具體、可渲染、可被模型逐字元解析**的中間物。
- 把「白話文 → WaveJSON → 渲染給人確認 → 同一份餵回模型」做成 round-trip，等於**readback 確認**：
  人看到的圖 == 模型理解的規格，兩邊鎖在同一個具體物上。

## 主迴圈

```
① Agent 寫 RTL ──同步──► 產生 WaveJSON（標出「我打算讓它這樣動」）──► npx wavedrom 渲染 SVG
                                                                          │
                                                        ② 工程師看圖
                                                                          │
                                          ③ 工程師白話文回饋（「第3拍 ack 才該拉高」）
                                                                          │
                          ④ LLM：白話文 → 修正後 WaveJSON ──► 渲染：「我理解成這樣，對嗎？」
                                                                          │  ← readback 確認，擋誤解
                                          ⑤ 工程師點頭 / 再修（回③）
                                                                          │
                          ⑥ 對齊後的 WaveJSON 餵回 LLM（grounding）
                                                                          │
                          ⑦ LLM 依「對齊後的具體波形」改 RTL ──────► 回①
```

## System prompt（草稿）

```
你是 RTL 前端設計協作 agent。你跟工程師用「時序圖」對齊意圖，而非只靠文字。

迴圈規則：
1. 每次你寫/改 RTL，同步輸出一份 WaveJSON，標出該模組「預期的」時序行為
   （至少含 clk、重要輸入、輸出、handshake/control 訊號）。
2. 工程師會用白話文給回饋。你先把白話文「翻譯成 WaveJSON」並渲染給他看，
   問一句「我理解成這樣，對嗎？」——不要跳過這個確認直接改 RTL。
3. 工程師確認後，以那份 WaveJSON 為準（that is the spec）去改 RTL。
4. WaveJSON 與 RTL 必須一致：訊號名、bit width、時序關係要對得上你寫的 code。
5. 不確定就把不確定點畫進波形（用 x 標未定）並問，不要臆測。
6. 你只生成 WaveJSON 文字；渲染交給 wavedrom 工具，你不要描述像素。
```

## WaveJSON 對齊規則（給模型的硬約束）

- `wave` 字串長度 == 你 RTL 想表達的 cycle 數；`.` 表延續前一 cycle 值。
- 多 bit 匯流排用 `=` + `data:[...]`，`=`/數字槽數量**必須**等於 `data` 陣列長度（最常見的生成錯誤）。
- 訊號名與 bit width 必須對得上 RTL 宣告（`reg [7:0] data` → WaveJSON 標 `data[7:0]`）。
- handshake 標清楚因果：req/ack、valid/ready 的相對 edge 關係要在波形上看得出來。

## Phase 3：事後 debug 迴圈（連模擬一起驗證）

當 RTL 真的跑 VCS 掛了，把「實際」對上「預期」：

```
VCS sim ──► dump.vcd (native $dumpvars) 或 FSDB(+Verdi fsdb2vcd) + vcs.log
                │
        確定性工具：把 actual 波形轉成 WaveJSON 子集（同訊號、同視窗）
                │
        LLM 對照：intended WaveJSON  vs  actual WaveJSON  +  grep_log(error)
                │
        指出第一個分歧 cycle / 訊號 ──► 推斷 RTL bug ──► 改 code ──► 回主迴圈
```

確定性工具（typed，抗幻覺）：
| 工具 | 用途 |
|------|------|
| `render_wavedrom(wavejson)` | WaveJSON → SVG（`npx wavedrom`） |
| `nl_to_wavejson(text, context)` | 白話文 → WaveJSON（LLM，需 readback 確認） |
| `vcd_to_wavejson(vcd, signals, t0, t1)` | 實際 VCD 子集 → WaveJSON（確定性，給 overlay） |
| `diff_waves(intended, actual)` | 找第一個分歧 cycle/訊號 |
| `grep_log(pattern)` | vcs.log 錯誤定位 |
| `value_at(signal, t)` / `get_signal(name,t0,t1)` | 波形事實查詢（覆核用） |

## 抗幻覺驗證（每輪自動）

- **訊號名對齊**：WaveJSON 的訊號名逐一對 RTL 宣告 / VCD signal list 校驗，不存在即標紅。
- **data 槽校驗**：渲染前檢查 `=`/數字槽數 == `data` 長度。
- **intended vs actual**：debug 時一律用 `diff_waves` 給出確定性分歧點，LLM 只負責「解釋為什麼」。

## 待實作

- [ ] `nl_to_wavejson` + readback 渲染（主迴圈 ③④）
- [ ] `npx wavedrom` 離線封裝（render）
- [ ] RTL ↔ WaveJSON 一致性 linter（訊號名/width/data 槽）
- [ ] Phase 3：`vcd_to_wavejson` + `diff_waves`（native $dumpvars 路徑優先，FSDB 走 fsdb2vcd）
