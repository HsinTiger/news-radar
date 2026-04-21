"""
News Radar · Cloud Publish Queue (Phase 8.18)
============================================
職責：
    從 DB 的 publish queue 挑「最新的」一筆 queued draft，呼叫 Meta Graph API
    送到 FB / IG / Threads。**不呼叫任何 LLM**——brain work 留在 Mac（Phase 8.19
    會把 composer / scorer 的 LLM 呼叫改成 `claude -p` subprocess）。

契約（見 docs/architect_plan_disscussion.md Phase 8.18）：
    - 僅 import publisher / db / schema / image_manager。**禁止** import composer /
      scorer / reflector——brain 不能雲端化這條結構性防線。
    - 選稿：freshness-first（`pick_freshest_queued` = 按 news_items.published_at DESC）。
    - 發出後把「比被挑中那筆還舊」的 queued draft 全標 stale。
    - Cadence：
        - 距上次成功發文 < 60 min → 跳過（honour 1h upper bound）
        - 60 ≤ 距今 < 120 min → 正常 freshness-first 發
        - 距今 ≥ 120 min → queue 空也要想辦法發，放寬挑 stale/approved（2h lower bound）
    - `--force` flag 忽略 cadence 檢查（人工觸發 / workflow_dispatch 用）。

執行入口：
    - GitHub Actions `pipeline.yml` 每小時 `0 * * * *` 呼叫。
    - 或本機 debug：`python run_publish_queue.py [--force] [--dry-run]`。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src import db as dbmod
from src import image_manager
from src.content_quality_guard import (
    check_quality,
    format_issues,
    has_blocking_issues,
)
from src.local_notify import notify_quality_block
from src.publisher import publish_to_fb, publish_to_ig, publish_to_threads
from src.schema import PublishResult
from src.token_utils import refresh_threads_token

# ---------- Cadence 參數（與 config.yaml 的 min/max_interval_minutes 對齊）----------
MIN_INTERVAL_SECONDS = 60 * 60           # 1h upper bound（頻率上限 = 至少間隔 60 min）
MAX_INTERVAL_SECONDS = 2 * 60 * 60       # 2h lower bound（頻率下限 = 至多間隔 120 min）


# ---------- _publish_one outcome codes（Phase 8.20 追加）----------
# exit code 映射寫在 main()；任何非 ALL_PLATFORMS_FAILED 都算「pipeline 正常運作」
# 設計原則：workflow red 只保留給『真的出事』的情境。預期內的操作（queue 空、
# guard 主動攔、資料缺損已 mark_failed）都 exit 0。
OUTCOME_PUBLISHED = "published"               # ≥1 平台成功 → exit 0
OUTCOME_QUALITY_BLOCKED = "quality_blocked"   # guard 主動拒發 → exit 0（guard 正常工作）
OUTCOME_NO_PLATFORM_DRAFTS = "no_platform_drafts"  # 資料異常、已 mark_failed → exit 0
OUTCOME_ALL_PLATFORMS_FAILED = "all_platforms_failed"  # Meta API 三平台全敗 → exit 1


# ---------- 工具 ----------

def _parse_iso(s: str) -> Optional[datetime]:
    """robust ISO8601 解析；失敗回 None（當作沒發過）。"""
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _seconds_since_last_publish(conn) -> Optional[float]:
    iso = dbmod.last_successful_publish_at(conn)
    dt = _parse_iso(iso)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _decide_cadence(conn, force: bool) -> tuple[bool, str, bool]:
    """回傳 (should_publish, reason, allow_fallback)
    - allow_fallback=True 表示走 2h lower bound 路徑（可挑 stale）。
    """
    if force:
        return True, "force=True，略過 cadence 檢查", True

    elapsed = _seconds_since_last_publish(conn)
    if elapsed is None:
        return True, "無歷史發文紀錄，允許首發", False

    mins = elapsed / 60.0
    if elapsed < MIN_INTERVAL_SECONDS:
        return False, f"距上次成功發文 {mins:.1f} min (<60)，honour 1h upper bound → 跳過", False
    if elapsed < MAX_INTERVAL_SECONDS:
        return True, f"距上次成功發文 {mins:.1f} min (60–120)，freshness-first 正常發", False
    return True, f"距上次成功發文 {mins:.1f} min (≥120)，2h lower bound → 允許 fallback 挑 stale", True


# ---------- 選稿 ----------

def _pick_draft(conn, allow_fallback: bool):
    """回傳 (row, mode) 或 (None, "empty")。mode ∈ {"fresh", "fallback"}."""
    row = dbmod.pick_freshest_queued(conn)
    if row is not None:
        return row, "fresh"
    if allow_fallback:
        row = dbmod.pick_fallback_any_approved(conn)
        if row is not None:
            return row, "fallback"
    return None, "empty"


# ---------- 發布 ----------

async def _publish_one(conn, row, dry_run: bool = False) -> str:
    """把挑到的 draft 的三個 platform_drafts 各發一次。

    回傳 OUTCOME_* 字串。main() 負責把它映射成 exit code。
    設計原則：_publish_one 只講「發生什麼」、不講「workflow 該紅還是綠」。
    """
    draft_id = row["id"]
    print(f"\n[PublishQueue] 選中 draft: {draft_id[:16]}…")
    print(f"   ↳ 新聞: {(row['news_title'] or '')[:60]}")
    print(f"   ↳ 新聞發佈: {row['news_published_at']}")

    platform_drafts = dbmod.get_platform_drafts(conn, draft_id)
    if not platform_drafts:
        print(f"   ⚠️ 找不到 platform_drafts → 無法發布，標 failed")
        if not dry_run:
            dbmod.mark_queue_failed(conn, draft_id, reason="no_platform_drafts")
        return OUTCOME_NO_PLATFORM_DRAFTS

    # ---------- Phase 8.20：品質守門員（攔下 Phase 8.19 前的 emergency_template）----------
    # 檢查每個平台的 final/full_text；只要有任一個 platform_draft 觸發 block 級規則，
    # 整筆 draft 不發，標 failed，跳 Mac 通知。Hsin 要求不刪 DB、他手動處理。
    guard_news_title = row["news_title"] or ""
    block_reasons: list[str] = []
    for pd_row in platform_drafts:
        text_to_check = pd_row["final_text"] or pd_row["full_text"] or ""
        issues = check_quality(text_to_check, title=guard_news_title)
        if has_blocking_issues(issues):
            block_reasons.append(f"{pd_row['platform']}: {format_issues(issues)}")
    if block_reasons:
        one_line = " || ".join(block_reasons)
        print(f"   🛑 [QualityGuard] 擋下代班假文 → {one_line}")
        if not dry_run:
            dbmod.mark_queue_failed(conn, draft_id, reason=f"quality_guard: {one_line}")
            notify_quality_block(draft_id=draft_id, reasons_one_line=one_line[:200])
        # 不記 publish_log——這不是「發文失敗」而是 guard 主動拒發。
        # workflow 維持 exit 0（guard 做它該做的事，不是系統壞）。
        return OUTCOME_QUALITY_BLOCKED

    image_url = row["og_image_url"]
    # og_video_url 暫不主動使用（Phase 8.18 範圍內；影片 path 已在 Phase 8.16 實作，
    # 需 composer 決定是否走影片發文，此處維持純文字 + 圖片路徑）

    any_success = False
    for pd_row in platform_drafts:
        platform = pd_row["platform"]          # facebook / instagram / threads
        full_text = pd_row["final_text"] or pd_row["full_text"]
        posted_at = datetime.now(timezone.utc).isoformat()

        if dry_run:
            print(f"   · [dry-run] {platform} ({len(full_text)} 字) → 不呼叫 API")
            continue

        if platform == "facebook":
            print(f"   ↳ [📘 FB] Plan A (image_url)...")
            result = await publish_to_fb(full_text, image_url=image_url)
            error_msg = str(result.get("error", ""))
            is_fetch_fail = "failed to download" in error_msg.lower() or "1353045" in error_msg
            if not result.get("success") and is_fetch_fail and image_url:
                print(f"   ⚠️ [📘 FB] Plan A 失敗 → Plan B 下載後上傳...")
                local_path = await image_manager.download_image(image_url)
                if local_path:
                    result = await publish_to_fb(full_text, local_file_path=local_path)
                    image_manager.cleanup_cache()
        elif platform == "instagram":
            if not image_url:
                result = {"success": False, "error": {"local_reject": "IG 需要 image_url"}}
            else:
                result = await publish_to_ig(full_text, image_url)
        elif platform == "threads":
            if not image_url:
                result = {"success": False, "error": {"local_reject": "Threads 需要 image_url"}}
            else:
                result = await publish_to_threads(full_text, image_url)
        else:
            print(f"   ⚠️ 未知平台 {platform}，跳過")
            continue

        success = bool(result.get("success"))
        dbmod.log_publish(conn, PublishResult(
            draft_id=draft_id,
            platform=platform,
            platform_post_id=result.get("id"),
            posted_at=datetime.now(timezone.utc).isoformat(),
            success=success,
            error_message=str(result.get("error", "")) if not success else None,
        ))
        if success:
            any_success = True
            print(f"   ✅ [{platform}] 成功 id={result.get('id')}")
        else:
            print(f"   ❌ [{platform}] 失敗: {str(result.get('error'))[:200]}")

    if dry_run:
        return OUTCOME_PUBLISHED  # dry-run 不改 DB、也不記失敗——視為正常走完

    if any_success:
        dbmod.mark_queue_published(conn, draft_id)
        stale_count = dbmod.mark_queue_stale_except(conn, draft_id)
        if stale_count:
            print(f"   ↳ freshness-first: 把 {stale_count} 筆比它舊的 queued 標 stale")
        return OUTCOME_PUBLISHED

    # 三平台全軍覆沒 → 避免無限輪迴，標 failed（人工可改回 queued 重試）。
    # 這是少數會讓 workflow red 的情境——代表 Meta API 側真的有問題，應該要收到通知。
    dbmod.mark_queue_failed(conn, draft_id)
    return OUTCOME_ALL_PLATFORMS_FAILED


# ---------- Main ----------

async def main() -> int:
    parser = argparse.ArgumentParser(description="News Radar Cloud Publisher (Phase 8.18)")
    parser.add_argument("--force", action="store_true", help="忽略 1h upper bound，強制挑一筆發（也允許 fallback）")
    parser.add_argument("--dry-run", action="store_true", help="只印要發的內容，不呼叫 API、不改 DB")
    args = parser.parse_args()

    dbmod.init_db()
    refresh_threads_token()

    conn = dbmod.get_conn()
    try:
        # 1. Cadence 決策
        should_publish, reason, allow_fallback = _decide_cadence(conn, force=args.force)
        print(f"[Cadence] {reason}")
        if not should_publish:
            print("[PublishQueue] 本 cycle 跳過。")
            # 給一個健康度快照方便 log
            print(f"[Queue] 狀態分佈: {dbmod.count_queue_status(conn)}")
            return 0

        # 2. 選稿
        row, mode = _pick_draft(conn, allow_fallback=allow_fallback)
        if row is None:
            print(f"[PublishQueue] queue 空（mode={mode}），無稿可發。")
            print(f"[Queue] 狀態分佈: {dbmod.count_queue_status(conn)}")
            return 0
        print(f"[PublishQueue] 選稿 mode={mode}")

        # 3. 發布
        outcome = await _publish_one(conn, row, dry_run=args.dry_run)
        print(f"[PublishQueue] 完成，outcome={outcome}")
        print(f"[Queue] 最終狀態分佈: {dbmod.count_queue_status(conn)}")

        # Outcome → exit code 映射（Phase 8.20 exit semantics）
        #   PUBLISHED / QUALITY_BLOCKED / NO_PLATFORM_DRAFTS → 0（pipeline 正常運作）
        #   ALL_PLATFORMS_FAILED                              → 1（Meta API 真的出事，workflow red）
        # dry-run 永遠 0。
        if args.dry_run:
            return 0
        if outcome == OUTCOME_ALL_PLATFORMS_FAILED:
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
