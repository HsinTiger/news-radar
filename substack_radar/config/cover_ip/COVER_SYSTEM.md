# News Radar · Cover System v0.1

> 雙 IP（瑞瑞 / 達達）× 構圖模板 × 標題排版 × 分類色票的封面視覺系統規格書。
> 可直接放進 `substack_radar/config/cover_ip/`，取代/擴充 `DESIGN_BRIEF.md`。
> 視覺版：`News Radar Cover System.html`。日期 2026-06-21。

## 0. 一條鐵律先講
**每張封面只有一個 sienna。** 角色的 sienna 圍巾 / 蝴蝶結是已鎖造型的一部分，
**它就是該封面唯一的 sienna**。所以**有角色的封面，標題一律純 ink `#141414`**，
不再加第二個 sienna 強調字。純文字 fallback 封面（無角色）才可用一個 sienna 強調詞。

美學常數（不可違反）：背景 paper-cream `#F2EEE5`（永不純白）、線/字 ink `#141414`（永不純黑）、
單一強調 sienna `#C84A32`、輔助 stone-grey `#8A8378`。hero 字體 = Noto Serif TC 900；
kicker/label = JetBrains Mono。媒材 = 暖黏土定格動畫。禁：霓虹、漸層、3D 光暈、動漫臉、emoji、裝飾邊框、純白底。

---

## D1. 角色定裝表（造型已鎖 v1）

兩隻角色直接沿用 `modelsheet_hero_v1.png` / `modelsheet_poses_v1.png`。可加表情/動作/道具，
**不可改配色、媒材、五官比例**。

### 瑞瑞 · `robot` — 單眼雷達機器人（好奇與探索）
- **身體**：stone-grey `#8A8378` 軟磨砂黏土，圓潤厚實，頭:身 ≈ 1:1，大頭短肢，squash-and-stretch。
- **標記**：頭頂旋轉雷達天線 + 紅球尖 + 「ping!」火花；**單顆**大玻璃鏡頭眼（招牌）。
- **sienna**：針織圍巾 `#C84A32`（= 封面唯一 sienna）。
- **道具**：放大鏡（必備）、數據夾板/鏡頭可換。
- **表情**：`gotcha`（預設·硬題）/ `skeptical` / `smug`。
- **出場**：硬科技/數據/財報 —— `us_stocks` `tw_stocks` `ai_model` `ai_agent` `ai_application`
  `tech_product_launch` `supply_chain` `earnings` + `company` mode。
- **重生 prompt**（= `image_brain.CHARACTERS["robot"]["look"]`）：
  > A chunky rounded desk-robot analyst made of soft matte clay, stone-grey #8A8378 body,
  > a small spinning radar dish antenna on its head emitting a tiny "ping!" spark, one big
  > glossy single lens-eye that sparkles, stubby articulated arms, a sienna-red #C84A32
  > knitted scarf. Squash-and-stretch rubbery posing, exaggerated and lively.

### 達達 · `owl` — 雷達貓頭鷹（智慧與聰明）
- **身體**：warm stone-grey `#8A8378` 羽毛，手捏指紋質感，圓球身、短翅、羽毛炸開幅度大。
- **標記**：兩顆超大雷達盤眼 + 圓框細金屬眼鏡；暖赭黃喙與三趾腳。
- **sienna**：蝴蝶結圍巾 `#C84A32`（= 封面唯一 sienna）。
- **道具**：放大鏡 / 樹枝棲木 / 書，依場景換。
- **表情**：`ahha`（預設·軟題）/ `wink` / `pondering`。
- **出場**：人文/反共識/訪談/輕主題 —— `evening` `podcast` `culture` `contrarian` + `podcast` mode。
- **重生 prompt**（= `image_brain.CHARACTERS["owl"]["look"]`）：
  > A plump rounded owl made of soft matte clay, warm stone-grey #8A8378 feathers with
  > hand-molded texture, two huge radar-dish eyes behind round wire spectacles, a small
  > sienna-red #C84A32 bow-tie scarf, stubby wings. Feathers puffed up, very expressive,
  > theatrical squash-and-stretch posing.

裁切好的角色 PNG（已把背景正規化為 `#F2EEE5`）：
`assets/robot_gotcha.png` `robot_skeptical.png` `robot_smug.png`、
`assets/owl_ahha.png` `owl_wink.png` `owl_pondering.png`（另有 `*_sm` 表情頭像）。

---

## D2. 構圖模板（基準 1456 × 816，安全區 64px）

共通規則：**角色與標題分佔兩側、永不重疊**；角色視線/放大鏡**指向標題區**；
hero 文字占畫面 **40–60%**；四邊安全區 64px。模板代號寫進 pipeline 的 `layout` 欄。

| 代號 | 名稱 | 適用 | 角色 | 標題 |
|---|---|---|---|---|
| **A** | 角色靠邊 + 標題佔位 | 短～中標 ≤20 字（日更主力） | 一側 ~38% 寬，站姿 | 對側 ~52% |
| **B** | 角色小品 + 大標主導 | 長標 + 副標 >20 字 | 角落 ~24%，指回標題 | 主畫面 ~60% |
| **C** | 角色 × 情境道具 | 觀察/敘事題 | 一側 ~40% + 象徵道具 | 對側 ~46% |

---

## D3. 標題排版

