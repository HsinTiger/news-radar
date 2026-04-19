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
from src.publisher import publish_to_fb, publish_to_threads, publish_to_ig
from src.token_utils import refresh_threads_token
from src.analyst import run_analysis_cycle
from src.reflector import run_reflection


# ---------- 策略參數 (Milestone 6.2 · Hunter 0.9) ----------
AUTO_PUBLISH_THRESHOLD = 0.9    # Hunter 精準門檻：只有 0.9+ 才算「獵殺成功」
RESCUE_PUBLISH_THRESHOLD = 0.8  # Rescue 模式放寬門檻（距上次發文 ≥ 2hr 時用）
MIN_SCORE_THRESHOLD = 0.65      # 低於此分數直接捨棄，不佔用 token
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
) -> bool:
    """單平台發布 + publish_log。ok=False 或缺圖 → 不呼叫 API，直接記失敗。
    回傳：True 表示 API 回報成功；False 表示任何原因失敗。
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

    # 呼叫對應 API
    result = {"success": False}
    
    if platform_key == "fb":
        # --- Facebook 智慧備援 (Plan A -> Plan B) ---
        print(f"   ↳ [📘 FB] 嘗試 Plan A (網址抓取)...")
        result = await publish_to_fb(full_text, image_url=image_url)
        
        # 偵測是否為圖片抓取失敗 (通常包含 "download failed" 或代碼 1353045)
        error_msg = str(result.get("error", ""))
        is_fetch_fail = "failed to download" in error_msg.lower() or "1353045" in error_msg
        
        if not result.get("success") and is_fetch_fail and image_url:
            print(f"   ⚠️ [📘 FB] Plan A 圖片抓取失敗，觸發備援 Plan B (下載後上傳)...")
            local_path = await image_manager.download_image(image_url)
            if local_path:
                result = await publish_to_fb(full_text, local_file_path=local_path)
                # 清除快取 (選配)
                image_manager.cleanup_cache()
            else:
                print(f"   ❌ [📘 FB] Plan B 下載圖片也失敗了，將記錄最終錯誤")
    
    elif platform_key == "threads":
        if not image_url:
            msg = "Threads 需要 image_url 才能發布"
            print(f"   ↳ [🧵 Threads Skip] {msg}")
            dbmod.log_publish(conn, PublishResult(
                draft_id=draft_id, platform=db_name, platform_post_id=None,
                posted_at=posted_at, success=False, error_message=msg,
            ))
            return False
        result = await publish_to_threads(full_text, image_url)
    elif platform_key == "ig":
        if not image_url:
            msg = "IG 需要 image_url 才能發布"
            print(f"   ↳ [📸 IG Skip] {msg}")
            dbmod.log_publish(conn, PublishResult(
                draft_id=draft_id, platform=db_name, platform_post_id=None,
                posted_at=posted_at, success=False, error_message=msg,
            ))
            return False
        result = await publish_to_ig(full_text, image_url)
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

async def process_item(conn, row, publish_threshold: Optional[float] = None) -> str:
    """單篇新聞處理。回傳狀態字串：
    - "published"：達標且至少一個平台成功發布（Hunter 獵殺成功）
    - "drafted"  ：未達 publish_threshold，僅落草稿
    - "dropped"  ：低於 MIN_SCORE_THRESHOLD 或無可發平台

    publish_threshold: 自動發布門檻，預設用全域 AUTO_PUBLISH_THRESHOLD。
    main loop 會依 cadence 動態傳入（例：Rescue Mode 傳 0.8）。
    """
    if publish_threshold is None:
        publish_threshold = AUTO_PUBLISH_THRESHOLD
    news_id = row["id"]
    title = row["title"]
    content = row["clean_markdown"] or ""
    og_image = row["og_image_url"]
    news_url = row["url"]

    print(f"\n[Pipeline] 處理新聞: {title[:40]}...")

    # 1. AI 評分
    score_data = await score_news(title, content)
    
    if not score_data:
        # --- 應變邏輯：如果主編 AI 配額用盡，執行強行發布 (M6.1 Fallback) ---
        print(f" ⚠️  [Pipeline] AI 評分模組失效 (可能是 429 配額問題)，啟動『暴力發布模式』...")
        from src.scorer import NewsScore
        score_data = NewsScore(
            confidence_score=1.0,  # 強制過關
            score_breakdown={
                "data_density": 0.8, 
                "strategic_signal": 0.8, 
                "news_novelty": 0.8, 
                "persona_fit": 1.0
            },
            editorial_note="[緊急代班] 強化數據深度，從底層邏輯反思科技與產業的連動。"
        )
    
    score = score_data.confidence_score
    print(f" ↳ AI 評分: {score:.2f} | 主編指令: {score_data.editorial_note[:60]}")

    if score < MIN_SCORE_THRESHOLD:
        print(f" ↳ [Dropped] 分數低於門檻 ({MIN_SCORE_THRESHOLD})")
        dbmod.update_status(conn, news_id, "dropped")
        return "dropped"

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
    
    platforms = ["fb", "ig", "threads"]
    active_platforms = []
    for p in platforms:
        if p in ["ig", "threads"] and not is_accessible:
            print(f"   ↳ [Skip] {p} 因無有效圖片跳過")
            continue
        active_platforms.append(p)

    if not active_platforms:
        print(" ↳ [Error] 無可用平台（媒介門檻限制）")
        return "dropped"

    bundle = await compose_multi_platform(
        title, 
        content, 
        final_img_url, 
        editorial_note=score_data.editorial_note,
        platforms=active_platforms
    )
    if not bundle:
        # --- 應變邏輯：如果寫作 AI 也配額用盡，使用高品質範本進行緊急發布 (M6.1 Emergency Fallback) ---
        print(" ⚠️  [Pipeline] 寫作 AI 配額用盡，啟動『緊急範本發布』...")
        from src.schema import MultiPlatformDraft, PlatformVariant
        
        # 建立一個通用的高品質變體
        emergency_v = PlatformVariant(
            title=f"🚀 {title}",
            body=f"【系統代班速報】\n\n科技格局正在發生結構性位移，護城河的定義已從產品轉向生態數據。這反映了產業變遷下的必然選擇。與其追逐破碎的新聞，不如冷靜看清底層的戰略邏輯，體諒轉型期帶來的陣痛。面對充滿挑戰的市場，數據密度高的決策，將會成為未來的勝負點。",
            hashtags=["#科技戰略", "#商業洞察", "#數據驅動"],
            char_count=300
        )
        bundle = MultiPlatformDraft(
            fb=emergency_v,
            ig=emergency_v,
            threads=emergency_v,
            image_url=final_img_url
        )

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

    # 4. 建立 Draft（舊表相容 / 以 FB 變體為 canonical）
    fb_variant, fb_full_text, _ = finalized["fb"]
    draft_id = hashlib.sha1(f"{news_id}_v1".encode()).hexdigest()
    legacy_content = _build_legacy_content(fb_variant, bundle.image_url)

    draft = Draft(
        id=draft_id,
        news_id=news_id,
        persona_version="1.1",  # Milestone 3.1
        content=legacy_content,
        full_text=fb_full_text,
        confidence_score=score,
        score_breakdown=ScoreBreakdown(**score_data.score_breakdown.model_dump()),
        llm_provider="google",
        llm_model="gemini-1.5-flash-8b", # Scorer 使用 8B
        generated_at=datetime.now(timezone.utc).isoformat(),
        status="pending_review",
    )

    auto_publish = score >= publish_threshold
    if auto_publish:
        draft.status = "auto_approved"
        print(f" ↳ [Auto-Publish] 分數 ≥ {publish_threshold}，啟動三平台發布")
    else:
        print(f" ↳ [Drafted] 分數 {score:.2f} < {publish_threshold}，存入草稿等待人工複核")

    # 5. 寫入 drafts 表
    dbmod.insert_draft(conn, draft)

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
    if auto_publish:
        for platform_key in ("fb", "threads", "ig"):
            if platform_key not in finalized:
                continue
            variant, full_text, ok = finalized[platform_key]
            success = await _publish_platform(
                conn, draft_id, platform_key, variant, full_text, ok, bundle.image_url,
            )
            publish_results[platform_key] = success
            if success:
                any_success = True
        dbmod.update_status(conn, news_id, "published" if any_success else "publish_failed")
    else:
        dbmod.update_status(conn, news_id, "drafted")

    # 8. 寫視覺化預覽 .md（drafts/ 資料夾，每篇都寫）
    save_md_draft(draft, finalized, bundle.image_url, news_url)

    # 9. 本地檔案室存檔（archive/，僅在實際發布成功時寫入）
    if auto_publish and any_success:
        save_archive_md(draft, finalized, bundle.image_url, news_url, publish_results)
        print(" ↳ [Done] 端到端流程完成")
        return "published"

    if auto_publish and not any_success:
        print(" ↳ [Publish-Failed] 已達門檻但所有平台都發布失敗")
        return "drafted"

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
    args = parser.parse_args()

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
            else:
                override = None
            should_publish, threshold, reason = decide_cadence(conn, override_threshold=override)
            print(f"\n[Cadence] {reason}")

            pending_items = dbmod.get_pending_items(conn)
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
                    outcome = await process_item(conn, row, publish_threshold=threshold)
                    if outcome == "published":
                        published_count += 1

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
