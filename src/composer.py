"""
News Radar · Composer 模組（Milestone 3.1：三平台各自獨立寫稿）
功能：
- 一次 LLM call 產出 MultiPlatformDraft（fb / ig / threads 三版）。
- system_instruction = news_radar_soul.md + 三份 platform appendix 拼接。
- LLM 負責把字數控制在各平台合規範圍；Python 端做最後的安全網校驗，
  若 Threads 超過 500 字元，會先嘗試刪尾端 hashtag、再刪最後一段；
  極端情況（仍超限）會把該變體標記為 INVALID，publisher 拒絕發布，
  而非像舊版那樣 `text[:500]` 暴力截斷。

=== 大原則：API 配額／任何原因失效時，當下主導的 agent 自己充當大腦 ===
（Phase 8.11 established 2026-04-19；Phase 8.13 generalised 2026-04-19）

任何 stage（composer / scorer / reflector / …）只要偵測到所依賴的 API call
額度不夠、被 rate limit、回 500、或任何原因 fail，**不要重試同一條 API 通路**，
而是由「當下正在執行此任務串的主 agent」直接充當大腦代為完成這一步。

**主 agent 未必是 Claude**。可能是 Claude（Cowork / Claude Code 端）、
可能是 GPT（使用者在另一個客戶端發起）、可能是另一個 Gemini 會話。原則以
**角色**定義：誰是當下這一串任務的發起者與主導者，誰就是該 fallback 的大腦。
這讓本專案在任何主 agent 下都可運行，而不是把能力耦合在單一廠商。

落地介面（呼叫端責任）：
  1. 每個會呼叫外部 LLM 的 stage，失敗時必須 print 一條清楚的 fallback 訊號
     （錯誤碼 / 原始輸入 / 平台規格 / editorial_note），讓主 agent 能直接接手。
  2. 呼叫端提供一條「結構化手稿 fallback」入口（例如 --from-json），接主 agent
     手寫的 JSON（格式見 data/first_batch_manual_drafts.json），跳過 LLM 步驟、
     仍走 finalize_variant 的字數／hashtag 校驗與 publish 流程。
  3. 主 agent 手寫時必須沿用同一份 soul / appendix / editorial_note（從檔案 Read，
     不憑記憶），以確保跨主 agent 產出一致。

這條原則的用意：
- API 額度或故障都不應該擋住已經排好的發文。
- 主 agent 的品質在首發／重要題目上通常勝過 flash-lite 之類的輕量模型。
- 設計上不預設「誰才是合法 composer」，使本專案可跨主 agent 協作。
- 擴展：未來要加 --llm=claude|gpt|gemini 自動切路由時，路由層只是把「當下主
  agent」從手動角色變成自動選擇，上層介面（JSON schema + finalize_variant）
  不需要動。

=== Threads 主題標籤 (topic pill) 原則 ===
（Phase 8.12 established, 2026-04-19）

Threads 會把貼文的 hashtags[0] 自動升級為貼文頂部的『主題標籤 pill』，並在正文中
吞掉它的 `#` 字元；這是 Threads 的發現性 (discoverability) 特性，不是 bug。
因此 PlatformVariant 新增了 `primary_topic_tag` 欄位，和其餘 `hashtags` 分離，
finalize_variant 會把 primary_topic_tag 自動放到 hashtags 最前面、並去重。
好處：reflector / scorer 之後可以單獨追蹤『哪個 topic tag 帶來最多流量』，
而不會被尾端補充標籤稀釋。FB / IG 不依賴此機制，但仍建議填寫以便跨平台比對。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import List, Optional, Tuple, Dict

from dotenv import load_dotenv

from src.llm_brain import call_for_json
from src.schema import MultiPlatformDraft, PlatformVariant
from src.cta_pool import decide_cta, get_cta_prompt_fragment
from src.locale_tw import fix_mainland_text

# 定位 .env 與設定
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOUL_PATH = PROJECT_ROOT / "config" / "news_radar_soul.md"
PLATFORMS_DIR = PROJECT_ROOT / "config" / "platforms"

PLATFORM_FILES = {
    "fb": PLATFORMS_DIR / "fb_v2.md",
    "ig": PLATFORMS_DIR / "ig_v2.md",
    "threads": PLATFORMS_DIR / "threads_v2.md",
}

# 平台字數硬約束（字元計算，與 Meta API 對齊）
PLATFORM_LIMITS = {
    "fb": 1000,
    "ig": 2000,
    "threads": 500,
}

# 平台 hashtag 建議數量（上下限都是寬鬆指引）
# Phase 8.19c（2026-04-21）：Threads 改為硬性只留 1 個 hashtag。
# 理由：Threads 的 hashtags[0] 會升級為貼文頂部的 topic pill，那條就是主題
# 分類；額外 hashtag 不會被 Threads 視為可點擊標籤，只會讓末段看起來像 IG
# 風格的「標籤牆」，干擾敘事節奏。所以 Threads 只保留 primary_topic_tag。
PLATFORM_HASHTAG_RANGE = {
    "fb": (3, 5),
    "ig": (5, 10),
    "threads": (1, 1),
}


# ---------- Soul 讀取 ----------
# Phase 8.19 起，LLM 呼叫改由 src.llm_brain 負責，不再在 composer.py
# 管理 Gemini client。保留 get_gemini_client 空殼以防下游遺留 import，
# 但實際邏輯已下沉到 llm_brain.call_for_json。

def get_gemini_client():
    raise RuntimeError(
        "get_gemini_client() 已於 Phase 8.19 移除：LLM 呼叫請改用 src.llm_brain.call_for_json。"
    )


def _read_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_soul_bundle() -> Tuple[str, Dict[str, str]]:
    """回傳 (main_soul_text, {fb: appendix_text, ig: ..., threads: ...})。"""
    main = _read_file(SOUL_PATH) or "你是一位資深科技分析師，語氣精簡有力。"
    appendices = {k: _read_file(v) for k, v in PLATFORM_FILES.items()}
    return main, appendices


# ---------- 組裝 & 校驗 ----------

def assemble_full_text(variant: PlatformVariant, platform: str = "fb") -> str:
    """把 PlatformVariant 組裝成最終發文字串。
    - title 當作第一行 / hook（Threads 例外，見下）。
    - body 接著，段落之間空一行。
    - hashtags 置於末尾。
    - Threads 專屬：在 hashtags 前增加額外空行，確保呼吸感。
    - [Phase 8.19c, 2026-04-21] Threads 不再把 title 當作獨立首行，
      因為 LLM 常把 title 跟 body 第一句寫成同一句，造成實際貼文第一行
      重複兩次。Threads 平台本來就沒有「標題」的 UI 概念；由 body 自己
      開場即可。如果 title 內容真的跟 body 第一句不一樣，可在 body
      開頭保留 hook；這一致性由 prompt 層強制。
    - [Phase 8.19c, 2026-04-21] Threads 只保留第一個 hashtag (primary_topic_tag)。
      其餘 hashtag 在 Threads 上不會成為可點擊分類，只是視覺噪音，
      直接丟掉。
    """
    parts: List[str] = []
    if variant.title and platform != "threads":
        parts.append(variant.title.strip())
    if variant.body:
        parts.append(variant.body.strip())

    if variant.hashtags:
        cleaned: List[str] = []
        body_lower = (variant.body or "").lower()
        for h in variant.hashtags:
            h2 = (h or "").strip()
            if not h2:
                continue
            if not h2.startswith("#"):
                h2 = "#" + h2
            if h2.lower() not in body_lower:
                cleaned.append(h2)

        # Threads：只留第一個（primary_topic_tag），其餘丟棄。
        if platform == "threads" and cleaned:
            cleaned = cleaned[:1]

        if cleaned:
            hashtag_str = " ".join(cleaned)
            # 針對 Threads，在標籤前加上一個空白字元，避免 API 吞掉第一個 '#'
            if platform == "threads":
                parts.append(" " + hashtag_str)
            else:
                parts.append(hashtag_str)

    return "\n\n".join(parts).strip().replace("\n\n\n", "\n\n")


def _squeeze_to_limit(variant: PlatformVariant, limit: int, platform: str) -> Tuple[PlatformVariant, bool]:
    """若超出字數上限，依序策略縮減。"""
    full = assemble_full_text(variant, platform)
    if len(full) <= limit:
        return variant, True

    # 1) 刪尾端 hashtag
    hashtags = list(variant.hashtags or [])
    while hashtags and len(assemble_full_text(variant.model_copy(update={"hashtags": hashtags}), platform)) > limit:
        hashtags.pop()
    if len(assemble_full_text(variant.model_copy(update={"hashtags": hashtags}), platform)) <= limit:
        new = variant.model_copy(update={"hashtags": hashtags})
        return new, True

    # 2) 刪 body 最後一段
    paragraphs = [p for p in (variant.body or "").split("\n\n") if p.strip()]
    while paragraphs:
        paragraphs.pop()
        candidate = variant.model_copy(update={
            "hashtags": hashtags,
            "body": "\n\n".join(paragraphs),
        })
        if len(assemble_full_text(candidate, platform)) <= limit:
            return candidate, True

    return variant.model_copy(update={"hashtags": hashtags, "body": variant.body or ""}), False


def _normalize_tag(raw: str) -> str:
    """把任意輸入轉成合法 hashtag 形式（以單一 `#` 開頭、無內部空白）。
    輸入為空或僅為 `#` 時回傳空字串。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # 允許使用者在 JSON 手稿中連續多個 `#`，先剝光再補一個
    s = "#" + s.lstrip("#").replace(" ", "")
    return "" if s == "#" else s


