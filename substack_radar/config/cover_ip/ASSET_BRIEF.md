# 委託 · 補完 IP 表情素材庫（瑞瑞 robot / 達達 owl）

> **給 claude design 的任務。** 為 News Radar 封面合成器（route 3）產出**全套 10 張角色表情裁切圖**
> （每隻 5 個表情），直接放進這個專案的 `cover_ip/assets/`。造型沿用已鎖 v1，**不可改配色/媒材/五官比例**。
>
> **鏡像友善**：合成器會視標題位置把角色**水平鏡像**以面向標題側 → 請讓姿勢左右翻轉後仍自然，
> **不要放會穿幫的不對稱文字/標誌**（放大鏡、書、眼鏡這類本體配件可留）。

## 造型基準（已鎖 v1，務必一致）
- 參考：`uploads/modelsheet_hero_v1.png`、`uploads/modelsheet_poses_v1.png`（兩角色定裝與表情都在裡面）。
- **瑞瑞 `robot`**：stone-grey `#8A8378` 軟黏土機器人，頭頂旋轉雷達天線+紅球尖+「ping!」火花，**單顆**大玻璃鏡頭眼，短肢，sienna-red `#C84A32` 針織圍巾（= 唯一的紅）。
- **達達 `owl`**：warm stone-grey `#8A8378` 黏土貓頭鷹，兩顆超大雷達盤眼+圓框細金屬眼鏡，暖赭黃喙與三趾腳，sienna-red `#C84A32` 蝴蝶結（= 唯一的紅）。

## 要產出的 10 張（單一角色、全身、表情依下表）
| 檔名 | 角色 | 表情/動作（精確描述，對齊 pipeline）| 用於 |
|---|---|---|---|
| `robot_gotcha.png` | 瑞瑞 | holding a magnifier up to its single eye, leaning forward, triumphant little smirk | 揭露/數據抓包（預設）|
| `robot_skeptical.png` | 瑞瑞 | one brow raised, radar antenna tilted, arms crossed, doubtful look | 質疑某個說法 |
| `robot_smug.png` | 瑞瑞 | arms crossed, corner-of-mouth smug grin, one eye winking | 早就說了 |
| `robot_curious.png` | 瑞瑞 | leaning in wide-eyed, single lens-eye sparkling huge, antenna perked up, both stubby hands reaching forward eagerly | 新東西/科普 |
| `robot_presenting.png` | 瑞瑞 | standing upright, one arm gesturing outward to present, confident open posture | 拆解/數據導讀 |
| `owl_ahha.png` | 達達 | feathers bursting outward, both wings flung up, one eye huge through a magnifier | 頓悟/洞察（預設）|
| `owl_wink.png` | 達達 | playful single-eye wink, a wing gesturing knowingly | 了然/反共識 |
| `owl_pondering.png` | 達達 | head tilted, one wing under the beak, spectacles glinting, facing a big question mark | 開放提問 |
| `owl_reading.png` | 達達 | perched, looking down at an open book held in its wings, spectacles glinting, absorbed | 深度/書評/分析 |
| `owl_warm.png` | 達達 | gentle closed-eye smile, wings softly folded, content and reflective | 反思/人文/哲學 |

## 檔案規格（重要 —— 決定能不能自動同步回 codebase）
- **背景透明**（PNG alpha）為佳；cream `#F2EEE5` 純底也可（合成器會自動 key 掉）。
- **單一角色、全身、置中**，四周留少量空白；**畫面內不要任何文字、不要場景道具**（放大鏡/眼鏡這類角色本體配件可留）。
- 直幅，短邊約 **800px**。
- **每檔 ≤ 240KB**（請用 PNG 量化/壓縮壓到此大小）——這樣我才能透過 MCP（256KB 取檔上限）直接抓回專案、零手動下載。
  - 若壓不到 240KB 又要保畫質：另存一份 `{name}_web.png`（≤240KB 給我同步）+ 保留全尺寸 `{name}.png`（信哥手動下載）。
- 命名**完全照上表**，放進 `cover_ip/assets/`。

## 驗收
- 6 張跨檔角色一致、對得上 v1 定裝；透明去背乾淨（邊緣無 cream 殘框）。
- 放大到封面約 500–670px 高仍清晰。
- 檔名與路徑完全正確（pipeline 靠檔名自動探查，錯一個字就認不得）。
