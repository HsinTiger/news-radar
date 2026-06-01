"""
News Radar · Full Pipeline (Cloud Edition)
============================================
2026-06-01: 一鍵在 GitHub Actions 上跑完整流程。

流程：Init DB → (pipeline: Harvest → Score → Compose → Publish) → Engagement polling
全部不依賴本機 Claude CLI，改用 LiteLLM / Gemini API / Groq 等免費雲端 LLM。

環境變數需求（GitHub Actions secrets 中設定）：
  必要 — Meta API:
    META_APP_ID, META_APP_SECRET
    FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN
    IG_BUSINESS_ACCOUNT_ID, IG_ACCESS_TOKEN
    THREADS_USER_ID, THREADS_ACCESS_TOKEN, THREADS_APP_ID, THREADS_APP_SECRET
  必要 — 至少一個 LLM provider:
    GEMINI_API_KEY  (Google AI Studio, 免費 tier)
    或 GROQ_API_KEY (Groq free tier)
    或 OPENCODE_API_KEY (opencode.ai 免費 big-pickle)
  選用 — 更多 LLM 選項:
    ANTHROPIC_API_KEY (Claude API)
    LITELLM_MODEL    (指定模型，預設 gemini/gemini-2.5-flash)
"""

import asyncio
import os
import sys
import time as time_module
from datetime import datetime, timezone
from pathlib import Path

# 確保 import 可以從專案根目錄找到模組
_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))


async def main():
    t0 = time_module.time()

    print(f"\n{'='*60}")
    print(f"📡 News Radar · Cloud Full Pipeline")
    print(f"   Started at: {datetime.now(timezone.utc).isoformat()}")
    print(f"   Running on: {'GitHub Actions' if os.getenv('GITHUB_ACTIONS') else 'Local'}")
    print(f"{'='*60}\n")

    # ---- Step 1: Init DB ----
    from src import db as dbmod
    dbmod.init_db()
    print("[OK] DB initialized\n")

    # ---- Step 2: Let pipeline handle harvest → score → compose → publish ----
    # run_pipeline.py's main() parses sys.argv internally.
    # --harvest-now: 強制抓 RSS（不受 throttle 限制）
    # --publish-now: 忽略 cadence 限制，立即發文
    print("🚀 Step 2: Pipeline (harvest → score → compose → publish)...")
    from run_pipeline import main as pipeline_main

    sys.argv = ["run_pipeline.py", "--harvest-now", "--publish-now"]

    try:
        await pipeline_main()
        print("[Pipeline] ✅ Pipeline completed")
    except Exception as e:
        print(f"[Pipeline][Error] {e}")
        # 不 abort：繼續 engagement polling

    # ---- Step 3: Engagement polling ----
    print("\n📊 Step 3: Polling engagement data...")
    conn = dbmod.get_conn()
    try:
        from src.engagement import sync_bucket_polls
        summary = await sync_bucket_polls(conn)
        print(f"[Engagement] total={summary['total']} "
              f"ok={summary['ok']} "
              f"failed={summary['failed']} "
              f"rate_limited={summary['rate_limited']}")
    except Exception as e:
        print(f"[Engagement][Error] {e}")
    finally:
        conn.close()

    # ---- Done ----
    elapsed = time_module.time() - t0
    print(f"\n{'='*60}")
    print(f"✅ News Radar Pipeline Complete ({elapsed:.1f}s)")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
