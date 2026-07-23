"""
News Radar · Pipeline 主程式（Milestone 3.1 · Multi-Platform Native）
流程：Harvest → Score → 一次 LLM 產三版 (FB / IG / Threads) → finalize 壓字數 →
     upsert platform_drafts → 各平台獨立發布 → 寫 3-section 預覽 .md。

關鍵改動（相對 Milestone 2 / 3）：
- 不再用單一 full_text 發三平台，改用 MultiPlatformDraft，每平台各一篇。
- publisher 的 text[:500] 暴力截斷已移除；此處由 composer.finalize_variant 壓字數。
- drafts 表保留 FB 變體作為 canonical full_text（給舊的 Reflector 路徑用）；
  真正的每平台內容落在 platform_drafts。
- save_md_draft 變成三區塊預覽（🧵 Threads / 📘 FB / 📸 IG），人工可分平台審閱與編輯。
"""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple, Optional

from src import db as dbmod
from src import image_manager
from src.schema import (
    Draft,
    DraftContent,
    PlatformVariant,
    PublishResult,
    ScoreBreakdown,
)
from src.scorer import score_news
from src.composer import compose_multi_platform, finalize_variant
from src.content_quality_guard import (
    check_quality,
    format_issues,
    has_blocking_issues,
    should_request_rewrite,
)
from src.topic_classifier import classify_topic, compute_weighted_score
from src.publisher import (
    publish_to_fb, publish_to_threads, publish_to_ig,
    publish_ig_carousel, publish_threads_carousel, publish_fb_carousel,
)
from src.cover_pipeline import prepare_publish_image
from src.token_utils import refresh_threads_token
from src.analyst import run_analysis_cycle
from src.reflector import run_reflection


# ---------- 策略參數 (Phase 8.20 · 量取勝 2 週實驗，2026-04-23) ----------
# 原設定：0.9 / 0.8（嚴選），導致 2026-04-20 之後 queue 長期空置（見 §7.1）。
# 2026-04-23 直接降到 0.7 / 0.65（近乎全開）——composer 寫出來的幾乎都發，
# 只受 MIN_PUBLISH_INTERVAL（1hr）與 MAX_PUBLISH_PER_SLOT（=1）節流。
# 目的：2 週後台資料收集期，資料量優先；兩週後回頭看 analyst 互動數據再決定是否回升。
AUTO_PUBLISH_THRESHOLD = 0.7    # Hunter 精準門檻：距上次發文 1–2hr 時用
RESCUE_PUBLISH_THRESHOLD = 0.65 # Rescue 模式放寬門檻（距上次發文 ≥ 2hr 時用）
MIN_SCORE_THRESHOLD = 0.65      # 低於此分數直接捨棄，不佔用 token
                                # ⚠️ RESCUE == MIN：rescue 時段等於「composer 產出全發」
MAX_POSTS_PER_SLOT = 8          # 每 cycle 最多掃描 N 篇候選（直到獵殺 1 篇為止）
MAX_PUBLISH_PER_SLOT = 1        # 每 cycle 最多自動發布 N 篇，避免洗版

# Harvest 節流：兩次 RSS 抓取最少相隔秒數（1.5 小時）
HARVEST_THROTTLE_SECONDS = 90 * 60
HARVEST_STATE_FILE = Path(__file__).resolve().parent / "state" / "last_harvest.txt"

# ---------- 發文間隔規則 (Milestone 6.3 · Cadence) ----------
# 業主原話：「最少每兩小時、最多每一小時三大平台要輸出一篇」
# 翻譯為 pipeline 邏輯：兩次成功發文之間 → 下限 1hr（避免洗版）、上限 2hr（避免空窗）
MIN_PUBLISH_INTERVAL_SECONDS = 60 * 60        # 1 hr，兩篇之間至少間隔這麼久
SOFT_MAX_PUBLISH_INTERVAL_SECONDS = 2 * 60 * 60  # 2 hr 上限：超過就切 Rescue 門檻
HEARTBEAT_SECONDS = 30 * 60                   # 30 分鐘心跳，讓 cadence 解析度足夠細

# 對應 config/platforms/*.md 的版本；若改動 appendix 請同步 bump
APPENDIX_VERSION = "1.0"
CANONICAL_TO_COMPOSER_PLATFORM = {
    "facebook": "fb",
    "instagram": "ig",
    "threads": "threads",
}
PLATFORM_ALIASES = {
    "facebook": "fb",
    "fb": "fb",
    "instagram": "ig",
    "ig": "ig",
    "threads": "threads",
}


# ---------- Draft 組裝輔助 ----------

def _build_legacy_content(fb_variant: PlatformVariant, image_url: Optional[str]) -> DraftContent:
    """把 FB 變體塞回舊版 DraftContent schema，讓 drafts 表維持相容。
    欄位規則：
      - title / hashtags / image_url 取 FB 變體
      - hook ~ macro_insight 依 body 的段落順序填；不足則補佔位字串
      - ending_question 用 hashtags 串起來（沒東西就佔位）
    """
    body = (fb_variant.body or "").strip()
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]

    def g(i: int) -> str:
        return paragraphs[i] if i < len(paragraphs) else "(見 platform_drafts)"

    hashtags_joined = " ".join(fb_variant.hashtags or []).strip() or "(見 platform_drafts)"

    return DraftContent(
        title=fb_variant.title.strip() or "(untitled)",
        hook=g(0),
        framework=g(1),
        validation=g(2),
        macro_insight=g(3),
        ending_question=hashtags_joined,
        hashtags=list(fb_variant.hashtags or []),
        image_url=image_url,
    )


