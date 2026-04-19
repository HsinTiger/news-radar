"""
News Radar · FB Token 驗證腳本
功能：讀取 .env 中的 FB_PAGE_ID 與 FB_PAGE_ACCESS_TOKEN，發送一則測試貼文到粉專。
"""
import os
import httpx
from dotenv import load_dotenv
from pathlib import Path

# 定位 .env 檔案
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")

def verify_fb_token():
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        print("[Error] 請確認 .env 檔案中已填入 FB_PAGE_ID 與 FB_PAGE_ACCESS_TOKEN")
        return

    print(f"[Module: Verification] 正在測試發佈到粉專 (ID: {FB_PAGE_ID})...")
    
    # Meta Graph API Endpoint
    url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
    
    payload = {
        "message": "📡 News Radar 自動化管線：金鑰驗證中...\n這是一則由系統自動產生的測試貼文，完成後可手動刪除。",
        "access_token": FB_PAGE_ACCESS_TOKEN
    }

    try:
        response = httpx.post(url, data=payload)
        result = response.json()
        
        if response.status_code == 200:
            print("[Success] 貼文發佈成功！")
            print(f" ↳ 貼文 ID: {result.get('id')}")
            print(f" ↳ 請到粉專檢查：https://www.facebook.com/{FB_PAGE_ID}")
        else:
            print(f"[Error] 發佈失敗 (Status: {response.status_code})")
            print(f" ↳ 錯誤訊息: {result.get('error', {}).get('message')}")
            
    except Exception as e:
        print(f"[Error] 執行過程發生異常: {str(e)}")

if __name__ == "__main__":
    verify_fb_token()
