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
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src import db as dbmod
from src import image_manager
from src.content_quality_guard import (
    check_quality,
    combine_visible_text,
    format_issues,
    has_blocking_issues,
    should_request_rewrite,
)
from src.local_notify import notify_quality_block
from src.publisher import (
    publish_to_fb, publish_to_ig, publish_to_threads,
    publish_ig_carousel, publish_threads_carousel, publish_fb_carousel,
)
from src.cover_pipeline import prepare_publish_image
from src.cover_uploader import upload_cards
from src.schema import PublishResult, CarouselCards
from src.token_utils import refresh_threads_token
# NOTE: substack_radar.cards is a PIL RENDERER (no LLM/brain), so importing it
# here does NOT breach the "no composer/scorer/reflector" cloud firewall.
from substack_radar.cards import build_cards, render_cards

# ---------- Cadence 參數（與 config.yaml 的 min/max_interval_minutes 對齊）----------
MIN_INTERVAL_SECONDS = 60 * 60           # 1h upper bound（頻率上限 = 至少間隔 60 min）
MAX_INTERVAL_SECONDS = 2 * 60 * 60       # 2h lower bound（頻率下限 = 至多間隔 120 min）


# ---------- _publish_one outcome codes（Phase 8.20 追加）----------
# exit code 映射寫在 main()。Partial / all-failed 都讓 workflow red，避免把
# 平台缺口誤報成功；queue 空、guard 主動攔與已治理的資料缺損仍 exit 0。
OUTCOME_PUBLISHED = "published"               # 所有實際 platform_drafts 皆有 success evidence → exit 0
OUTCOME_QUALITY_BLOCKED = "quality_blocked"   # guard 主動拒發 → exit 0（guard 正常工作）
OUTCOME_EDITOR_KILLED = "editor_killed"       # 總編輯閘殺/退稿 → exit 0（編輯正常工作，非系統壞）
OUTCOME_NO_PLATFORM_DRAFTS = "no_platform_drafts"  # 資料異常、已 mark_failed → exit 0
OUTCOME_PARTIAL_FAILURE = "partial_failure"   # 部分平台成功、部分失敗 → 保留 queued + exit 1
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


def _seconds_since_last_publish(
    conn,
    platforms: set[str] | None = None,
) -> Optional[float]:
    iso = dbmod.last_successful_publish_at(conn, platforms=platforms)
    dt = _parse_iso(iso)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _decide_cadence(
    conn,
    force: bool,
    platforms: set[str] | None = None,
) -> tuple[bool, str, bool]:
    """回傳 (should_publish, reason, allow_fallback)
    - allow_fallback=True 表示走 2h lower bound 路徑（可挑 stale）。
    """
    if force:
        return True, "force=True，略過 cadence 檢查", True

    elapsed = _seconds_since_last_publish(conn, platforms=platforms)
    if elapsed is None:
        return True, "無歷史發文紀錄，允許首發", False

    mins = elapsed / 60.0
    if elapsed < MIN_INTERVAL_SECONDS:
        return False, f"距上次成功發文 {mins:.1f} min (<60)，honour 1h upper bound → 跳過", False
    if elapsed < MAX_INTERVAL_SECONDS:
        return True, f"距上次成功發文 {mins:.1f} min (60–120)，freshness-first 正常發", False
    return True, f"距上次成功發文 {mins:.1f} min (≥120)，2h lower bound → 允許 fallback 挑 stale", True


# ---------- 選稿 ----------

def _pick_draft(
    conn,
    allow_fallback: bool,
    platforms: set[str] | None = None,
    *,
    recovery_only: bool = False,
):
    """回傳 (row, mode) 或 (None, "empty")。mode ∈ {"fresh", "fallback"}."""
    # EDITORIAL_MODE 時段路由：晚優先發政治桶、早午優先發市場桶（soft——桶空自動退回最新）。
    from src.slot_routing import slot_routing_enabled, current_slot, bucket_categories
    prefer = bucket_categories(current_slot()) if slot_routing_enabled() else None
    row = dbmod.pick_freshest_queued(
        conn,
        prefer_categories=(prefer or None),
        platforms=platforms,
        recovery_only=recovery_only,
    )
    if row is not None:
        return row, "fresh"
    if allow_fallback:
        row = dbmod.pick_fallback_any_approved(
            conn, platforms=platforms, recovery_only=recovery_only
        )
        if row is not None:
            return row, "fallback"
    return None, "empty"


