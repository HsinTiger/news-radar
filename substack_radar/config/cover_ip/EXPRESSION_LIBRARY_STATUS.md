# 表情素材庫 · 產出狀態與補完指南 (v0.1)

> 來源只有 `uploads/modelsheet_hero_v1.png` + `modelsheet_poses_v1.png` 兩張定裝。
> claude design 只能**從既有 v1 畫面裁切**（無法無中生有新姿勢）。下面誠實列出：哪些已交付、哪些需用你的圖像模型補生。

## ✅ 已交付（7 個 · 真的存在於 v1，已去背/壓縮/命名）
每個都有 `{name}.png`（全尺寸短邊 800，畫質母檔，>240KB 需手動下載）
+ `{name}_web.png`（短邊 ~480–645、≤240KB）。

> ⚠️ 同步注意（2026-06-22 實測）：MCP `get_file` 上限 256KB 是壓在 **base64** 上（≈192KB 解碼），
> 連 `_web`（≤240KB）都會被截斷、抓不回來。**只有 ≤~190KB 原檔能用 MCP 自動拉**。
> → 這 7 張請從 claude.ai **手動下載 `_web.png` 丟進 `cover_ip/assets/`**（合成器已認得 `_web` 後綴，免改名）。

| 檔名 | 表情 | 來源 | 全身？ |
|---|---|---|---|
| `robot_gotcha`     | 舉放大鏡貼單眼、前傾、得意奸笑（預設·硬題） | poses 左上 | ✅ |
| `robot_skeptical`  | 手托下巴、挑眉、半瞇、狐疑 | poses 中上 | ✅ |
| `robot_smug`       | 雙手抱胸、嘴角上揚、單眼+火花 | poses 右上 | ✅ |
| `owl_ahha`         | 羽毛炸開、雙翅上甩、放大鏡放大單眼（預設·軟題） | poses 左下 | ✅ |
| `owl_wink`         | 翅膀比了然手勢、半瞇俏皮、開喙 | poses 中下 | ✅ |
| `owl_warm`         | 瞇眼微笑、雙翅輕收、溫暖滿足 | poses 右下 | ✅ |
| `owl_pondering`    | 歪頭、面對大問號、眼鏡反光 | hero 右下 | 半身* |

\* `owl_pondering` 在 v1 只有半身版；合成器貼到封面 500–670px 高仍清楚，但非全身。若要全身請依下方 prompt 補生。

## ⚠ 需補生（7 個 · v1 沒有這些姿勢，無法裁切）
用你的圖像模型（ChatGPT / nanobanana）+ **對應角色的 v1 參考圖**生圖，再交給 Claude Code 去背/壓縮/命名。
每條 = 固定造型 block（見 `cover_prompt_template.txt`）+ 下面這句場景/動作。**務必附參考圖鎖造型。**

### 瑞瑞 robot
- `robot_presenting` — `standing upright full body, one stubby arm gesturing outward presenting, confident open posture, single lens-eye calm, scarf settled`
- `robot_curious` — `leaning in eagerly full body, single lens-eye sparkling huge, radar antenna perked straight up, both stubby hands reaching forward, no magnifier`
- `robot_alert` — `urgent leaning stance full body, radar dish spinning fast with motion streaks, single lens-eye wide open, a small alarm spark above`
- `robot_celebrating` — `both arms thrown up in triumph full body, radar dish lit with a ping, sparkles around, joyful open-mouthed cheer`

### 達達 owl
- `owl_reading` — `perched full body, looking down at an open book held in both wings, spectacles glinting, absorbed calm expression`
- `owl_cautionary` — `full body, one wing raised palm-out in a 'careful/stop' gesture, brow furrowed over the spectacles, wary cautioning look`
- `owl_teaching` — `perched upright full body, one wing pointing out toward the side (at an imagined small diagram), spectacles on, didactic explaining pose`

> 共通結尾（所有補生都要）：`single subject, centered, full body, plain cream #F2EEE5 background,
> no text, no scene props beyond the character's own magnifier/glasses/book, symmetric enough to mirror horizontally.`
> 生回來丟進 `cover_ip/assets/`，跟 Claude Code 說一聲就套同一條去背/壓縮管線（透明、短邊800、_web ≤240KB）。

## 去背/壓縮管線規格（已套用在 ✅ 那 7 個）
- 邊緣 flood-fill 去背（保留角色內部的淺色：機器人鏡片、貓頭鷹眼白）→ 移除小雜點/動態線/落地陰影 → 裁邊留 16px → 短邊 resize。
- `_web`：短邊 480–645 + 輕度 posterize，自動壓到 ≤240KB（無可見色帶）。
- 全尺寸：短邊 800，畫質母檔。
