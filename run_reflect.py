"""
News Radar · Reflector 入口腳本（Milestone 3）
用法：
    # 正常執行：抓互動 + 掃 drafts + 讀 CSV → 呼叫 Gemini → append 到 soul.md
    python run_reflect.py

    # Dry-run：只印將送給 LLM 的 prompt，不改 soul、不呼叫 API
    python run_reflect.py --dry-run

    # 跳過互動 API（離線、或正在 Meta rate limit 時）
    python run_reflect.py --skip-engagement
"""
import asyncio
import json
import argparse

from src import engagement as engagement_mod
from src.reflector import run_reflection


def parse_args():
    parser = argparse.ArgumentParser(description="News Radar Reflector")
    parser.add_argument("--dry-run", action="store_true", help="只印 prompt，不呼叫 LLM")
    parser.add_argument("--skip-engagement", action="store_true",
                        help="略過 Meta 互動 API 呼叫")
    return parser.parse_args()


async def main():
    args = parse_args()

    if args.skip_engagement:
        async def _noop(conn, max_posts: int = 50):
            print("[Engagement] 已略過（--skip-engagement）")
            return {"total": 0, "ok": 0, "failed": 0, "failures": []}
        engagement_mod.sync_all_posts = _noop  # type: ignore

    result = await run_reflection(dry_run=args.dry_run)
    print()
    print("=== Reflector 結束 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
