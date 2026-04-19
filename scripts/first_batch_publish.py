"""
News Radar · 首發三平台一次性腳本
================================
用途：
- 跳過 run_pipeline.py 的 Hunter 掃描，**直接指定一則 news_items.id**，
  跑完「compose → 顯示三份草稿 → 等待你手動確認 → 依序 publish 到 FB / IG / Threads」。
- 這是 Phase 8.11 三平台首發專用。會同步寫 drafts / platform_drafts / publish_log
  三張表，讓 engagement_stats 與 reflector 之後能吃到。

使用：
    cd news_radar
    source .venv/bin/activate  # 或你的 venv 啟動方式
    python scripts/first_batch_publish.py                 # 預設用 GPT-Rosalind
    python scripts/first_batch_publish.py --id <news_id>  # 指定其他新聞
    python scripts/first_batch_publish.py --dry-run       # 只 compose 不發布

流程：
    1) 讀 DB 取出新聞本文 + og_image
    2) 呼叫 compose_multi_platform() 一次 LLM call 產三個平台版本
    3) 對每個變體跑 finalize_variant() 做字數安全網（超限 → 終止，不強截）
    4) 把三份 full_text 完整印出來
    5) 等你在 terminal 輸入大寫 YES 才 publish，其他輸入一律中止
    6) Publish 成功後寫 drafts / platform_drafts / publish_log
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 確保可以 import src.xxx
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.composer import compose_multi_platform, finalize_variant
from src.publisher import publish_to_fb, publish_to_ig, publish_to_threads

import mimetypes
try:
    import httpx  # 專案已有此依賴
except ImportError:
    httpx = None

# --- 視覺預覽策略 ---
# 預設只印 URL + media hint(content-type / size)。不落地任何檔案。
# 原因:
#   1) 未來來源可能是影片,本機下載會吃儲存空間。
#   2) Meta API 發文時直接吃 URL,本地檔案不是必要資產。
#   3) 使用者要離線看圖時才 opt-in --download-preview。
_VIDEO_MIME_PREFIXES = ("video/", "application/vnd.apple.mpegurl")


def probe_media_url(url: str) -> dict:
    """HEAD request 抓 URL 的 content-type / size,回傳 dict(hint 字典)。
    不實際下載內容,網路流量低。任何錯誤都吞掉,回 {"error": "..."}。
    """
    if not url:
        return {"error": "no url"}
    if httpx is None:
        return {"error": "httpx not installed"}
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            # 部分 CDN 不支援 HEAD,退回 GET range:bytes=0-0
            r = client.head(url, headers={"User-Agent": "news-radar/1.0"})
            if r.status_code >= 400:
                r = client.get(
                    url,
                    headers={
                        "User-Agent": "news-radar/1.0",
                        "Range": "bytes=0-0",
                    },
                )
            r.raise_for_status()
            ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
            cl = r.headers.get("content-length")
            is_video = any(ct.startswith(p) for p in _VIDEO_MIME_PREFIXES)
            return {
                "content_type": ct or "?",
                "size_bytes": int(cl) if cl and cl.isdigit() else None,
                "is_video": is_video,
                "final_url": str(r.url),
            }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def download_preview_image(url: str, dest_dir: Path, item_id: str) -> Path | None:
    """opt-in 才呼叫: 把 URL 抓到本機(只對圖片合理)。失敗回 None。"""
    if not url or httpx is None:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            r = client.get(url, headers={"User-Agent": "news-radar/1.0"})
            r.raise_for_status()
            ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
            # 拒絕下載影片(太大 & 通常用不到)
            if any(ct.startswith(p) for p in _VIDEO_MIME_PREFIXES):
                print(f"  [warn] URL 是影片 ({ct}),拒絕本地下載;請直接看 URL。")
                return None
            ext = mimetypes.guess_extension(ct) if ct else None
            if not ext:
                url_lo = url.lower().split("?")[0]
                for e in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                    if url_lo.endswith(e):
                        ext = e
                        break
            if not ext:
                ext = ".bin"
            path = dest_dir / f"{item_id}_preview{ext}"
            path.write_bytes(r.content)
            return path
    except Exception as e:
        print(f"  [warn] 下載預覽圖失敗 ({type(e).__name__}: {e})")
        return None


def fmt_size(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}TB"

# 預設首發新聞：OpenAI 發布 GPT-Rosalind 生命科學研究模型
DEFAULT_ITEM_ID = "086b474abb81ae20d9d7e267631c93892ffee15e"

PLATFORM_TO_DB = {
    "fb": "facebook",
    "ig": "instagram",
    "threads": "threads",
}

# 首發專用 editorial note：逼模型用標準分析師姿態出場
FIRST_BATCH_EDITORIAL_NOTE = """這是 News Radar 三平台正式首發第一篇。
目標：以『冷靜且具同理心的資深科技商業戰略評論家』之姿出場，展示 fused soul 的標準品質。

