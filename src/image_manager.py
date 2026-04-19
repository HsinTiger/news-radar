"""
News Radar · Image Manager (Milestone 5.1)
功能：
- 下載遠端圖片至在地 cache。
- [NEW] 媒介門檻校驗（MediaGatekeeper）。
- [NEW] X (Twitter) 與新聞鏡像尋檢（MirrorSeeker）。
"""
import httpx
import os
import asyncio
from pathlib import Path
from typing import Optional, List
import hashlib

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "assets" / "image_cache"

async def download_image(url: str) -> Optional[str]:
    """下載圖片並存入在地 cache。回傳：絕對路徑。"""
    if not url:
        return None
        
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    
    # 產生安全檔名
    ext = url.split("?")[0].split(".")[-1].lower()
    if ext not in ["jpg", "jpeg", "png", "webp"]:
        ext = "jpg"
    
    url_hash = hashlib.sha1(url.encode()).hexdigest()
    filename = f"{url_hash}.{ext}"
    local_path = CACHE_DIR / filename
    
    if local_path.exists():
        return str(local_path)
        
    print(f"[ImageManager] 正在下載備援圖片: {url[:60]}...")
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # 模擬瀏覽器 User-Agent
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                local_path.write_bytes(resp.content)
                print(f" ↳ [Success] 圖片已存至: {local_path.name}")
                return str(local_path)
            else:
                print(f" ↳ [Error] 下載失敗 (Status: {resp.status_code})")
                return None
    except Exception as e:
        print(f" ↳ [Error] 下載過程發生異常: {e}")
        return None

async def check_media_accessibility(url: str) -> bool:
    """檢查媒體網址是否能被 Meta 伺服器成功抓取。"""
    if not url: return False
    try:
        # 模擬一個通用的 User-Agent，若 200 則視為可連通
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            resp = await client.head(url)
            return resp.status_code == 200
    except:
        return False

async def find_mirror_image(title: str) -> Optional[str]:
    """[DEP] 在目前的無人值守環境下，暫時停用自動搜尋鏡像。
    未來將整合專項 API 以提供更穩定的鏡像來源。
    """
    return None

def cleanup_cache(max_files: int = 20):
    """
    簡單清理舊圖片，避免佔用過多空間。
    """
    if not CACHE_DIR.exists():
        return
        
    files = sorted(CACHE_DIR.glob("*"), key=lambda x: x.stat().st_mtime)
    if len(files) > max_files:
        to_delete = files[:len(files)-max_files]
        for f in to_delete:
            try:
                f.unlink()
            except:
                pass
