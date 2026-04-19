"""
News Radar · Threads Token 換領與 ID 獲取工具
功能：
1. 將短效 Threads User Token 換成長效 (60天) Token。
2. 自動獲取 THREADS_USER_ID。
3. 自動更新到 .env 檔案中。
"""
import os
import httpx
from dotenv import load_dotenv, set_key
from pathlib import Path

# 定位 .env 檔案
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

THREADS_APP_SECRET = os.getenv("THREADS_APP_SECRET")

def exchange_threads_token(short_token: str):
    if not THREADS_APP_SECRET:
        print("[Error] 請先確保 .env 中已填入 THREADS_APP_SECRET")
        return

    print("[Module: Threads] 正在換領長效 Token...")
    
    THREADS_APP_ID = os.getenv("THREADS_APP_ID")
    
    # 1. 換領長效 Token (60天)
    exchange_url = "https://graph.threads.net/access_token"
    params = {
        "grant_type": "th_exchange_token",
        "client_id": THREADS_APP_ID,
        "client_secret": THREADS_APP_SECRET,
        "access_token": short_token
    }

    try:
        resp = httpx.get(exchange_url, params=params)
        data = resp.json()
        
        if resp.status_code != 200:
            print(f"[Error] 換領失敗: {data.get('error', {}).get('message')}")
            print(f" ↳ 完整錯誤: {data}")
            return

        long_token = data.get("access_token")
        print("[Success] 已取得長效 Token。")

        # 2. 獲取 Threads User ID
        print("[Module: Threads] 正在獲取 User ID...")
        me_url = f"https://graph.threads.net/v1.0/me?fields=id,username&access_token={long_token}"
        me_resp = httpx.get(me_url)
        me_data = me_resp.json()

        user_id = me_data.get("id")
        username = me_data.get("username")

        if not user_id:
            print(f"[Error] 無法獲取 User ID: {me_data.get('error', {}).get('message')}")
            return

        print(f"[Success] 帳號驗證成功：{username} (ID: {user_id})")

        # 3. 寫回 .env
        set_key(str(env_path), "THREADS_USER_ID", user_id)
        set_key(str(env_path), "THREADS_ACCESS_TOKEN", long_token)
        print(f"[Finished] 已將 THREADS_USER_ID 與 THREADS_ACCESS_TOKEN 寫入 .env")

    except Exception as e:
        print(f"[Error] 執行過程發生異常: {str(e)}")

if __name__ == "__main__":
    token = input("請輸入從 Graph API Explorer 取得的 Threads 短效 Token: ").strip()
    if token:
        exchange_threads_token(token)
    else:
        print("未輸入 Token，程式結束。")
