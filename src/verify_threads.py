"""
News Radar · Threads 終極驗證腳本
功能：直接測試目前的 THREADS_ACCESS_TOKEN 是否有效，並獲取 User ID。
"""
import os
import httpx
from dotenv import load_dotenv, set_key
from pathlib import Path

# 定位 .env 檔案
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

TOKEN = os.getenv("THREADS_ACCESS_TOKEN")

def verify_threads_direct():
    # 如果 .env 裡還是空的，先用使用者剛才給的那串
    current_token = TOKEN or "THAAib3ZAmkEXBBYmFJUkt0cXZARODFxV1YySlIzUExIVGJPMm5yRlNLdzc2TWZAXSEI0MnhPRVphTWstWHY4bXZAOY2ZASUnZAyNnNkRG91U3pPQTZAZAa2t0dUVHaDNDQkF2SG5aWnRsaWhnVVNTbG1hQU1ka282b2lkVkk4cGk0bGZA2UUgzMWt3UkpGc1ZACSXlOSEkZD"
    
    print(f"[Module: Threads] 正在驗證 Token 有效性...")
    
    url = f"https://graph.threads.net/v1.0/me?fields=id,username&access_token={current_token}"
    
    try:
        resp = httpx.get(url)
        data = resp.json()
        
        if resp.status_code == 200:
            user_id = data.get("id")
            username = data.get("username")
            print(f"[Success] Token 驗證成功！")
            print(f" ↳ 帳號：{username}")
            print(f" ↳ ID：{user_id}")
            
            # 寫回 .env 確保後續管線可用
            set_key(str(env_path), "THREADS_USER_ID", user_id)
            set_key(str(env_path), "THREADS_ACCESS_TOKEN", current_token)
            print("[Finished] 帳號資訊已同步至 .env")
        else:
            print(f"[Error] 驗證失敗: {data.get('error', {}).get('message')}")
            
    except Exception as e:
        print(f"[Error] 異常: {str(e)}")

if __name__ == "__main__":
    verify_threads_direct()
