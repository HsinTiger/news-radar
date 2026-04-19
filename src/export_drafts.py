"""
News Radar · Export 模組
功能：將資料庫中的待審核草稿匯出為 CSV 方便人類閱讀。
"""
import pandas as pd
import sqlite3
import os
from pathlib import Path

# 定位 DB
DB_PATH = Path(__file__).resolve().parent.parent / "db" / "news_radar.db"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "drafts_for_review.csv"

def export_to_csv():
    if not DB_PATH.exists():
        print(f"找不到資料庫: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    
    # 取出待審核項目
    query = "SELECT title, confidence_score, full_text, generated_at FROM drafts ORDER BY generated_at DESC"
    df = pd.read_sql_query(query, conn)
    
    if df.empty:
        print("目前資料庫中沒有草稿。")
        return

    # 確保全形標點與換行在 CSV 中能被正常開啓 (使用 utf-8-sig)
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"✅ 成功匯出至: {OUTPUT_PATH}")
    conn.close()

if __name__ == "__main__":
    export_to_csv()
