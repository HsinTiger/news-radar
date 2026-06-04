# 這套 flow 對 RTL designer 的價值（與「取代」的誠實邊界）

部門背景：一群 RTL circuit designer，日常 VCS 模擬。問題：這工具怎麼幫他們、能不能取代傳統前端設計？

## 先講結論：是「放大」不是「取代」

以 2026 的模型能力（含 DeepSeek V4），**不能取代前端 designer**，但能**吃掉他們 30–60% 的低價值時間**，把人推向更高槓桿的工作。原因很實在：
- 前端設計的難處不在「打字寫 Verilog」，而在**微架構決策、時序收斂、跨模組協定、corner case 推理**——這些需要對整顆晶片與專案脈絡的判斷，模型還做不到可信賴的程度。
- 但 designer 每天**真正花掉的時間**很多在低價值環節：盯波形找一個 off-by-one、重寫 testbench、把腦中時序畫成文件、追一條 X-propagation……這些正是這套 flow 的甜蜜點。

## 對應到 designer 日常的具體幫助

| Designer 日常痛點 | 這套 flow 怎麼幫 | 階段 |
|------------------|-----------------|------|
| 腦中想好時序，但寫成 RTL 容易 off-by-one（同拍/延遲拍搞錯） | 白話文 → WaveJSON 先**畫出來確認**，再生 RTL，意圖在 code 前就對齊 | ① 主迴圈 |
| Spec / 時序圖跟 code 漸漸不同步 | RTL 與 WaveJSON 綁在一起生成，**圖即文件、文件即規格** | ① |
| Review 時口頭講時序講不清、雞同鴨講 | 用同一份可渲染 WaveJSON 溝通，人跟人、人跟 agent 都看同一個具體物 | ① |
| 模擬掛了，肉眼在 Verdi 翻幾千訊號找 failing point | 確定性 `diff_waves` 自動定位**第一個分歧 cycle/訊號**，LLM 解釋成因 | ② debug |
| vcs.log 幾萬行錯誤訊息要人工 grep | `grep_log` + LLM 關聯錯誤↔波形↔RTL | ② |
| 寫 directed testbench 把某時序逼出來 | 從 WaveJSON 反向生 stimulus / assertion 草稿 | 延伸 |

**量化期待（保守）**：debug 定位時間、波形↔意圖溝通成本、文件維護成本是最先見效的三塊。別承諾「設計自動化」，要承諾「**debug 與對齊的加速**」——這是可驗證、可被工程師信任的。

## 為什麼「取代」現在不成立（要對主管誠實的點）

1. **正確性責任無法外包**：RTL 是要流片的，幻覺成本極高。模型生成的 RTL/波形**必須**經過確定性驗證（VCS 模擬、`diff_waves`、assertion）才可信——所以人仍是把關者。
2. **判斷密集而非打字密集**：pipeline 切幾級、handshake 用 valid/ready 還是 credit、clock domain crossing 怎麼處理——這些是經驗與全域權衡，不是模型強項。
3. **模型無 vision、無晶片全局記憶**：它看不到你的 floorplan、不知道隔壁組的協定改了——脈絡仍在人腦裡。

## 正確的導入策略（避免「自動化卻沒人信」的失敗）

1. **先做 debug 加速（迴圈②）**，不是設計生成。debug 有確定性 ground-truth（模擬結果），最容易讓 designer 信任、最快見效。
2. **再做設計協同（迴圈①）**，定位成「資淺工程師的時序對齊副駕」與「文件自動化」，不是「自動寫 RTL」。
3. **每一步都留人類 sign-off**：模型產出 → VCS 驗證 → 人確認。把模型當「會畫圖會找 bug 的實習生」，不是「資深 designer」。
4. **用 §EVALUATION 的驗證集量測**：訊號名幻覺率、failing-signal 命中率、波形還原率——拿數字說服部門，而不是 demo 感覺。

## 對部門的一句話定位

> 「它不取代你的判斷，它取代你**盯波形、追 log、畫時序圖、同步文件**的那幾個小時。
> 你把省下的時間花在微架構與時序收斂——那才是 designer 的價值。」

延伸閱讀：[`SKILL_DRAFT.md`](./SKILL_DRAFT.md)（迴圈與工具）、[`examples/round_trip_example.md`](./examples/round_trip_example.md)（實際走一遍）、[`EVALUATION.md`](./EVALUATION.md)（可行性與驗證）。
