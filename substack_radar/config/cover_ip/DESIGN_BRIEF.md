# 設計委託 · News Radar 封面視覺系統（雙 IP × 標題排版 × 分類色票）

> **給 claude design 的任務書。** 目標：把已鎖定的雙 IP 角色（瑞瑞 / 達達）發展成一套
> **可重複套用的封面視覺系統**，讓每天 5+ 篇 Substack 封面「又一致、又抓眼球、縮圖也讀得到」。
> 你交付的是**設計系統規格 + 模板 + 可貼進 pipeline 的 prompt 鷹架/色票 tokens**，不是一次性美圖。

---

## 0. 一句話任務
為 News Radar Substack 建立封面系統：固定雙角色 IP × 標題排版模板 × 分類色票，
產出**模板 + tokens + prompt 鷹架**，能被自動化管線每天套用、且通過縮圖可讀性測試。

## 1. 背景脈絡
- **品牌**：News Radar（IG @smartmmmoney、Substack）。每天 5 篇日更（早報/晚報/3× podcast 萃取）+ 週日 1 篇公司營運分析。
- **受眾**：關注美國/台灣科技、商業、投資、AI 的中文讀者。
- **調性**：硬商業邏輯 × 暖哲學靈魂。**可愛但可信（GitHub Octocat 等級的專業萌），絕非幼稚 chibi。**
- **撰稿端已上線**：AI 寫稿時會自動選角（程式已 merge），規則見 `cover_characters.md`：
  - **瑞瑞 · 單眼雷達機器人（好奇與探索）** → 硬科技/數據/財報/公司分析題。（pipeline 代號 `robot`）
  - **達達 · 雷達貓頭鷹（智慧與聰明）** → 人文/反共識/訪談（podcast）/輕主題。（pipeline 代號 `owl`）
- **名字**：瑞瑞（機器人）/ 達達（貓頭鷹），2026-06-21 暫定。pipeline 代號用 species `robot`/`owl`（name-proof）。

## 2. 已鎖定的素材（請先讀這些）
- `substack_radar/config/cover_ip/modelsheet_hero_v1.png`（1536×1024）— 兩角色 hero pose + 各 3 表情。**這是 v1 定裝基準，造型以此為準。**
- `substack_radar/config/cover_ip/modelsheet_poses_v1.png`（1672×941）— 兩角色乾淨 3-pose 排（上 瑞瑞/robot / 下 達達/owl）。
- `substack_radar/config/cover_characters.md` — 角色人設聖經 + 動態選角規則 + scene 撰寫鐵律。
- `src/image_brain.py` — `CHARACTERS`（造型字典）、`_CLAY_STYLE_TAIL`（美學聖經字串）、`build_cover_prompt_block()`（**現行**：組「角色造型 + 一句 scene + 黏土美學」成文字 prompt，交人手動生圖）。
- `substack_radar/promise_cover.py` — **現行**自動 cover.png 渲染（純文字海報 + 分類色票，Python 決定論生成）。
  → **系統關鍵抉擇**：角色封面要**取代**這條純文字線、還是**並存/混搭**？請在 D6 給明確建議。

### 既有美學常數（不可違反）
- 背景 paper-cream `#F2EEE5`（永不純白）。線/字 ink `#141414`（永不純黑 #000）。
- **單一強調色** sienna-red `#C84A32`，每張封面只用一次。輔助 muted stone-grey `#8A8378`。
- 字體：hero = Noto Serif TC weight 900；kicker/label = JetBrains Mono。
- 媒材：暖黏土定格動畫（claymation）、tilt-shift macro、手捏指紋質感、圓潤厚實。
- 禁：霓虹、漸層、3D 光暈、動漫臉、emoji、裝飾邊框、純白底。

## 3. 交付清單（Deliverables）
- **D1 角色定裝表（formalize）**：把 v1 render 整理成正式 model sheet——正面定裝 + 4 表情（抓到了/狐疑/得意/沉思）+ 招牌動作 + 身形比例 + 禁區（不可幼稚化、不可改配色/媒材）。每角色輸出一張乾淨參考圖規格 + 「重生這隻角色」的標準 prompt。
- **D2 封面構圖模板（2-3 種 layout）**：對應不同標題長度與題型——例：①角色靠邊 + 標題佔位、②角色小品 + 大標主導、③角色 × 情境道具。每種定義角色位置、標題安全區、留白、視覺動線。基準畫布 **Substack hero 1456×816**。
- **D3 標題排版系統**：字體階層、字級範圍、行數上限、**hero 文字佔畫面 40–60%**、sienna 單一強調字規則、與角色不打架的疊放規則。給「短標（≤12 字）」與「長標 + 副標」兩套。
- **D4 分類色票 tokens**：在 cream/ink/sienna/stone-grey 基底上，決定是否給每個 topic_category 一個**次要 accent**（或全系列維持單一 sienna）。輸出 tokens 表（hex + 用途 + 與底色對比度 ≥ AA）。建議用可機器讀格式（JSON/YAML 片段）。
- **D5 Prompt 鷹架**：一個可重複的封面生成 prompt 範本，吃「角色參考圖 + 一句 scene + 標題」→ 穩定輸出封面。**格式對齊現行 `build_cover_prompt_block` 輸出**，讓 pipeline 直接替換套用。
- **D6 整合建議**：角色封面 vs `promise_cover.py` 純文字海報——取代/並存/混搭？給明確建議 + 落地路徑。若建議「Python 自動合成角色底圖 + 標題排版」，列出技術做法（用哪張參考、文字疊放、字體、輸出 1456×816）。

## 4. 限制（不可違反）
1. 配色鐵律見 §2（cream 底、ink 字、**ONE** sienna、stone-grey）。
2. 角色造型已鎖 v1——可加表情/動作/道具，**不可改配色、媒材、五官比例**。
3. **縮圖可讀**：feed/email 縮圖（約 60×40px）仍認得出角色 + 讀得到標題鉤子。
4. 中文標題為主（Noto Serif TC）。
5. 可愛但可信，非幼稚。

## 5. 驗收標準（Acceptance）
用以下**真實標題**各做 1 張封面 mockup，證明模板可套：
- **瑞瑞/robot（硬題）**：「NVIDIA 的 5 兆美元幻象：晶片之王，還是基礎設施的終局？」
- **瑞瑞/robot（硬題·短）**：「萬億俱樂部之外：三支 AI 基礎設施股的翻倍劇本」
- **達達/owl（軟題·反問）**：「為什麼我們總是為用不到的特權買單？」
- **達達/owl（軟題·觀察）**：「可口可樂的『無聊』：你錯過的不是成長，是時間的複利」

通過條件：
- 縮圖測試：60×40px 仍認得出是哪隻角色 + 讀得到標題最強鉤子。
- 一致性：同一角色跨 3 張 mockup 不走樣（對得上 v1 定裝）。
- 產出 **machine-usable**：色票 tokens（JSON/YAML）+ 可直接貼進 pipeline 的 prompt 範本（純文字）。
- 一份 `COVER_SYSTEM.md` 規格書（模板說明 + tokens + 字級 + 用法），可直接放進本 repo 取代/擴充本 brief。

## 6. 落地對接點（交付後我方如何接）
- 文字 prompt 範本 → 回填 `src/image_brain.py`（`_CLAY_STYLE_TAIL` / `build_cover_prompt_block`）。
- 色票 tokens → 餵 `substack_radar/promise_cover.py`（或新的合成器）。
- 定裝參考圖 → 存 `substack_radar/config/cover_ip/`，作為每次生圖的 reference image。
- 規格書 → `substack_radar/config/cover_ip/COVER_SYSTEM.md`。
