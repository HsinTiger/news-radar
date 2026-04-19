"""
News Radar · Token Utils
功能：將 Meta 短期權杖換領為 60 天長效權杖 (Long-lived Access Token)。
"""
import requests
import os
from dotenv import load_dotenv, set_key
from pathlib import Path

# 定位 .env
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

def exchange_permanent_page_token(short_user_token: str):
    """
    1. Short User -> Long User (60 days)
    2. Long User -> Page Token (Permanent)
    """
    app_id = os.getenv("META_APP_ID")
    app_secret = os.getenv("META_APP_SECRET")
    page_id = os.getenv("FB_PAGE_ID")
    
    # Step 1: Long-lived User Token
    print("[*] 步驟 1: 換領 60 天長效用戶權杖...")
    url = "https://graph.facebook.com/v20.0/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_user_token
    }
    r1 = requests.get(url, params=params).json()
    long_user_token = r1.get("access_token")
    if not long_user_token:
        print(f"❌ 失敗: {r1}")
        return None

    # Step 2: Get Permanent Page Token
    print("[*] 步驟 2: 換領永久粉專權杖...")
    url = f"https://graph.facebook.com/v20.0/{page_id}"
    params = {
        "fields": "access_token",
        "access_token": long_user_token
    }
    r2 = requests.get(url, params=params).json()
    perm_page_token = r2.get("access_token")
    
    if perm_page_token:
        print("✅ 成功取得永久粉專權杖！")
        return perm_page_token
    else:
        # 如果失敗，嘗試從 /me/accounts 找
        print("[!] 嘗試從用戶帳號列表尋找專頁權杖...")
        url = "https://graph.facebook.com/v20.0/me/accounts"
        params = {"access_token": long_user_token}
        r3 = requests.get(url, params=params).json()
        for item in r3.get("data", []):
            if str(item.get("id")) == str(page_id):
                print(f"✅ 在列表找到專頁 '{item.get('name')}' 的永久權杖！")
                return item.get("access_token")
                
        print(f"❌ 找不到該 Page ID 的權杖: {r3}")
        return None

def exchange_long_lived_token(short_token: str, platform: str = "fb"):
    """
    platform: 'fb' or 'threads'
    """
    app_id = os.getenv("META_APP_ID" if platform == "fb" else f"{platform.upper()}_APP_ID")
    app_secret = os.getenv("META_APP_SECRET" if platform == "fb" else f"{platform.upper()}_APP_SECRET")
    
    if not app_id or not app_secret:
        print(f"錯誤：找不到 {platform} 的 APP_ID 或 APP_SECRET")
        return None

    print(f"[*] 正在換領 {platform} 長效權杖...")
    
    url = "https://graph.facebook.com/v20.0/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token
    }
    
    # Threads 有不同的 endpoint
    if platform == "threads":
        url = "https://graph.threads.net/access_token"
        params = {
            "grant_type": "th_exchange_token", # Threads 專用
            "client_secret": app_secret,
            "access_token": short_token
        }

    response = requests.get(url, params=params)
    data = response.json()
    
    if "access_token" in data:
        long_token = data["access_token"]
        print(f"✅ 換領成功！新權杖有效期限：{data.get('expires_in', '未知')} 秒")
        return long_token
    else:
        print(f"❌ 換領失敗：{data}")
        return None

def update_env_token(key: str, value: str):
    set_key(str(ENV_PATH), key, value)
    print(f"💾 已更新 .env 中的 {key}")

def refresh_threads_token():
    """自動續約 Threads 60 天權杖。Meta 要求權杖需大於 24 小時且未過期。"""
    old_token = os.getenv("THREADS_ACCESS_TOKEN")
    if not old_token:
        return None

    print("[*] 正在嘗試自動續約 Threads 權杖...")
    url = "https://graph.threads.net/refresh_access_token"
    params = {
        "grant_type": "th_refresh_token",
        "access_token": old_token
    }
    
    r = requests.get(url, params=params).json()
    new_token = r.get("access_token")
    
    if new_token:
        update_env_token("THREADS_ACCESS_TOKEN", new_token)
        print("✅ Threads 權杖續約成功，效期重新計算為 60 天。")
        return new_token
    else:
        # 如果失敗（可能是還沒到 24 小時），通常可以忽略
        print(f" ↳ 續約跳過或暫時失敗（正常現象）: {r.get('error', {}).get('message', '尚未到續約時間')}")
        return old_token

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("用法: python src/token_utils.py <platform> <short_token>")
        print("平台可選: fb, threads")
    else:
        platform = sys.argv[1]
        short_token = sys.argv[2]
        long_token = exchange_long_lived_token(short_token, platform)
        if long_token:
            prefix = platform.upper()
            if platform == "fb":
                update_env_token("FB_PAGE_ACCESS_TOKEN", long_token)
                # 一般來說，Page Token 同時可以用於 IG
                update_env_token("IG_ACCESS_TOKEN", long_token)
            else:
                update_env_token("THREADS_ACCESS_TOKEN", long_token)