# ---------- 發布 ----------

async def _publish_one(
    conn,
    row,
    dry_run: bool = False,
    platforms: set[str] | None = None,
) -> str:
    """Publish the selected draft to the explicitly requested platforms.

    回傳 OUTCOME_* 字串。main() 負責把它映射成 exit code。
    設計原則：_publish_one 只講「發生什麼」、不講「workflow 該紅還是綠」。
    """
    draft_id = row["id"]
    print(f"\n[PublishQueue] 選中 draft: {draft_id[:16]}…")
    print(f"   ↳ 新聞: {(row['news_title'] or '')[:60]}")
    print(f"   ↳ 新聞發佈: {row['news_published_at']}")

    platform_drafts = dbmod.get_platform_drafts(conn, draft_id)
    if platforms is not None:
        platform_drafts = [
            platform_draft
            for platform_draft in platform_drafts
            if platform_draft["platform"] in platforms
        ]
    if not platform_drafts:
        print(f"   ⚠️ 找不到本次 requested platform_drafts → 無法發布")
        if not dry_run and platforms is None:
            dbmod.mark_queue_failed(conn, draft_id, reason="no_platform_drafts")
        return OUTCOME_NO_PLATFORM_DRAFTS

    # Decode the persisted cards before the quality gate.  Card text is visible
    # content even though Meta receives it as rendered pixels, so caption-only
    # validation is not sufficient.
    carousel = None
    cjson = row["carousel_json"] if "carousel_json" in row.keys() else None
    if cjson:
        try:
            carousel = CarouselCards.model_validate_json(cjson)
        except Exception as exc:  # noqa: BLE001
            print(f"   ⚠️ carousel_json 解析失敗 → 純單圖：{exc}")

    # ---------- Phase 8.20：品質守門員（攔下 Phase 8.19 前的 emergency_template）----------
    # 檢查每個平台的 final/full_text；只要有任一個 platform_draft 觸發 block 級規則，
    # 整筆 draft 不發，標 failed，跳 Mac 通知。Hsin 要求不刪 DB、他手動處理。
    guard_news_title = row["news_title"] or ""
    recovery_strict = os.getenv("AUTOMATION_MODE", "").strip().lower() == "recovery"
    block_reasons: list[str] = []
    for pd_row in platform_drafts:
        text_to_check = pd_row["final_text"] or pd_row["full_text"] or ""
        visible_text = combine_visible_text(text_to_check, carousel)
        issues = check_quality(
            visible_text,
            title=guard_news_title,
            recovery=recovery_strict,
        )
        if not dry_run:
            dbmod.record_quality_evaluation(
                conn,
                draft_id=draft_id,
                news_id=row["news_id"],
                platform=pd_row["platform"],
                stage="pre_publish",
                attempt=1,
                full_text=visible_text,
                issues=issues,
            )
        if has_blocking_issues(issues) or (
            recovery_strict and should_request_rewrite(issues)
        ):
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

    # ---------- Phase 4：總編輯閘（EDITORIAL_MODE 才跑；fail-open 絕不擋發文）----------
    # 在「機械式品質守門員」之後，加一層「總編輯」：跑編審五關殺填充物。殺/退＝不發、
    # 標 failed、通知信哥手動處理（沿用 quality guard 的 notify 流程＝MVP 的人在環）。
    from src.slot_routing import editorial_mode, current_slot, editor_enforce
    if editorial_mode():
        try:
            from src.editor_desk import editor_review
            _topic = row["topic_category"] if "topic_category" in row.keys() else None
            try:
                _dw = dbmod.get_topic_weight(conn, _topic) if _topic else None
            except Exception:
                _dw = None
            _texts = [(pd["final_text"] or pd["full_text"] or "") for pd in platform_drafts]
            _review_body = max(_texts, key=len) if _texts else ""
            verdict = await editor_review(
                title=guard_news_title, body=_review_body,
                topic_category=_topic, demand_weight=_dw, slot=current_slot(),
            )
            one_line = f"[{verdict.verdict}] {verdict.reason} | ④{verdict.readable}"
            if verdict.verdict in ("殺", "退"):
                if editor_enforce():
                    print(f"   🛑 [總編輯閘] 真殺 {one_line}")
                    if not dry_run:
                        dbmod.mark_queue_failed(conn, draft_id, reason=f"editor_{verdict.verdict}: {one_line[:180]}")
                        notify_quality_block(draft_id=draft_id, reasons_one_line=("總編" + one_line)[:200])
                    return OUTCOME_EDITOR_KILLED
                # shadow mode（預設）：只記 log、照常發，讓信哥先觀察副編判斷再決定 enforce。
                print(f"   👻 [總編輯閘·shadow] 本來會「{verdict.verdict}」（未 enforce、照發）：{one_line}")
            else:
                print(f"   ✅ [總編輯閘] 發：{verdict.reason[:60]}")
        except Exception as exc:  # noqa: BLE001
            print(f"   🛑 [總編輯閘] 例外，fail-closed：{exc}")
            if not dry_run:
                dbmod.mark_queue_failed(
                    conn, draft_id, reason=f"editor_error: {str(exc)[:180]}"
                )
            return OUTCOME_EDITOR_KILLED

    image_url = row["og_image_url"]
    # og_video_url 暫不主動使用（Phase 8.18 範圍內；影片 path 已在 Phase 8.16 實作，
    # 需 composer 決定是否走影片發文，此處維持純文字 + 圖片路徑）

    # Phase 9.5: topic_category drives the cover-image topic chip color.
    # Older queue rows may not carry it — fall back to None and the
    # cover_pipeline defaults to "macro" (gray chip).
    topic_category = row["topic_category"] if "topic_category" in row.keys() else None

    # Phase 10 (2026-06-03)：2–4 圖卡 carousel（compose 階段持久化在 draft 層）。
    # 有內容時每平台優先發 carousel，任何一步失敗就 fall through 到單圖。
    # Map DB-side platform names ("facebook"/"instagram"/"threads") to the
    # cover_pipeline platform_key codes ("fb"/"ig"/"threads").
    _COVER_KEY = {"facebook": "fb", "instagram": "ig", "threads": "threads"}

    any_success = False
    for pd_row in platform_drafts:
        platform = pd_row["platform"]          # facebook / instagram / threads
        full_text = pd_row["final_text"] or pd_row["full_text"]
        posted_at = datetime.now(timezone.utc).isoformat()

        if dry_run:
            print(f"   · [dry-run] {platform} ({len(full_text)} 字) → 不呼叫 API")
            continue

        # Idempotency guard (Phase 9.5+, 2026-05-02): if publish_log already
        # has success=1 for this (draft, platform), skip the API call to
        # prevent duplicate posts on retry/restart. Counts toward any_success
        # because the post IS live, just from an earlier run.
        if dbmod.has_successful_publish(conn, draft_id, platform):
            print(f"   ↳ [{platform} Skip] 已成功發過此 (draft, platform)，跳過防重複")
            any_success = True
            continue

        # Phase 2: FB + IG both go through render → upload → URL.
        cover_key = _COVER_KEY.get(platform)
        if cover_key is None:
            print(f"   ⚠️ 未知平台 {platform}，跳過")
            continue

        # Phase 10 carousel-first (2026-06-03)：render 2–4 卡 → upload → 發 carousel；
        # 任何一步失敗都 fall through 到下面的單圖路徑（發文絕不因圖卡失敗中斷）。
        if carousel is not None:
            try:
                cards = build_cards(title=pd_row["title"] or "", subtitle="", carousel=carousel)
                if len(cards) >= 2:
                    cdir = Path(tempfile.mkdtemp(prefix="cards_"))
                    card_paths = render_cards(
                        cards=cards, topic_category=topic_category or "other",
                        aspect=cover_key, output_dir=cdir,  # ig/fb/threads ∈ ASPECTS
                    )
                    slug = re.sub(r"[^A-Za-z0-9_]", "", f"{draft_id}_{cover_key}")[:40]
                    card_urls = upload_cards(card_paths, slug)
                    if len(card_urls) >= 2:
                        if platform == "instagram":
                            cresult = await publish_ig_carousel(card_urls, full_text)
                        elif platform == "threads":
                            cresult = await publish_threads_carousel(card_urls, full_text)
                        else:  # facebook
                            cresult = await publish_fb_carousel(card_urls, full_text)
                        if cresult.get("success"):
                            format_at = datetime.now(timezone.utc).isoformat()
                            dbmod.log_publish(conn, PublishResult(
                                draft_id=draft_id, platform=platform,
                                platform_post_id=cresult.get("id"),
                                posted_at=datetime.now(timezone.utc).isoformat(),
                                success=True, error_message=None,
                            ))
                            dbmod.mark_recovery_actual_format(
                                conn, draft_id, platform, "carousel", format_at
                            )
                            any_success = True
                            print(f"   ✅ [{platform}] carousel 成功 id={cresult.get('id')}（{len(card_urls)} 卡）")
                            continue
                        print(f"   ⚠️ [{platform}] carousel 失敗 → 降級單圖：{str(cresult.get('error'))[:160]}")
            except Exception as exc:  # noqa: BLE001
                print(f"   ⚠️ [{platform}] carousel 流程例外 → 降級單圖：{exc}")

        prep = await prepare_publish_image(
            platform_key=cover_key,
            original_image_url=image_url,
            draft_id=draft_id,
            title=pd_row["title"] or "",
            topic_category=topic_category,
        )
        publish_image_url = prep["image_url"]
        used_cover_cdn = (publish_image_url is not None
                          and publish_image_url != image_url
                          and cover_key in ("fb", "ig"))

        if platform == "facebook":
            if used_cover_cdn:
                print(f"   ↳ [📘 FB] 用 rendered cover URL: {publish_image_url}")
            elif publish_image_url:
                print(f"   ↳ [📘 FB] cover 不可用，用原圖網址")
            else:
                print(f"   ↳ [📘 FB] 無可用圖片 → 純文字發布")
            result = await publish_to_fb(full_text, image_url=publish_image_url)
            # Fallback: if FB can't fetch the image (external CDN), retry text-only
            error_msg = str(result.get("error", ""))
            needs_text = any(phrase in error_msg.lower() for phrase in
                ["object with id 'none'", "failed to download", "1353045", "unsupported post"])
            if not result.get("success") and needs_text and publish_image_url:
                print(f"   ⚠️ [📘 FB] 圖片上傳失敗 → 降級為純文字發布...")
                result = await publish_to_fb(full_text)
        elif platform == "instagram":
            if not publish_image_url:
                result = {"success": False, "error": {"local_reject": "IG 需要 image_url"}}
            else:
                if used_cover_cdn:
                    print(f"   ↳ [📸 IG] 用 rendered cover URL: {publish_image_url}")
                result = await publish_to_ig(full_text, publish_image_url)
        elif platform == "threads":
            if not publish_image_url:
                result = {"success": False, "error": {"local_reject": "Threads 需要 image_url"}}
            else:
                result = await publish_to_threads(full_text, publish_image_url)
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
            dbmod.mark_recovery_actual_format(
                conn,
                draft_id,
                platform,
                "feed",
                datetime.now(timezone.utc).isoformat(),
            )
            any_success = True
            print(f"   ✅ [{platform}] 成功 id={result.get('id')}")
        else:
            print(f"   ❌ [{platform}] 失敗: {str(result.get('error'))[:200]}")

    if dry_run:
        return OUTCOME_PUBLISHED  # dry-run 不改 DB、也不記失敗——視為正常走完

    pending = dbmod.pending_publish_platforms(conn, draft_id)
    if not pending:
        dbmod.mark_queue_published(conn, draft_id)
        return OUTCOME_PUBLISHED

    if any_success:
        print(
            "   ⚠️ [Partial] 已有平台成功，但仍缺 "
            f"{','.join(sorted(pending))}；draft 保持 queued 等待平台別重試"
        )
        return OUTCOME_PARTIAL_FAILURE

    # 本次 requested platforms 全敗：保持 queued，下一個同平台 cycle 可自動重試。
    # Workflow red 讓 owner 看得到平台/API 問題，但不犧牲其他平台待發狀態。
    print(
        "   ❌ [RetryQueued] 本次 requested platforms 全敗；"
        f"仍缺 {','.join(sorted(pending))}，保留 queued"
    )
    return OUTCOME_ALL_PLATFORMS_FAILED