本則新聞的關鍵角度（非必須全寫，但這是判斷取捨的基準）：
- OpenAI 發布領域專用 frontier model（GPT-Rosalind）給生命科學，而非通用大模型的下一個 parameter bump。
  這是產品線策略轉折：從「比誰更聰明」轉向「比誰更深入特定垂直」。
- 錨定客戶名單（Amgen / Moderna / Allen Institute / Thermo Fisher）本身就是市場訊號，
  代表 OpenAI 已經不再只做 B2C，而是往製藥價值鏈前段嵌入。
- 分銷三路齊發（ChatGPT / Codex / API + Life Sciences plugin），這是分銷戰略不是單純產品上架。
- 藥物研發從 target discovery 到 FDA approval 平均 10–15 年，早期壓縮的邊際效益在下游會被放大。

寫作要求：
- 第一句務必有客觀事實或核心數據（不要用反問句、不要用『你知道嗎』）。
- 用自己咀嚼後的敘述語氣，展示『資深科技記者 + 分析師』的混合語感。
- 絕對禁止『這說明了兩件事』、『拆解兩層邏輯』這類八股結構。
- 冷靜而具同理心的第三人稱，不要『我的反思』、『我認為』這類主觀套話。
- 讓讀者在『通勤 90 秒』能帶走核心洞察；技術詞要轉譯，數字要有地基。
"""


def load_item(item_id: str) -> dict:
    db = sqlite3.connect(PROJECT_ROOT / "data" / "01_harvest" / "news_radar.db")
    cur = db.cursor()
    cur.execute(
        "SELECT id, title, clean_markdown, og_image_url, url, feed_name "
        "FROM news_items WHERE id = ?",
        (item_id,),
    )
    row = cur.fetchone()
    db.close()
    if not row:
        raise SystemExit(f"[Error] 找不到 news_items.id = {item_id}")
    return {
        "id": row[0],
        "title": row[1],
        "body": row[2] or "",
        "og_image_url": row[3],
        "url": row[4],
        "feed_name": row[5],
    }


def banner(text: str):
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def write_drafts_to_db(
    item: dict,
    finalized: dict,
    persona_version: str = "news_radar_soul_v1",
    appendix_version: str = "v2",
) -> str:
    """把 drafts + platform_drafts 寫回 DB，回傳 draft_id。"""
    draft_id = hashlib.sha1(
        (item["id"] + persona_version + datetime.now(timezone.utc).isoformat()).encode()
    ).hexdigest()[:16]

    db = sqlite3.connect(PROJECT_ROOT / "data" / "01_harvest" / "news_radar.db")
    cur = db.cursor()

    # 以 FB 變體當 drafts 主內容（舊表單欄位），其他平台存 platform_drafts
    fb_variant = finalized.get("fb")
    if fb_variant:
        v2, full_text, _ = fb_variant
        cur.execute(
            """
            INSERT INTO drafts(
                id, news_id, persona_version, title, hashtags,
                image_url, full_text, generated_at, status,
                llm_provider, llm_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                item["id"],
                persona_version,
                v2.title,
                json.dumps(v2.hashtags or [], ensure_ascii=False),
                item["og_image_url"],
                full_text,
                datetime.now(timezone.utc).isoformat(),
                "approved",  # 已通過人工 YES
                "gemini",
                "gemini-2.0-flash-lite",
            ),
        )

    # platform_drafts 三筆
    for p, (v2, full_text, _) in finalized.items():
        cur.execute(
            """
            INSERT INTO platform_drafts(
                draft_id, platform, title, body, hashtags, full_text,
                char_count, appendix_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                PLATFORM_TO_DB[p],
                v2.title,
                v2.body,
                json.dumps(v2.hashtags or [], ensure_ascii=False),
                full_text,
                v2.char_count,
                appendix_version,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    db.commit()
    db.close()
    return draft_id


def record_publish_result(draft_id: str, platform: str, resp: dict | None, err: str | None):
    db = sqlite3.connect(PROJECT_ROOT / "data" / "01_harvest" / "news_radar.db")
    cur = db.cursor()
    platform_post_id = None
    if resp and isinstance(resp, dict):
        platform_post_id = str(resp.get("id") or resp.get("post_id") or "")
    cur.execute(
        """
        INSERT INTO publish_log(
            draft_id, platform, platform_post_id,
            posted_at, success, error_message
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            PLATFORM_TO_DB[platform],
            platform_post_id,
            datetime.now(timezone.utc).isoformat(),
            1 if err is None else 0,
            err,
        ),
    )
    db.commit()
    db.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default=DEFAULT_ITEM_ID, help="news_items.id")
    parser.add_argument(
        "--platforms",
        nargs="+",
        default=["fb", "ig", "threads"],
        choices=["fb", "ig", "threads"],
    )
    parser.add_argument("--dry-run", action="store_true", help="只 compose，不 publish")
    parser.add_argument(
        "--model",
        default=None,
        help="覆寫 Gemini 模型（預設 gemini-2.0-flash-lite）。"
             "當 lite 日配額爆掉時可切 gemini-2.0-flash 或 gemini-2.5-flash。",
    )
    parser.add_argument(
        "--from-json",
        default=None,
        help="從 JSON 檔載入已經寫好的三份草稿，跳過 Gemini LLM call。"
             "大原則：任何 API call 因配額或任何原因失效時，由當下主導此任務串的"
             "主 agent（Claude / GPT / Gemini 皆可）充當大腦、手寫草稿後走這條路，"
             "避免因 API 額度擋住首發／日常發文。",
    )
    parser.add_argument(
        "--download-preview",
        action="store_true",
        help="opt-in: 把 og_image 下載到 data/first_batch_preview/ 以便離線查看。"
             "預設關閉——大原則是『只印 URL,不落地』,避免影片與本機空間問題。",
    )
    parser.add_argument(
        "--video-url",
        default=None,
        help="短影片測試用。傳入公開 .mp4 URL 時,三平台 publisher 改走影片路徑"
             "(FB /videos、IG REELS、Threads media_type=VIDEO),不再走圖片。"
             "也可在 --from-json 的 JSON 裡放 top-level video_url 欄位。",
    )
    args = parser.parse_args()

    item = load_item(args.id)

    # 若有 --from-json 且 JSON 裡有 image_url / video_url,以 JSON 的為準（手稿可能指定不同 asset）
    effective_image_url = item["og_image_url"]
    effective_video_url = args.video_url  # CLI override 優先

    if args.from_json:
        try:
            with open(args.from_json, "r", encoding="utf-8") as f:
                _manual = json.load(f)
            if _manual.get("image_url"):
                effective_image_url = _manual["image_url"]
            if not effective_video_url and _manual.get("video_url"):
                effective_video_url = _manual["video_url"]
        except Exception:
            pass

    # 決定「主要 media URL」：影片優先（若有影片就拿它來 probe & preview）
    primary_media_url = effective_video_url or effective_image_url

    banner("[1/3] 新聞源 + 視覺預覽")
    print(f"  ID         : {item['id']}")
    print(f"  來源       : {item['feed_name']}")
    print(f"  標題       : {item['title']}")
    print(f"  新聞 URL   : {item['url']}  ← cmd+click 看完整原文")
    print(f"  本文長度   : {len(item['body'])} chars")
    print()
    if effective_video_url:
        print(f"  🎬 影片 URL : {effective_video_url}")
        print(f"     ↑ cmd+click 直接在瀏覽器開 / 另存檔預覽 (不落地)")
        if effective_image_url and effective_image_url != effective_video_url:
            print(f"  📷 封面圖 URL(備援): {effective_image_url}")
    else:
        print(f"  📷 媒體 URL : {primary_media_url}")
        print(f"     ↑ cmd+click 直接在瀏覽器開 (不落地)")

    # HEAD 打一次, 把 content-type / size / 是否為影片 印出來當 hint
    probe = probe_media_url(primary_media_url)
    if "error" in probe:
        print(f"     media probe: [warn] {probe['error']}")
    else:
        kind = "影片 (VIDEO)" if probe["is_video"] else "圖片 (IMAGE)"
        print(f"     media hint : type={probe['content_type']}  "
              f"size={fmt_size(probe['size_bytes'])}  →  {kind}")
        if probe["is_video"]:
            print(f"     ⚠ 影片類型不下載本機,請直接用上方 URL 在瀏覽器預覽")

    # 若使用者明確給了 --video-url 但 probe 顯示不是影片,警告
    if effective_video_url and probe.get("is_video") is False:
        print(f"     ⚠ 警告:你傳入 --video-url 但 content-type={probe.get('content_type')} 不是影片。"
              f"Meta API 會失敗;請確認 URL 指向 .mp4 檔案本身而非播放頁。")

    # opt-in 才落地
    if args.download_preview and not probe.get("is_video", False):
        preview_dir = PROJECT_ROOT / "data" / "first_batch_preview"
        preview_path = download_preview_image(primary_media_url, preview_dir, item["id"])
        if preview_path:
            print(f"  本機預覽   : {preview_path.resolve()}")
            print(f"  (快速打開)  open \"{preview_path.resolve()}\"")

    finalized = {}

    if args.from_json:
        banner(f"[2/3] 從 JSON 讀入手寫草稿（跳過 Gemini）: {args.from_json}")
        from src.schema import PlatformVariant  # 延遲 import 避免循環
        with open(args.from_json, "r", encoding="utf-8") as f:
            manual = json.load(f)

        # 若 JSON 帶 item_id，檢查一致性
        if manual.get("item_id") and manual["item_id"] != item["id"]:
            print(f"  [warn] JSON item_id={manual['item_id']} 不等於 --id {item['id']}")
        # 若 JSON 有 image_url，優先採用
        if manual.get("image_url"):
            item["og_image_url"] = manual["image_url"]

        for p in args.platforms:
            if p not in manual:
                print(f"  [{p}] JSON 裡沒這個平台，跳過。")
                continue
            d = manual[p]
            v = PlatformVariant(
                title=d["title"],
                body=d["body"],
                hashtags=d.get("hashtags", []),
                char_count=d.get("char_count", 0),
            )
            v2, full_text, ok = finalize_variant(v, p)
            finalized[p] = (v2, full_text, ok)
    else:
        banner("[2/3] 正在呼叫 Gemini composer（三平台一次生成）...")
        t0 = time.time()
        draft = await compose_multi_platform(
            title=item["title"],
            content=item["body"],
            og_image=item["og_image_url"],
            editorial_note=FIRST_BATCH_EDITORIAL_NOTE,
            platforms=args.platforms,
            model=args.model,
        )
        dt = time.time() - t0
        print(f"  LLM call 耗時 {dt:.1f}s")

        if not draft:
            raise SystemExit(
                "[Error] Composer 回傳 None。\n"
                "Phase 8.13 大原則：任何 stage 的 API 失效（configured quota / 429 / 500 / network）都不走重試，\n"
                "由『當下主導此任務串的主 agent（Claude / GPT / Gemini 皆可）』直接充當大腦，\n"
                "依同一份 soul + appendix + editorial_note 手寫 PlatformVariant JSON，\n"
                "存成 JSON 後用 --from-json 載入。這條原則的用意：\n"
                "正確性優先於 API 通路；不因額度擋住已排好的發文；不預設哪個廠商才是合法 composer。"
            )

        for p in args.platforms:
            v = getattr(draft, p)
            if not v:
                print(f"  [{p}] 變體缺失，跳過。")
                continue
            v2, full_text, ok = finalize_variant(v, p)
            finalized[p] = (v2, full_text, ok)

    banner("[3/3] 三份 draft 全文（請仔細看）")
    for p, (v2, full_text, ok) in finalized.items():
        print()
        print(f"------- {p.upper()} (char_count={v2.char_count}, ok={ok}) -------")
        print(full_text)
        print()

    # 存一份 JSON 方便事後 debug
    debug_path = PROJECT_ROOT / "data" / "first_batch_debug.json"
    debug_path.parent.mkdir(exist_ok=True)
    with open(debug_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "item": item,
                "drafts": {
                    p: {
                        "title": v2.title,
                        "body": v2.body,
                        "hashtags": v2.hashtags,
                        "full_text": full_text,
                        "char_count": v2.char_count,
                        "ok": ok,
                    }
                    for p, (v2, full_text, ok) in finalized.items()
                },
                "composed_at": datetime.now(timezone.utc).isoformat(),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"[info] draft 已另存一份到 {debug_path.relative_to(PROJECT_ROOT)}")

    bad = [p for p, (_, _, ok) in finalized.items() if not ok]
    if bad:
        raise SystemExit(f"[Error] 下列平台字數壓不下來，終止發布：{bad}")

    if args.dry_run:
        print("\n[--dry-run] 只預覽，不 publish。結束。")
        return

    # === 人工確認點 ===
    banner("確認發布")
    print("輸入  YES  送出三個平台；其他任何輸入（包含 Enter）=中止。")
    try:
        ans = input("> ").strip()
    except EOFError:
        ans = ""
    if ans != "YES":
        print("中止。沒有任何貼文被送出。")
        return

    # === 先寫 drafts / platform_drafts，拿到 draft_id ===
    draft_id = write_drafts_to_db(item, finalized)
    print(f"[info] draft_id = {draft_id}")

    # === Publish 依序：FB → Threads → IG ===
    # 若有 effective_video_url,三平台走影片路徑;否則走圖片路徑（沿用 og_image_url）。
    publish_image_url = item["og_image_url"] if not effective_video_url else None
    publish_video_url = effective_video_url
    media_mode = "VIDEO" if publish_video_url else "IMAGE"
    banner(f"開始 publish (mode={media_mode})")
    if publish_video_url:
        print(f"  video_url = {publish_video_url}")
    else:
        print(f"  image_url = {publish_image_url}")

    results = {}
    for p in ("fb", "threads", "ig"):
        if p not in finalized:
            continue
        v2, full_text, _ = finalized[p]
        print(f"\n>>> [{p.upper()}] publishing ({v2.char_count} 字, {media_mode}) ...")
        t0 = time.time()
        try:
            if p == "fb":
                r = await publish_to_fb(full_text, image_url=publish_image_url, video_url=publish_video_url)
            elif p == "threads":
                r = await publish_to_threads(full_text, image_url=publish_image_url, video_url=publish_video_url)
            else:
                r = await publish_to_ig(full_text, image_url=publish_image_url, video_url=publish_video_url)
            ok_flag = bool(r.get("success")) if isinstance(r, dict) else False
            if ok_flag:
                print(f"    ✔ 成功，耗時 {time.time()-t0:.1f}s")
                print(f"    回傳: {json.dumps(r, ensure_ascii=False)[:400]}")
                results[p] = {"ok": True, "resp": r, "err": None}
                record_publish_result(draft_id, p, r, None)
            else:
                err_msg = json.dumps(r, ensure_ascii=False)[:500] if isinstance(r, dict) else str(r)
                print(f"    ✗ 失敗(publisher 回報): {err_msg}")
                results[p] = {"ok": False, "resp": r, "err": err_msg}
                record_publish_result(draft_id, p, r, err_msg)
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            print(f"    ✗ 失敗(例外): {err_msg}")
            results[p] = {"ok": False, "resp": None, "err": err_msg}
            record_publish_result(draft_id, p, None, err_msg)

    # 若至少一個平台發布成功 → 標 published
    if any(r["ok"] for r in results.values()):
        db = sqlite3.connect(PROJECT_ROOT / "data" / "01_harvest" / "news_radar.db")
        cur = db.cursor()
        cur.execute(
            "UPDATE drafts SET status='published' WHERE id=?", (draft_id,)
        )
        cur.execute(
            "UPDATE news_items SET status='published' WHERE id=?", (item["id"],)
        )
        db.commit()
        db.close()

    banner("完成")
    for p, r in results.items():
        tag = "OK" if r["ok"] else "FAIL"
        print(f"  [{p}] {tag}  {r.get('err') or ''}")


if __name__ == "__main__":
    asyncio.run(main())