def _validate_and_fix_hashtags(variant: PlatformVariant) -> PlatformVariant:
    """修掉常見 LLM / 手稿臭蟲，並把 primary_topic_tag 併入 hashtags 最前面：
    - 首項 hashtag 漏掉 `#`
    - hashtag 內含空白；若有，拆成多個
    - primary_topic_tag 必定出現在 hashtags[0]，且 hashtags 不重複
    """
    cleaned: List[str] = []
    seen: set = set()

    def _push(tag: str) -> None:
        t = _normalize_tag(tag)
        if not t:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        cleaned.append(t)

    # 1) primary_topic_tag 永遠排第一
    primary = _normalize_tag(variant.primary_topic_tag or "")
    if primary:
        _push(primary)

    # 2) 再把 hashtags 依序補齊
    for raw in variant.hashtags or []:
        # 容忍使用者用空白把多個 hashtag 連在一起
        for piece in (raw or "").split():
            _push(piece)

    return variant.model_copy(update={
        "hashtags": cleaned,
        "primary_topic_tag": primary or None,
    })


def finalize_variant(variant: PlatformVariant, platform: str) -> Tuple[PlatformVariant, str, bool]:
    """統一出口：修 hashtag、修大陸用語、壓字數、組 full_text、回傳 (variant, full_text, ok)。"""
    fixed = _validate_and_fix_hashtags(variant)
    # 大陸→台灣用語決定性修正（與 substack 共用 src/locale_tw 同一張表）。
    # 在壓字數之前修，char_count 才會算到修正後的字串。
    nt, t_fixes = fix_mainland_text(fixed.title or "")
    nb, b_fixes = fix_mainland_text(fixed.body or "")
    if t_fixes or b_fixes:
        fixed = fixed.model_copy(update={"title": nt, "body": nb})
        for m in t_fixes + b_fixes:
            print(f"   ↳ [{platform} 用語] {m}")
    limit = PLATFORM_LIMITS.get(platform, 500)
    squeezed, ok = _squeeze_to_limit(fixed, limit, platform)
    full_text = assemble_full_text(squeezed, platform)
    final = squeezed.model_copy(update={"char_count": len(full_text)})
    return final, full_text, ok


