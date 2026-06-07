#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import db as dbmod
c = dbmod.get_conn()
r = c.execute("SELECT id FROM drafts WHERE status IN ('auto_approved','published') ORDER BY generated_at DESC LIMIT 1").fetchone()
c.close()
if r:
    print(r["id"])
else:
    sys.exit(1)