def save_archive_md(
    draft: Draft,
    finalized: Dict[str, Tuple[PlatformVariant, str, bool]],
    image_url: Optional[str],
    news_url: str,
    publish_results: Dict[str, bool],
) -> Optional[Path]:
    """Milestone 6.2 · 本地檔案室：把已發布作品同步落地到 archive/YYYY/MM/DD/slug.md。
    publish_results: {platform_key: success_bool}，用於標注每平台的實際結果。
    回傳寫入的檔案路徑（若成功），否則 None。
    """
    try:
        now = datetime.now()
        archive_root = Path(__file__).resolve().parent / "data" / "04_publish" / "archive"
        day_dir = archive_root / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
        day_dir.mkdir(parents=True, exist_ok=True)

        import re
        safe_title = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', draft.content.title or "post")
        slug = safe_title[:60].strip().replace(" ", "_") or "post"
        filename = f"{now.strftime('%H%M%S')}_{slug}.md"
        file_path = day_dir / filename

        blocks = []
        for key in ("fb", "ig", "threads"):
            if key not in finalized:
                blocks.append(f"### [{key.upper()}] — SKIP（媒介門檻阻斷）")
                continue
            variant, full_text, ok = finalized[key]
            label = dbmod.PLATFORM_LABEL[key]
            publish_ok = publish_results.get(key)
            if publish_ok is True:
                status_tag = "✅ 已發布"
            elif publish_ok is False:
                status_tag = "❌ 發布失敗"
            else:
                status_tag = "— 未發布"
            blocks.append(
                f"### {label} ({variant.char_count} 字) · {status_tag}\n\n{full_text}"
            )

        body = "\n\n---\n\n".join(blocks)

        md = f"""# [Archive · Score {draft.confidence_score:.2f}] {draft.content.title}

- 發布時間：{now.isoformat(timespec='seconds')}
- 原始新聞：[連結]({news_url})
- 配圖：{image_url or '（無）'}

---

{body}

---
**AI 理據**：{draft.score_breakdown.model_dump_json()}
"""
        file_path.write_text(md, encoding="utf-8")
        print(f" ↳ [Archive] 本地檔案室已存檔：data/04_publish/archive/{now.strftime('%Y/%m/%d')}/{filename}")
        return file_path
    except Exception as e:
        print(f" ↳ [Archive][Error] 存檔失敗（不影響發布流程）：{e}")
        return None


def _read_last_harvest_ts() -> Optional[datetime]:
    try:
        if not HARVEST_STATE_FILE.exists():
            return None
        raw = HARVEST_STATE_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _write_last_harvest_ts(ts: datetime) -> None:
    try:
        HARVEST_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        HARVEST_STATE_FILE.write_text(ts.isoformat(), encoding="utf-8")
    except Exception as e:
        print(f" ↳ [Harvest][WARN] 無法寫入節流時間戳：{e}")


def _seconds_since_last_successful_publish(conn) -> Optional[float]:
    """從 publish_log 查最近一次 success=1 的 posted_at，回傳距今秒數。
    若從未發過任何成功貼文，回傳 None。
    """
    row = conn.execute(
        "SELECT posted_at FROM publish_log WHERE success = 1 ORDER BY posted_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    try:
        # SQLite 存的是 ISO 字串
        last = datetime.fromisoformat(str(row["posted_at"]))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds()
    except Exception:
        return None


def decide_cadence(conn, override_threshold: Optional[float] = None) -> Tuple[bool, float, str]:
    """依「1hr ≤ 間隔 ≤ 2hr」規則決定本 cycle 的行動。
    回傳：(should_publish, threshold_to_use, reason_text)

    分支：
      - 無歷史 → should_publish=True, threshold=AUTO_PUBLISH_THRESHOLD
      - 距上次 < 1hr → should_publish=False（本 cycle 跳過發布）
      - 1hr ≤ 距上次 < 2hr → should_publish=True, threshold=AUTO_PUBLISH_THRESHOLD（嚴格 0.9）
      - 距上次 ≥ 2hr → should_publish=True, threshold=RESCUE_PUBLISH_THRESHOLD（放寬 0.8，避免空窗）
      - override_threshold 覆寫（給 --publish-now 用）
    """
    if override_threshold is not None:
        return True, override_threshold, f"override 強制門檻 {override_threshold}"

    elapsed = _seconds_since_last_successful_publish(conn)
    if elapsed is None:
        return True, AUTO_PUBLISH_THRESHOLD, "無歷史發文紀錄，正常啟動"

    elapsed_min = elapsed / 60.0
    if elapsed < MIN_PUBLISH_INTERVAL_SECONDS:
        return (
            False,
            AUTO_PUBLISH_THRESHOLD,
            f"距上次發文僅 {elapsed_min:.1f} 分鐘 (<60min)，本 cycle 跳過發布、繼續累積素材",
        )
    if elapsed < SOFT_MAX_PUBLISH_INTERVAL_SECONDS:
        return (
            True,
            AUTO_PUBLISH_THRESHOLD,
            f"距上次發文 {elapsed_min:.1f} 分鐘 (60–120min)，採嚴格門檻 {AUTO_PUBLISH_THRESHOLD}",
        )
    return (
        True,
        RESCUE_PUBLISH_THRESHOLD,
        f"距上次發文 {elapsed_min:.1f} 分鐘 (≥120min) → Rescue Mode，放寬門檻 {RESCUE_PUBLISH_THRESHOLD}",
    )


async def maybe_run_harvest(force: bool = False) -> bool:
    """若距離上次 harvest ≥ HARVEST_THROTTLE_SECONDS，執行一次 run_harvest。
    回傳 True 表示這次 cycle 有執行 harvest。
    """
    now = datetime.now(timezone.utc)
    last = _read_last_harvest_ts()
    if (not force) and last is not None:
        elapsed = (now - last).total_seconds()
        if elapsed < HARVEST_THROTTLE_SECONDS:
            remaining = int(HARVEST_THROTTLE_SECONDS - elapsed)
            print(
                f"[Harvest] 節流中：距上次 {int(elapsed)}s，"
                f"再等 {remaining}s 才會重新抓 RSS"
            )
            return False

    print("[Harvest] 觸發一次 RSS 抓取 + 清洗入庫...")
    try:
        from run_harvest import run_harvest_once
        await run_harvest_once()
        _write_last_harvest_ts(now)
        # 2026-07-05：每次 harvest 後把 >14 天舊素材正文清掉，讓 DB 體積永遠 bound 在
        # 100MB 以下——否則 DB 撐爆 GitHub 檔案上限、state branch 整條 push 掛掉，
        # 投稿與排程一起死（見 db.prune_old_source_text）。fail-safe，不擋 harvest。
        try:
            from src import db as _dbmod
            _c = _dbmod.get_conn()
            _dbmod.prune_old_source_text(_c, keep_days=14)
            _c.close()
        except Exception as _e:
            print(f"[Harvest][prune][warn] 舊素材清理略過：{_e}")
        return True
    except Exception as e:
        print(f"[Harvest][Error] 抓取失敗：{e}")
        return False


def save_md_draft(
    draft: Draft,
    finalized: Dict[str, Tuple[PlatformVariant, str, bool]],
    image_url: Optional[str],
    news_url: str,
) -> None:
    """把三平台變體寫成同一份 .md 視覺化預覽（一平台一區塊）。"""
    drafts_dir = Path(__file__).resolve().parent / "data" / "03_compose" / "pending_drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d")
    import re
    # 移除非法檔名字元，保留 CJK / 字母 / 數字 / 底線
    safe_title = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', draft.content.title or "draft")
    slug = safe_title[:40].strip().replace(" ", "_") # 增加長度至 40，讓標題更完整
    filename = f"{date_str}_{slug}.md"
    file_path = drafts_dir / filename

    # 順序：Threads（最短，最容易先看）→ FB → IG
    blocks = []
    for key in ("fb", "ig", "threads"):
        if key not in finalized:
            blocks.append(f"### [{key.upper()}] - SKIP (媒介門檻阻斷)")
            continue
        variant, full_text, ok = finalized[key]
        label = dbmod.PLATFORM_LABEL[key]
        status_tag = "" if ok else " ⚠️ 字數超限 / 拒發"
        blocks.append(
            f"### {label} 預覽 ({variant.char_count} 字){status_tag}\n\n{full_text}"
        )
    preview_body = "\n\n---\n\n".join(blocks)

    md_content = f"""# [AI Score: {draft.confidence_score:.2f}] {draft.content.title}

![Image]({image_url or ''})

---

{preview_body}

---
**原始新聞來源**: [連結]({news_url})
**生成時間**: {draft.generated_at}
**AI 理據**: {draft.score_breakdown.model_dump_json()}
"""
    file_path.write_text(md_content, encoding="utf-8")
    print(f" ↳ [Saved] 三平台預覽已存至: data/03_compose/pending_drafts/{filename}")


