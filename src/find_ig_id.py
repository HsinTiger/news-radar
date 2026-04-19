import requests
import os
from dotenv import load_dotenv
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

def find_linked_ig_account():
    token = os.getenv("FB_PAGE_ACCESS_TOKEN")
    page_id = os.getenv("FB_PAGE_ID")
    
    if not token or not page_id:
        print("錯誤：找不到 FB_PAGE_ACCESS_TOKEN 或 FB_PAGE_ID")
        return

    print(f"[*] 正在從專頁 {page_id} 尋找連結的 Instagram 帳號...")
    
    url = f"https://graph.facebook.com/v20.0/{page_id}"
    params = {
        "fields": "instagram_business_account,name",
        "access_token": token
    }
    
    response = requests.get(url, params=params).json()
    
    if "instagram_business_account" in response:
        ig_id = response["instagram_business_account"]["id"]
        print(f"✅ 找到連結的 Instagram 專業帳號！")
        print(f"帳號名稱: {response.get('name')}")
        print(f"正確的 IG_BUSINESS_ACCOUNT_ID: {ig_id}")
        return ig_id
    else:
        print(f"❌ 該 Facebook 專頁似乎尚未連結任何 Instagram 專業帳號。")
        print(f"Meta 回傳結果: {response}")
        return None

if __name__ == "__main__":
    find_linked_ig_account()
