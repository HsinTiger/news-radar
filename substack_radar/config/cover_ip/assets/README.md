# 角色素材庫（Cover System route 3）

`character_cover.py` 的合成器吃這個資料夾的角色裁切圖，貼到 cream 封面上、再排標題。
**丟一張命名正確的 PNG 進來，就自動被認得、零改 code。**

## 命名慣例
```
{species}_{expression}.png        完整解析度（優先用）
{species}_{expression}_sm.png     縮圖後備（MVP 暫用，會被同名全尺寸蓋過）
```
- `species`：`robot`（瑞瑞）/ `owl`（達達）。
- `expression`：robot = `gotcha`(預設) / `skeptical` / `smug`；owl = `ahha`(預設) / `wink` / `pondering`。
- 找不到指定表情 → 退到該角色的預設表情 → 再退到任一張；都沒有 → 改用純文字保底封面。

## 規格
- 單一角色、**透明背景或 cream `#F2EEE5` 純底**（合成器會把 cream key 成透明，cream 封面上完全無縫）。
- 直幅、角色置中、四周留一點空白；建議短邊 ≥ 900px（封面會把角色放到約 500–670px 高，太小會糊）。
- 維持 v1 定裝：stone-grey 身體、單一 sienna 圍巾/蝴蝶結、黏土質感。造型基準見 `../modelsheet_*_v1.png`。

## 怎麼補新素材

這是低頻的資產維護，不是每日寫稿流程。依 `../modelsheet_*_v1.png` 的既有角色造型
製作透明背景、全身、置中的新表情，依上述規則命名後放進本資料夾；每日 pipeline
只選用現有 PNG，不建立或輸出任何生圖 prompt。

## 目前狀態（2026-06-22）✅ 全 14 表情上線
全 14 張**全尺寸透明去背**裁切圖（短邊 800、RGBA）都在這個資料夾，由 claude design 從信哥用
ChatGPT/Gemini 生的圖處理，經本機（信哥下載整包專案到 ~/Downloads）拷進 repo：
- **robot（7）**：gotcha、skeptical、smug、curious、presenting、alert、celebrating
- **owl（7）**：ahha、wink、warm、pondering、cautionary、reading、teaching

實測：合成器自動選表情（依 topic/mode/標題語氣）+ 水平鏡像讓角色面向標題，封面清晰、構圖正確。
要再加表情/姿勢，照上面命名慣例丟新檔即可（`_web` 後綴也認得）。
