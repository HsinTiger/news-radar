"""
News Radar · Substack Compose CLI
=================================

Daily / Weekly 寫稿 driver。排程預設每天中午兩篇 Podcast Weekly 延伸文，
週日一篇 Weekly 公司分析；morning/evening 仍保留給手動取材與相容舊入口。

每次跑出 1 篇草稿 + 封面圖 + 精簡 metadata report，
同時寫到 **兩個位置**讓 Hsin 從家裡／公司都看得到：

    Path A (本地 repo)：~/news_radar/data/substack_drafts/<date>/<mode>_<slug>/
    Path B (OneDrive)：~/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/
                       文件/antigravity_workspace/substack/autogen/<date>/<mode>_<slug>/

每個資料夾長這樣：

    Article_Substack.md   # 貼到 Substack 的純淨版（標題＋副標＋本文）
    Article_Full.md       # 完整 review 版（profile、audit warnings、原料來源）
    cover.png             # 1456×816 Substack hero
    metadata.json         # 機讀格式

可選步驟（環境變數開關，**opt-in 模式**）：
    SUBSTACK_AUTO_DRAFT=1 — 啟用透過 python-substack 自動建立 Substack 草稿。
                           預設關閉（=0 或未設）走純 OneDrive paste 流程。
    SUBSTACK_COOKIES_STRING — 從 Chrome DevTools 抓的 cookie header 字串
                              （登入 Substack 後 → F12 → Network → 任一 request
                                → Headers → Request Headers → Cookie 整段複製）。
                              通常 2-4 週過期，失效時 CLI 會明確報錯，重抓即可。
    SUBSTACK_PUBLICATION_URL — 你的 Substack URL，例如 https://hsin73.substack.com
LLM backend 架構：
    SUBSTACK_COMPOSER_BACKEND=codex_cli,claude_cli
        - 預設依序嘗試；可用逗號清單顯式覆寫
        - 實際成功的 provider/model 會寫入 generated_by，不預先假定是哪一個模型
        - Podcast/公司文先消化主來源，再建立 5–10 源外部證據包
        - 最終寫手的 WebSearch / WebFetch 關閉，只使用上述已驗證材料

    封面由 deterministic cover renderer 產生；writer 不輸出圖片 prompt。
    角色素材不可用時會退回純文字海報，不影響正文草稿。

注意：python-substack 是社群非官方 wrapper，Substack 沒有公開 Write API。
若哪天失效或你想退出，OneDrive autogen/ 的 Article_Substack.md 永遠是手動 paste 後備。

用法：
    python substack_radar/compose.py morning            # 自動依環境變數決定要不要 push draft
    python substack_radar/compose.py morning --no-draft # 只寫檔，不 push draft
    python substack_radar/compose.py evening
    python substack_radar/compose.py morning --news-id <hash>  # 指定特定新聞
    python substack_radar/compose.py evening --topic "為什麼 AI 訓練成本指數下降而商業價值卻指數上升"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# Make src/ importable when running as a script
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env from repo root so GEMINI_API_KEY / SUBSTACK_* are visible.
# Why this is here (not in src/llm_brain): the rest of the codebase loads
# .env at the run_pipeline.py entry. Our CLI is a separate entry point,
# so we own loading .env here. dotenv is optional — if missing, fall back
# to whatever the shell already exported.
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(_REPO_ROOT / ".env")
except Exception:
    pass

from substack_radar.audience import (  # noqa: E402
    DEFAULT_SUBSTACK_AUDIENCE,
    validate_substack_audience,
)
from substack_radar.composer import (  # noqa: E402
    SubstackDraft,
    assert_reader_ready_markdown,
    audit_substack_draft,
    autofix_cjk_spacing,
    autofix_dashes,
    autofix_mainland_terms,
    autofix_traditional,
    compose_substack_article,
    plan_editorial_research,
    resolve_editorial_profile,
    strip_generated_footer,
    strip_production_instructions,
    word_range_for,
)
from substack_radar.editorial_research import (  # noqa: E402
    InsufficientResearchError,
    build_research_pack,
)
from substack_radar.draft_receipts import (  # noqa: E402
    clear_remote_receipt,
    get_remote_receipt,
    store_publication_receipt,
    store_publish_intent,
    store_remote_receipt,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

LOCAL_BASE = _REPO_ROOT / "data" / "substack_drafts"

# OneDrive mount on Hsin's Mac. If running on a machine without this path
# (e.g. CI / sandbox), the OneDrive write is skipped with a warning.
ONEDRIVE_BASE = Path(
    "/Users/hsin/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/"
    "文件/antigravity_workspace/substack/autogen"
)

EVENING_TOPICS_PATH = Path(__file__).resolve().parent / "config" / "substack_evening_topics.yaml"

# 科技/商業題材加重，只影響 Daily source pool 的挑文偏好。
_TECH_TOPICS = {"us_stocks", "tw_stocks", "ai_model", "ai_agent", "ai_application",
                "tech_product_launch", "supply_chain", "earnings"}
_TECH_BOOST = 1.2

# company mode（每週日 09:00 公司營運分析）
COMPANY_NEXT_PATH = _REPO_ROOT / "data" / "substack_drafts" / ".company_next"
COMPANY_WATCHLIST_PATH = _REPO_ROOT / "substack_radar" / "config" / "company_watchlist.yaml"
# Tracks news_items already used as a Substack source — SHARED by morning+evening
# so the two daily slots never pick the same item. Legacy .evening_used.json is
# still merged on load for backward-compat.
SUBSTACK_USED_PATH = _REPO_ROOT / "data" / "substack_drafts" / ".substack_used.json"
EVENING_USED_PATH = _REPO_ROOT / "data" / "substack_drafts" / ".evening_used.json"

# NEWS_RADAR_DB 覆寫：讓 Substack「立即」快速通道把 --news-id 指向一份從 canonical Release
# 拉下來的暫存 DB（drain_substack_fast.sh），完全不碰主 DB。預設仍是本機主 DB。
NEWS_DB_PATH = Path(os.environ.get("NEWS_RADAR_DB") or (_REPO_ROOT / "data" / "01_harvest" / "news_radar.db"))


# ---------------------------------------------------------------------------
# Slug helpers — Chinese-safe
# ---------------------------------------------------------------------------

def _slug_from_title(title: str, max_len: int = 40) -> str:
    """Filesystem-safe slug. Strips emoji + control chars, keeps CJK."""
    cleaned = unicodedata.normalize("NFKC", title)
    cleaned = re.sub(r"[\\/:*?\"<>|\n\r\t]", "", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return cleaned[:max_len] or f"draft_{datetime.now():%H%M%S}"


# ---------------------------------------------------------------------------
# Source selection
# ---------------------------------------------------------------------------

def _db_written_ids() -> set:
    """DB 單一真相（2026-06-20）：news_items.substack_written_at 非 NULL = 已被任何 mode
    寫成 substack 文。欄位不存在（首次）→ 回空 set。"""
    if not NEWS_DB_PATH.exists():
        return set()
    try:
        conn = sqlite3.connect(str(NEWS_DB_PATH))
        try:
            rows = conn.execute(
                "SELECT id FROM news_items WHERE substack_written_at IS NOT NULL"
            ).fetchall()
            return {r[0] for r in rows}
        finally:
            conn.close()
    except Exception:
        return set()  # 欄尚未建立


def _load_used() -> set:
    """Shared 'already used as a Substack source' set，所有 mode 共用 → 不重複撰寫同一來源。
    來源：legacy JSON（.substack_used / .evening_used）+ DB 欄 substack_written_at（單一真相）。"""
    used: set = set()
    for path in (SUBSTACK_USED_PATH, EVENING_USED_PATH):
        if path.exists():
            try:
                used |= set(json.loads(path.read_text(encoding="utf-8")).get("used", []))
            except Exception:
                pass
    used |= _db_written_ids()   # DB 欄併入 → company/podcast/morning/evening 全自動去重
    return used


def _lock_path() -> Path:
    """Path to a simple filesystem lock for _mark_used concurrency."""
    return SUBSTACK_USED_PATH.with_suffix(".used.lock")


def _acquire_lock(lock: Path, timeout_s: int = 30) -> bool:
    """Try to atomically create the lock file. Returns True if acquired."""
    import os, time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            try:
                pid = int(lock.read_text().strip())
                # Stale lock: process that held it is gone
                os.kill(pid, 0)
            except (OSError, ValueError):
                lock.unlink(missing_ok=True)
                continue  # retry
        time.sleep(0.1)
    return False


def _release_lock(lock: Path) -> None:
    lock.unlink(missing_ok=True)


def _mark_used(news_id: str, limit: int = 300) -> None:
    """Reserve a source in the local selection history.

    This is deliberately not remote-draft evidence.  Selection may happen
    before composition, so writing ``substack_written_at`` here created false
    completion claims when the LLM or Substack API failed later.
    """
    lock = _lock_path()
    if not _acquire_lock(lock):
        print(f"[_mark_used] ⚠️ 無法取得檔案鎖（30s timeout），跳過標記 {news_id}")
        return
    try:
        used = list(_load_used())
        if news_id in used:
            return
        used.append(news_id)
        used = used[-limit:]
        SUBSTACK_USED_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUBSTACK_USED_PATH.write_text(
            json.dumps({"used": used}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    finally:
        _release_lock(lock)


def _record_substack_evidence(
    news_id: str,
    draft_id: Optional[int | str] = None,
    publication: Optional[Dict[str, str]] = None,
) -> bool:
    """Persist local-written and remote-draft evidence as distinct facts."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(str(NEWS_DB_PATH))
        try:
            for column in (
                "substack_written_at TEXT",
                "substack_draft_id TEXT",
                "substack_drafted_at TEXT",
                "substack_post_id TEXT",
                "substack_post_url TEXT",
                "substack_published_at TEXT",
            ):
                try:
                    conn.execute(f"ALTER TABLE news_items ADD COLUMN {column}")
                except sqlite3.OperationalError:
                    pass
            cursor = conn.execute(
                """
                UPDATE news_items
                   SET substack_written_at=COALESCE(substack_written_at,?)
                 WHERE id=?
                """,
                (now, news_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"source row not found: {news_id}")
            if draft_id is not None:
                conn.execute(
                    """
                    UPDATE news_items
                       SET substack_draft_id=?,
                           substack_drafted_at=COALESCE(substack_drafted_at,?)
                     WHERE id=?
                    """,
                    (str(draft_id), now, news_id),
                )
            if publication:
                post_id = str(publication.get("post_id") or "").strip()
                public_url = str(publication.get("public_url") or "").strip()
                if not post_id or not public_url.startswith("https://"):
                    raise ValueError("publication evidence requires post id and HTTPS URL")
                conn.execute(
                    """
                    UPDATE news_items
                       SET substack_post_id=?,
                           substack_post_url=?,
                           substack_published_at=COALESCE(substack_published_at,?)
                     WHERE id=?
                    """,
                    (post_id, public_url, now, news_id),
                )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as exc:
        print(f"[_record_substack_evidence] ⚠️ DB evidence write failed: {exc}")
        return False


def _existing_substack_evidence(news_id: Optional[str]) -> Dict[str, Optional[str]]:
    if not news_id or not NEWS_DB_PATH.exists():
        return {"draft_id": None, "post_id": None, "public_url": None}
    try:
        conn = sqlite3.connect(str(NEWS_DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(news_items)")}
            names = {
                "draft_id": "substack_draft_id",
                "post_id": "substack_post_id",
                "public_url": "substack_post_url",
            }
            select = [
                f"{column} AS {alias}" if column in columns else f"NULL AS {alias}"
                for alias, column in names.items()
            ]
            row = conn.execute(
                f"SELECT {','.join(select)} FROM news_items WHERE id=?",
                (news_id,),
            ).fetchone()
            return dict(row) if row else {key: None for key in names}
        finally:
            conn.close()
    except Exception:
        return {"draft_id": None, "post_id": None, "public_url": None}


def _signal_density(text: str) -> float:
    """Zero-token signal: digits / % / $ per 100 chars (capped)."""
    if not text:
        return 0.0
    hits = sum(1 for c in text if c.isdigit() or c in "%$€£¥")
    return min(hits / max(len(text) / 100.0, 1.0), 10.0)


# Feeds whose items are especially valued as Substack source material.
_INSPIRATION_FEEDS = {
    "Good News Network", "Positive News", "Hacker News Front Page",
    "Ars Technica", "The Verge", "MIT Technology Review",
    "SEC Press Releases", "Federal Reserve Press", "Motley Fool",
}


def _score_pool_item(row, now) -> float:
    """Deterministic, zero-token score for ONE news_items row — the single scoring
    rule shared by BOTH morning and evening (2026-05-30). row columns:
    (id, title, clean_markdown, topic_category, source_type, feed_name,
     word_count, published_at)."""
    from datetime import datetime
    _id, _title, body, _topic, stype, feed, wc, pub = row
    score = 0.0
    if stype == "video":            # YouTube transcript
        score += 1.5
    if feed in _INSPIRATION_FEEDS:  # curated inspiration / first-hand feeds
        score += 1.0
    try:                            # freshness decay (~3-day scale)
        pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        hours = max((now - pub_dt).total_seconds() / 3600.0, 0.0)
        score += pow(2.718, -hours / 72.0)
    except Exception:
        pass
    score += _signal_density(body or "") * 0.15   # concrete-number density
    wc = wc or len(body or "")
    if 500 <= wc <= 4000:                          # word-count sweet spot
        score += 0.5
    if (_topic or "") in _TECH_TOPICS:             # 美/台科技商業加重（2026-06-20）
        score += _TECH_BOOST
    return score


def _last_used_feed_name(conn) -> Optional[str]:
    """Look up the feed_name of the most recently used news item, to
    enable source-diversity checks in _pick_top_from_pool."""
    used = _load_used()
    if not used:
        return None
    # "most recently used" = last appended = last element in iteration order
    last_id = list(used)[-1]
    try:
        row = conn.execute(
            "SELECT feed_name FROM news_items WHERE id = ?", (last_id,)
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _pick_top_from_pool(window_days: int, label: str) -> Optional[Tuple[str, str, str, str]]:
    """Score the recent harvested pool deterministically and return the top UNUSED
    item as (id, title, clean_markdown, topic_category), marking it used.

    2026-05-30: this is the SINGLE selection path for both Substack slots. Substack
    selection is now fully decoupled from the news_radar LLM scorer — it no longer
    reads `weighted_score`; every source is scored by `_score_pool_item` (script,
    zero token). morning takes the top unused item; evening (run later, sharing the
    used-set) takes the next.

    2026-06-18 (Hsin feedback): add source-diversity penalty. If the last-used item
    had the same feed_name as a candidate, penalize it by 2.0 so consecutive slots
    (morning → evening) don't both pick Motley Fool S&P ETF comparisons."""
    if not NEWS_DB_PATH.exists():
        print(f"[{label}] ⚠️ News DB not found at {NEWS_DB_PATH}")
        return None
    used = _load_used()
    conn = sqlite3.connect(str(NEWS_DB_PATH))
    try:
        rows = conn.execute(
            f"""
            SELECT id, title, clean_markdown, topic_category, source_type,
                   feed_name, word_count, published_at
            FROM news_items
            WHERE published_at >= datetime('now', '-{int(window_days)} days')
              AND status NOT IN ('dropped', 'filtered')
              AND COALESCE(feed_name,'') NOT IN ('user_submission','user_substack')
              AND clean_markdown IS NOT NULL AND LENGTH(clean_markdown) > 300
            ORDER BY published_at DESC
            LIMIT 300
            """
        ).fetchall()
        last_feed = _last_used_feed_name(conn)
    except Exception as exc:
        print(f"[{label}] ⚠️ query failed: {exc}")
        return None
    finally:
        conn.close()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if last_feed:
        print(f"[{label}] last-used feed = {last_feed!r}  (penalising -2.0 if same)")

    best, best_score = None, -1.0
    for r in rows:
        if r[0] in used:
            continue
        s = _score_pool_item(r, now)
        # Source-diversity penalty: if the last-used item was from the same feed,
        # subtract 2.0 so the next slot naturally picks something different.
        if last_feed and r[5] == last_feed:
            s -= 2.0
        if s > best_score:
            best_score, best = s, r
    if best is None:
        return None
    nid, title, body, topic, *_ = best
    _mark_used(nid)
    print(f"[{label}] ✅ id={nid[:10]} score={best_score:.2f} feed={best[5] if len(best) > 5 else '?'} title={title[:48]!r}")
    return (nid, title, body or "", topic or "other")


def pick_morning_news(news_id: Optional[str] = None) -> Optional[Tuple[str, str, str, str]]:
    """Type-A morning source. 2026-05-30: now uses the SAME deterministic scorer as
    evening (`_pick_top_from_pool`) — no more dependency on the news_radar LLM
    `weighted_score`. Window = **3 days** (早報偏時效新聞). `--news-id` still pins a
    specific row (no used-marking)."""
    if news_id:
        if not NEWS_DB_PATH.exists():
            print(f"[ERROR] News DB not found at {NEWS_DB_PATH}")
            return None
        conn = sqlite3.connect(str(NEWS_DB_PATH))
        try:
            row = conn.execute(
                "SELECT id, title, clean_markdown, topic_category "
                "FROM news_items WHERE id = ?",
                (news_id,),
            ).fetchone()
            return tuple(row) if row else None  # type: ignore[return-value]
        finally:
            conn.close()
    return _pick_top_from_pool(window_days=3, label="MorningPick")


def pick_evening_inspiration() -> Optional[Tuple[str, str, str, str]]:
    """Type-B evening source. Same deterministic scorer as morning, but a wider
    window = **7 days** (晚報不綁時效、可挖更深的選題). The shared used-set means it
    won't repeat morning's pick (morning runs first)."""
    return _pick_top_from_pool(window_days=7, label="EveningPick")


def flush_stale_podcast_candidates(window_days: int = 7) -> int:
    """Quarantine and remove stale Podcast-only candidates from active state.

    Historical Substack evidence and sources referenced by social drafts remain
    in ``news_items``. Only unused, unreferenced rows outside the active window
    move into a recoverable JSON payload table in the same canonical database.
    """
    if not NEWS_DB_PATH.exists():
        return 0
    conn = sqlite3.connect(str(NEWS_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        news_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(news_items)")
        }
        evidence_columns = (
            "substack_written_at",
            "substack_draft_id",
            "substack_drafted_at",
            "substack_post_id",
            "substack_post_url",
            "substack_published_at",
        )
        conditions = [
            "feed_name = 'YouTube Podcast'",
            "published_at < datetime('now', ?)",
        ]
        conditions.extend(
            f"{column} IS NULL"
            for column in evidence_columns
            if column in news_columns
        )
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "drafts" in tables:
            draft_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(drafts)")
            }
            if "news_id" in draft_columns:
                conditions.append(
                    "NOT EXISTS (SELECT 1 FROM drafts d WHERE d.news_id = news_items.id)"
                )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS substack_podcast_quarantine(
              source_id TEXT PRIMARY KEY,
              quarantined_at TEXT NOT NULL,
              reason TEXT NOT NULL,
              source_payload TEXT NOT NULL
            )
            """
        )
        rows = conn.execute(
            "SELECT * FROM news_items WHERE " + " AND ".join(conditions),
            (f"-{int(window_days)} days",),
        ).fetchall()
        if not rows:
            conn.commit()
            return 0

        quarantined_at = datetime.now(timezone.utc).isoformat()
        for row in rows:
            payload = json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
            conn.execute(
                """
                INSERT OR REPLACE INTO substack_podcast_quarantine(
                  source_id,quarantined_at,reason,source_payload
                ) VALUES(?,?,?,?)
                """,
                (
                    row["id"],
                    quarantined_at,
                    f"outside_{int(window_days)}_day_window",
                    payload,
                ),
            )
        conn.executemany(
            "DELETE FROM news_items WHERE id = ?",
            [(row["id"],) for row in rows],
        )
        conn.commit()
        print(
            f"[PodcastFlush] quarantined {len(rows)} unused source(s) "
            f"outside {int(window_days)} days"
        )
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def pick_podcast_interview(window_days: int = 7) -> Optional[Tuple[str, str, str, str]]:
    """Podcast source for the daily noon batch. Draws ONLY from the dedicated podcast
    pool (feed_name='YouTube Podcast'), preferring the longest fresh, unused
    interview — length is the best proxy for a substantive Q&A episode (vs. a clip).
    The seven-day window stays fresh while two daily slots provide enough
    throughput. Shares the used-set so the batch cannot select one episode twice."""
    if not NEWS_DB_PATH.exists():
        print(f"[PodcastPick] ⚠️ News DB not found at {NEWS_DB_PATH}")
        return None
    try:
        flush_stale_podcast_candidates(window_days=window_days)
    except Exception as exc:
        print(f"[PodcastFlush] failed closed: {exc}")
        return None
    used = _load_used()
    conn = sqlite3.connect(str(NEWS_DB_PATH))
    try:
        rows = conn.execute(
            f"""
            SELECT id, title, clean_markdown, topic_category, word_count, published_at
            FROM news_items
            WHERE feed_name = 'YouTube Podcast'
              AND source_type = 'video'
              AND published_at >= datetime('now', '-{int(window_days)} days')
              AND status NOT IN ('dropped', 'filtered')
              AND clean_markdown IS NOT NULL AND LENGTH(clean_markdown) > 3000
            ORDER BY published_at DESC
            LIMIT 200
            """
        ).fetchall()
    except Exception as exc:
        print(f"[PodcastPick] ⚠️ query failed: {exc}")
        return None
    finally:
        conn.close()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    best, best_score = None, -1.0
    for r in rows:
        nid, title, body, topic, wc, pub = r
        if nid in used:
            continue
        score = min((wc or len(body or "")) / 40000.0, 2.5)  # longer interview = better, capped
        try:                                                  # mild freshness nudge (~7-day scale)
            pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            hours = max((now - pub_dt).total_seconds() / 3600.0, 0.0)
            score += pow(2.718, -hours / 168.0)
        except Exception:
            pass
        if score > best_score:
            best_score, best = score, r
    if best is None:
        print("[PodcastPick] ⚠️ no unused podcast interview in pool "
              f"(last {window_days}d). Has the noon harvest run?")
        return None
    nid, title, body, topic, *_ = best
    _mark_used(nid)
    print(f"[PodcastPick] ✅ id={nid[:10]} score={best_score:.2f} "
          f"len={len(body or '')} title={title[:48]!r}")
    return (nid, title, body or "", topic or "other")


def pick_evening_topic(override_topic: Optional[str] = None) -> Tuple[str, str, str]:
    """Return (title, content_seed, topic_category) for type-B evening slot.

    File format (`config/substack_evening_topics.yaml`):

        topics:
          - title: "為什麼 AI 訓練成本指數下降而商業價值卻指數上升"
            seed: |
              論文出處：...
              核心觀察：...
              想挑戰的常識：...
            topic_category: ai_model

    Cycles through topics in order, marking used ones (cursor file).
    Fallback: if topic file missing, returns a placeholder asking user to add topics.
    """
    if override_topic:
        return (
            override_topic,
            (
                f"使用者指定的選題：{override_topic}\n"
                "先說清楚它回應哪個現實問題，再用具體證據拆機制、處理反方，"
                "最後提出一個讀者能真正回信回答的問題。"
            ),
            "other",
        )

    if not EVENING_TOPICS_PATH.exists():
        return (
            "TODO: 補充 evening 選題池",
            (
                f"請於 {EVENING_TOPICS_PATH} 補上 evening 選題（書／Podcast／概念）。"
                f"目前先用一個泛用的『高 agency 工作者該如何分配注意力』作為占位主題。"
            ),
            "other",
        )

    # 簡易 YAML 解析（避免引入 pyyaml 額外依賴；topic 結構固定）
    text = EVENING_TOPICS_PATH.read_text(encoding="utf-8")
    topics = _parse_yaml_topics(text)
    if not topics:
        return (
            "TODO: 補充 evening 選題池",
            "evening_topics.yaml 為空，請補充。",
            "other",
        )

    # Round-robin via cursor file
    cursor_path = EVENING_TOPICS_PATH.with_suffix(".cursor")
    cursor = 0
    if cursor_path.exists():
        try:
            cursor = int(cursor_path.read_text().strip())
        except ValueError:
            cursor = 0
    pick = topics[cursor % len(topics)]
    cursor_path.write_text(str((cursor + 1) % len(topics)))
    return (
        pick.get("title", ""),
        pick.get("seed", ""),
        pick.get("topic_category", "other"),
    )


def _parse_yaml_topics(text: str) -> List[Dict[str, str]]:
    """Minimal YAML topic-list parser (avoid pyyaml dep).

    Supports format:

        topics:
          - title: "..."
            seed: |
              line1
              line2
            topic_category: ai_model
    """
    topics: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None
    seed_lines: Optional[List[str]] = None
    seed_indent: Optional[int] = None

    for raw in text.splitlines():
        # Blank lines: inside a seed block they become empty seed lines;
        # outside they are skipped.
        if not raw.strip():
            if seed_lines is not None:
                seed_lines.append("")
            continue
        # Full-line comments only outside seed blocks; comments inside a
        # multi-line string are content, not metadata.
        if seed_lines is None and raw.lstrip().startswith("#"):
            continue
        if raw.strip() == "topics:":
            continue
        stripped = raw.lstrip()
        indent = len(raw) - len(stripped)

        # If we're collecting a seed block and this line is INDENTED deeper
        # than the `seed: |` declaration → it's part of the seed body.
        if seed_lines is not None and seed_indent is not None and indent > seed_indent:
            # Preserve relative indentation by stripping only the base
            # indent (seed_indent + 2 spaces for the YAML block-scalar
            # convention; if line has less, just lstrip).
            content = raw[seed_indent + 2 :] if len(raw) > seed_indent + 2 else stripped
            seed_lines.append(content)
            continue

        # Otherwise: indentation dropped → finalize seed block.
        if seed_lines is not None and seed_indent is not None and indent <= seed_indent:
            if current is not None:
                current["seed"] = "\n".join(seed_lines).rstrip()
            seed_lines = None
            seed_indent = None

        if stripped.startswith("- "):
            if current:
                topics.append(current)
            current = {}
            stripped = stripped[2:]
        if ":" in stripped and current is not None:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "|":
                seed_lines = []
                seed_indent = indent
                continue
            current[key] = val.strip("\"'")
    if seed_lines is not None and current is not None:
        current["seed"] = "\n".join(seed_lines).rstrip()
    if current:
        topics.append(current)
    return topics


def _resolve_company_ticker(args) -> str:
    """company mode ticker：--ticker > .company_next（信哥從候選挑的）> watchlist top（永不卡稿）。"""
    if getattr(args, "ticker", None):
        return args.ticker.strip()
    if COMPANY_NEXT_PATH.exists():
        for line in COMPANY_NEXT_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line.split()[0]
    try:
        import yaml
        wl = yaml.safe_load(COMPANY_WATCHLIST_PATH.read_text(encoding="utf-8")) or {}
        for t in (wl.get("watchlist") or []):
            return str(t["ticker"] if isinstance(t, dict) else t)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Cover rendering — reuse cover_renderer with new "substack" spec
# ---------------------------------------------------------------------------

# Footer is deterministic and intentionally outside the writer prompt.
BRAND_TAGLINE = "「把複雜世界寫成人話，保留真正值得你判斷的部分。」"


def build_footer_block() -> str:
    """Brand promise + native Subscribe placeholder, added deterministically."""
    return (
        "\n\n---\n\n"
        f"> **{BRAND_TAGLINE}**\n"
        "> \n"
        "> 📅 每天兩篇對談延伸 · 每週一篇公司深拆\n"
        "> ✉️ 你可以直接回信，告訴我哪個判斷值得再追\n\n"
        "*點此訂閱 → 不錯過下一篇拆解。*\n"
    )


def append_footer_block(*, article_md_path: Path) -> None:
    """Append the reader-facing brand promise and subscription CTA."""
    block = build_footer_block()
    existing = article_md_path.read_text(encoding="utf-8")
    cleaned = strip_production_instructions(existing.rstrip() + block)
    assert_reader_ready_markdown(cleaned)
    article_md_path.write_text(cleaned + "\n", encoding="utf-8")
    print("[Footer] ✅ reader-facing tagline + subscribe CTA appended")


def render_substack_cover(
    *,
    title: str,
    subtitle: str,
    topic_category: str,
    output_dir: Path,
    source_image_path: Optional[Path] = None,
    character: Optional[str] = None,
    expression: Optional[str] = None,
    mode: Optional[str] = None,
) -> Optional[Path]:
    """Render the 1456×816 Substack hero cover. Returns saved PNG path or None.

    Cover System (2026-06-21) cascade when ``source_image_path`` is None:
      route 3 → ``character_cover`` composites the locked IP character (瑞瑞/達達)
                onto a cream canvas + overlays the title — used when a character
                asset exists in config/cover_ip/assets/.
      route 2 → ``promise_cover`` typographic poster (cream + ink + one accent) —
                the fallback when no character asset is available yet.
    Pass ``source_image_path`` only for the legacy blurred-photo cover.
    """
    if source_image_path is None:
        # Route 3: character cover (returns None if no asset → fall through).
        try:
            from substack_radar.character_cover import render_character_cover

            p = render_character_cover(
                title=title,
                subtitle=subtitle,
                topic_category=topic_category or "other",
                character=character,
                output_dir=output_dir,
                expression=expression,
                mode=mode,
            )
            if p is not None:
                print(f"[Cover] ✅ character cover ({character or 'auto'}) → {p.name}")
                return p
            print("[Cover] ℹ️ no character asset yet → promise_cover poster fallback.")
        except Exception as exc:
            print(f"[Cover] ⚠️ character_cover failed ({exc}); using promise_cover.")
        # Route 2: typographic poster fallback.
        try:
            from substack_radar.promise_cover import render_promise_cover

            return render_promise_cover(
                title=title,
                subtitle=subtitle,
                topic_category=topic_category or "other",
                output_dir=output_dir,
            )
        except Exception as exc:
            print(f"[Cover] ⚠️ promise_cover failed ({exc}); falling back to legacy cover.")

    try:
        from PIL import Image
        from src.cover_renderer import CoverInput, render_cover
    except Exception as exc:
        print(f"[Cover] ⚠️ Cannot import cover_renderer: {exc}")
        return None

    # If no source image, synthesize a flat fallback (cover_pipeline does this for
    # social posts; we replicate the simplest variant here to avoid coupling).
    img_path = source_image_path
    if img_path is None or not img_path.exists():
        # Synthesize a neutral 1456×816 base (deep navy with noise).
        synth = output_dir / "_synth_bg.png"
        synth.parent.mkdir(parents=True, exist_ok=True)
        try:
            import random

            random.seed(42)
            base = Image.new("RGB", (1456, 816), (12, 16, 30))
            px = base.load()
            for y in range(0, 816, 4):
                for x in range(0, 1456, 4):
                    n = random.randint(-6, 6)
                    px[x, y] = (12 + n, 16 + n, 30 + n)
            base.save(synth, "PNG")
            img_path = synth
        except Exception as exc:
            print(f"[Cover] ⚠️ Fallback synth failed: {exc}")
            return None

    inp = CoverInput(
        image_path=img_path,
        title=title,
        subtitle=subtitle,
        topic_category=topic_category or "other",
        brand_name="主力爸爸我錯了 · Substack",
        date_str=date.today().isoformat(),
    )
    try:
        out = render_cover(inp, "substack", output_dir=output_dir)  # type: ignore[arg-type]
        # Rename to canonical cover.png; remove intermediates so the
        # final folder only contains user-facing artifacts.
        canonical = output_dir / "cover.png"
        if out != canonical:
            shutil.copyfile(out, canonical)
            try:
                out.unlink()
            except OSError:
                pass
        # Clean the synth background if it was created by us.
        synth = output_dir / "_synth_bg.png"
        if synth.exists():
            try:
                synth.unlink()
            except OSError:
                pass
        return canonical
    except Exception as exc:
        print(f"[Cover] ⚠️ render_cover failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Substack draft push via python-substack (Phase 1 semi-automation)
# ---------------------------------------------------------------------------
# Why this instead of email-to-draft: Substack has no email-to-draft feature
# nor a public Write API. The community-maintained ma2za/python-substack
# library (v0.1.18, 2026-03-07) reverse-engineers Substack's internal post
# endpoints using cookie auth. Risk: unofficial, cookies expire 2-4 weeks,
# Substack could break/detect it. We treat this as opt-in (SUBSTACK_AUTO_DRAFT=1).
# The OneDrive paste path is the always-available fallback.

def push_to_substack_draft(
    *,
    article_md_path: Path,
    title: str,
    subtitle: str,
    cover_path: Optional[Path] = None,
    source_id: Optional[str] = None,
    audience: str = DEFAULT_SUBSTACK_AUDIENCE,
) -> Optional[int]:
    """Create a Substack draft and return its id; never publishes it.

    Skipped (returns ``None``) when:
      - SUBSTACK_AUTO_DRAFT is not "1"
      - python-substack not installed
      - Required env vars not set
      - Any API/auth failure (logged, doesn't raise)

    Required env vars (when SUBSTACK_AUTO_DRAFT=1):
      - SUBSTACK_COOKIES_STRING : full cookie header string copied from Chrome
                                  DevTools after logging in to substack.com.
                                  See README setup for the click-by-click guide.
      - SUBSTACK_PUBLICATION_URL : e.g. https://hsin73.substack.com

    Delivery defaults to ``everyone``. Paid or other restricted delivery must
    be supplied as an explicit function argument; ambient environment values
    are deliberately ignored.
    """
    if os.getenv("SUBSTACK_AUTO_DRAFT") != "1":
        print(
            "[Substack] ℹ️ SUBSTACK_AUTO_DRAFT != '1'; skipping draft push. "
            "Open Article_Substack.md and paste manually."
        )
        return None

    cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    pub_url = os.getenv("SUBSTACK_PUBLICATION_URL")
    if not (cookies and pub_url):
        print(
            "[Substack] ⚠️ SUBSTACK_AUTO_DRAFT=1 but SUBSTACK_COOKIES_STRING or "
            "SUBSTACK_PUBLICATION_URL missing; skipping. Article on disk."
        )
        return None

    try:
        from substack import Api  # type: ignore
        from substack.post import Post  # type: ignore
    except ImportError as exc:
        print(
            f"[Substack] ⚠️ python-substack not installed ({exc}); "
            "run `pip install python-substack`. Skipping draft push."
        )
        return None

    audience = validate_substack_audience(audience)
    body_md = article_md_path.read_text(encoding="utf-8")

    # Article_Substack.md starts with "# <title>\n\n*<subtitle>*\n\n<body>".
    # python-substack's Post object takes title/subtitle as separate fields,
    # so we strip them out of the markdown before from_markdown() ingests it.
    # Without this, the draft would have the title duplicated as an H1 at the top.
    body_md = _strip_title_subtitle_lines(body_md, title=title, subtitle=subtitle)
    body_md = strip_production_instructions(body_md)
    assert_reader_ready_markdown(body_md)

    try:
        api = Api(cookies_string=cookies, publication_url=pub_url)
        user_id = api.get_user_id()
        post = Post(
            title=title,
            subtitle=subtitle,
            user_id=user_id,
            audience=audience,
        )
        post.from_markdown(body_md, api=api)

        # Optional: upload cover and prepend as captionedImage.  This function
        # only creates the durable draft.  The caller may separately continue
        # through explicit one-off publish-now after the draft receipt exists.
        if cover_path and cover_path.exists():
            try:
                image = api.get_image(str(cover_path))
                # Insert cover at index 0 (top of body)
                post.add({"type": "captionedImage", "src": image.get("url")})
            except Exception as exc:
                print(f"[Substack] ⚠️ Cover upload failed (continuing without): {exc}")

        draft = api.post_draft(post.get_draft())
        draft_id = draft.get("id") if isinstance(draft, dict) else None
        if draft_id is None:
            print("[Substack] ❌ post_draft returned no draft id; remote creation is unproven")
            return None
        if source_id:
            try:
                store_remote_receipt(source_id, draft_id)
            except Exception as exc:
                print(
                    "[Substack] 🛑 remote draft exists but durable receipt write failed: "
                    f"source={source_id} draft_id={draft_id} error={exc}"
                )
        print(
            f"[Substack] ✅ Draft created. id={draft_id!s} "
            f"audience={audience} cover={'yes' if cover_path else 'no'}"
        )
        # Return draft_id (int) on success, None on failure, so caller can
        # build the public URL for notify email.
        return draft_id
    except Exception as exc:
        print(
            f"[Substack] ❌ Draft push failed: {type(exc).__name__}: {exc}\n"
            f"    Common causes:\n"
            f"    - Cookie expired (re-copy from Chrome DevTools)\n"
            f"    - SUBSTACK_PUBLICATION_URL wrong (must be your-name.substack.com)\n"
            f"    - python-substack version mismatch with Substack backend\n"
            f"    Article still saved on disk; paste manually from OneDrive."
        )
        return None


def _iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _published_identity(
    payload: Any,
    *,
    draft_id: int | str,
    publication_url: str,
) -> Optional[Dict[str, str]]:
    expected = str(draft_id)
    for row in _iter_dicts(payload):
        raw_id = row.get("id") or row.get("post_id") or row.get("draft_id")
        if raw_id is not None and str(raw_id) != expected:
            continue
        public_url = next(
            (
                str(row.get(key) or "").strip()
                for key in ("canonical_url", "public_url", "post_url", "url")
                if str(row.get(key) or "").startswith("https://")
            ),
            "",
        )
        slug = str(row.get("slug") or "").strip().strip("/")
        if not public_url and slug:
            public_url = f"{publication_url.rstrip('/')}/p/{slug}"
        if public_url and (
            raw_id is not None
            or slug
            or "/p/" in urlparse(public_url).path
        ):
            return {"post_id": str(raw_id or expected), "public_url": public_url}
    return None


def _public_url_is_live(public_url: str) -> bool:
    parsed = urlparse(public_url)
    if parsed.scheme != "https" or not parsed.netloc or "/publish/" in parsed.path:
        return False
    try:
        request = Request(
            public_url,
            headers={"User-Agent": "news-radar-publication-readback/1.0"},
        )
        with urlopen(request, timeout=30) as response:  # no Substack cookies
            status = int(getattr(response, "status", 0) or response.getcode())
            final_url = str(response.geturl())
        return 200 <= status < 400 and "/publish/" not in urlparse(final_url).path
    except Exception as exc:
        print(f"[Substack] ⚠️ public URL readback failed: {type(exc).__name__}: {exc}")
        return False


def publish_substack_draft(
    draft_id: int | str,
    *,
    source_id: str,
) -> Optional[Dict[str, str]]:
    """Publish one existing draft and return only publicly read-back evidence.

    A durable intent is written immediately before ``publish_draft``.  If the
    process then crashes or the response is ambiguous, later runs query the
    published-post endpoint but never blindly resend the newsletter request.
    """
    cookies = os.getenv("SUBSTACK_COOKIES_STRING")
    publication_url = os.getenv("SUBSTACK_PUBLICATION_URL")
    if not cookies or not publication_url:
        print("[Substack] ❌ publish-now requires cookies and publication URL")
        return None
    try:
        from substack import Api  # type: ignore
    except ImportError as exc:
        print(f"[Substack] ❌ python-substack not installed: {exc}")
        return None

    try:
        api = Api(cookies_string=cookies, publication_url=publication_url)
        receipt = get_remote_receipt(source_id) or {}
        if receipt.get("draft_id") and receipt["draft_id"] != str(draft_id):
            raise ValueError("publish receipt points to a different Substack draft")

        existing = _published_identity(
            receipt,
            draft_id=draft_id,
            publication_url=publication_url,
        )
        if existing and _public_url_is_live(existing["public_url"]):
            return existing

        # A draft may already have been published manually or by a previous
        # process that crashed before saving its intent.  Read back first so we
        # never resend an already-delivered newsletter.
        try:
            already_published = api.get_published_posts(
                offset=0,
                limit=25,
                order_by="post_date",
                order_direction="desc",
            )
            existing = _published_identity(
                already_published,
                draft_id=draft_id,
                publication_url=publication_url,
            )
        except Exception:
            existing = None
        if existing and _public_url_is_live(existing["public_url"]):
            store_publication_receipt(
                source_id,
                draft_id,
                existing["post_id"],
                existing["public_url"],
            )
            return existing

        payload: Any = None
        if receipt.get("publish_attempted_at"):
            print("[Substack] previous publish result ambiguous; readback only, no resend")
        else:
            api.prepublish_draft(draft_id)
            store_publish_intent(source_id, draft_id)
            send = os.getenv("SUBSTACK_PUBLISH_SEND", "1") != "0"
            payload = api.publish_draft(
                draft_id,
                send=send,
                share_automatically=False,
            )

        identity = _published_identity(
            payload,
            draft_id=draft_id,
            publication_url=publication_url,
        )
        if not identity:
            published = api.get_published_posts(
                offset=0,
                limit=25,
                order_by="post_date",
                order_direction="desc",
            )
            identity = _published_identity(
                published,
                draft_id=draft_id,
                publication_url=publication_url,
            )
        if not identity or not _public_url_is_live(identity["public_url"]):
            print(
                f"[Substack] ⚠️ draft {draft_id} exists but public publication "
                "readback is not proven"
            )
            return None
        store_publication_receipt(
            source_id,
            draft_id,
            identity["post_id"],
            identity["public_url"],
        )
        print(
            f"[Substack] ✅ Published. post_id={identity['post_id']} "
            f"url={identity['public_url']}"
        )
        return identity
    except Exception as exc:
        print(f"[Substack] ⚠️ publish-now failed or is ambiguous: {type(exc).__name__}: {exc}")
        return None


def _strip_title_subtitle_lines(md: str, *, title: str, subtitle: str) -> str:
    """Strip the `# title` and `*subtitle*` lines we wrote in
    write_article_substack_md so python-substack's from_markdown doesn't
    duplicate them as body content."""
    lines = md.splitlines()
    out: List[str] = []
    skipped_title = False
    skipped_subtitle = False
    for line in lines:
        s = line.strip()
        if not skipped_title and s == f"# {title}":
            skipped_title = True
            continue
        if not skipped_subtitle and s == f"*{subtitle}*":
            skipped_subtitle = True
            continue
        # Skip the blank lines right after the metadata pair
        if (skipped_title or skipped_subtitle) and not out and s == "":
            continue
        out.append(line)
    return "\n".join(out).lstrip()


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def write_article_substack_md(out_dir: Path, draft: SubstackDraft, sources_block: str = "") -> Path:
    """Write the reader-ready version that goes straight into Substack.

    Public source links and the actual provider/model remain reader-facing.
    Image-search prompts and authoring instructions stay out of this file.
    """
    path = out_dir / "Article_Substack.md"
    body = strip_generated_footer(strip_production_instructions(draft.body_markdown))
    provenance = (getattr(draft, "generated_by", None) or "").strip()
    provenance_block = (
        f"> 🧠 **產文路線**：{provenance}\n\n" if provenance else ""
    )
    md = (
        f"{provenance_block}"
        f"# {draft.title}\n\n"
        f"*{draft.subtitle}*\n\n"
        f"{body}\n\n"
        f"{sources_block}"
    )
    md = strip_production_instructions(md)
    assert_reader_ready_markdown(md)
    path.write_text(md, encoding="utf-8")
    return path


def write_article_full_md(
    out_dir: Path,
    draft: SubstackDraft,
    *,
    mode: str,
    editorial_profile: str,
    source: Dict[str, Any],
    audit_warnings: List[str],
) -> Path:
    """The FULL metadata version. For Hsin's review."""
    path = out_dir / "Article_Full.md"
    warning_block = (
        "\n".join(f"- ⚠️ {w}" for w in audit_warnings)
        if audit_warnings
        else "- ✅ 沒有命中黑名單"
    )
    md = f"""# {draft.title}

**Subtitle:** {draft.subtitle}

**🧠 產文路線 / Generated by:** `{getattr(draft, "generated_by", None) or "unknown"}`

**Mode:** `{mode}`  |  **Editorial profile:** `{editorial_profile}`

---

## 原料來源 (Source)

```
title: {source.get("title")}
topic_category: {source.get("topic_category")}
id: {source.get("id", "n/a")}
```

## Audit warnings (自我體檢)

{warning_block}

---

## 本文

{strip_production_instructions(draft.body_markdown)}
"""
    path.write_text(md, encoding="utf-8")
    return path


def write_metadata(
    out_dir: Path,
    draft: SubstackDraft,
    mode: str,
    editorial_profile: str,
    source: Dict[str, Any],
    audit_warnings: List[str],
) -> None:
    metadata = {
        "title": draft.title,
        "subtitle": draft.subtitle,
        "mode": mode,
        "editorial_profile": editorial_profile,
        "editorial_kind": source.get("editorial_kind", editorial_profile),
        "generated_by": getattr(draft, "generated_by", None),
        "source": source,
        "audit_warnings": audit_warnings,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def mirror_to_onedrive(local_dir: Path, mirror_dir: Path) -> bool:
    """Copy entire draft folder to OneDrive autogen path. Skip if OneDrive
    not mounted (CI / sandbox)."""
    if not ONEDRIVE_BASE.exists() and not mirror_dir.parent.parent.exists():
        print(f"[OneDrive] ℹ️ Base not mounted at {ONEDRIVE_BASE}; skipping mirror.")
        return False
    try:
        mirror_dir.mkdir(parents=True, exist_ok=True)
        for child in local_dir.iterdir():
            target = mirror_dir / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)
        print(f"[OneDrive] ✅ Mirrored to {mirror_dir}")
        return True
    except Exception as exc:
        print(f"[OneDrive] ❌ Mirror failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _load_bundle_curated(path: str) -> str:
    """深度素材包精華：取『完整逐字稿素材』標題之前的全部內容（重點參考資料＋各源關鍵
    數據與要角＋對應書面深度報告）。逐字稿全文常達數萬字、不塞進 prompt；高訊號的含數據句／
    要角／書面報告才是寫手對焦要的。讀不到就回空字串，讓 compose 照舊用原始素材。"""
    try:
        p = Path(path)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8")
        marker = "## 完整逐字稿素材"
        return (text.split(marker, 1)[0].strip() if marker in text else text.strip())
    except Exception:
        return ""


def _sources_block(*, source_title=None, source_url=None, reports=None) -> str:
    """Reader-facing source ledger, placed after the article to reduce front-load."""
    reports = reports or []
    rep_links, seen = [], set()
    for r in reports:
        u = (r.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        rep_links.append(f"[{(r.get('title') or u)[:80]}]({u})")
    if not source_url and not rep_links:
        return ""
    block = ["---", "", "### 📚 本文取材", "", "公開來源，可點擊查證。"]
    if source_url:
        block.append(f"- **主來源**：[{(source_title or '原始來源')[:80]}]({source_url})")
    if rep_links:
        block.extend(("", f"**延伸研究**（{len(rep_links)} 源）"))
        block.extend(f"{index}. {link}" for index, link in enumerate(rep_links, 1))
    return "\n".join(block) + "\n\n"


async def run(args: argparse.Namespace) -> int:
    """Outer wrapper catches any unexpected failure and routes to notify.

    Normal flow returns 0 on success, non-zero exit codes on specific
    failure modes (LLM, source-pick, etc.). Each documented failure
    point notifies before returning.
    """
    try:
        return await _run_inner(args)
    except Exception as exc:
        import traceback as _tb
        tb_short = "\n".join(_tb.format_exc().splitlines()[-30:])
        try:
            from src.notify import notify_substack_failure
            notify_substack_failure(
                mode=args.mode,
                error_msg=f"{type(exc).__name__}: {exc}",
                traceback_short=tb_short,
                extra_context={"unexpected_exception": True},
            )
        except Exception as notify_exc:
            print(f"[notify] ❌ failure-path notify itself failed: {notify_exc}")
        raise


# ---------------------------------------------------------------------------
# Inline Image Generation (2026-06-01 Hsin directive)
# ---------------------------------------------------------------------------

async def _run_inner(args: argparse.Namespace) -> int:
    today = date.today().isoformat()
    mode: str = args.mode

    remote_required = bool(
        getattr(args, "require_substack_draft", False)
        or getattr(args, "publish_now", False)
    )
    if remote_required:
        missing = []
        if args.no_draft:
            missing.append("--no-draft conflicts with --require-substack-draft")
        if os.getenv("SUBSTACK_AUTO_DRAFT") != "1":
            missing.append("SUBSTACK_AUTO_DRAFT=1")
        if not os.getenv("SUBSTACK_COOKIES_STRING"):
            missing.append("SUBSTACK_COOKIES_STRING")
        if not os.getenv("SUBSTACK_PUBLICATION_URL"):
            missing.append("SUBSTACK_PUBLICATION_URL")
        if missing:
            print(
                "[Substack] ❌ remote Substack write is required but preflight failed: "
                + ", ".join(missing)
            )
            return 5

    # Import notify lazily so missing src/notify.py (unlikely) doesn't break the
    # pipeline. Wrap every notify call in try/except inside the helper itself.
    try:
        from src.notify import notify_substack_failure, notify_substack_success
    except Exception:
        notify_substack_failure = lambda **kw: None  # type: ignore[assignment]
        notify_substack_success = lambda **kw: None  # type: ignore[assignment]

    # 0) Optional token-free inspiration harvest (--harvest). launchd jobs pass it
    # so each slot writes from freshly-harvested material instead of paid web
    # research. Never fatal — a stale pool is better than no draft.
    if getattr(args, "harvest", False):
        try:
            if mode == "podcast":
                # podcast slot harvests its OWN pool (long-form interview channels)
                # into feed_name='YouTube Podcast'; no RSS / general-YT needed here.
                from substack_radar.youtube_transcripts import (
                    harvest_youtube_transcripts,
                    PODCAST_SOURCES_PATH,
                )

                await asyncio.to_thread(
                    harvest_youtube_transcripts,
                    sources_path=PODCAST_SOURCES_PATH,
                    feed_name="YouTube Podcast",
                    tags=["youtube", "video", "podcast"],
                    min_chars=3000,        # an interview, not a clip
                    global_budget_s=420,   # a touch more room for 9 long episodes
                )
            else:
                from substack_radar.harvest_inspiration import _run as _harvest_run
                import argparse as _ap

                await _harvest_run(_ap.Namespace(no_youtube=args.no_youtube, dry_run=False))
        except Exception as exc:
            print(f"[Harvest] ⚠️ pre-compose harvest failed (continuing): {exc}")

    # 1) Pick source
    # --source-file works for BOTH morning & evening (2026-05-30): write from a
    # first-hand document (e.g. an earnings-call transcript) regardless of slot.
    # This is the recommended path for "巨人之聲"-style first-hand深度文.
    if args.source_file:
        sf = Path(args.source_file).expanduser().resolve()
        if not sf.exists():
            print(f"[ERROR] --source-file not found: {sf}")
            return 4
        raw_content = sf.read_text(encoding="utf-8")
        # Title resolution: --topic > first markdown H1 > filename stem.
        if args.topic:
            raw_title = args.topic
        else:
            first_h1 = next(
                (ln[2:].strip() for ln in raw_content.splitlines() if ln.startswith("# ")),
                None,
            )
            raw_title = first_h1 or sf.stem.replace("_", " ").replace("-", " ")
        topic_category = args.topic_category or "other"
        source = {
            "title": raw_title,
            "topic_category": topic_category,
            "source_file": str(sf),
            "source_bytes": sf.stat().st_size,
        }
        print(f"[Source] --source-file {sf.name} ({sf.stat().st_size} bytes) [mode={mode}]")
    elif mode == "morning":
        # morning: top item from the deterministic pool scorer (same as evening),
        # or --news-id to pin a specific row.
        pick = pick_morning_news(news_id=args.news_id)
        if pick is None:
            print("[ERROR] No suitable morning source in the harvested pool (last 3 days).")
            notify_substack_failure(
                mode=mode,
                error_msg="No suitable morning source in harvested pool (last 3 days)",
                extra_context={
                    "likely_cause": "inspiration pool empty — harvester hasn't run, or all recent items already used",
                    "fix_hint": ".venv/bin/python tools/substack_harvest_inspiration.py  (or run compose with --harvest)",
                },
            )
            return 2
        news_id, raw_title, raw_content, topic_category = pick
        source = {
            "id": news_id,
            "title": raw_title,
            "topic_category": topic_category,
        }
    elif mode == "podcast":
        # podcast: longest fresh, unused interview from the dedicated pool.
        pick = pick_podcast_interview()
        if pick is None:
            print("[ERROR] No suitable podcast interview in the harvested pool (last 7 days).")
            notify_substack_failure(
                mode=mode,
                error_msg="No suitable podcast interview in pool (last 7 days)",
                extra_context={
                    "likely_cause": "podcast pool empty — noon harvest hasn't run, all episodes used, or none had subtitles",
                    "fix_hint": ".venv/bin/python -m substack_radar.youtube_transcripts (podcast sources)",
                },
            )
            return 2
        news_id, raw_title, raw_content, topic_category = pick
        source = {
            "id": news_id,
            "title": raw_title,
            "topic_category": topic_category,
            "via": "podcast_pool",
        }
    elif mode == "company":
        # 每週公司營運分析（週日 09:00）。第一階段只讀 yfinance
        # 財報事實；消化後再依證據缺口搜 5–10 個延伸來源。
        ticker = _resolve_company_ticker(args)
        if not ticker:
            print("[ERROR] company mode 需要 ticker（--ticker / .company_next / watchlist）。")
            return 2
        from substack_radar.company_financials import fetch_financials
        print(f"[Company] {ticker} → 先抓 yfinance 財報事實…")
        fin_data, fin_md = fetch_financials(ticker)
        raw_title = fin_data.get("name") or ticker          # 占位；composer 依 editorial brief 生標題
        topic_category = "tw_stocks" if ".TW" in ticker.upper() else "us_stocks"
        raw_content = fin_md
        source = {
            "id": None,
            "title": raw_title,
            "topic_category": topic_category,
            "via": f"company:{ticker}",
            "ticker": ticker,
        }
    else:
        # Evening (no --source-file): harvested inspiration pool (token-free,
        # ranked) → yaml round-robin fallback. --topic override always wins.
        insp = pick_evening_inspiration() if not args.topic else None
        if insp is not None:
            news_id, raw_title, raw_content, topic_category = insp
            if args.topic_category:
                topic_category = args.topic_category
            source = {
                "id": news_id,
                "title": raw_title,
                "topic_category": topic_category,
                "via": "inspiration_pool",
            }
        else:
            raw_title, raw_content, topic_category = pick_evening_topic(args.topic)
            if args.topic_category:
                topic_category = args.topic_category  # explicit override wins
            source = {
                "title": raw_title,
                "topic_category": topic_category,
            }
    print(f"[Source] mode={mode} title={raw_title!r} topic={topic_category}")
    primary_content = raw_content or ""

    # 來源區塊用：主來源網址 + 引用到的書面報告（給 Article_Substack.md 最上面那塊）。
    used_reports: list = []
    source_url = None
    if mode == "company":
        # 公開、可點擊的主來源。實際取值仍由 yfinance client 完成。
        ticker_for_url = source.get("ticker") or ""
        source_url = f"https://finance.yahoo.com/quote/{ticker_for_url}/financials/"
    _sid = source.get("id")
    if _sid:
        try:
            import sqlite3 as _sq
            _cx = _sq.connect(str(NEWS_DB_PATH))
            _row = _cx.execute("SELECT url FROM news_items WHERE id=?", (_sid,)).fetchone()
            _cx.close()
            source_url = _row[0] if _row and _row[0] else None
        except Exception:
            source_url = None

    # 1b) 深度素材包：有 --bundle 就把其精華疊進素材並自動升到 Weekly profile。
    if getattr(args, "bundle", None):
        bundle_extra = _load_bundle_curated(args.bundle)
        if bundle_extra:
            if mode not in {"podcast", "company"}:
                raw_content = (raw_content or "") + (
                    "\n\n===== 深度素材包（多源綜合用）=====\n"
                    + bundle_extra
                )
                print(f"[Bundle] 疊入深度素材包 {Path(args.bundle).name}（+{len(bundle_extra):,} 字元）")
            else:
                print(
                    f"[Bundle] {Path(args.bundle).name} 僅當延伸來源種子；"
                    "階段一仍只消化當次主來源"
                )
            # 來源引用：素材包的影音一手源 + 書面深度報告都納入來源區塊
            try:
                import json as _json
                _bj = Path(args.bundle).with_suffix(".json")
                if _bj.exists():
                    _bd = _json.loads(_bj.read_text(encoding="utf-8"))
                    for _s in _bd.get("sources", []):
                        if _s.get("url"):
                            used_reports.append({"title": "🎥 " + (_s.get("title") or ""),
                                                 "url": _s["url"], "quality": True})
                    used_reports += _bd.get("reports", [])
            except Exception:
                pass
        else:
            print(f"[Bundle] ⚠️ 找不到或讀不到素材包：{args.bundle}")

    # 1c) Deep modes are deliberately two-pass.  First digest only the primary
    # source; then use its evidence gaps to collect 5–10 readable external sources.
    profile = resolve_editorial_profile(
        mode,
        override=getattr(args, "editorial_profile", "auto"),
        has_deep_bundle=bool(getattr(args, "bundle", None)),
    )
    word_floor, word_cap = word_range_for(profile)
    source["editorial_kind"] = profile.article_kind
    print(
        f"[Compose] editorial_profile={profile.name}/{profile.article_kind} "
        f"words={word_floor}-{word_cap}"
    )
    research_brief = None
    research_sources = []
    if mode in {"podcast", "company"}:
        if getattr(args, "no_reports", False):
            error_msg = "深度題型要求 5–10 個延伸來源；--no-reports 不再允許產生假深度稿"
            print(f"[EditorialResearch] ❌ {error_msg}")
            notify_substack_failure(mode=mode, error_msg=error_msg)
            return 6
        print("[EditorialResearch] stage 1/2 消化主來源與定義查證問題…")
        research_brief = await plan_editorial_research(
            title=raw_title,
            content=primary_content,
            mode=mode,  # type: ignore[arg-type]
            topic_category=topic_category,
            editorial_profile=profile.name,
            has_deep_bundle=bool(getattr(args, "bundle", None)),
        )
        if research_brief is None:
            notify_substack_failure(
                mode=mode,
                error_msg="主來源消化失敗；未進入延伸調研",
                extra_context={"source_title": raw_title},
            )
            return 6
        print("[EditorialResearch] stage 2/2 搜尋並讀取 5–10 個延伸證據源…")
        try:
            research_sources = await asyncio.to_thread(
                build_research_pack,
                research_brief.research_queries,
                primary_url=source_url,
                seed_sources=used_reports,
            )
        except InsufficientResearchError as exc:
            print(f"[EditorialResearch] ❌ {exc}")
            notify_substack_failure(
                mode=mode,
                error_msg=f"延伸調研未達 5 個可用來源：{exc}",
                extra_context={
                    "source_title": raw_title,
                    "research_queries": research_brief.research_queries,
                },
            )
            return 6
        used_reports = [source_item.model_dump() for source_item in research_sources]
        source["research_brief"] = research_brief.model_dump(
            exclude={"generated_by"}
        )
        source["research_sources"] = [
            item.model_dump(exclude={"excerpt"}) for item in research_sources
        ]
        source["research_source_count"] = len(research_sources)
        print(
            f"[EditorialResearch] ✅ form={research_brief.article_form} "
            f"sources={len(research_sources)}"
        )

    # 2) Final writer. Deep modes receive the digest/evidence pack, not the full
    # transcript again; Daily modes retain the direct single-pass path.
    draft = await compose_substack_article(
        title=raw_title,
        content=raw_content or "",
        mode=mode,  # type: ignore[arg-type]
        topic_category=topic_category,
        editorial_note=args.editorial_note or "",
        editorial_profile=profile.name,
        has_deep_bundle=bool(getattr(args, "bundle", None)),
        research_brief=research_brief,
        research_sources=research_sources,
    )
    if draft is None:
        print("[ERROR] LLM total failure. Aborting.")
        notify_substack_failure(
            mode=mode,
            error_msg="LLM 寫稿失敗（設定的寫手後端皆未成功）",
            extra_context={
                "backends": os.getenv(
                    "SUBSTACK_COMPOSER_BACKEND",
                    "codex_cli,claude_cli",
                ),
                "fix_hint": "check the first configured backend's auth and CLI availability",
                "source_title": raw_title,
            },
        )
        return 3
    print(f"[Compose] ✅ title={draft.title!r}")

    # 2b) Deterministic mainland-term auto-fix (Optimization B, 2026-05-30).
    # Reader-ready is a data boundary, not a request to the model.  Strip any
    # stale authoring blocks before language polish, audit, file writes, or API.
    original_body = draft.body_markdown
    draft.body_markdown = strip_generated_footer(
        strip_production_instructions(original_body)
    )
    assert_reader_ready_markdown(draft.body_markdown)
    if draft.body_markdown != original_body.strip():
        print("[ReaderReady] removed legacy authoring instructions from body")

    # The 大陸→台灣 lookup table does not ship in the writer prompt; the
    # unambiguous half is enforced here at zero token cost, before audit + writes.
    fixes = autofix_traditional(draft)  # 簡→繁台灣 (OpenCC s2tw) — 最後防線，先跑
    fixes += autofix_mainland_terms(draft)
    fixes += autofix_dashes(draft)  # 破折號 ×N → 逗號（跳過 deterministic blockquotes）
    fixes += autofix_cjk_spacing(draft)  # 盤古之白：中英數間補空格 (skip code/quote/URL)
    if fixes:
        print(f"[AutoFix] 🔧 {len(fixes)} 處自動修正：")
        for f in fixes:
            print(f"  - {f}")

    # 3) Audit (remaining ambiguous terms + blacklist still surface as warnings)
    warnings = audit_substack_draft(draft, profile=profile)
    if warnings:
        print(f"[Audit] ⚠️ {len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("[Audit] ✅ Clean")

    # 4) Output paths
    slug = _slug_from_title(draft.title)
    folder_name = f"{mode}_{slug}"
    local_dir = LOCAL_BASE / today / folder_name
    local_dir.mkdir(parents=True, exist_ok=True)
    mirror_dir = ONEDRIVE_BASE / today / folder_name

    # 5) Write files
    sources_md = _sources_block(source_title=source.get("title"), source_url=source_url, reports=used_reports)
    article_md = write_article_substack_md(local_dir, draft, sources_block=sources_md)
    write_article_full_md(
        local_dir,
        draft,
        mode=mode,
        editorial_profile=profile.name,
        source=source,
        audit_warnings=warnings,
    )
    write_metadata(local_dir, draft, mode, profile.name, source, warnings)

    # 5a) Deterministic brand footer + Subscribe placeholder.
    append_footer_block(article_md_path=article_md)

    # 5b) Deterministic Substack cover.  The renderer selects its own character
    # and layout from topic/mode; the writer spends no tokens on image prompts.
    cover_path = render_substack_cover(
        title=draft.title,
        subtitle=draft.subtitle,
        topic_category=topic_category or "other",
        output_dir=local_dir,
        source_image_path=None, # Fallback to synth noise
        character=None,
        mode=mode,  # → pick_expression maps 發文類別 → 角色表情
    )
    
    print(f"[Files] wrote {local_dir}")

    # 6) Mirror to OneDrive
    mirror_to_onedrive(local_dir, mirror_dir)

    # 7) Optional Substack draft push, followed by explicit publish-now.
    source_id = source.get("id")
    existing_evidence = _existing_substack_evidence(source_id)
    draft_id: Optional[int | str] = existing_evidence.get("draft_id")
    if draft_id:
        print(f"[Substack] reusing canonical remote draft id={draft_id}")
    if not args.no_draft:
        if draft_id is None:
            draft_id = push_to_substack_draft(
                article_md_path=article_md,
                title=draft.title,
                subtitle=draft.subtitle,
                cover_path=cover_path,
                source_id=source_id,
            )
    publication: Optional[Dict[str, str]] = None
    if getattr(args, "publish_now", False) and draft_id is not None and source_id:
        publication = publish_substack_draft(draft_id, source_id=source_id)
    evidence_recorded = True
    if source_id:
        _mark_used(source_id)
        evidence_recorded = _record_substack_evidence(
            source_id,
            draft_id=draft_id,
            publication=publication,
        )
        receipt_complete = not getattr(args, "publish_now", False) or publication is not None
        if draft_id is not None and evidence_recorded and receipt_complete:
            try:
                clear_remote_receipt(source_id, draft_id)
            except Exception as exc:
                print(f"[Substack] ⚠️ receipt cleanup deferred: {exc}")
    if getattr(args, "require_substack_draft", False) and draft_id is None:
        print("[Substack] ❌ local article exists but remote draft creation is unproven")
        return 5
    if (
        getattr(args, "publish_now", False)
        and publication is not None
        and not evidence_recorded
    ):
        print(
            "[Substack] ⚠️ public post exists but canonical evidence is pending "
            "receipt reconciliation"
        )
        return 8
    if (
        getattr(args, "require_substack_draft", False)
        and draft_id is not None
        and not evidence_recorded
    ):
        print(
            "[Substack] ⚠️ remote draft exists but canonical evidence is pending "
            "receipt reconciliation; do not call post_draft again"
        )
        return 6
    if getattr(args, "publish_now", False) and publication is None:
        print(
            "[Substack] ⚠️ remote draft is preserved, but public publication "
            "is unproven; status must remain partial"
        )
        return 7

    # 8) Notify Hsin via configured channel (Gmail / macOS / both).
    # Read the final reader-ready article (public footer already appended).
    try:
        final_body_md = article_md.read_text(encoding="utf-8")
    except Exception:
        final_body_md = draft.body_markdown
    pub_url = os.getenv("SUBSTACK_PUBLICATION_URL", "https://hsin73.substack.com")
    draft_url = (
        publication["public_url"]
        if publication
        else f"{pub_url}/publish/post/{draft_id}" if draft_id else None
    )
    from substack_radar.composer import _count_chinese_chars
    notify_substack_success(
        mode=mode,
        draft_title=draft.title,
        draft_subtitle=draft.subtitle,
        draft_url=draft_url,
        body_markdown=final_body_md,
        metadata={
            "chinese_chars": _count_chinese_chars(draft.body_markdown),
            "word_floor": word_floor,
            "word_cap": word_cap,
            "editorial_profile": profile.name,
        },
        audit_warnings=warnings,
        onedrive_path=str(mirror_dir),
    )

    print(f"\n✨ Draft ready:")
    print(f"    Local:    {local_dir}")
    print(f"    OneDrive: {mirror_dir}")
    print(f"    Open Article_Substack.md → paste into Substack editor.")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate one Daily or Weekly Substack draft."
    )
    p.add_argument("mode", choices=["morning", "evening", "podcast", "company"], help="Which slot to compose")
    p.add_argument("--news-id", default=None, help="(morning) override: specific news_items.id")
    p.add_argument("--ticker", default=None, help="(company) 股票代號，如 NVDA / 2330.TW；不給則讀 .company_next 或 watchlist top")
    p.add_argument(
        "--bundle",
        default=None,
        help=(
            "(any mode) 深度素材包 markdown 路徑（scripts/enrich_youtube_sources.py 產出）。"
            "其精華（重點參考資料＋各源關鍵數據與要角）會疊進素材並自動選用 Weekly profile。"
            "drain_substack.py 偵測到 YouTube 種子時會自動帶入。"
        ),
    )
    p.add_argument(
        "--no-reports",
        action="store_true",
        help=(
            "(diagnostic only) disable external research. Podcast/company now fail closed "
            "instead of producing a deep draft without 5–10 readable sources."
        ),
    )
    p.add_argument("--topic", default=None, help="(evening) override: free-text topic / 文章主題")
    p.add_argument(
        "--source-file",
        default=None,
        help=(
            "(evening) path to markdown/text file used as raw material. "
            "Equivalent to morning's news_items.clean_markdown — LLM 看 file 內容當素材。"
            "Title 取自 --topic > file 第一行 H1 > 檔名。需要 --topic 比較保險。"
        ),
    )
    p.add_argument(
        "--topic-category",
        default=None,
        help="(evening) override topic_category (e.g. ai_application / policy_geopolitics). 預設 'other'.",
    )
    p.add_argument("--editorial-note", default="", help="Editor's mandate to the writer")
    p.add_argument(
        "--editorial-profile",
        choices=["auto", "daily", "weekly"],
        default="auto",
        help="Writing depth. auto maps morning/evening to daily and podcast/company/deep bundles to weekly.",
    )
    p.add_argument(
        "--no-draft",
        action="store_true",
        help="Skip python-substack draft push (still writes files to disk + OneDrive)",
    )
    p.add_argument(
        "--require-substack-draft",
        action="store_true",
        help=(
            "Fail unless Substack returns a remote draft id. Used by governed "
            "control-plane submissions; never publishes the draft."
        ),
    )
    p.add_argument(
        "--publish-now",
        action="store_true",
        help=(
            "After all reader-ready, cover, and audit gates, publish the same "
            "remote draft and require public URL readback."
        ),
    )
    p.add_argument(
        "--harvest",
        action="store_true",
        help="Run token-free inspiration harvest (RSS + YouTube) before composing. launchd uses this.",
    )
    p.add_argument(
        "--no-youtube",
        action="store_true",
        help="With --harvest: skip the YouTube transcript step (RSS only).",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(asyncio.run(run(args)))