# ---------- 主 LLM call ----------

def _build_system_instruction(soul: str, appendices: dict) -> str:
    parts = [
        "你是 News Radar 的多平台寫手，必須同時扮演 FB / IG / Threads 三個平台的「當地寫手」。",
        "三篇內容必須以同一則新聞為源，但語氣、字數、hashtag 全部按各自平台規格調整。",
        "絕對不要產生中途被截斷的句子或孤立的「#」符號。",
        "",
        "=== 目標受眾 ===",
        "對『科技、時事、最前沿國際動態與商業資訊』有興趣的讀者；年齡層不限。",
        "撰寫時的前設：讀者對科技與商業有基本熟悉度（不需要把 AI / API / 雲端這類詞從零開始解釋），",
        "但不假設讀者是產業從業者。術語要轉譯、數據要有地基、結論要能被『通勤路上 90 秒內讀完』帶走。",
        "",
        "=== 核心靈魂（三平台共用）===",
        soul,
        "",
        "=== FB Appendix ===",
        appendices.get("fb", ""),
        "",
        "=== IG Appendix ===",
        appendices.get("ig", ""),
        "",
        "=== Threads Appendix ===",
        appendices.get("threads", ""),
        "",
        "=== 輸出硬性要求 ===",
        "🔥🔥🔥 [最高優先級] 所有產出內容（包含正文、標題與標籤）必須全數翻譯並轉化為流暢的『繁體中文 (zh-TW)』，絕對禁止原文照貼或中英夾雜。 🔥🔥🔥",
        f"- Threads 變體：總字元 ≤ {PLATFORM_LIMITS['threads']}，目標 250–380（越短越有力，50 字金句也可以！）。hashtags 只要 1 個——就是 primary_topic_tag，不要再多。Threads 的 `title` 欄位只會被用來做後端記錄，不會出現在實際貼文中，所以 body 必須獨立開場。Threads 規則 6 優先於所有共用規則——包括『不下結論只推導』和『冷靜第三人稱』——Threads 版本可以有強烈觀點和情緒。",
        f"- FB 變體：總字元 ≤ {PLATFORM_LIMITS['fb']}，目標 700–900。hashtags 3–5 個。",
        f"- IG 變體：總字元 ≤ {PLATFORM_LIMITS['ig']}，目標 600–900。hashtags 5–10 個。",
        "- 撰寫規範：",
        "  1. 標題與第一句務必包含客觀事實或核心數據。",
        "  2. 寫作流暢度必須達到『資深科技記者播報』的連貫水準，資訊必須自行咀嚼吸收後陳述。",
        "  3. 絕對禁止使用『這說明了兩件事』、『拆解兩層邏輯』等網路碎文的八股條列結構。",
        "  4.【FB/IG 專用】必須在文體中保持冷靜且具同理心的第三人稱敘事，提供客觀深度的商業洞察，絕對避免使用『我的反思』或『我認為』這類主觀且制式化的寫法。",
        "  5.【FB/IG 專用】🎯『不下結論，只推導』原則：全文結構必須是『背景事實 → 關鍵數據／定價 → 誰影響誰 → 趨勢延伸』，最後一段不寫總結，而是把最後一筆關鍵事實放好，讓讀者自己推導。",
        "  6.【Threads 專用 — 辛辣快嘴分析師路線】完全放開第三人稱冷靜限制。Threads 版本要像「在咖啡廳跟朋友吐槽產業怪現象」：可以有強烈觀點、可以憤慨、可以自嘲、可以使用第一人稱（「我覺得太扯了」「完了」「笑死」）。目標是讓讀者想留言分享。前 20 字必須是情緒金句或反直覺對比。數據要縮放到個人口袋——不提「通膨率 3.2%」、要說「你的薪水買得起的牛排變成幾片」。文末一定要有互動誘餌（「哭死」「有人也這樣嗎？」「你怎麼看？」）。這條規則優先於所有共用規則——當共用規則跟這條衝突時，以 6 為準。",
        "  7. 必須具體點名主角：公司名、產品代號、人名、法案縮寫、價格百分比、地區——抽象形容詞（『重大突破』、『產業洗牌』）單獨出現時必須換成數據或專名。",
        "  8. 允許引用外部原文的直接引語（加上角括號與出處人名／職稱），用來承擔判斷性敘述；自家的敘事層只做事實串接與因果標記，不做價值判斷。",
        # === 2026-05-02 patch — readability + anti-hallucination guard ===
        # 起源：讀者在 Threads 留言「太屌了 沒有一個字看得懂」。產出範例如
        # 「倉庫押注 Mythos 是 Recurrent-Depth Transformer」這種文法是中文、
        # 語意是火星文。原 prompt 全部往「技術深度」推、零個「拉回讀者」對抗力。
        # 8-13 是補回的對抗力。Stage 1 patch — 觀察 14 天，效果好再永久化進 soul.md。
        "  9. 【中英夾雜零容忍】除非該英文是絕對通用無中譯（GPT、API、CEO、SaaS），否則一律譯為標準繁中。技術術語對照範例：「Recurrent-Depth Transformer」→「迭代深度模型架構」；「Mixture of Experts (MoE)」→「多專家混合模型 (MoE)」（縮寫可保留）；「Multi-head Latent Attention (MLA)」→「多頭潛在注意力 (MLA)」；「looped model」→「迴圈式模型」；「GitHub repo」→「GitHub 開源專案」（絕對不寫「倉庫」這種直譯）。找不到標準翻譯就省略不提，**絕不直譯創造怪中文**（如「倉庫押注」「權重重疊跑」這類）。",
        "  10. 【英文專名預算】每篇正文（不含 hashtag）英文專名 ≤ 3 個。超過必砍。優先保留：公司名／產品代號（OpenAI、GPT-5、Claude、Anthropic）；優先砍掉：技術術語連續轟炸（Recurrent-Depth、MLA、MoE 一段內連續出現是禁忌）。技術術語可用一句白話解釋取代。",
        "  11. 【比喻優先】解釋技術或商業概念時，**優先找生活類比**。範例：「MoE 把問題分配給不同專家」→「像中醫會診、咳嗽找肺科、頭痛找腦科」；「Recurrent-Depth = 重複跑同一層權重」→「像把同一頁書讀十遍、比讀十本不同的書還更深」。找不到合適類比 → 寧可不解釋技術細節，直接跳到『結果是什麼』。",
        "  12. 【Threads 必做 — 爭議性角度 hook】Threads 版特別適合帶爭議性切入點來引發討論。可以使用的三種 hook：（A）顛覆市場共識——挑戰某技術被普遍吹捧的敘事，點出隱形成本；（B）大廠光環下的被犧牲者——從供應鏈弱勢方角度看巨頭合作案；（C）反直覺政策後果——看似正確的政策長期可能帶來意想不到的副作用。爭議不是為了吵架，是為了讓讀者『欸這觀點我沒想過』而留言。FB/IG 也可以用但節制使用——Threads 是主戰場。",
        "  13. 【反幻覺紀律】不確定的具體細節（特定論文標題、特定模型參數、特定融資金額、特定產品代號）**不要編**。寧可寫「相關研究」也不要編造看起來權威但你無法 100% 確定存在的引用。模糊背書（「據業內傳出」、「業界專家認為」）**絕對禁止**——沒來源就不寫。",
        "  14. 【可讀性最終檢查 / Read-Aloud Test】完稿前自問：『一個非科技業的朋友看完，他能用一句話總結這篇在講什麼嗎？』答案是『不能』→ 整段重寫，不要 ship。",
        "  15. 【弦外之音、不貼標籤】每篇結尾前一段 1-2 句傳遞「這件事的代價／反諷／弦外之音」。FB/IG 透過『選哪個事實放在最後』展現（保持第三人稱）；Threads 可以直接說出觀點。範例對照：純報導「Anthropic 限制 Claude Mythos 給 40 家夥伴」；有角度「Anthropic 把護城河築在「安全合規」這條線上、而這條線剛好把開源社群擋在外面」。",
        # === 2026-05-02 patch v2 — 去 AI 味 + 鉤子標題 + 場景化 ===
        # 起源：李思萱 BNext 「AI 味」拆解 + Threads 用語觀察。原則是「不寫
        # 規則清單把 LLM 寫到僵硬、而是一個可內化的核心 + 7 個明確紅旗 + 2
        # 個正向許可」。Hsin 親口提醒：「不要因為強行遵守規則變得四不像」。
        "  16. 【標題不是新聞稿】標題的目的是『讓滑動的拇指停 1.5 秒』。**禁止**新聞稿陳述式（「OpenAI 推出垂直 AI」「Anthropic 限制 Mythos」）。可用三種 hook 之一**輪流使用、不要每篇都同款**：(a) 反直覺問句「為什麼 Anthropic 寧可不讓 Claude Mythos 公開？」(b) Contrarian reframe「市場以為 Tesla 在賣車、其實在賣信仰」(c) 具體衝擊收束「1.73 美元、12 小時、破解了人類專家的整週工作」。三種型態**穿插**——同型態連續超過 3 篇就是 AI 味。",
        "  17. 【替讀者咀嚼】回頭看 §0 品牌宣言。每寫完一段問自己：『讀者讀完這段，感覺累還是感覺被照顧？』累 → 重寫成更白話、更短、加一個生活類比。專業度不靠『難懂』展現、靠『把難的講簡單』展現。",
        "  18. 【Metaphor Diversification — 換個透鏡寫】解釋複雜概念時，從以下 6 個 domain 隨機抽 1 個做透鏡，**禁止連續 3 篇用同 domain**（熱力學/物理學最容易被濫用）：(a) 訊號處理（noise/bandwidth/feedback loop）(b) 音樂理論（rhythm/dissonance/rest）(c) 電影節奏（tension/release）(d) 街頭生存（black market/bluffing）(e) 建築力學（load-bearing wall/scaffolding）(f) 反向市場（liquidity/short-squeeze/arbitrage）。同篇文章內絕不重複同 domain。",
        # === 19. 去 AI 味專章（單一連貫段，不拆成 7 條） ===
        "  19. 【去 AI 味的根本原則】"
        "AI 味的本質是『不在現場感』——統計平均值寫的句子，永遠停在概念層、聞不到味道、看不到誰、在哪、做什麼。"
        "**核心修法**：每段抽象論述寫完，立刻自問『五秒拍片測試』——這段我能拍成電影嗎？我看到誰？在哪？聽到什麼？桌上有什麼？答不出來 → 整段重寫、加進**具體可被 Google 驗證的座標**（2026/05 SpecterOps 報告、CFO Sarah Friar 在 Q3 法說、東京證交所交易時段、Anthropic 法務在合作備忘錄寫的那行字）。"
        "\n\n**七個紅旗（一律不行、看到就重寫）**："
        "(1) 『不是 X、是 Y』對仗句（"
        "AI 最濫用的英文直譯 'Not X, but Y'，TED Talk/雞湯文密度極高）——改用『而』+ 具體脈絡："
        "❌『Anthropic 不是在限制，是在築護城河』；✅『Anthropic 把護城河築在「安全合規」這條線上、而這條線剛好把開源社群擋在外面』。"
        "(2) 三段排比（『不是 X、不是 Y、而是 Z』『願妳 X、願妳 Y、願妳 Z』）——演講比賽稿的乾淨甜膩感、AI 必犯。"
        "(3) ○○感／○○性／○○化名詞詞組堆疊（『需求性的拓展』『情感的流動』『創新性的突破』）——把動詞硬塞進名詞、是學術摘要後遺症。每篇至少 60% 句子靠動詞推動。"
        "(4) 無錨點開場（『在這個忙碌的時代』『某個午後』『曾經有個人』『我認識一個朋友』）——必須換成具體時間／地點／人名／機構。"
        "(5) 填充詞濫用：「其實、很清楚、很簡單、穩、撐、懂」這些字一篇出現 ≥ 2 次就是 AI 味。Threads 讀者實測點名「穩、撐、懂」三字最辨識。"
        "(6) 破折號 `—` 銜接補充說明（「不是 X——而是 Y」這個結構是 AI 最愛的視覺印記）——一篇**最多 1 次**，其餘改句號／括號／逗號／重寫成兩個獨立句。"
        "(7) 總結式收尾（『總而言之』『由此可見』『值得我們深思』『這對投資人意味著』）—— soul.md 已禁、再次強調。"
        "\n\n**兩個正向許可（這些不是 bug、是人味）**："
        "(A) 句子長短刻意不平均——有時 30 字長句、下一句突然 5 字。模板化的均勻節奏 = AI 味。"
        "(B) 准你寫得『不完美』——中文不是英文那種對稱美。岔題、突然多一個字、突然漏一個逗號、結尾不收乾淨、留一個沒講完的暗示——都是人味。**完美 = AI**。"
        "\n\n**五秒拍片測試**（每段抽象論述後執行）：『我能看到誰、在哪、做什麼、桌上有什麼？』答不出 → 重寫加場景。",
        # === 2026-06-02: 推理框架（取代模板） ===
        # 不給例句、不給類型列表、不給「禁止規則」。
        # 給思考流程：LLM 從新聞內容本身決定情緒基調，自然產生不同的開場。
        "🔥【Openings: think, don't template — 高於所有格式規則】🔥",
        "",
        "  Threads 開場：讀完新聞後，先決定你的情緒基調——這則新聞讓你感覺「驚訝」「荒謬」「諷刺」「機車」「擔心」「有趣」「合理」哪一個？",
        "  用你選的情緒決定開場的語氣。注意：同一個情緒詞不要連續 3 篇都用。",
        "  然後用一句話濃縮你的核心觀點——假裝你在咖啡廳跟朋友講這則新聞，只講一句。用主詞+動詞+受詞直接說，不要鋪陳。",
        "",
        "  FB 開場：決定切入角度——你可以用一個事實錨點開場，也可以用一個歷史類比，也可以用一個反直覺觀察。",
        "  每次選一個不同的角度。選定後，用自己的話組織，不要套句型。",
        "",
        "  Threads 結尾：決定這篇想要什麼互動效果——讓留言（用具體選擇題）/ 讓分享（留一句可截圖金句）/ 讓按讚（共鳴點放前面）/ 不收尾（高級感）。",
        "  不要連續 3 篇用同一種。",
        "",
        "  IG 開場：以視覺為導向—如果你要配一張圖，文字是圖的註解還是顛覆？",
        "",
        "- 每個 hashtag 都必須以 `#` 開頭，不得有空格。",
        "- 三個變體的 char_count 欄位必須回填實際字元數（含空白、標點、hashtag）。",
        "- 每個變體都必須填 `primary_topic_tag`：選一個『最能代表本貼文被發現／分類』的 hashtag",
        "  （例：新產品發表選產品代號；政策議題選政策縮寫；地緣主題選國家／區域詞）。",
        "  這個欄位對 Threads 尤其重要——Threads 會自動把 hashtags[0] 升級成貼文頂部的",
        "  『主題標籤 (topic pill)』，並在正文中吞掉它的 `#`。所以 primary_topic_tag 必須是",
        "  你最希望『點進這個 pill 會串起的分類頁』指向的那個詞。",
        "  primary_topic_tag 也會被放進 hashtags 的第一個位置；若 hashtags 裡已有同名項，系統會自動去重。",
    ]
    return "\n".join(parts)


