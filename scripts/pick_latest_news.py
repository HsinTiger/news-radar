#!/usr/bin/env python3
"""Pick the latest unscored news item from DB and print title/content."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import db as dbmod

conn = dbmod.get_conn()
row = conn.execute(
    "SELECT title, clean_markdown FROM news_items "
    "WHERE clean_markdown IS NOT NULL AND LENGTH(clean_markdown) > 200 "
    "AND status IN ('fetched','scored') "
    "ORDER BY fetched_at DESC LIMIT 1"
).fetchone()
conn.close()

if row:
    print(row["title"])
    print(row["clean_markdown"][:2000])
else:
    sys.exit(1)
