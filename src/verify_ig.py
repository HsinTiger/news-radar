"""
News Radar · IG Token 驗證腳本
功能：測試能否獲取 IG 企業帳號資訊。
"""
import os
import httpx
from dotenv import load_dotenv
from pathlib import Path

# 定位 .env 檔案
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

IG_BUSINESS_ACCOUNT_ID = os.getenv("IG_BUSINESS_ACCOUNT_ID")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")

def verify_ig():
    if not IG_BUSINESS_ACCOUNT_ID or not IG_ACCESS_TOKEN:
        print("[Error] 請確認 .env 中已填入 IG_BUSINESS_ACCOUNT_ID 與 IG_ACCESS_TOKEN")
        return

    print(f"[Module: Instagram] 正在嘗試從不同入口驗證帳號 (ID: {IG_BUSINESS_ACCOUNT_ID})...")
    
    # 嘗試 入口 A: Meta Graph API (常用於粉專連動)
    url_a = f"https://graph.facebook.com/v20.0/{IG_BUSINESS_ACCOUNT_ID}?fields=username,name&access_token={IG_ACCESS_TOKEN}"
    
    # 嘗試 入口 B: Instagram Graph API (直接對應 IG Token)
    url_b = f"https://graph.instagram.com/v20.0/me?fields=id,username&access_token={IG_ACCESS_TOKEN}"
    
    try:
        # 先試 B (因為 Token 是 IGAA 開頭)
        print(" ↳ 嘗試直接從 graph.instagram.com 驗證...")
        resp = httpx.get(url_b)
        if resp.status_code == 200:
            data = resp.json()
            print(f"[Success] IG 驗證成功 (入口 B)！")
            print(f" ↳ 帳號名稱：@{data.get('username')}")
            return

        # 再試 A
        print(f" ↳ 入口 B 失敗 ({resp.status_code})，嘗試從 graph.facebook.com 驗證...")
        resp = httpx.get(url_a)
        data = resp.json()
        if resp.status_code == 200:
            print(f"[Success] IG 驗證成功 (入口 A)！")
            print(f" ↳ 帳號名稱：{data.get('name')} (@{data.get('username')})")
        else:
            print(f"[Error] 全線驗證失敗")
            print(f" ↳ 錯誤細節: {data.get('error', {}).get('message')}")
            
    except Exception as e:
        print(f"[Error] 異常: {str(e)}")

if __name__ == "__main__":
    verify_ig()