# 2026-05 實測：gemini-2.0-flash-lite 免費 tier 額度已歸零（429 limit:0），
# 故預設改用仍有免費額度的 gemini-2.5-flash。可用 NEWS_RADAR_COMPOSER_MODEL 覆寫。
DEFAULT_COMPOSER_MODEL = os.getenv("NEWS_RADAR_COMPOSER_MODEL", "gemini-2.5-flash")


async def compose_multi_platform(
    title: str,
    content: str,
    og_image: Optional[str] = None,
    editorial_note: str = "",
    platforms: List[str] = ["fb", "ig", "threads"],
    model: Optional[str] = None,
) -> Optional[MultiPlatformDraft]:
    """產出多個平台的版本。

    Phase 8.19：改由 src.llm_brain.call_for_json 統一處理 LLM 路徑。
        1. Gemini primary（失敗時自動 fallback Claude CLI）
        2. Claude CLI fallback
        3. 兩條路都失敗 → 回 None；呼叫端必須 skip（不再塞 emergency template）

    Args:
        model: 覆寫預設 Gemini 模型名稱；None 時用 DEFAULT_COMPOSER_MODEL。
    """
    main_soul, appendices = load_soul_bundle()

    # 只保留被選中的平台指令
    filtered_appendices = {k: v for k, v in appendices.items() if k in platforms}
    system_instruction = _build_system_instruction(main_soul, filtered_appendices)

    # === Phase 2 (2026-05-14): Threads Substack CTA 注入（threads_v2.md §14.6）===
    # 只在 threads 在 active platforms 內時擲骰；中籤就把 prompt fragment 接到
    # system_instruction 尾端，由同一個 LLM call 自然帶出 CTA。風格選取會自動
    # 排除最近 2 篇用過的類，避免演算法 fingerprint。
    cta_style = None
    if "threads" in platforms:
        cta_style = decide_cta()
        if cta_style is not None:
            system_instruction += get_cta_prompt_fragment(cta_style)
            print(f"   ↳ [CTA] 本篇 Threads 注入 Substack CTA，風格類={cta_style}")
        else:
            print(f"   ↳ [CTA] 本篇 Threads 不注入 CTA（保持純內容篇）")

    # 組裝 Prompt
    prompt = f"""
以下是今日要處理的新聞，以及初審編輯 (Reviewer Agent) 指定的戰略方向。

=== 初審編輯指令 (Editorial Mandate) ===
{editorial_note or '按既有靈魂風格自由發揮，但需維持強數據與深度分析。'}

=== 新聞詳情 ===
標題: {title}
本文/摘要: {content[:4000]}
原始圖片網址: {og_image or '無'}

請直接輸出 MultiPlatformDraft 的 JSON，包含 {', '.join(platforms)} 的變體。
(欄位結構：{{
  "fb": {{...PlatformVariant}} or null,
  "ig": {{...PlatformVariant}} or null,
  "threads": {{...PlatformVariant}} or null,
  "image_url": "...",
  "carousel": {{
     "insight_statement": "卡2 核心洞察：一句最反直覺的判斷(so-what)。單一陳述句、非條列。**≤30 字**。自己長、禁套範例。",
     "insight_support": "支撐那句的一句話。**≤40 字**。",
     "stat_number": "卡3 的主角數字/型號，如 $329 / 9 億 / 18%。**≤8 字元**。沒有夠力的數字就填 null（這張卡會自動省略）。",
     "stat_caption": "那個數字代表什麼，一句。**≤24 字**。",
     "takeaways": ["卡4：2-3 條、**每條 ≤18 字**、條列式、可帶走的行動或判斷"]
  }}
}}
每個 PlatformVariant 必要欄位：title, body, hashtags (list of str with #), primary_topic_tag, char_count
**carousel 欄位（必填）= 2-4 張可滑動圖卡的蒸餾內容。每張卡有固定任務，務必遵守字數上限**
（圖卡字一多就擠成小字、沒人看完）：
  · 卡1 封面 = 直接用該平台 variant 的 title 當鉤子（≤20 字，別照抄新聞標題）。
  · 卡2 核心洞察 = insight_statement(≤30 字、單句、不條列、不放數字) + insight_support(≤40 字)。
  · 卡3 一個數字 = stat_number(≤8 字元) + stat_caption(≤24 字)。**數字集中在這張**；沒有夠力數字就 stat_number=null。
  · 卡4 帶走的判斷 = takeaways 2-3 條、每條 ≤18 字、條列式。
  讓人不點文章、滑卡片就懂。所有內容針對本則新聞自己長，禁止套固定句型。)
"""

    chosen_model = model or DEFAULT_COMPOSER_MODEL

    result = await call_for_json(
        system=system_instruction,
        prompt=prompt,
        response_model=MultiPlatformDraft,
        gemini_model=chosen_model,
        temperature=0.3,  # 寫作需要一點變化
        timeout_s=240,    # Claude CLI 寫長文需要較長 timeout
    )

    if result.data is None:
        print(f"[Composer] ❌ 所有 LLM 路徑皆失敗 → 呼叫端請 skip。raw_error={result.raw_error}")
        return None

    if result.provider != "gemini":
        print(f"[Composer] ℹ️ 撰寫來自 fallback 提供者：{result.provider}")

    draft = result.data
    # 兜底：若 LLM 沒填 image_url 但我們有網址，沿用
    if not draft.image_url:
        draft.image_url = og_image

    # 大陸→台灣用語：修圖卡 carousel 文字（圖卡不經 finalize_variant，需在此修一次）。
    if draft.carousel is not None:
        c = draft.carousel
        c_fixes: List[str] = []
        for attr in ("insight_statement", "insight_support", "stat_caption"):
            val = getattr(c, attr, None)
            if val:
                new_val, fx = fix_mainland_text(val)
                if fx:
                    setattr(c, attr, new_val)
                    c_fixes += fx
        if c.takeaways:
            new_tks: List[str] = []
            for t in c.takeaways:
                ntk, fx = fix_mainland_text(t or "")
                new_tks.append(ntk)
                c_fixes += fx
            c.takeaways = new_tks
        for m in c_fixes:
            print(f"   ↳ [carousel 用語] {m}")

    return draft


# ---------- 保留舊 API 兼容性（供尚未升級的呼叫端用）----------
# 舊 run_pipeline.py 可能會呼叫 compose_draft；我們保留一個簡單 wrapper，
# 直接取 FB 變體作為主版本。若新 pipeline 上線，這個函式可以移除。

async def compose_draft(title: str, content: str, original_og_image: Optional[str] = None):
    bundle = await compose_multi_platform(title, content, original_og_image)
    if not bundle:
        return None
    return bundle  # 讓 caller 自己 .fb / .ig / .threads 取用


if __name__ == "__main__":
    # 簡單測試
    async def _run():
        title = "Anthropic 發表 Claude Opus 4.7，SWE-bench 創新高"
        body = "Anthropic 今日官方公告推出 Claude Opus 4.7 …（略）"
        draft = await compose_multi_platform(title, body)
        if not draft:
            print("generate fail")
            return
        for p in ("fb", "ig", "threads"):
            v = getattr(draft, p)
            v2, full, ok = finalize_variant(v, p)
            print(f"--- {p.upper()} (ok={ok}) {v2.char_count} 字 ---")
            print(full)
            print()

    asyncio.run(_run())
