# Cover IP · 封面角色設定（News Radar / @smartmmmoney）

> **目的**：每篇 Substack 封面使用既有的專業角色 PNG，讓讀者一眼認得品牌。
> Python 依 mode/topic 選角色與表情，再由 renderer 合成 `cover.png`；撰稿 AI 不選角、
> 不寫 scene，也不產生任何圖片 prompt。
>
> **命名**：**瑞瑞**（機器人，好奇與探索）/ **達達**（貓頭鷹，智慧與聰明）（2026-06-21 暫定）。
> pipeline 代號使用 species：`robot` / `owl`（name-proof，永不隨改名動）。
>
> **v1 定裝參考圖（已鎖定）**：`cover_ip/modelsheet_hero_v1.png`（hero + 各 3 表情）、
> `cover_ip/modelsheet_poses_v1.png`（乾淨 3-pose 排）。造型以這兩張為基準；完整視覺系統
> 委託見 `cover_ip/DESIGN_BRIEF.md`。

---

## 共用美學聖經（Shared Style Bible — Python 自動補在 prompt 尾端）

```
Warm soft-clay claymation miniature, tilt-shift macro, soft diffused studio light,
tactile hand-molded fingerprint texture, rounded chunky forms, medium detail.
Palette: paper-cream background #F2EEE5, ink-black #141414, ONE sienna-red #C84A32
accent used once, muted stone-grey #8A8378. Cute but credible — GitHub-Octocat-level
charm, NOT babyish, NOT chibi-overload. Single subject, centered, generous negative
space for a title overlay. No text, no watermark, no logo.
```

兩個角色共用同一套黏土定格動畫美學 → 不管哪隻出場，整個 feed 看起來都是同一個品牌。
差別只在「主角是誰 + 當篇場景」。

---

## 角色 A · `robot`（瑞瑞）— 單眼雷達機器人（好奇與探索 / The Curious Explorer）

**人設**：桌上型黏土單眼小機器人。骨子裡是「好奇與探索」——拿放大鏡到處戳、追著數字背後的
破綻跑，挖到的瞬間得意地「抓到了！」。自信、愛現，但挖出來的東西是真的硬。

**固定長相（Python 補，模型不用寫）**：
```
A chunky rounded desk-robot analyst made of soft matte clay, stone-grey #8A8378 body,
a small spinning radar dish antenna on its head emitting a tiny "ping!" spark, one big
glossy single lens-eye that sparkles, stubby articulated arms, a sienna-red #C84A32
knitted scarf. Squash-and-stretch rubbery posing, exaggerated and lively.
```

**招牌動作／表情（依場景選一）**：
- `gotcha`（預設·硬題）：「抓到了！」往前衝，舉著放大鏡把單顆鏡頭眼放到超大、閃閃發光，得意小奸笑。
- `skeptical`：狐疑挑眉、radar 天線歪一邊、單手叉腰——對某個數字「你確定？」的懷疑臉。
- `smug`：雙手抱胸、嘴角上揚的得意奸笑——「我早就算到了」。

**何時出場**：**硬科技 / 數據 / 財報題**。
`us_stocks`、`tw_stocks`、`ai_model`、`ai_agent`、`ai_application`、`tech_product_launch`、
`supply_chain`、`earnings`，以及 **company（每週公司營運分析）mode**。

---

## 角色 B · `owl`（達達）— 雷達貓頭鷹（智慧與聰明 / The Wise Owl）

**人設**：戴圓眼鏡的黏土貓頭鷹智者，主打「智慧與聰明」。看得比別人遠、問得比別人深，
常常一個歪頭就把問題問到骨子裡。誇張、戲劇化，但氣質是「想通了」的睿智，不是耍寶。

**固定長相（Python 補，模型不用寫）**：
```
A plump rounded owl made of soft matte clay, warm stone-grey #8A8378 feathers with
hand-molded texture, two huge radar-dish eyes behind round wire spectacles, a small
sienna-red #C84A32 bow-tie scarf, stubby wings. Feathers puffed up, very expressive,
theatrical squash-and-stretch posing.
```

**招牌動作／表情（依場景選一）**：
- `ahha`（預設·軟題）：「啊哈！」全身羽毛炸開、雙翅往上一甩，一隻眼透過放大鏡放到超大——剛想通的瞬間。
- `wink`：俏皮眨單眼、翅膀比個了然的手勢——「你懂的」。
- `pondering`：歪頭沉思、一翅托著下巴、眼鏡反光——對著一個大問號發呆。

**何時出場**：**人文 / 反共識 / 訪談 / 輕主題**。
`evening`（獨立選題、書、概念、哲學）、`podcast`（長訪談萃取）、文化／社會／生活類，
以及任何「翻框架、講人性、慢思考」而非「拚數字」的題目。

---

## 動態選角規則（確定性 renderer 用）

1. **先看 mode**：`company` → `robot`（瑞瑞）；`podcast` → `owl`（達達）。
2. **再看 topic_category**：落在 robot 的硬科技/財報清單 → `robot`；其餘人文/反共識/輕主題 → `owl`。
3. Python 以 `image_brain.pick_character(topic_category, mode)` 選角，以
   `pick_expression(...)` 選既有表情素材；writer 不輸出角色或生圖 prompt。
4. 若角色素材不存在，renderer 退回純文字海報，仍會產出 `cover.png`。
