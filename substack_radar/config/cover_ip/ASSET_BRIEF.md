# 委託 · 補完 IP 表情素材庫（瑞瑞 robot / 達達 owl）

> **給 claude design 的任務。** 為 News Radar 封面合成器（route 3）產出**14 張角色表情裁切圖
> （每隻 7 個表情，一週七天的份量）**，直接放進這個專案的 `cover_ip/assets/`。造型沿用已鎖 v1，
> **不可改配色/媒材/五官比例**。每個表情對應一種發文情緒/類別，搭配合成器的自動水平鏡像
> ＝每隻 14 種視覺變體，feed 不撞臉。
>
> **鏡像友善**：合成器會視標題位置把角色**水平鏡像**以面向標題側 → 請讓姿勢左右翻轉後仍自然，
> **不要放會穿幫的不對稱文字/標誌**（放大鏡、書、眼鏡這類本體配件可留）。

## 造型基準（已鎖 v1，務必一致）
- 參考：`uploads/modelsheet_hero_v1.png`、`uploads/modelsheet_poses_v1.png`（兩角色定裝與表情都在裡面）。
- **瑞瑞 `robot`**：stone-grey `#8A8378` 軟黏土機器人，頭頂旋轉雷達天線+紅球尖+「ping!」火花，**單顆**大玻璃鏡頭眼，短肢，sienna-red `#C84A32` 針織圍巾（= 唯一的紅）。
- **達達 `owl`**：warm stone-grey `#8A8378` 黏土貓頭鷹，兩顆超大雷達盤眼+圓框細金屬眼鏡，暖赭黃喙與三趾腳，sienna-red `#C84A32` 蝴蝶結（= 唯一的紅）。

## 要產出的 14 張（單一角色、全身、表情依下表）
每個表情對應一種**實際發文情緒/類別**（合成器依文章 mode/topic_category/標題語氣自動選；對不到就退該角色預設）。

### 瑞瑞 `robot`（7 個 · 硬題：科技/數據/財報）
| 檔名 | 表情/動作（精確描述）| 對應發文類別 |
|---|---|---|
| `robot_gotcha.png` | holding a magnifier up to its single eye, leaning forward, triumphant little smirk | **預設·硬題**：morning 深度新聞、`us_stocks`/`tw_stocks` 美台股 |
| `robot_presenting.png` | standing upright, one arm gesturing outward to present, confident open posture | `earnings` 財報、`company` 週日公司分析（數據導讀）|
| `robot_curious.png` | leaning in wide-eyed, single lens-eye sparkling huge, antenna perked up, both stubby hands reaching forward eagerly | `ai_model`/`ai_agent`/`ai_application` AI、`tech_product_launch` 新品 |
| `robot_skeptical.png` | one brow raised, radar antenna tilted, arms crossed, doubtful look | `supply_chain` 供應鏈／質疑某個說法 |
| `robot_alert.png` | radar dish spinning fast with motion streaks, single lens-eye wide open, a small alarm spark, urgent leaning stance | 突發／急殺／暴跌行情 |
| `robot_smug.png` | arms crossed, corner-of-mouth smug grin, one eye winking | 打臉行情／「早就說了」語氣 |
| `robot_celebrating.png` | both arms thrown up in triumph, radar dish lit, sparkles around, joyful open-mouthed cheer | 突破／新高／大漲 |

### 達達 `owl`（7 個 · 軟題：人文/反共識/訪談）
| 檔名 | 表情/動作（精確描述）| 對應發文類別 |
|---|---|---|
| `owl_ahha.png` | feathers bursting outward, both wings flung up, one eye huge through a magnifier | **預設·軟題**：`podcast` 訪談萃取（頓悟洞察）|
| `owl_reading.png` | perched, looking down at an open book held in its wings, spectacles glinting, absorbed | `evening` 晚報（獨立選題/書/深度）|
| `owl_pondering.png` | head tilted, one wing under the beak, spectacles glinting, facing a big question mark | 標題「為什麼…？」開放提問 |
| `owl_warm.png` | gentle closed-eye smile, wings softly folded, content and reflective | `culture` 人文／反思 |
| `owl_wink.png` | playful single-eye wink, a wing gesturing knowingly | `contrarian` 反共識 |
| `owl_cautionary.png` | one wing raised palm-out in a 'careful' gesture, brow furrowed over the spectacles, a wary cautioning look | 風險／泡沫／示警類 |
| `owl_teaching.png` | perched upright, one wing pointing out at a small floating diagram, spectacles on, didactic explaining pose | 科普／解析／講解（什麼是…/入門）|

> 合成器選表情邏輯：先 character（robot 硬題 / owl 軟題）→ 再依 topic_category/mode/標題語氣選表情。
> 14 個表情把目前 4 個 mode + 13 個 topic_category + 常見題型（突發/新高/打臉/提問/示警/科普）全包進去了。
> （選表情是按「文章內容」而非「星期幾」——7 張是為了讓一週的 feed 夠豐富、不撞臉。）

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
