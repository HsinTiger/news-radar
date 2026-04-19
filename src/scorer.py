"""
News Radar · Scorer 2.0 (Reviewer Agent / 初審編輯)
功能：使用 Gemini 1.5 Flash-8B 以極低成本擔任主編，篩選具備「數據密度」與「戰略信號」的內容。
"""
import os
import json
from typing import Optional, Dict, Any
from google import genai
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from pathlib import Path

# 定位 .env 檔案
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

class ScoreBreakdown(BaseModel):
    data_density: float = Field(description="數據密度：是否包含具體數字、比例、金額 (0~1.0)")
    strategic_signal: float = Field(description="戰略信號：是碎消息還是結構性轉變 (0~1.0)")
    news_novelty: float = Field(description="新穎度：是否提供新視角或第一手官宣 (0~1.0)")
    persona_fit: float = Field(description="靈魂契合度：是否符合『狐說八道』的反思/數據風格 (0~1.0)")

class NewsScore(BaseModel):
    confidence_score: float = Field(description="加權總分 (0~1.0)，決定是否插隊發布")
    score_breakdown: ScoreBreakdown
    editorial_note: str = Field(description="給寫作 Agent 的主編指令：點出這篇新聞『說明了哪兩件事』或『應從哪個代價角度反思』")

def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in .env")
    return genai.Client(api_key=api_key)

async def score_news(title: str, content: str) -> Optional[NewsScore]:
    """使用 Gemini 1.5 Flash-8B 擔任 Reviewer Agent"""
    client = get_gemini_client()
    
    # 載入戰略指令（由 Reflector 2.0 產出）
    directive_path = Path(__file__).resolve().parent.parent / "config" / "strategic_directives.md"
    current_directives = ""
    if directive_path.exists():
        current_directives = f"\n\n=== 目前戰略調整指令 ===\n{directive_path.read_text(encoding='utf-8')}"
    
    system_instruction = f"""
你現在是『科技商業速報』的初審主編。你的風格融合了蕭上農的底層反思、游庭皓的數據架構與 IEO 的宏觀視野。
你的任務是從大量的科技碎新聞中，挑選出真正具備「結構性影響」與「高數據密度」的珍珠。

評選標準：
1. 數據密度 (data_density): 沒數字就沒真相。優選包含財報數據、市佔變動、融資倍數的文章。
2. 戰略信號 (strategic_signal): 優先選擇影響「價值鏈搬移」或「護城河存亡」的事件。
3. 靈魂契合度 (persona_fit): 是否能延伸出『反思』？是否有『代價』可以探討？
{current_directives}

請給出加權總分，並寫下一段 Editorial Note。這段 Note 會直接影響後續寫作者的切入點。
"""

    prompt = f"請評閱以下新聞：\n標題：{title}\n內容：\n{content[:3000]}"
    
    try:
        response = client.models.generate_content(
            model='gemini-flash-latest',
            contents=prompt,
            config={
                'system_instruction': system_instruction,
                'response_mime_type': 'application/json',
                'response_schema': NewsScore,
                'temperature': 0.2, # 評分需要穩定性
            },
        )
        return response.parsed
    except Exception as e:
        print(f"[Error: Scorer 2.0] 評閱失敗: {str(e)}")
        # 降級處理：給予基礎分以防管線中斷
        return None

if __name__ == "__main__":
    import asyncio
    test_title = "Amazon 收購 Globalstar, 這是一場對 Apple 的供應鏈突擊"
    test_content = "亞馬遜今日宣佈以 11.8 億美元收購衛星服務商 Globalstar... 此舉被視為在庫柏計畫(Project Kuiper)上的重大進算..."
    
    async def run_test():
        score = await score_news(test_title, test_content)
        if score:
            print(f"Confidence Score: {score.confidence_score}")
            print(f"Editorial Note: {score.editorial_note}")
            print(f"Breakdown: {score.score_breakdown}")
            
    asyncio.run(run_test())