# ---------- 發布輔助 ----------

async def _publish_platform(
    conn,
    draft_id: str,
    platform_key: str,
    variant: PlatformVariant,
    full_text: str,
    ok: bool,
    image_url: Optional[str],
    topic_category: Optional[str] = None,
    carousel=None,
) -> bool:
    """單平台發布 + publish_log。ok=False 或缺圖 → 不呼叫 API，直接記失敗。
    回傳：True 表示 API 回報成功；False 表示任何原因失敗。

    Phase 9.5: image goes through ``cover_pipeline.prepare_publish_image``
    first — FB gets a rendered branded cover via bytes upload, Threads /
    IG keep their pre-cover behaviour (Threads no cover by design; IG
    cover-URL hosting is Phase 2).
    """
    db_name = dbmod.PLATFORM_DB_NAME[platform_key]
    posted_at = datetime.now(timezone.utc).isoformat()

    if not ok:
        msg = "finalize_variant ok=False（壓字數後仍超限）"
        print(f"   ↳ [{dbmod.PLATFORM_LABEL[platform_key]} Skip] {msg}")
        dbmod.log_publish(conn, PublishResult(
            draft_id=draft_id, platform=db_name, platform_post_id=None,
            posted_at=posted_at, success=False, error_message=msg,
        ))
        return False

    # Idempotency guard (Phase 9.5+, 2026-05-02): if this (draft, platform)
    # already has a success=1 row in publish_log, skip the API call to
    # prevent duplicate posts on retry/restart. Treat as success — the
    # post IS live, just from an earlier run.
    if dbmod.has_successful_publish(conn, draft_id, db_name):
        print(
            f"   ↳ [{dbmod.PLATFORM_LABEL[platform_key]} Skip] "
            "已成功發過此 (draft, platform)，跳過防重複"
        )
        return True

    # --- Phase 10 (2026-06-02): 2–4 圖卡 carousel（有蒸餾卡片內容時優先）。
    # 任何一步失敗都 fall through 到下面的單圖流程，發文絕不因圖卡失敗而中斷。 ---
    # Phase 10 carousel: try structured carousel first, then auto-generate from text.
    _carousel_to_use = carousel
    if _carousel_to_use is None and variant.body:
        # Auto-generate 4 cards from the platform text (split by paragraphs)
        from src.schema import CarouselCards
        paras = [p.strip() for p in variant.body.split("\n") if p.strip() and len(p.strip()) > 10][:4]
        if len(paras) >= 2:
            _carousel_to_use = CarouselCards(
                insight_statement=paras[0] if len(paras) > 0 else None,
                insight_support=paras[1] if len(paras) > 1 else None,
                stat_number=None,
                stat_caption=None,
                takeaways=paras[2:4],
            )
            print(f"   ↳ [Phase 10] 自動生成 {len(_carousel_to_use.takeaways)} 張圖卡（從 body 內容）")

    if _carousel_to_use is not None:
        try:
            import re as _re
            import tempfile as _tmp
            from substack_radar.cards import build_cards, render_cards
            from src.cover_uploader import upload_cards

            cards = build_cards(title=variant.title or "", subtitle="", carousel=_carousel_to_use)
            if len(cards) >= 2:
                cdir = Path(_tmp.mkdtemp(prefix="cards_"))
                card_paths = render_cards(
                    cards=cards, topic_category=topic_category or "other",
                    aspect=platform_key, output_dir=cdir,  # ig/fb/threads ∈ ASPECTS
                )
                slug = _re.sub(r"[^A-Za-z0-9_]", "", f"{draft_id}_{platform_key}")[:40]
                card_urls = upload_cards(card_paths, slug)
                if len(card_urls) >= 2:
                    label = dbmod.PLATFORM_LABEL[platform_key]
                    print(f"   ↳ [{label}] 發布 {len(card_urls)} 張圖卡 carousel")
                    if platform_key == "ig":
                        result = await publish_ig_carousel(card_urls, full_text)
                    elif platform_key == "threads":
                        result = await publish_threads_carousel(card_urls, full_text)
                    else:  # fb
                        result = await publish_fb_carousel(card_urls, full_text)
                    if result.get("success"):
                        dbmod.log_publish(conn, PublishResult(
                            draft_id=draft_id, platform=db_name,
                            platform_post_id=result.get("id"),
                            posted_at=datetime.now(timezone.utc).isoformat(),
                            success=True, error_message=None,
                        ))
                        print(f"   ✅ [{label}] carousel 成功 id={result.get('id')}")
                        return True
                    print(f"   ⚠️ [{label}] carousel 失敗 → 降級單圖：{str(result.get('error'))[:160]}")
        except Exception as exc:
            print(f"   ⚠️ carousel 流程例外 → 降級單圖：{exc}")

    # Phase 2: FB and IG both go through render → upload → URL.
    # prep["image_url"] is the cover-cdn raw URL (when render+upload
    # succeed) or the original news image URL (any failure step).
    # local_file_path is preserved in the shape but always None now.
    prep = await prepare_publish_image(
        platform_key=platform_key,
        original_image_url=image_url,
        draft_id=draft_id,
        title=variant.title or "",
        topic_category=topic_category,
    )
    publish_image_url = prep["image_url"]
    used_cover_cdn = (publish_image_url is not None
                      and publish_image_url != image_url
                      and platform_key in ("fb", "ig"))

    # 呼叫對應 API
    result = {"success": False}

    if platform_key == "fb":
        if used_cover_cdn:
            print(f"   ↳ [📘 FB] 用 rendered cover URL: {publish_image_url}")
        elif publish_image_url:
            print(f"   ↳ [📘 FB] cover 不可用，用原圖網址")
        else:
            print(f"   ↳ [📘 FB] 無可用圖片 → 純文字發布")
        result = await publish_to_fb(full_text, image_url=publish_image_url)
        # Fallback: if FB can't fetch the image (common for external CDN URLs
        # like Reddit, Motley Fool, etc.), retry as text-only.
        error_msg = str(result.get("error", ""))
        needs_text_fallback = any(phrase in error_msg.lower() for phrase in
            ["object with id 'none'", "failed to download", "1353045", "unsupported post"])
        if not result.get("success") and needs_text_fallback and publish_image_url:
            print(f"   ⚠️ [📘 FB] 圖片上傳失敗 → 降級為純文字發布...")
            result = await publish_to_fb(full_text)
            if not result.get("success"):
                print(f"   ❌ [📘 FB] 下載 fallback 也失敗")

    elif platform_key == "threads":
        if not publish_image_url:
            msg = "Threads 需要 image_url 才能發布"
            print(f"   ↳ [🧵 Threads Skip] {msg}")
            dbmod.log_publish(conn, PublishResult(
                draft_id=draft_id, platform=db_name, platform_post_id=None,
                posted_at=posted_at, success=False, error_message=msg,
            ))
            return False
        result = await publish_to_threads(full_text, publish_image_url)
    elif platform_key == "ig":
        if not publish_image_url:
            msg = "IG 需要 image_url 才能發布"
            print(f"   ↳ [📸 IG Skip] {msg}")
            dbmod.log_publish(conn, PublishResult(
                draft_id=draft_id, platform=db_name, platform_post_id=None,
                posted_at=posted_at, success=False, error_message=msg,
            ))
            return False
        if used_cover_cdn:
            print(f"   ↳ [📸 IG] 用 rendered cover URL: {publish_image_url}")
        else:
            print(f"   ↳ [📸 IG] cover 不可用，用原圖網址")
        result = await publish_to_ig(full_text, publish_image_url)
    else:
        raise ValueError(f"未知平台 key: {platform_key}")

    dbmod.log_publish(conn, PublishResult(
        draft_id=draft_id,
        platform=db_name,
        platform_post_id=result.get("id"),
        posted_at=datetime.now(timezone.utc).isoformat(),
        success=bool(result.get("success")),
        error_message=str(result.get("error", "")) if not result.get("success") else None,
    ))
    return bool(result.get("success"))


