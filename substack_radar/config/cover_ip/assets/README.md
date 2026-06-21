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

## 怎麼生新素材（用你的 ChatGPT / nanobanana）
1. 跑 pipeline 任一篇 → 它的 `cover_prompts.md` 裡有 route-1 的 D5 角色 prompt（含固定造型 + 場景）。
   或直接拿 `image_brain.CHARACTERS[species]["look"]` + 想要的表情 hint。
2. 加一句「single character, **transparent background**（或 plain #F2EEE5 background）, full body, centered, no title text」。
3. 丟 ChatGPT image / nanobanana 生圖 → 存成上面的命名 → 放進這個資料夾。完成。

## 目前狀態（2026-06-21）
- `robot_gotcha_sm.png`、`owl_ahha_sm.png`：claude design v1 裁切的**縮圖**（250–270px），MVP 測試用。
- ⏳ **待補**：全尺寸 `robot_gotcha.png` / `owl_ahha.png`（design 專案裡有，但超過 MCP 256KB 取檔上限 →
  請從 claude.ai 下載拖進來，或用上面流程重生）＋其餘表情（skeptical/smug/wink/pondering）。
