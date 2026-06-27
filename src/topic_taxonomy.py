"""
News Radar · Topic Taxonomy（Phase 8.20 Step 1）

這個檔案是「主題分類」的單一事實來源 (single source of truth)：
  - category_id（snake_case，永遠穩定，進 DB / 進 pydantic schema 都用這個）
  - display_name（中文顯示，給 UI / 週報 / docs 用）
  - seed_weight（Hsin 於 2026-04-21 拍板的初始權重；之後由 back-prop 迴路調整）
  - description（白話定義 + 例子，給 LLM classifier 在 prompt 裡參考）

🛑 修改 category_id 視同 schema 變動，必須同步：
  - data/01_harvest/schema.sql 的 topic_weights seed
  - src/db.py 的 seed 步驟
  - config/topic_keywords.yaml 的鍵（Step 2 會加）
  - tests/unit/test_topic_taxonomy.py

調整 seed_weight 則只要改這裡；但一旦 back-prop 開始跑，topic_weights 表
的權重會自己演化，這裡的數字只在**冷啟動（新 DB）**時被使用一次。

—— 2026-04-21
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class TopicCategory:
    id: str
    display_name: str
    seed_weight: float
    description: str


# 順序即為 Hsin 的優先級；back-prop 之後會打亂，但初始顯示用這個順序
TOPIC_CATEGORIES: List[TopicCategory] = [
    TopicCategory(
        id="ai_model",
        display_name="AI 基礎模型",
        seed_weight=1.70,
        description=(
            "AI 基礎模型的新發表或重要更新。例：GPT-5 / Claude Opus 4.7 / "
            "Gemini Ultra 2 發佈；開源 LLM（Llama / Mistral / DeepSeek）新版；"
            "多模態模型（文生圖、文生音、影片生成）新一代。重點是『模型本身』"
            "被視為產品。這類第一手官宣最稀缺，權重最高。"
        ),
    ),
    TopicCategory(
        id="ai_agent",
        display_name="AI Agent／自主系統",
        seed_weight=1.60,
        description=(
            "同時滿足以下三條才算 ai_agent（與 ai_application 最容易混淆，故從嚴）："
            " (1) 能『自主執行多步驟任務』——非單次 prompt → 單次 output；"
            " (2) 主動使用工具/function calling/外部 API，而非只回答問題；"
            " (3) 目標是『交付成果』（寫完 PR、完成研究報告、操作瀏覽器買票），"
            "不只是生成文字。 "
            "✓ 正例：Claude Code / Devin / Manus / Agent SDK / MCP 生態 / Computer Use / "
            "multi-agent 系統 / browser agent / SWE-bench 新紀錄。 "
            "✗ 反例：ChatGPT / Perplexity 的 Q&A（無多步驟任務）；"
            "單純『模型會叫函式』但只用於 JSON 輸出（沒有交付成果）。"
        ),
    ),
    TopicCategory(
        id="ai_application",
        display_name="AI 應用層產品",
        seed_weight=1.55,
        description=(
            "ai_application 的三條判準（與 ai_agent 的關鍵差別在『任務模式』）："
            " (1) 使用模式是單次 call → 單次 output（搜尋、翻譯、寫作、生圖）；"
            " (2) 沒有跨步驟推理或自主控制流；"
            " (3) 有明確的產品品牌且包裝給終端使用者。 "
            "✓ 正例：Perplexity / Cursor 的 autocomplete / Canva AI / Notion AI / "
            "ChatGPT Enterprise / NotebookLM / Apple Intelligence / v0.dev 生成元件。 "
            "✗ 反例：Devin 這種能跑整個任務的→算 ai_agent；模型本身發表→算 ai_model。"
        ),
    ),
    TopicCategory(
        id="supply_chain",
        display_name="產業鏈／供應鏈",
        seed_weight=1.40,
        description=(
            "半導體產業鏈、能源供應鏈、電池供應鏈等結構性資訊。例：台積電 / "
            "三星 / 中芯的產能動態；HBM / CoWoS / GaN / EUV 的供需；封測、"
            "光罩、基板廠的商業動態；關鍵材料（稀土、鎵、鋰）的戰略意涵。"
        ),
    ),
    TopicCategory(
        id="earnings",
        display_name="營收／財報",
        seed_weight=1.30,
        description=(
            "公司財報、月營收、法說會、業績指引。例：Nvidia / TSMC 法說；"
            "上市櫃公司每月營收公告；毛利率、EPS、guidance 調整；重大下修"
            "或上修事件。數據為主、情緒為輔。"
        ),
    ),
    TopicCategory(
        id="tw_stocks",
        display_name="台股個股／大盤",
        seed_weight=1.25,
        description=(
            "台股特定個股的動態（非財報、非供應鏈）、大盤籌碼、政策對台股的"
            "影響。例：主力買賣超、外資動向、台股 ETF 變動、櫃買市場熱點。"
            "如果某個台股消息同時屬於『supply_chain』或『earnings』，classifier"
            "應優先選更具體的那類。"
        ),
    ),
    TopicCategory(
        id="us_stocks",
        display_name="美股個股／大盤",
        seed_weight=1.25,
        description=(
            "美股個股動態、S&P 500 / Nasdaq 指數變動、FOMC 對股市的直接衝擊、"
            "科技七雄（Apple / Microsoft / Nvidia / Google / Amazon / Meta / "
            "Tesla）的非財報消息。與 tw_stocks 同邏輯，若能歸到更具體類別"
            "（supply_chain / earnings / ai_*）優先選那類。"
        ),
    ),
    TopicCategory(
        id="tech_product_launch",
        display_name="非 AI 科技新品",
        seed_weight=1.20,
        description=(
            "不是以 AI 為主角的科技新產品。例：iPhone 新世代、特斯拉新車、"
            "Apple Vision Pro、遊戲主機新代、消費電子新品、SaaS 主線更新。"
            "如果產品的賣點核心是 AI 能力，歸 ai_application 而非這類。"
        ),
    ),
    TopicCategory(
        id="policy_geopolitics",
        display_name="政策／地緣政治",
        seed_weight=1.00,
        description=(
            "立法、制裁、外交、關稅、跨國政策博弈。例：CHIPS 法案、對中制裁、"
            "歐盟 AI Act、貿易協定、技術出口管制。這類偏『結構變化』，短期"
            "數據密度不高，所以權重平平；但 strategic_signal 高的個案仍可靠"
            "scorer 的基礎分勝出。"
        ),
    ),
    # === 2026-06-27 新增：晚間 slot（政治/政策/軍事/時事）的桶 ===
    # 配合台灣新聞/政治/軍事來源大改 + 一天三篇的時段三分（早午=市場、晚=政治時事）。
    TopicCategory(
        id="tw_politics",
        display_name="台灣政治",
        seed_weight=1.10,
        description=(
            "台灣國內政治：選舉、立法院朝野攻防、法案表決、政黨與政治人物動態、"
            "地方政府施政與爭議。例：總預算/特別條例攻防、縣市長選戰、政院/立院/"
            "總統府動態。守中立、不選邊——只陳述可查證事實與各方原話。"
        ),
    ),
    TopicCategory(
        id="military_defense",
        display_name="軍事／國防",
        seed_weight=1.05,
        description=(
            "軍事、國防自主、軍購與地緣軍事衝突。例：無人機／無人載具產業與條例、"
            "軍購預算、台海安全、各國軍事行動與衝突。與 supply_chain/policy 重疊時，"
            "若主角是『國防/軍事』面向歸這類。常與投資（國防供應鏈）相關。"
        ),
    ),
    TopicCategory(
        id="current_affairs",
        display_name="時事／社會",
        seed_weight=0.90,
        description=(
            "台灣社會大小事與重大時事：災防（豪雨/淹水/地震）、民生與重大社會事件、"
            "公共議題。非純政治、非純科技商業，但有高關注度的『時事』。"
        ),
    ),
    TopicCategory(
        id="other",
        display_name="其它",
        seed_weight=0.70,
        description=(
            "以上各類都不沾邊的消息。不直接刷掉（保留意外之財的渠道），但"
            "權重打折。classifier 如果不確定就歸這類、別硬塞到其他類。"
        ),
    ),
]


def taxonomy_as_dict() -> Dict[str, TopicCategory]:
    """回傳 {category_id: TopicCategory}，方便下游以 id 查找。"""
    return {c.id: c for c in TOPIC_CATEGORIES}


def category_ids() -> List[str]:
    return [c.id for c in TOPIC_CATEGORIES]


def seed_weight_for(category_id: str) -> float:
    """查 seed_weight；未知類別回傳 other 的權重以保險。"""
    d = taxonomy_as_dict()
    if category_id in d:
        return d[category_id].seed_weight
    return d["other"].seed_weight


def classifier_prompt_block() -> str:
    """給 Step 2 classifier 的 system prompt 用：把 taxonomy 攤開成 LLM 讀得懂的條列。
    以 category_id 為 key（LLM 必須回傳這個字串），中文名只是輔助說明。
    """
    lines = ["可選類別（必須回傳其中一個 category_id，原字串不可改）："]
    for c in TOPIC_CATEGORIES:
        lines.append(f"- `{c.id}`（{c.display_name}）：{c.description}")
    return "\n".join(lines)