# ---------- 單篇新聞處理 ----------

async def process_item(
    conn,
    row,
    publish_threshold: Optional[float] = None,
    compose_only: bool = False,
    requested_platforms: Optional[set[str]] = None,
) -> str:
    """單篇新聞處理。回傳狀態字串：
    - "published"     ：所有實際目標平台皆有 success evidence
    - "partial"       ：部分平台成功，其餘保留 queued 等待重試
    - "publish_failed"：本次所有平台失敗，仍保留 queued 等待重試
    - "drafted"       ：未達 publish_threshold，僅落草稿
    - "dropped"       ：低於 MIN_SCORE_THRESHOLD 或無可發平台
    - "queued"        ：compose_only 模式下達門檻、入佇列等待 Cloud publisher
    - "skipped_no_llm"：（Phase 8.19）Gemini + Claude CLI 兩條路都失敗，主動 skip
    - "skipped_platform_scope"：素材指定平台與本次 scheduler scope 無交集
    - "skipped_target_scope"：Substack-only 素材不可進 Meta pipeline

    publish_threshold: 自動發布門檻，預設用全域 AUTO_PUBLISH_THRESHOLD。
    main loop 會依 cadence 動態傳入（例：Rescue Mode 傳 0.8）。

    compose_only (Phase 8.18): 只跑到 platform_drafts 入庫 + enqueue，不觸發 publisher。
    Mac 端 launchd 每小時呼叫 `run_pipeline.py --compose-only` 使用此路徑，
    Cloud 的 run_publish_queue.py 再從佇列挑最新一筆發文。
    """
    if publish_threshold is None:
        publish_threshold = AUTO_PUBLISH_THRESHOLD
    news_id = row["id"]
    title = row["title"]
    content = row["clean_markdown"] or ""
    og_image = row["og_image_url"]
    news_url = row["url"]
    tags_raw = row["tags"] if "tags" in row.keys() else None
    owner_submitted = (
        ("feed_name" in row.keys() and row["feed_name"] == "user_submission")
        or (tags_raw and "user_submission" in str(tags_raw))
    )

    if (
        ("feed_name" in row.keys() and row["feed_name"] == "user_substack")
        or (tags_raw and "substack_source" in str(tags_raw))
    ):
        print("   ↳ [TargetScope] Substack-only source，Meta pipeline fail-closed skip")
        return "skipped_target_scope"

    print(f"\n[Pipeline] 處理新聞: {title[:40]}...")

    # 1. AI 評分
    score_data = await score_news(title, content)

    if not score_data:
        # Phase 8.19：徹底移除『暴力發布模式』。
        # scorer.py 已內建 Gemini → Claude CLI 雙路徑；若仍回 None，代表
        # (1) 雲端 Gemini quota 用盡 且 (2) 本機 Claude CLI 不可用。
        # 舊邏輯會強塞 confidence_score=1.0 讓每一篇新聞不分品質都 auto-approve，
        # 這跟 composer 的 emergency template 一樣是 quality-crushing footgun。
        # 正確做法是 skip，news_item 保留 `scored` 以外的 status 讓下一輪再試。
        print(
            f" ⚠️  [Pipeline] AI 評分雙路徑皆失敗 → skip 本篇（不評分、不入 queue）。"
            f"下一輪 Gemini quota 恢復後會重試。"
        )
        return "skipped_no_llm"

    score = score_data.confidence_score
    print(f" ↳ AI 評分: {score:.2f} | 主編指令: {score_data.editorial_note[:60]}")

    if score < MIN_SCORE_THRESHOLD and not owner_submitted:
        print(f" ↳ [Dropped] 分數低於門檻 ({MIN_SCORE_THRESHOLD})")
        dbmod.update_status(conn, news_id, "dropped")
        return "dropped"
    if score < MIN_SCORE_THRESHOLD and owner_submitted:
        print(
            f" ↳ [OwnerDirective] relevance score {score:.2f} 低於新聞門檻，"
            "但 owner 投稿保留處理；後續品質閘仍 fail-closed"
        )

    # --- Phase 8.20：主題分類 + 加權分數 ---
    # 這步跑在 media gating 前是刻意的：就算沒圖發不成 IG/Threads，我們依然
    # 想把 topic 寫進 news_items（給 back-prop reflector 將來算訊號覆蓋率用）。
    topic_cls = await classify_topic(title, content)
    topic_weight = dbmod.get_topic_weight(conn, topic_cls.category_id, default=1.0)
    weighted = compute_weighted_score(score, topic_weight)
    print(
        f" ↳ [Topic] {topic_cls.category_id} "
        f"(conf={topic_cls.confidence:.2f}, weight={topic_weight:.2f}) "
        f"→ weighted_score={weighted:.2f}"
    )
    dbmod.set_news_topic(
        conn,
        news_id,
        category_id=topic_cls.category_id,
        confidence=topic_cls.confidence,
        rationale=topic_cls.rationale,
        weighted_score=weighted,
    )
    dbmod.bump_topic_sample_count(conn, topic_cls.category_id)

    # --- Milestone 5.1: 媒介門檻校驗 (Media Gating) ---
    from src.image_manager import check_media_accessibility, find_mirror_image
    
    final_img_url = og_image
    is_accessible = await check_media_accessibility(og_image)
    
    if not is_accessible and og_image:
        print(f" ⚠️  [MediaGatekeeper] 原始圖不可存取，嘗試尋找鏡像...")
        mirror = await find_mirror_image(title)
        if mirror:
            final_img_url = mirror
            is_accessible = True
        else:
            print(f" ❌  [MediaGatekeeper] 找不到有效鏡像圖。")
    
    # 2. 一次 LLM call 產三版（帶入主編指令）
    print(" ↳ [Composer] 接收主編指令，產出三平台變體...")

    # Phase 9.5+ change (2026-05-02): no longer skip IG/Threads when the
    # news source has no usable og:image. cover_pipeline now generates a
    # branded fallback cover (deep-navy + title + brand bar) for any
    # missing-image case, so all three platforms are always publishable.
    # The earlier `is_accessible` gate caused ~30% of drafts to ship
    # FB-only.
    # === 2026-06-03: Parse platform tags from user submissions ===
    # tags_json stores ["platform:fb", "platform:ig", ...] for user submissions
    active_platforms = ["fb", "ig", "threads"]
    if tags_raw:
        try:
            tags_list = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
            plat_tags = [
                PLATFORM_ALIASES.get(str(t).replace("platform:", "").strip())
                for t in tags_list
                if str(t).startswith("platform:")
            ]
            plat_tags = [value for value in plat_tags if value]
            if plat_tags:
                active_platforms = list(dict.fromkeys(plat_tags))
                print(f"   \u21b7 [UserSubmission] \u4f7f\u7528\u8005\u6307\u5b9a\u5e73\u53f0: {active_platforms}")
        except (json.JSONDecodeError, TypeError):
            pass
    if requested_platforms is not None:
        requested_keys = {
            CANONICAL_TO_COMPOSER_PLATFORM[value]
            for value in requested_platforms
        }
        active_platforms = [
            value for value in active_platforms if value in requested_keys
        ]
        if not active_platforms:
            print(
                "   ↳ [PlatformScope] 素材指定平台與本次 scheduler scope 無交集，"
                "保留素材供其他平台 cycle 使用"
            )
            return "skipped_platform_scope"
    if not is_accessible:
        print(
            "   ↳ [Note] 原圖不可存取，IG/Threads 將使用 cover_pipeline 的 "
            "fallback 背景渲染封面（不再 skip）"
        )

    # Phase 2 搜集：compose 前補「多源脈絡」增厚度（EDITORIAL_MODE 才跑；找不到/出錯就略過）。
    from src.slot_routing import editorial_mode as _ed_mode
    if _ed_mode():
        try:
            from src.gather import gather_brief
            _tc = getattr(topic_cls, "category_id", None)
            _brief = gather_brief(conn, news_id, title, topic_category=_tc)
            if _brief:
                content = content + "\n\n" + _brief
                print(f"   📚 [Gather] 補入多源脈絡（{_brief.count(chr(10))} 行同主題來源）")
        except Exception as _exc:  # noqa: BLE001 — 搜集失敗用原素材續寫，不擋出稿（活下去）
            print(f"   ⚠️ [Gather] 例外，用原素材續寫：{_exc}")

    bundle = await compose_multi_platform(
        title,
        content,
        final_img_url,
        editorial_note=score_data.editorial_note,
        platforms=active_platforms
    )
    if not bundle:
        # Phase 8.19：徹底移除 emergency template。
        # composer.py 已內建 Gemini → Claude CLI 雙路徑；若仍回 None，
        # 代表 (1) 雲端 Gemini quota 用盡 且 (2) 本機 Claude CLI 不可用。
        # 此時不該塞「系統代班速報」垃圾範本進 queue，應果斷 skip，
        # 讓 Cloud publisher 的 freshness-first fallback 挑舊一點但真貨的素材。
        print(" ⚠️  [Pipeline] 寫作 LLM 雙路徑皆失敗 → skip 本篇（不入 queue）")
        return "skipped_no_llm"

    # 3. 逐平台 finalize（修 hashtag、壓字數）
    finalized: Dict[str, Tuple[PlatformVariant, str, bool]] = {}
    # 3. 逐一處理有生成的平台變體
    for platform_key in active_platforms:
        raw_variant = getattr(bundle, platform_key)
        if not raw_variant:
            continue

        variant, full_text, ok = finalize_variant(raw_variant, platform_key)
        finalized[platform_key] = (variant, full_text, ok)
        tag = "✅" if ok else "⚠️"
        print(f"   ↳ {dbmod.PLATFORM_LABEL[platform_key]}: {variant.char_count} 字 {tag}")
    missing_variants = [
        platform for platform in active_platforms if platform not in finalized
    ]
    if missing_variants:
        print(
            " ⚠️  [Pipeline] Composer 未回傳所有 requested variants → skip 本篇："
            + ",".join(missing_variants)
        )
        return "skipped_no_llm"

    # 3.5 品質證據 + 一次受限重寫。
    # block 仍 fail-closed；rewrite 只給 composer 一次修正機會。第二次仍命中
    # rewrite 時保留草稿供人工看，但絕不進自動發布 queue。每次判定只保存規則
    # 與文字 hash，不保存另一份全文。
    draft_id = hashlib.sha1(f"{news_id}_v1".encode()).hexdigest()

    def evaluate_quality(attempt: int) -> dict[str, list]:
        findings: dict[str, list] = {}
        for platform_key, (_variant, ftext, _ok) in finalized.items():
            issues = check_quality(ftext, title=title)
            findings[platform_key] = issues
            dbmod.record_quality_evaluation(
                conn,
                draft_id=draft_id,
                news_id=news_id,
                platform=dbmod.PLATFORM_DB_NAME[platform_key],
                stage="compose",
                attempt=attempt,
                full_text=ftext,
                issues=issues,
            )
        return findings

    quality_findings = evaluate_quality(1)
    compose_block = [
        f"{platform}: {format_issues(issues)}"
        for platform, issues in quality_findings.items()
        if has_blocking_issues(issues)
    ]
    if compose_block:
        print(
            f" 🛑 [QualityGuard·compose] 偵測到拒發指紋，skip 本篇不寫 draft："
            f" {' || '.join(compose_block)}"
        )
        dbmod.update_status(conn, news_id, "dropped")
        return "dropped_quality_block"

    rewrite_requests = [
        f"{platform}: {format_issues(issues)}"
        for platform, issues in quality_findings.items()
        if should_request_rewrite(issues)
    ]
    rewrite_unresolved = False
    if rewrite_requests:
        print(
            "   ↳ [QualityGuard·rewrite] 命中可修正品質問題，執行唯一一次重寫："
            + " || ".join(rewrite_requests)
        )
        rewrite_note = (
            f"{score_data.editorial_note}\n\n"
            "QUALITY REWRITE (one attempt only): Rewrite every requested platform "
            "variant. Preserve source-backed facts and the core insight. Remove or "
            "attribute unsupported numeric claims; do not invent citations. Fix these "
            f"deterministic findings: {' || '.join(rewrite_requests)}"
        )
        retry_bundle = await compose_multi_platform(
            title,
            content,
            final_img_url,
            editorial_note=rewrite_note,
            platforms=active_platforms,
        )
        if retry_bundle:
            retry_finalized = dict(finalized)
            for platform_key in active_platforms:
                raw_variant = getattr(retry_bundle, platform_key)
                if raw_variant:
                    retry_finalized[platform_key] = finalize_variant(
                        raw_variant, platform_key
                    )
            finalized = retry_finalized
            bundle = retry_bundle
            quality_findings = evaluate_quality(2)
            retry_blocks = [
                f"{platform}: {format_issues(issues)}"
                for platform, issues in quality_findings.items()
                if has_blocking_issues(issues)
            ]
            if retry_blocks:
                print(
                    " 🛑 [QualityGuard·rewrite] 重寫後出現 block，skip 本篇："
                    + " || ".join(retry_blocks)
                )
                dbmod.update_status(conn, news_id, "dropped")
                return "dropped_quality_block"
            rewrite_unresolved = any(
                should_request_rewrite(issues)
                for issues in quality_findings.values()
            )
        else:
            rewrite_unresolved = True
            print("   ⚠️ [QualityGuard·rewrite] 重寫 LLM 失敗，原稿只存人工複核")

    # 4. 建立 Draft（舊表相容；若本輪沒有 FB，就用第一個實際目標作 canonical）
    canonical_key = "fb" if "fb" in finalized else active_platforms[0]
    canonical_variant, canonical_full_text, _ = finalized[canonical_key]
    legacy_content = _build_legacy_content(canonical_variant, bundle.image_url)

    draft = Draft(
        id=draft_id,
        news_id=news_id,
        persona_version="1.1",  # Milestone 3.1
        content=legacy_content,
        full_text=canonical_full_text,
        confidence_score=score,
        score_breakdown=ScoreBreakdown(**score_data.score_breakdown.model_dump()),
        llm_provider="google",
        llm_model="gemini-1.5-flash-8b", # Scorer 使用 8B
        generated_at=datetime.now(timezone.utc).isoformat(),
        status="pending_review",
    )

    auto_publish = (
        (owner_submitted or score >= publish_threshold)
        and not rewrite_unresolved
    )
    if auto_publish:
        draft.status = "auto_approved"
        reason = "owner submission" if owner_submitted else f"分數 ≥ {publish_threshold}"
        print(f" ↳ [Auto-Publish] {reason}，啟動指定平台發布")
    elif rewrite_unresolved:
        print(
            " ↳ [Quality Hold] 重寫後仍有 rewrite issue；保留 pending_review，"
            "不進自動發布 queue"
        )
    else:
        print(f" ↳ [Drafted] 分數 {score:.2f} < {publish_threshold}，存入草稿等待人工複核")

    # 5. 寫入 drafts 表
    dbmod.insert_draft(conn, draft)
    # Phase 10 (2026-06-03)：把 carousel 圖卡內容持久化到 draft 層，讓雲端
    # run_publish_queue 能 render+發 carousel（沒有則維持單圖）。
    if bundle.carousel is not None:
        # 簡→繁台灣 backstop：carousel 卡片內容是獨立欄位，不經 finalize_variant，
        # 所以雲端 Gemini 吐的簡體字必須在這裡攔下（與 caption 同一套 OpenCC s2tw）。
        from src.locale_tw import fix_mainland_text as _fmt, to_traditional as _t2t
        _tw = lambda s: _fmt(_t2t(s))[0] if s else s
        c = bundle.carousel
        c = c.model_copy(update={
            "insight_statement": _tw(c.insight_statement),
            "insight_support": _tw(c.insight_support),
            "stat_number": _tw(c.stat_number),
            "stat_caption": _tw(c.stat_caption),
            "takeaways": [_tw(t) for t in (c.takeaways or [])],
        })
        dbmod.set_carousel_json(conn, draft_id, c.model_dump_json())

    # 6. 寫入三個 platform_drafts row
    created_at = datetime.now(timezone.utc).isoformat()
    for platform_key, (variant, full_text, _ok) in finalized.items():
        dbmod.upsert_platform_draft(
            conn,
            draft_id=draft_id,
            platform=dbmod.PLATFORM_DB_NAME[platform_key],
            title=variant.title,
            body=variant.body,
            hashtags=list(variant.hashtags or []),
            full_text=full_text,
            char_count=variant.char_count,
            appendix_version=APPENDIX_VERSION,
            created_at=created_at,
        )

    # 7. 自動發布路徑
    publish_results: Dict[str, bool] = {}
    any_success = False
    if compose_only and auto_publish:
        # Phase 8.18 雲本混合：達門檻的 draft 入 publish queue，由 Cloud 端 run_publish_queue.py 發文
        dbmod.enqueue_draft(conn, draft_id, publish_at=datetime.now(timezone.utc).isoformat())
        dbmod.update_status(conn, news_id, "queued")
        print(f" ↳ [Compose-Only] draft 已入 publish queue，等待 Cloud 發文")
        save_md_draft(draft, finalized, bundle.image_url, news_url)
        return "queued"
    elif compose_only and not auto_publish:
        # 沒達門檻的草稿在 compose-only 模式下不進 queue，靜待人工 review
        dbmod.update_status(conn, news_id, "drafted")
        save_md_draft(draft, finalized, bundle.image_url, news_url)
        return "drafted"
    elif auto_publish:
        # Direct mode also enters the canonical queue before touching Meta.
        # A partial/all-failed API attempt therefore remains recoverable and the
        # platform-aware publisher can retry only tuples lacking success evidence.
        dbmod.enqueue_draft(conn, draft_id, publish_at=datetime.now(timezone.utc).isoformat())
        for platform_key in ("fb", "threads", "ig"):
            if platform_key not in finalized:
                continue
            variant, full_text, ok = finalized[platform_key]
            success = await _publish_platform(
                conn, draft_id, platform_key, variant, full_text, ok, bundle.image_url,
                topic_category=row["topic_category"] if "topic_category" in row.keys() else None,
                carousel=bundle.carousel,
            )
            publish_results[platform_key] = success
            if success:
                any_success = True
        pending_platforms = dbmod.pending_publish_platforms(conn, draft_id)
        if not pending_platforms:
            dbmod.mark_queue_published(conn, draft_id)
        elif any_success:
            dbmod.update_status(conn, news_id, "publish_partial")
        else:
            dbmod.update_status(conn, news_id, "publish_failed")
    else:
        dbmod.update_status(conn, news_id, "drafted")

    # 8. 寫視覺化預覽 .md（drafts/ 資料夾，每篇都寫）
    save_md_draft(draft, finalized, bundle.image_url, news_url)

    # 9. 本地檔案室存檔（archive/，僅在實際發布成功時寫入）
    if auto_publish and any_success:
        save_archive_md(draft, finalized, bundle.image_url, news_url, publish_results)
        if dbmod.pending_publish_platforms(conn, draft_id):
            print(" ↳ [Partial] 部分平台成功；保留 queued，只重試缺失平台")
            return "partial"
        print(" ↳ [Done] 所有指定平台皆有 success evidence")
        return "published"

    if auto_publish and not any_success:
        print(" ↳ [Publish-Failed] 已達門檻但所有平台都發布失敗")
        return "publish_failed"

    return "drafted"


