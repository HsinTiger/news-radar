"""
News Radar · Analyst Agent (數據先知)
功能：抓取多平台成效數據，按平台屬性進行『差異化評估』，並識別演算法偏好。
"""
import os
import json
import asyncio
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from .db import get_conn, insert_engagement, latest_engagement_per_post
from google import genai
from pydantic import BaseModel, Field

# 定位 .env 檔案並載入
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

# ---------- 平台權重的定義 (根據使用者策略調整) ----------
PLATFORM_WEIGHTS = {
    "facebook": {
        "reach": 0.5,       # 權威陣地，看重廣度 (post_impressions_unique)
        "comments": 0.3,
        "shares": 0.2
    },
    "instagram": {
        "saved": 0.5,        # 知識圖譜，收藏最值錢
        "shares": 0.3,       # 轉發擴散
        "likes": 0.2
    },
    "threads": {
        "quotes": 0.4,       # 戰略引用，信號最強
        "replies": 0.3,      # 深度討論
        "views": 0.3,        # 破圈瀏覽
    }
}

# ---------- API Tokens ----------
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN")

class StrategicObservation(BaseModel):
    topic_tag: str = Field(description="觀測到的題材標籤或結構特點")
    performance_verdict: str = Field(description="成效評價（如：高收藏、引發激辯、破圈失敗）")
    platform: str
    rationale: str = Field(description="背後的數據支撐（例如：Saves 較平均值高出 30%）")
    suggested_action: str = Field(description="給 Scorer 或 Reflector 的調整建議")

class PerformanceReport(BaseModel):
    observations: List[StrategicObservation]
    summary_of_algorithm_shift: str = Field(description="針對目前各平台演算法偏好變化的綜述")

async def fetch_real_world_metrics(platform: str, post_id: str) -> Dict:
    """
    實戰調用 Meta Graph API 撈取真實數據。
    針對不同平台撈取專屬指標。
    """
    print(f"[Analyst: API] 正在從 {platform} 抓取實時數據 (PostID: {post_id})...")
    
    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if platform == "facebook":
                # FB Insights: 觸及與互動
                url = f"https://graph.facebook.com/v20.0/{post_id}/insights"
                params = {
                    "metric": "post_impressions_unique,post_engaged_users",
                    "access_token": FB_PAGE_ACCESS_TOKEN
                }
                resp = await client.get(url, params=params)
                data = resp.json()
                
                metrics = {"reach": 0, "comments": 0, "shares": 0}
                for item in data.get("data", []):
                    if item["name"] == "post_impressions_unique":
                        metrics["reach"] = item["values"][0]["value"]
                # FB Shares 需從 object 自身抓取
                obj_url = f"https://graph.facebook.com/v20.0/{post_id}"
                obj_resp = await client.get(obj_url, params={"fields": "shares,comments.summary(true)", "access_token": FB_PAGE_ACCESS_TOKEN})
                obj_data = obj_resp.json()
                metrics["shares"] = obj_data.get("shares", {}).get("count", 0)
                metrics["comments"] = obj_data.get("comments", {}).get("summary", {}).get("total_count", 0)
                return metrics

            elif platform == "instagram":
                # IG Insights: 收藏 (saved) 最重要
                url = f"https://graph.facebook.com/v20.0/{post_id}/insights"
                params = {
                    "metric": "reach,saved,engagement,shares",
                    "access_token": IG_ACCESS_TOKEN
                }
                resp = await client.get(url, params=params)
                data = resp.json()
                
                metrics = {"reach": 0, "saved": 0, "engagement": 0, "shares": 0, "likes": 0}
                for item in data.get("data", []):
                    metrics[item["name"]] = item["values"][0]["value"]
                return metrics

            elif platform == "threads":
                # Threads Insights: 引用 (quotes) 最重要
                url = f"https://graph.threads.net/v1.0/{post_id}/insights"
                params = {
                    "metric": "views,likes,replies,reposts,quotes",
                    "access_token": THREADS_ACCESS_TOKEN
                }
                resp = await client.get(url, params=params)
                data = resp.json()
                
                metrics = {"views": 0, "likes": 0, "replies": 0, "reposts": 0, "quotes": 0}
                for item in data.get("data", []):
                    metrics[item["name"]] = item["values"][0]["value"]
                return metrics
                
        except Exception as e:
            print(f" ⚠️ [Analyst: API Error] {platform} 抓取失敗: {e}")
    
    return {}

