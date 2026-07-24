"""
News Radar · Harvest 入口
Milestone 1：一行指令端到端跑完採集 → 清洗 → 過濾 → 寫入 SQLite

用法：
    cd news_radar
    python run_harvest.py

零 token 消耗。純 Deterministic 層。
執行結束會印出 HarvestReport 與 log 寫入 logs/execution_log.jsonl。
"""
from __future__ import annotations
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# 允許直接 `python run_harvest.py` 或 `python -m news_radar.run_harvest`
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx

from src import db as dbmod
from src.fetcher import load_config, harvest_all_feeds, fetch_html
from src.cleaner import clean_and_filter
from src.schema import HarvestReport


async def _process_single(client, item, cfg, conn, report):
    """抓 HTML → 清洗 → 過濾 → 寫 DB"""
    # 若已存在，跳過（連 HTML 都不抓，省頻寬）
    if dbmod.news_exists(conn, item.id):
        return

    if item.clean_markdown:
        html = "" 
        print(f"[Harvest] URL 預先解析 (如 YouTube)，跳過 HTML 抓取: {item.url[:40]}")
    else:
        from src.fetcher import fetch_html
        html = await fetch_html(client, item.url)
        if html is None:
            report.errors.append(f"fetch_failed:{item.url}")
            return

    updated_item, passed, reason = await clean_and_filter(item, html, cfg)

    if not passed:
        updated_item.status = "dropped"
        updated_item.drop_reason = reason
        dbmod.upsert_news(conn, updated_item)
        report.items_dropped += 1
        report.drop_reasons[reason] = report.drop_reasons.get(reason, 0) + 1
        return

    inserted = dbmod.upsert_news(conn, updated_item)
    if inserted:
        report.items_new += 1


def _select_feed_config(cfg: dict, feed_tag: str | None) -> dict:
    """Return a shallow config copy restricted to one explicit feed tag.

    The normal production harvest keeps its existing all-feed behavior.  A
    caller such as the Recovery setup canary may ask the same fetcher/cleaner
    path to exercise only authoritative sources without maintaining a second
    harvester implementation.
    """
    if feed_tag is None:
        return cfg
    normalized = str(feed_tag).strip()
    if not normalized:
        raise ValueError("feed_tag must be a non-empty string")
    feeds = [
        feed
        for feed in cfg.get("feeds", [])
        if normalized in (feed.get("tags") or [])
    ]
    if not feeds:
        raise ValueError(f"no configured feeds carry tag: {normalized}")
    return {**cfg, "feeds": feeds}


async def run_harvest_once(
    *,
    feed_tag: str | None = None,
    write_log: bool = True,
) -> HarvestReport:
    """可被其他模組 import 的入口（例如 run_pipeline.py --loop）。
    與 main() 行為一致：抓 RSS → 清洗 → 寫 DB → 回傳 HarvestReport。
    """
    started = datetime.now(timezone.utc)
    report = HarvestReport(
        started_at=started.isoformat(),
        finished_at="",
    )

    # 1. 載入設定 + 初始化 DB
    cfg = _select_feed_config(load_config(), feed_tag)
    dbmod.init_db()
    conn = dbmod.get_conn()

    # 2. 抓所有 RSS
    print("\n=== 啟動 News Radar Harvest ===")
    feed_results: dict[str, dict] = {}
    all_items = await harvest_all_feeds(
        cfg,
        feed_diagnostics=feed_results,
    )
    report.feeds_checked = len(cfg["feeds"])
    report.feed_results = feed_results
    report.items_found = len(all_items)
    for feed_name, result in feed_results.items():
        if result.get("status") != "ok":
            report.errors.append(
                f"feed_failed:{feed_name}:{result.get('error_type') or 'unknown'}"
            )

    # 3. 逐篇抓原始 HTML 並清洗（並行，但限制併發避免被當 DDoS）
    sem = asyncio.Semaphore(5)

    async def _with_limit(client, item):
        async with sem:
            await _process_single(client, item, cfg, conn, report)

    async with httpx.AsyncClient() as client:
        await asyncio.gather(*[_with_limit(client, it) for it in all_items])

    conn.close()

    # 4. 結尾報告
    finished = datetime.now(timezone.utc)
    report.finished_at = finished.isoformat()

    print("\n=== Harvest Report ===")
    print(f"  耗時          : {(finished - started).total_seconds():.1f}s")
    print(f"  檢查 feeds    : {report.feeds_checked}")
    print(f"  RSS entries   : {report.items_found}")
    print(f"  新增入庫      : {report.items_new}")
    print(f"  過濾 Drop     : {report.items_dropped}")
    if report.drop_reasons:
        print("  Drop 原因分布 :")
        for reason, count in sorted(report.drop_reasons.items(), key=lambda x: -x[1]):
            print(f"     {reason:30s}  {count}")
    if report.errors:
        print(f"  錯誤          : {len(report.errors)} 筆（前 3）")
        for e in report.errors[:3]:
            print(f"     {e}")

    # 5. 寫 jsonl log。Disposable canary 可明確停用，避免把測試執行
    # 混入 production execution history。
    if write_log:
        log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(exist_ok=True)
        with open(log_dir / "execution_log.jsonl", "a", encoding="utf-8") as f:
            f.write(report.model_dump_json() + "\n")
        print(f"\n  Log 寫入      : logs/execution_log.jsonl")
    else:
        print("\n  Log 寫入      : disabled (disposable run)")

    return report


# 對外保持 `main` 名稱以維持 CLI 與既有呼叫點相容
main = run_harvest_once


if __name__ == "__main__":
    asyncio.run(run_harvest_once())