# ---------- Main ----------

async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help=f"啟動每 {HEARTBEAT_SECONDS//60} 分鐘心跳模式")
    parser.add_argument("--harvest-now", action="store_true", help="不受節流限制，啟動即跑一次 harvest")
    parser.add_argument(
        "--publish-now",
        action="store_true",
        help="立即推送一篇：強制 harvest + 放寬門檻至 MIN_SCORE_THRESHOLD，用於『我現在就要一篇上架』場景"
    )
    parser.add_argument(
        "--compose-only",
        action="store_true",
        help="Phase 8.18：只 harvest + score + compose + enqueue，不發文。Mac launchd 每小時用此模式。"
    )
    parser.add_argument(
        "--buffer-target",
        type=int,
        default=2,
        help="Phase 8.18：compose-only 模式下，queue buffer 目標筆數（預設 2）"
    )
    parser.add_argument(
        "--platforms",
        default="facebook,instagram,threads",
        help="Canonical comma list restricting compose scope: facebook,instagram,threads",
    )
    args = parser.parse_args()
    requested_platforms = {
        value.strip() for value in args.platforms.split(",") if value.strip()
    }
    allowed_platforms = set(CANONICAL_TO_COMPOSER_PLATFORM)
    if not requested_platforms or not requested_platforms <= allowed_platforms:
        parser.error("--platforms must contain only facebook,instagram,threads")

    dbmod.init_db()

    cycle_count = 0
    while True:
        cycle_count += 1
        # Threads 長效權杖自動續約
        refresh_threads_token()

        # --- [M6.2] Idle Loop Fix：cycle 開頭觸發 harvest（受節流保護）---
        # --publish-now / --harvest-now 皆強制抓一次；--loop 遵循節流
        force_harvest = (args.harvest_now or args.publish_now) and cycle_count == 1
        if args.loop or force_harvest:
            await maybe_run_harvest(force=force_harvest)

        conn = dbmod.get_conn()
        try:
            # --- [M6.0] 數據複盤與反思心跳 ---
            # 每 12 次心跳 (30min × 12 = 6hr) 執行一次深度複盤
            if args.loop and cycle_count % 12 == 1:
                print(f"\n[Heartbeat] 第 {cycle_count} 次循環：啟動數據複盤與策略反思...")
                try:
                    await run_analysis_cycle()
                    await run_reflection()
                except Exception as e:
                    print(f" ↳ [Error] 複盤循環失敗: {e}")

            # --- [M6.3] Cadence 決策：1hr ≤ 間隔 ≤ 2hr ---
            if args.publish_now:
                override = MIN_SCORE_THRESHOLD  # 極度寬鬆：保證推得出東西
            elif args.compose_only:
                # Phase 8.18: compose-only 不看 publish cadence（發文時機是 Cloud publisher 的事）
                # 用 AUTO_PUBLISH_THRESHOLD 做門檻，但強制 should_publish=True 讓流程跑
                override = None
            else:
                override = None

            if args.compose_only:
                # Phase 8.18：compose-only 模式的 buffer 上限檢查
                queued_n = dbmod.count_queued_pending_for_platforms(
                    conn, requested_platforms
                )
                # EDITORIAL_MODE 時段 buffer：只看「這個 slot 桶」有沒有料；桶空就 compose 一筆，
                # 即使總 buffer 已被別桶（Mac 端非 slot-aware 填充）塞滿——否則晚間政治稿永遠擠不進。
                from src.slot_routing import editorial_mode, current_slot, bucket_categories
                _slot = current_slot() if editorial_mode() else None
                if _slot:
                    slot_n = dbmod.count_queued_in_categories(
                        conn,
                        bucket_categories(_slot),
                        platforms=requested_platforms,
                    )
                    should_publish = slot_n < 1
                    threshold = AUTO_PUBLISH_THRESHOLD
                    reason = (f"slot={_slot} 桶已有料 ({slot_n})，跳過 compose" if slot_n >= 1
                              else f"slot={_slot} 桶空 → compose 一筆")
                    if not should_publish:
                        print(f"\n[Compose-Only] {reason}")
                elif queued_n >= args.buffer_target:
                    print(f"\n[Compose-Only] queue 已有 {queued_n} 筆 queued (≥{args.buffer_target})，buffer 已滿，本 cycle 跳過 compose")
                    should_publish = False
                    threshold = AUTO_PUBLISH_THRESHOLD
                    reason = f"buffer 滿 ({queued_n}/{args.buffer_target})"
                else:
                    should_publish = True
                    threshold = AUTO_PUBLISH_THRESHOLD
                    reason = f"compose-only，buffer {queued_n}/{args.buffer_target}，繼續 compose"
            else:
                should_publish, threshold, reason = decide_cadence(conn, override_threshold=override)
            print(f"\n[Cadence] {reason}")

            pending_items = dbmod.get_pending_items(conn)
            # 2026-06-27 時段選題路由：晚=政治桶、早午=市場桶優先（soft bias、桶內維持原序）。
            # 藏在 EDITORIAL_MODE flag 後——關＝reorder no-op、完全沿用舊 weighted_score 行為（活下去）。
            from src.slot_routing import reorder_by_slot, current_slot, editorial_mode
            if editorial_mode():
                _slot = current_slot()
                if _slot:
                    pending_items = reorder_by_slot(pending_items, _slot)
                    print(f"[Slot] EDITORIAL_MODE on：slot={_slot} → 候選優先排「{_slot}」桶")
            if not pending_items:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 無待處理項目。")
            elif not should_publish:
                print(
                    f"[Cadence] 本 cycle 不發文（僅累積素材，共 {len(pending_items)} 筆 pending）"
                )
            else:
                print(
                    f"\n=== News Radar Pipeline 啟動 | 待處理: {len(pending_items)} "
                    f"| 動態門檻: {threshold} | 本輪目標發布: {MAX_PUBLISH_PER_SLOT} ==="
                )
                published_count = 0
                scanned = 0
                # Hunter 獵殺模式：掃描到獵殺成功 MAX_PUBLISH_PER_SLOT 篇就收手
                for row in pending_items:
                    if scanned >= MAX_POSTS_PER_SLOT:
                        print(
                            f"[Hunter] 已掃描 {scanned} 篇仍未達獵殺額度，本輪收手（剩下留待下次 cycle）"
                        )
                        break
                    if published_count >= MAX_PUBLISH_PER_SLOT:
                        print(
                            f"[Hunter] 已獵殺 {published_count} 篇，本輪停止掃描"
                        )
                        break
                    scanned += 1
                    outcome = await process_item(
                        conn, row,
                        publish_threshold=threshold,
                        compose_only=args.compose_only,
                        requested_platforms=requested_platforms,
                    )
                    if outcome in ("published", "queued"):
                        published_count += 1
                    # compose-only：每 cycle 只 compose 一筆入 queue，避免一次爆量塞滿 buffer
                    if args.compose_only and published_count >= 1:
                        print(f"[Compose-Only] 本 cycle 已 compose 一筆入 queue，收手")
                        break

                print(
                    f"[Hunter] 本輪總結：掃描 {scanned} / 發布 {published_count}"
                )
        finally:
            conn.close()

        if not args.loop:
            break

        print(f"=== 進入休眠，{HEARTBEAT_SECONDS//60} 分鐘後進行下次心跳檢查... ===")
        await asyncio.sleep(HEARTBEAT_SECONDS)

    print("\n=== Pipeline 執行完畢 ===")


if __name__ == "__main__":
    asyncio.run(main())