| | 短標 ≤12 字（單層） | 長標 + 副標（雙層） |
|---|---|---|
| 主標字體 | Noto Serif TC **900** | Noto Serif TC **900** |
| 主標字級 | 160–220 px @1456w，1–2 行 | 96–130 px，≤3 行 |
| 副標 | —（無） | Noto Sans TC 500，30–40 px，≤2 行 |
| hero 占比 | 高 ≈55% | 主+副合計高 ≈50% |
| 抽詞 | 直接用全標 | 從長標抽 ≤8 字最強鉤子當主標 |
| 強調 | **純 ink，不上 sienna**（圍巾即 sienna），靠字級製造階層 | 同左 |

kicker / 分類 label：JetBrains Mono，stone-grey 或 ink，**不上 accent**（single_sienna 模式）。

---

## D4. 分類色票 tokens

機器可讀檔：`cover_tokens.json` / `cover_tokens.yaml`。

- **基底固定**：cream `#F2EEE5`、ink `#141414`（on cream 16.8:1, AAA）、
  sienna `#C84A32`（4.6:1, AA-large）、stone `#8A8378`（meta only, 2.7:1）。
- **分類 accent 是選用的**。v0.1 建議 `default_mode: single_sienna`（全系列單一 sienna，feed 最一致）。
- 若日後要做分類色碼：每個 category 的 accent **取代** sienna、**絕不疊加**；
  下表 accent 已預驗 AA-large 且同屬暖低彩度家族。

| topic_category | accent | 角色 | on cream |
|---|---|---|---|
| us_stocks / tw_stocks | `#C84A32` | robot | 4.6:1 ✓ |
| ai_model / ai_agent / ai_application | `#B5532A` | robot | 4.5:1 ✓ |
| tech_product_launch | `#A8602B` | robot | 4.5:1 ✓ |
| supply_chain | `#7E5A33` | robot | 5.2:1 ✓ |
| earnings / company | `#9A4B2E` | robot | 5.3:1 ✓ |
| evening | `#3F5A4E` | owl | 6.1:1 ✓ |
| podcast | `#4A5568` | owl | 6.3:1 ✓ |
| culture | `#6B5340` | owl | 6.0:1 ✓ |
| contrarian | `#8A3A2E` | owl | 6.4:1 ✓ |

面積比目標：cream ≥60% / ink ≈25% / stone ≈12% / 強調 ≤3%（恰好一個元素）。

---

## D5. Prompt 鷹架

純文字檔：`cover_prompt_template.txt`（對齊 `build_cover_prompt_block()`）。
三格：`{character_block}`（Python 補 = CHARACTERS look）+ `{scene}`（模型寫的一句中文場景）
+ `{style_tail}`（Python 補 = `_CLAY_STYLE_TAIL`）。連同對應角色的 v1 參考圖一起送圖像模型。
Composition 區固定要求：單一主體、靠 `{anchor}` 側、視線 `{gaze}` 朝標題區、留 55–60% 負空間、
不渲染文字。範例見該檔尾。

---

## D6. 整合建議：並存、分流，不立即取代

| 路線 | 用於 | 做法 |
|---|---|---|
| **1 角色封面（主力）** | 早報/晚報/podcast/weekly company | D5 prompt + v1 參考圖 → 角色底圖；Python 疊 D3 標題 |
| **2 純文字海報（保底）** | 生圖失敗/超急/純數據快訊 | `promise_cover.py` 改吃 D4 tokens，允許一個 sienna 強調詞，排版對齊 D3 |
| **3 半自動合成（推薦中程）** | 規模化 | 預生去背角色 PNG 存 `cover_ip/`；Python 依 layout A/B/C 貼角色 + Pillow 排標題；5 秒出圖、零走樣 |

落地步驟：
1. **選角 + 選 layout** — 撰稿 AI 填 `cover_character`，模型估標題長度 → A/B/C（`image_brain.pick_character`）。
2. **組 prompt（路線1）/ 選底圖（路線3）** — 填 D5 三格 / 挑對應表情 PNG。
3. **疊標題** — Pillow：Noto Serif TC 900 主標 + Sans 副標，純 ink。
4. **套色票 + 出圖** — D4 tokens 決定 kicker；輸出 1456×816 PNG ≤ 4MB。
5. **縮圖自檢** — 降到 60×40 驗角色可辨 + 鉤子可讀；不過則回 Step 1 換 layout。

---

## 驗收（§5 真實標題已驗）

| Mock | 標題 | 角色 | 模板 |
|---|---|---|---|
| 01 | NVIDIA 的 5 兆美元幻象：晶片之王，還是基礎設施的終局？ | 瑞瑞 gotcha | B 大標主導 |
| 02 | 萬億俱樂部之外：三支 AI 基礎設施股的翻倍劇本 | 瑞瑞 smug | A 角色靠邊 |
| 03 | 為什麼我們總是為用不到的特權買單？ | 達達 pondering | A 短標單層 |
| 04 | 可口可樂的「無聊」：你錯過的不是成長，是時間的複利 | 達達 ahha | C 角色×情境 |

四張全部：單一 sienna（圍巾）、標題純 ink、通過 ≈60×40px 縮圖可讀性、跨張對得上 v1 定裝。

## 交付檔案清單
- `COVER_SYSTEM.md`（本檔）
- `cover_tokens.json` / `cover_tokens.yaml`
- `cover_prompt_template.txt`
- `assets/robot_*.png` `assets/owl_*.png`（v1 角色裁切）
- `News Radar Cover System.html`（視覺規格書）