def calculate_success_score(metrics: Dict, platform: str) -> float:
    """按平台權重計算綜合得分 (0.0 ~ 100.0)"""
    weights = PLATFORM_WEIGHTS.get(platform, {})
    score = 0.0
    for key, weight in weights.items():
        score += metrics.get(key, 0) * weight
    return score

async def generate_feedback_directives(recent_performance: List[Dict]) -> Optional[PerformanceReport]:
    """使用 Gemini 分析近期數據趨勢，產出策略修正意見。"""
    api_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    
    prompt = f"""
你現在是 News Radar 的首席數據分析師。
以下是近期發布內容在 FB / IG / Threads 的真實績效回饋：

{json.dumps(recent_performance, ensure_ascii=False, indent=2)}

請分析這組數據，給出『平台差異化』的戰略洞察。
特別注意：
1. 哪些題材在 IG 上被瘋狂收藏（代表具備長久保存價值）？
2. 哪些內容在 Threads 引起引用（代表具備爭論性或深度觀點）？
3. 我們應該如何微調接下來的新聞選題標準 (Scorer) 或寫作靈魂 (Reflector)？
"""
    try:
        def _sync():
            return client.models.generate_content(
                model="gemini-2.5-flash-lite", # 使用 Lite 保持高效
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": PerformanceReport,
                }
            )
        response = await asyncio.to_thread(_sync)
        return response.parsed
    except Exception as e:
        print(f"[Analyst Error] AI 趨勢分析失敗: {e}")
        return None

async def run_analysis_cycle():
    """執行一次完整的『複盤 - 分析 - 回饋』循環"""
    print("🚀 [Analyst] 啟動數據複盤循環...")
    conn = get_conn()
    
    # 1. 撈取近期成功的發布紀錄
    from .db import list_successful_posts
    successful_posts = list_successful_posts(conn, limit=20)
    
    if not successful_posts:
        print("[Analyst] 目前尚無近期發布紀錄可供分析。")
        conn.close()
        return

    # 2. 逐一從 API 撈取真實數據並更新資料庫
    for post in successful_posts:
        real_metrics = await fetch_real_world_metrics(
            post["platform"], 
            post["platform_post_id"]
        )
        if real_metrics:
            insert_engagement(
                conn, 
                post["draft_id"], 
                post["platform"], 
                post["platform_post_id"],
                datetime.now().isoformat(),
                **real_metrics
            )

    # 3. 撈取最新匯總數據給 AI 分析
    stats = latest_engagement_per_post(conn)
    
    formatted_data = []
    for s in stats:
        formatted_data.append({
            "title": s["draft_title"],
            "platform": s["platform"],
            "metrics": {
                "likes": s["likes"], "comments": s["comments"],
                "shares": s["shares"], "saves": s["saves"],
                "quotes": s["quotes"], "views": s["views"]
            }
        })
    
    # 2. AI 分析趨勢
    report = await generate_feedback_directives(formatted_data)
    if report:
        print("\n=== 🔮 數據先知的戰略洞察 ===")
        print(f"演算法趨勢點評: {report.summary_of_algorithm_shift}")
        for obs in report.observations:
            print(f"📍 [{obs.platform}] {obs.topic_tag}: {obs.performance_verdict}")
            print(f"  ↳ 具體建議: {obs.suggested_action}")
        
        # 3. 將洞察存檔，待 Reflector 讀取
        feedback_path = os.path.join(os.path.dirname(__file__), "../config/analyst_feedback.json")
        with open(feedback_path, "w", encoding="utf-8") as f:
            json.dump(report.model_dump(), f, ensure_ascii=False, indent=2)
        print(f"\n[Analyst] 戰略建議已存至: {feedback_path}")

    conn.close()

if __name__ == "__main__":
    asyncio.run(run_analysis_cycle())
