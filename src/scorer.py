"""
News Radar · Scorer 2.0 (Reviewer Agent / 初審編輯)
功能：擔任初審主編，篩選具備「數據密度」與「戰略信號」的內容。

Phase 8.19：改走 src.llm_brain.call_for_json()
    → Gemini primary, Claude CLI fallback, 兩條路皆失敗時回 None。
    呼叫端（run_pipeline.py）遇到 None 要 skip，絕不用 emergency template 塞垃圾進 queue。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from src.llm_brain import call_for_json


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECOVERY_CONTRACT_PATH = PROJECT_ROOT / "config" / "recovery_content_contract.md"


def _is_recovery_mode() -> bool:
    return os.environ.get("AUTOMATION_MODE", "").strip().lower() == "recovery"


def _build_system_instruction(current_directives: str = "") -> str:
    """Build a mode-specific reviewer contract.

    Recovery must not inherit the legacy technology/business filter.  That
    filter treated food safety, public services, and legislative accountability
    as low-value merely because they lacked company financial metrics.
    """
    if _is_recovery_mode():
        recovery_contract = RECOVERY_CONTRACT_PATH.read_text(encoding="utf-8")
        return f"""
你是「台灣公共利益日報」的初審主編。你的任務不是尋找科技商業新聞，而是從候選中挑出今天最值得台灣人花注意力的一件事。

評分原則：
1. data_density：衡量可核對的具名來源、日期、金額、人數、法條、批次、期限或市場數據；不是只看財報或公司數字。
2. strategic_signal：衡量事件是否直接改變台灣人的金錢、安全、權利、公共服務、工作、食物、投資或國家政策，以及是否存在明確的責任機關與下一個問責節點。
3. news_novelty：衡量這次更新本身是否新鮮且有新事實；同題舊背景不能冒充今日進展。
4. persona_fit：衡量能否寫成有來源、可理解、有實際用途且不煽動的公共利益貼文。

不要因為食安、民生、法律、政策或政府監督題缺少產業財務數據而降分。反之，只有抽象產業評論、海外八卦、產品發布或無台灣直接影響時應降分。

Editorial Note 只能指示如何使用輸入已提供的事實，不得補造資訊。必須點出：最強的可驗證後果、應具名的來源、受影響者、讀者可採取的下一步，以及下一個可追蹤的日期／文件／責任機關。禁止「護城河、真正的賽局、底層邏輯、神話破滅、信任崩塌」等模板。
{current_directives}

=== Recovery editorial contract ===
{recovery_contract}

【輸出格式】回覆一個 JSON，欄位需完全符合以下 schema：
{{
  "confidence_score": float 0~1,
  "score_breakdown": {{
    "data_density": float 0~1,
    "strategic_signal": float 0~1,
    "news_novelty": float 0~1,
    "persona_fit": float 0~1
  }},
  "editorial_note": "給寫作者的主編指令"
}}
"""

    return f"""
你現在是『科技商業速報』的初審主編。你的風格融合了蕭上農的底層反思、游庭皓的數據架構與 IEO 的宏觀視野。
你的任務是從大量的科技碎新聞中，挑選出真正具備「結構性影響」與「高數據密度」的珍珠。

評選標準：
1. 數據密度 (data_density): 沒數字就沒真相。優選包含財報數據、市佔變動、融資倍數的文章。
2. 戰略信號 (strategic_signal): 優先選擇影響「價值鏈搬移」或「護城河存亡」的事件。
3. 靈魂契合度 (persona_fit): 是否能延伸出『反思』？是否有『代價』可以探討？
{current_directives}

請給出加權總分，並寫下一段 Editorial Note。這段 Note 會直接影響後續寫作者的切入點。

【輸出格式】回覆一個 JSON，欄位需完全符合以下 schema：
{{
  "confidence_score": float 0~1,
  "score_breakdown": {{
    "data_density": float 0~1,
    "strategic_signal": float 0~1,
    "news_novelty": float 0~1,
    "persona_fit": float 0~1
  }},
  "editorial_note": "給寫作者的主編指令"
}}
"""

# 定位 .env 檔案（僅用於載入環境變數，實際 LLM 呼叫在 llm_brain）
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)


# ---------- schemas（維持舊名，run_pipeline.py 直接 import）----------

class ScoreBreakdown(BaseModel):
    data_density: float = Field(description="數據密度：是否包含具體數字、比例、金額 (0~1.0)")
    strategic_signal: float = Field(description="戰略信號：是碎消息還是結構性轉變 (0~1.0)")
    news_novelty: float = Field(description="新穎度：是否提供新視角或第一手官宣 (0~1.0)")
    persona_fit: float = Field(description="靈魂契合度：是否符合『狐說八道』的反思/數據風格 (0~1.0)")


class NewsScore(BaseModel):
    confidence_score: float = Field(description="加權總分 (0~1.0)，決定是否插隊發布")
    score_breakdown: ScoreBreakdown
    editorial_note: str = Field(description="給寫作 Agent 的主編指令：點出這篇新聞『說明了哪兩件事』或『應從哪個代價角度反思』")


# ---------- 主評閱邏輯 ----------

async def score_news(title: str, content: str) -> Optional[NewsScore]:
    """使用 llm_brain 擔任 Reviewer Agent。

    Phase 8.19 新流程：
      1. 組 system + prompt
      2. 交給 llm_brain.call_for_json → 自動決定 Gemini / Claude CLI
      3. 若兩條路都失敗 → 回 None；呼叫端自行 skip（絕不降級成 emergency template）

    回傳：NewsScore 或 None（表示無可用 LLM，呼叫端 skip 這篇）。
    """
    # 載入戰略指令（由 Reflector 2.0 產出）
    directive_path = Path(__file__).resolve().parent.parent / "config" / "strategic_directives.md"
    current_directives = ""
    if directive_path.exists():
        current_directives = f"\n\n=== 目前戰略調整指令 ===\n{directive_path.read_text(encoding='utf-8')}"

    system_instruction = _build_system_instruction(current_directives)

    prompt = f"請評閱以下新聞：\n標題：{title}\n內容：\n{content[:3000]}"

    result = await call_for_json(
        system=system_instruction,
        prompt=prompt,
        response_model=NewsScore,
        gemini_model="gemini-flash-latest",
        temperature=0.2,  # 評分需要穩定性
    )

    if result.data is None:
        print(f"[Scorer 2.0] ❌ 所有 LLM 路徑皆失敗 → skip。raw_error={result.raw_error}")
        return None

    if result.provider != "gemini":
        print(f"[Scorer 2.0] ℹ️ 評分來自 fallback 提供者：{result.provider}")
    return result.data


if __name__ == "__main__":
    import asyncio

    test_title = "Amazon 收購 Globalstar, 這是一場對 Apple 的供應鏈突擊"
    test_content = "亞馬遜今日宣佈以 11.8 億美元收購衛星服務商 Globalstar..."

    async def run_test():
        score = await score_news(test_title, test_content)
        if score:
            print(f"Confidence Score: {score.confidence_score}")
            print(f"Editorial Note: {score.editorial_note}")
            print(f"Breakdown: {score.score_breakdown}")
        else:
            print("score_news returned None (both LLM paths unavailable)")

    asyncio.run(run_test())