# ---------- Main ----------

async def main() -> int:
    parser = argparse.ArgumentParser(description="News Radar Cloud Publisher (Phase 8.18)")
    parser.add_argument("--force", action="store_true", help="忽略 1h upper bound，強制挑一筆發（也允許 fallback）")
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Never revive stale drafts when the fresh queue is empty",
    )
    parser.add_argument(
        "--platforms",
        default="facebook,instagram,threads",
        help="Comma-separated canonical targets: facebook,instagram,threads",
    )
    parser.add_argument("--dry-run", action="store_true", help="只印要發的內容，不呼叫 API、不改 DB")
    args = parser.parse_args()
    platforms = {value.strip() for value in args.platforms.split(",") if value.strip()}
    allowed_platforms = {"facebook", "instagram", "threads"}
    if not platforms or not platforms <= allowed_platforms:
        parser.error(
            "--platforms must contain only facebook,instagram,threads"
        )

    dbmod.init_db()
    refresh_threads_token()

    conn = dbmod.get_conn()
    try:
        # 1. Cadence 決策
        should_publish, reason, allow_fallback = _decide_cadence(
            conn,
            force=args.force,
            platforms=platforms,
        )
        if args.no_fallback:
            allow_fallback = False
            reason += "；no-fallback=true，只允許 fresh queued draft"
        print(f"[Cadence] {reason}")
        if not should_publish:
            print("[PublishQueue] 本 cycle 跳過。")
            # 給一個健康度快照方便 log
            print(f"[Queue] 狀態分佈: {dbmod.count_queue_status(conn)}")
            return 0

        # 2. 選稿
        recovery_only = os.getenv("AUTOMATION_MODE", "").strip().lower() == "recovery"
        row, mode = _pick_draft(
            conn,
            allow_fallback=allow_fallback,
            platforms=platforms,
            recovery_only=recovery_only,
        )
        if row is None:
            print(f"[PublishQueue] queue 空（mode={mode}），無稿可發。")
            print(f"[Queue] 狀態分佈: {dbmod.count_queue_status(conn)}")
            return 0
        print(f"[PublishQueue] 選稿 mode={mode}")

        # 3. 發布
        outcome = await _publish_one(
            conn, row, dry_run=args.dry_run, platforms=platforms
        )
        print(f"[PublishQueue] 完成，outcome={outcome}")
        print(f"[Queue] 最終狀態分佈: {dbmod.count_queue_status(conn)}")

        # Outcome → exit code 映射（Phase 8.20 exit semantics）
        #   PUBLISHED / QUALITY_BLOCKED / NO_PLATFORM_DRAFTS → 0（pipeline 正常運作）
        #   ALL_PLATFORMS_FAILED                              → 1（Meta API 真的出事，workflow red）
        # dry-run 永遠 0。
        if args.dry_run:
            return 0
        if outcome in {OUTCOME_PARTIAL_FAILURE, OUTCOME_ALL_PLATFORMS_FAILED}:
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
