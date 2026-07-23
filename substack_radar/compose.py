"""
News Radar · Substack Compose CLI (Phase 1)
============================================

Daily 2-draft pipeline 的 driver。每天兩班：
- 09:00 morning（type A 深度新聞）— 從 news_radar 24h 高分 pool 抽 1
- 18:00 evening（type B 獨立選題）— 從 config/substack_evening_topics.yaml 抽 1

每次跑出 1 篇 1500 字長文草稿 + 封面圖 + 全套 metadata report，
同時寫到 **兩個位置**讓 Hsin 從家裡／公司都看得到：

    Path A (本地 repo)：~/news_radar/data/substack_drafts/<date>/<mode>_<slug>/
    Path B (OneDrive)：~/Library/CloudStorage/OneDrive-RealtekSemiconductorCorp/
                       文件/antigravity_workspace/substack/autogen/<date>/<mode>_<slug>/

每個資料夾長這樣：

    Article_Substack.md   # 貼到 Substack 的純淨版（標題＋副標＋本文）
    Article_Full.md       # 完整 metadata（含 hook type、metaphor domain、audit warnings、原料來源）
    cover.png             # 1456×816 Substack hero
    cover_prompt.txt      # 視覺架構師的提示詞（給 nano-banana 之類的 AI 繪圖工具用）
    chart_prompt.txt      # (可選) 機制解構示意圖提示詞
    metadata.json         # 機讀格式

可選步驟（環境變數開關，**opt-in 模式**）：
    SUBSTACK_AUTO_DRAFT=1 — 啟用透過 python-substack 自動建立 Substack 草稿。
                           預設關閉（=0 或未設）走純 OneDrive paste 流程。
    SUBSTACK_COOKIES_STRING — 從 Chrome DevTools 抓的 cookie header 字串
                              （登入 Substack 後 → F12 → Network → 任一 request
                                → Headers → Request Headers → Cookie 整段複製）。
                              通常 2-4 週過期，失效時 CLI 會明確報錯，重抓即可。
    SUBSTACK_PUBLICATION_URL — 你的 Substack URL，例如 https://hsin73.substack.com
    SUBSTACK_AUDIENCE — 草稿目標讀者（everyone / only_paid / founding / only_free）
                        預設 everyone。

LLM backend 架構（2026-05-12 重構）：
    SUBSTACK_COMPOSER_BACKEND=gemini_cli  # 2026-06-01: Pro accounts priority
        - 文章寫作優先走 Gemini CLI (tingsyuan -> hsin)
        - Claude CLI 不再負責 Substack draft
    SUBSTACK_COMPOSER_BACKEND=default
        - 備援路徑

    SUBSTACK_AI_COVER=1  # 預設 0（關）
        - 啟用 Gemini 生成 Moleskine 風格封面底圖（visual_soul.md aesthetic）
        - 失敗自動退回 photo-overlay / 合成噪點 base，不會 block 整個流程
        - 模型：gemini-2.5-flash-image-preview（可用 SUBSTACK_IMAGE_MODEL override）

    Gemini 在新架構中**唯一**的角色：image generation。文字／JSON 路徑全走 Claude CLI。

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
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

from substack_radar.composer import (  # noqa: E402
    SubstackDraft,
    audit_substack_draft,
    autofix_cjk_spacing,
    autofix_dashes,
    autofix_mainland_terms,
    autofix_traditional,
    compose_substack_article,
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
METAPHOR_HISTORY_PATH = _REPO_ROOT / "data" / "substack_drafts" / ".metaphor_history.json"
HOOK_HISTORY_PATH = _REPO_ROOT / "data" / "substack_drafts" / ".hook_history.json"

# 科技/商業題材加重（2026-06-20 Hsin：每天 5 篇想多著重美/台科技商業；3 篇 podcast 不動，
# 只偏 morning+evening 兩槽）。只影響 _score_pool_item 的挑文偏好。
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


def _record_substack_evidence(news_id: str, draft_id: Optional[int] = None) -> None:
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
            ):
                try:
                    conn.execute(f"ALTER TABLE news_items ADD COLUMN {column}")
                except sqlite3.OperationalError:
                    pass
            conn.execute(
                """
                UPDATE news_items
                   SET substack_written_at=COALESCE(substack_written_at,?)
                 WHERE id=?
                """,
                (now, news_id),
            )
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
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[_record_substack_evidence] ⚠️ DB evidence write failed: {exc}")


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


def pick_podcast_interview(window_days: int = 21) -> Optional[Tuple[str, str, str, str]]:
    """Type-C podcast source (13:00 slot). Draws ONLY from the dedicated podcast
    pool (feed_name='YouTube Podcast'), preferring the longest fresh, unused
    interview — length is the best proxy for a substantive Q&A episode (vs. a clip).
    Wider 21-day window since podcasts aren't time-sensitive. Shares the used-set
    so it won't collide with morning/evening picks."""
    if not NEWS_DB_PATH.exists():
        print(f"[PodcastPick] ⚠️ News DB not found at {NEWS_DB_PATH}")
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
              f"(last {window_days}d). Has the 13:00 harvest run?")
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
                f"請按 substack_soul.md 的方法論：先抓一個常識→打破它→提供更高解析度的"
                f"思維模型→留一個開放結尾。"
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


# ---------------------------------------------------------------------------
# Metaphor history (avoid repeating the same domain)
# ---------------------------------------------------------------------------

def load_recent_metaphor_domains(limit: int = 7) -> List[str]:
    if not METAPHOR_HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(METAPHOR_HISTORY_PATH.read_text(encoding="utf-8"))
        return data.get("recent", [])[-limit:]
    except Exception:
        return []


def append_metaphor_domain(domain: str, limit: int = 30) -> None:
    METAPHOR_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {"recent": []}
    if METAPHOR_HISTORY_PATH.exists():
        try:
            data = json.loads(METAPHOR_HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {"recent": []}
    data["recent"].append(domain)
    data["recent"] = data["recent"][-limit:]
    METAPHOR_HISTORY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_recent_hook_types(limit: int = 5) -> List[str]:
    """最近幾篇用過的標題 hook_type（鏡像 metaphor 機制，2026-06-20）→ 餵給下一篇強制避開，
    讓 soul §6.1 的『連兩篇別同型』真正跨篇生效。"""
    if not HOOK_HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HOOK_HISTORY_PATH.read_text(encoding="utf-8"))
        return data.get("recent", [])[-limit:]
    except Exception:
        return []


def append_hook_type(hook: str, limit: int = 30) -> None:
    if not hook:
        return
    HOOK_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {"recent": []}
    if HOOK_HISTORY_PATH.exists():
        try:
            data = json.loads(HOOK_HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {"recent": []}
    data["recent"].append(hook)
    data["recent"] = data["recent"][-limit:]
    HOOK_HISTORY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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


def _company_context(name: str, ticker: str):
    """補商業模式/競爭脈絡：自動搜對應書面分析（數字仍以財報事實為準）。回 (md, reports)。"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_enrich", str(_REPO_ROOT / "scripts" / "enrich_youtube_sources.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        reports = mod._find_reports(f"{name} business model moat competitive analysis", n=5)
    except Exception:
        reports = []
    if not reports:
        return "", []
    lines = ["## 參考書面分析（自動搜尋 · 補商業模式/競爭脈絡；數字仍一律以上方『財報事實』為準）"]
    for r in reports:
        tag = "⭐ " if r.get("quality") else ""
        lines.append(f"- {tag}{r.get('title','')}\n  {r.get('url','')}")
    return "\n".join(lines), reports


# ---------------------------------------------------------------------------
# Cover rendering — reuse cover_renderer with new "substack" spec
# ---------------------------------------------------------------------------

async def maybe_generate_ai_cover(
    *, cover_image_prompt: str, output_dir: Path
) -> Optional[Path]:
    """Legacy: AI cover generation via Gemini. **Deprecated 2026-05-12**.

    Default-OFF (gated by SUBSTACK_AI_COVER=1). Kept for archeology; primary
    flow is now ``append_cover_prompt_block`` which embeds the prompt into
    Article_Substack.md for human-driven generation via GPT web / NanoBanana.
    """
    try:
        from src.image_brain import generate_cover_image, is_ai_cover_enabled
    except Exception as exc:
        print(f"[Cover] ⚠️ image_brain unavailable: {exc}")
        return None
    if not is_ai_cover_enabled():
        return None
    target = output_dir / "ai_cover_raw.png"
    return await generate_cover_image(
        prompt=cover_image_prompt,
        out_path=target,
        size=(1456, 816),
    )


# 2026-05-12 Hsin directive — every Substack article ends with this tagline +
# subscribe placeholder. Tagline is fixed text per substack_soul.md §12.
BRAND_TAGLINE = (
    "「我專門拆解：那些你已經被市場說服、但其實正在害你的共識。」"
)


def build_footer_block() -> str:
    """Brand tagline + Cadence promise + Substack subscribe placeholder.

    Path A markdown approach: emit visible placeholder text + HTML comment.
    Hsin opens draft in Substack editor and replaces the placeholder block
    with the native Subscribe button widget (slash menu / + button).
    Path B (auto-insert via python-substack subscribeWithCaption node) is
    a future enhancement; for now Path A is reliable and idempotent.

    2026-05-16 加入 Cadence Promise 兩行 (per substack_soul.md §12.1):
    - 每天 3 分鐘 · 拿走一個被市場藏起來的共識
    - 365 天複利一個眼光
    Why: Hsin 5/16 觀察開信率 + 訂閱率不振、insight 是「讀者擔心的不是值不
    值得讀、是有沒有時間讀」。3 分鐘是低於 commute / 茶水間 friction 線下的
    數字。「複利」對齊 brand DNA。
    """
    return (
        "\n\n---\n\n"
        f"> **{BRAND_TAGLINE}**\n"
        "> \n"
        "> 📅 每天 3 分鐘 · 拿走一個被市場藏起來的共識\n"
        "> 🔄 365 天複利一個眼光\n\n"
        "<!-- substack-editor: 將此段替換為 Subscribe button widget "
        "(toolbar 的 + → Subscribe button) -->\n\n"
        "*點此訂閱 → 不錯過下一篇拆解。*\n"
    )


def append_footer_block(*, article_md_path: Path) -> None:
    """Append brand tagline + subscribe placeholder. **Order matters**:
    call this BEFORE append_cover_prompt_block so the footer sits between
    the article body and the cover-prompt instructions."""
    block = build_footer_block()
    existing = article_md_path.read_text(encoding="utf-8")
    article_md_path.write_text(existing.rstrip() + block, encoding="utf-8")
    print(f"[Footer] ✅ tagline + subscribe placeholder appended")


def append_cover_prompt_block(
    *, article_md_path: Path, draft, output_dir: Path,
    topic_category: str = "", mode: str = "",
) -> None:
    """Append the 2-character cover-IP prompt block to Article_Substack.md (and a
    standalone cover_prompts.md for copy-paste).

    2026-06-21 (Hsin directive): every draft now carries a固定 IP 角色 (robot=瑞瑞 雷達機器人 /
    owl=達達 雷達貓頭鷹). The model picked ``draft.cover_character`` + a one-line
    ``draft.cover_image_prompt`` scene; ``image_brain.build_cover_prompt_block``
    layers the canonical look + clay style bible so the IP stays on-model every time.
    If the model left the character null we fall back to ``pick_character(topic, mode)``
    — the cover never opens a hole. Hsin generates the actual image in NanoBanana / GPT.
    """
    try:
        from src.image_brain import build_cover_prompt_block
    except Exception as exc:
        print(f"[CoverPrompt] ⚠️ image_brain unavailable: {exc}")
        return
    block = build_cover_prompt_block(
        getattr(draft, "cover_image_prompt", None),
        character=getattr(draft, "cover_character", None),
        title=draft.title,
        subtitle=draft.subtitle,
        topic_category=topic_category,
        mode=mode,
    )
    # Append to the main markdown so Hsin sees it when he opens the file
    existing = article_md_path.read_text(encoding="utf-8")
    article_md_path.write_text(existing.rstrip() + block, encoding="utf-8")
    # Also write standalone for quick copy-paste
    (output_dir / "cover_prompts.md").write_text(block.lstrip(), encoding="utf-8")
    _picked = getattr(draft, "cover_character", None) or "auto"
    print(f"[CoverPrompt] ✅ IP={_picked} → appended to {article_md_path.name} + cover_prompts.md")


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

    Optional env vars:
      - SUBSTACK_AUDIENCE : "everyone" (default) | "only_paid" | "founding" | "only_free"
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

    audience = os.getenv("SUBSTACK_AUDIENCE", "everyone")
    body_md = article_md_path.read_text(encoding="utf-8")

    # Article_Substack.md starts with "# <title>\n\n*<subtitle>*\n\n<body>".
    # python-substack's Post object takes title/subtitle as separate fields,
    # so we strip them out of the markdown before from_markdown() ingests it.
    # Without this, the draft would have the title duplicated as an H1 at the top.
    body_md = _strip_title_subtitle_lines(body_md, title=title, subtitle=subtitle)

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

        # Optional: upload cover and prepend as captionedImage.
        # We deliberately do NOT auto-publish — only create draft. Hsin
        # eyeballs the cover placement in the Substack editor and Publish
        # manually.
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
    """The PASTE-READY version. Goes straight into Substack editor.

    Top line = 產文路線標記 (2026-05-31 Hsin directive): one deletable line marking
    which model/route wrote this draft. Single line (not the old cover block) —
    刪掉即可再貼上 Substack。
    sources_block（2026-06-22 Hsin directive）：緊接在 🧠 行後、標題前的『本文取材』區塊，
    列主來源 + 延伸參考的可點擊連結。**面向讀者保留**（增加可信度與厚度），不再是發布前刪除的私註。"""
    path = out_dir / "Article_Substack.md"
    route = getattr(draft, "generated_by", None) or "unknown"
    md = (
        f"> 🧠 產文路線：{route}　（發布前刪此行）\n\n"
        f"{sources_block}"
        f"# {draft.title}\n\n"
        f"*{draft.subtitle}*\n\n"
        f"{draft.body_markdown.strip()}\n"
    )
    path.write_text(md, encoding="utf-8")
    return path


def write_article_full_md(
    out_dir: Path,
    draft: SubstackDraft,
    *,
    mode: str,
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

**Mode:** `{mode}`  |  **Hook:** `{draft.hook_type}`  |  **Open-ending:** `{draft.open_ending_form}`
**Metaphor domain:** `{draft.metaphor_domain_used}`  |  **Estimated reading time:** {draft.reading_time_minutes} min

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

{draft.body_markdown.strip()}

---

## 視覺指引

**封面 IP 角色**：{getattr(draft, "cover_character", None) or "（自動依主題選 robot/owl）"}
**封面 scene**：{getattr(draft, "cover_image_prompt", None) or "（留空 → 用角色招牌動作 + 標題自動補）"}
完整封面 prompt（含固定造型 + 黏土美學）見 Article_Substack.md 末段「📸 封面圖 Prompt」與 cover_prompts.md。
內文視覺建議見本文中的 🖼 標記。

### 機制解構示意圖 prompt

{draft.chart_prompt or "(本篇未提供)"}
"""
    path.write_text(md, encoding="utf-8")
    return path


def write_prompts_and_metadata(
    out_dir: Path,
    draft: SubstackDraft,
    mode: str,
    source: Dict[str, Any],
    audit_warnings: List[str],
) -> None:
    if draft.chart_prompt:
        (out_dir / "chart_prompt.txt").write_text(draft.chart_prompt, encoding="utf-8")
    metadata = {
        "title": draft.title,
        "subtitle": draft.subtitle,
        "mode": mode,
        "generated_by": getattr(draft, "generated_by", None),
        "hook_type": draft.hook_type,
        "open_ending_form": draft.open_ending_form,
        "metaphor_domain_used": draft.metaphor_domain_used,
        "cover_character": getattr(draft, "cover_character", None),  # 2026-06-21 封面 IP
        "cover_image_prompt": getattr(draft, "cover_image_prompt", None),
        "reading_time_minutes": draft.reading_time_minutes,
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


def _podcast_reports_block(title: str):
    """podcast 一手逐字稿之外，自動上網搜對應書面深度報告（SemiAnalysis/Stratechery 等）當
    第二瞭望台，並抓前 1–2 篇高品質源可讀的正文片段。觸發 soul §14 巨人之聲多源綜合法——
    讓 13:00 那兩篇 podcast 取材的文章自動加厚、不靠人工。
    回傳 (context_text, reports)：context_text 疊進素材給 LLM；reports 給文章最上面的來源區塊引用。
    任何失敗回 ("", [])，不阻斷出稿。"""
    if not title:
        return "", []
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_enrich", str(_REPO_ROOT / "scripts" / "enrich_youtube_sources.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        reports = mod._find_reports(title, n=5)
    except Exception as e:
        print(f"[Podcast] ⚠️ 書面報告搜尋略過：{e}")
        return "", []
    if not reports:
        return "", []
    lines = [
        "\n\n===== 深度素材包（書面報告層 · 依 substack_soul.md §14 巨人之聲多源綜合法）=====",
        "## 對應書面深度報告（自動搜尋 · podcast 主題的第二瞭望台，用來交叉驗證/加厚，不要照抄）",
    ]
    for r in reports:
        tag = "⭐ " if r.get("quality") else ""
        lines.append(f"- {tag}{r.get('title','')}\n  {r.get('url','')}")
    # 前 1–2 篇高品質源抓可讀正文片段（付費牆通常只拿得到導言，仍是交叉驗證材料）。
    try:
        from scripts.submit_source import _fetch_page_text
        for r in [x for x in reports if x.get("quality")][:2]:
            txt = (_fetch_page_text(r["url"]) or "").strip()
            if len(txt) > 200:
                lines.append(f"\n### 摘錄自 {r.get('title','')[:50]}\n{txt[:1500]}")
    except Exception:
        pass
    return "\n".join(lines), reports


def _sources_block(*, source_title=None, source_url=None, reports=None) -> str:
    """Article_Substack.md 開頭、**面向讀者保留**的『本文取材』來源區塊（2026-06-22 Hsin：
    從『發布前刪除的私註』改成『保留、給讀者看』，增加可信度與厚度）。主來源 + 延伸參考
    用 markdown 連結呈現（點擊查證）。完全沒網址就回空字串。"""
    reports = reports or []
    rep_links, seen = [], set()
    for r in reports:
        u = (r.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        rep_links.append(f"[{(r.get('title') or u)[:60]}]({u})")
    if not source_url and not rep_links:
        return ""
    block = ["> 📚 **本文取材**（公開來源、可點擊查證）"]
    if source_url:
        block.append(f"> 主來源 — [{(source_title or '原始來源')[:60]}]({source_url})")
    if rep_links:
        block.append("> 延伸參考 — " + " ｜ ".join(rep_links))
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

async def generate_inline_images(
    *,
    article_md_path: Path,
    output_dir: Path,
) -> None:
    """Scan markdown for 🖼 markers, generate images via Pro account tokens, and embed them.

    2026-06-01 Update: Uses the new image_brain.generate_image which extracts 
    OAuth tokens from the CLI login to perform Pro-account image generation.
    """
    if not article_md_path.exists():
        return

    from src.image_brain import generate_image

    content = article_md_path.read_text(encoding="utf-8")
    blocks = content.split("\n\n")
    new_blocks = []
    img_idx = 1

    try:
        rel_output_dir = output_dir.relative_to(_REPO_ROOT)
    except Exception:
        rel_output_dir = output_dir

    for block in blocks:
        if "🖼 視覺位置" in block:
            lines = block.strip().split("\n")
            label = ""
            for line in lines:
                if "🖼 視覺位置" in line:
                    label = line.split("·")[-1].strip()
                    break

            prompt = ""
            for i, line in enumerate(lines):
                if "Path C" in line and i + 1 < len(lines):
                    prompt = lines[i + 1].replace("> ", "").strip()
                    break

            if not prompt:
                prompt = label

            img_filename = f"inline_{img_idx}_{label}.png".replace(" ", "_")
            img_path = output_dir / img_filename

            print(f"[Images] Generating inline_{img_idx} (Pro Account): {label}...")
            # This calls the new token-based generator
            success_path = await generate_image(
                prompt=prompt,
                out_path=img_path,
                size=(1024, 576),
            )

            if success_path:
                img_rel_path = rel_output_dir / img_filename
                new_blocks.append(f"![{label}]({img_rel_path})\n\n*{label}*")
                img_idx += 1
                continue
            else:
                print(f"[Images] Skipped inline_{img_idx} (Pro quota exhausted/not auth).")

        new_blocks.append(block)

    article_md_path.write_text("\n\n".join(new_blocks), encoding="utf-8")


async def _run_inner(args: argparse.Namespace) -> int:
    today = date.today().isoformat()
    mode: str = args.mode

    if getattr(args, "require_substack_draft", False):
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
                "[Substack] ❌ remote draft is required but preflight failed: "
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
        # podcast (13:00): longest fresh, unused interview from the YouTube Podcast pool.
        pick = pick_podcast_interview()
        if pick is None:
            print("[ERROR] No suitable podcast interview in the harvested pool (last 21 days).")
            notify_substack_failure(
                mode=mode,
                error_msg="No suitable podcast interview in pool (last 21 days)",
                extra_context={
                    "likely_cause": "podcast pool empty — 13:00 harvest hasn't run, all episodes used, or none had subtitles",
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
        # 每週公司營運分析（週日 09:00）。數字 = yfinance；商業模式/競爭脈絡 = 自動搜書面分析。
        ticker = _resolve_company_ticker(args)
        if not ticker:
            print("[ERROR] company mode 需要 ticker（--ticker / .company_next / watchlist）。")
            return 2
        from substack_radar.company_financials import fetch_financials
        print(f"[Company] {ticker} → 抓 yfinance 財報 + 補書面脈絡…")
        fin_data, fin_md = fetch_financials(ticker)
        raw_title = fin_data.get("name") or ticker          # 占位；composer 依 §6 生真標題
        topic_category = "tw_stocks" if ".TW" in ticker.upper() else "us_stocks"
        ctx_md, company_reports = _company_context(raw_title, ticker)
        raw_content = fin_md + ("\n\n" + ctx_md if ctx_md else "")
        source = {
            "id": None,
            "title": raw_title,
            "topic_category": topic_category,
            "via": f"company:{ticker}",
            "_company_reports": company_reports,
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

    # 來源區塊用：主來源網址 + 引用到的書面報告（給 Article_Substack.md 最上面那塊）。
    used_reports: list = []
    source_url = None
    if mode == "company":                       # 公司分析：來源 = 財報 + 參考書面分析
        used_reports = source.get("_company_reports", []) or []
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

    # 1b) 深度素材包（多源綜合）：有 --bundle 就把其精華疊進素材，啟動 soul §14 巨人之聲多源綜合法。
    if getattr(args, "bundle", None):
        bundle_extra = _load_bundle_curated(args.bundle)
        if bundle_extra:
            raw_content = (raw_content or "") + (
                "\n\n===== 深度素材包（多源綜合用 · 依 substack_soul.md §14 巨人之聲多源綜合法）=====\n"
                + bundle_extra
            )
            print(f"[Bundle] 疊入深度素材包 {Path(args.bundle).name}（+{len(bundle_extra):,} 字元）")
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

    # 1c) podcast 自動加書面深度報告層：一手逐字稿已在 raw_content，再上網搜對應書面報告當
    #     第二瞭望台 → §14 多源綜合，13:00 兩篇 podcast 取材文章自動加厚（你說的「搭配
    #     SemiAnalysis 查證」）。給了 --bundle 就以 bundle 為準、不重複搜。
    elif mode == "podcast" and not getattr(args, "no_reports", False):
        rblock, used_reports = _podcast_reports_block(raw_title)
        if rblock:
            raw_content = (raw_content or "") + rblock
            print("[Podcast] +書面深度報告層（§14 第二瞭望台）")

    # 2) Compose
    recent_domains = load_recent_metaphor_domains()
    recent_hooks = load_recent_hook_types()
    print(f"[Compose] recent_metaphor_domains={recent_domains} recent_hooks={recent_hooks}")
    draft = await compose_substack_article(
        title=raw_title,
        content=raw_content or "",
        mode=mode,  # type: ignore[arg-type]
        topic_category=topic_category,
        editorial_note=args.editorial_note or "",
        recent_metaphor_domains=recent_domains,
        recent_hook_types=recent_hooks,
    )
    if draft is None:
        print("[ERROR] LLM total failure. Aborting.")
        notify_substack_failure(
            mode=mode,
            error_msg="LLM 寫稿失敗（Claude CLI + Gemini 兩條路都掛）",
            extra_context={
                "backends": os.getenv("SUBSTACK_COMPOSER_BACKEND", "claude_cli"),
                "fix_hint": "check claude CLI auth: claude -p --output-format json ping",
                "source_title": raw_title,
            },
        )
        return 3
    print(f"[Compose] ✅ title={draft.title!r}")

    # 2b) Deterministic mainland-term auto-fix (Optimization B, 2026-05-30).
    # The 大陸→台灣 lookup table no longer ships in the soul prompt; the
    # unambiguous half is enforced here at zero token cost, before audit + writes.
    fixes = autofix_traditional(draft)  # 簡→繁台灣 (OpenCC s2tw) — 最後防線，先跑
    fixes += autofix_mainland_terms(draft)
    fixes += autofix_dashes(draft)  # 破折號 ×N → 逗號 (skip §13 marker/footer blockquotes)
    fixes += autofix_cjk_spacing(draft)  # 盤古之白：中英數間補空格 (skip code/quote/URL)
    if fixes:
        print(f"[AutoFix] 🔧 {len(fixes)} 處自動修正：")
        for f in fixes:
            print(f"  - {f}")

    # 3) Audit (remaining ambiguous terms + blacklist still surface as warnings)
    warnings = audit_substack_draft(draft)
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
    write_article_full_md(local_dir, draft, mode=mode, source=source, audit_warnings=warnings)
    write_prompts_and_metadata(local_dir, draft, mode, source, warnings)

    # 5a) Brand footer: tagline + Subscribe placeholder (per soul.md §12).
    append_footer_block(article_md_path=article_md)

    # 5b) Cover IP prompt (2026-06-21, Hsin directive): every draft gets a SHORT
    # cover prompt featuring a dynamically-picked 固定 IP 角色 (robot=瑞瑞 / owl=達達
    # 雷達貓頭鷹). Model chose draft.cover_character + a one-line scene; image_brain
    # layers the canonical look + clay style bible. Null character → pick by topic/mode.
    append_cover_prompt_block(
        article_md_path=article_md,
        draft=draft,
        output_dir=local_dir,
        topic_category=topic_category or "",
        mode=mode,
    )

    # 5c) Substack Cover (Synthetic Base)
    cover_path = render_substack_cover(
        title=draft.title,
        subtitle=draft.subtitle,
        topic_category=topic_category or "other",
        output_dir=local_dir,
        source_image_path=None, # Fallback to synth noise
        character=getattr(draft, "cover_character", None),  # route 3: IP character cover
        mode=mode,  # → pick_expression maps 發文類別 → 角色表情
    )
    
    # 5d) AI Inline images (2026-06-01 Hsin directive)
    # Scan for 🖼 markers and replace them with actual generated images.
    # Non-essential: image-gen failure (Pro quota exhausted, no temp dir, …) must
    # NOT flip an otherwise-successful draft run to exit 1 (2026-06-03 evening fix).
    try:
        await generate_inline_images(article_md_path=article_md, output_dir=local_dir)
    except Exception as exc:
        print(f"[Images] ⚠️ inline image gen failed (continuing): {exc}")

    print(f"[Files] wrote {local_dir}")

    # 6) Mirror to OneDrive
    mirror_to_onedrive(local_dir, mirror_dir)

    # 7) Optional Substack draft push (opt-in via SUBSTACK_AUTO_DRAFT=1)
    draft_id: Optional[int] = None
    if not args.no_draft:
        draft_id = push_to_substack_draft(
            article_md_path=article_md,
            title=draft.title,
            subtitle=draft.subtitle,
            cover_path=cover_path,
        )
    source_id = source.get("id")
    if source_id:
        _mark_used(source_id)
        _record_substack_evidence(source_id, draft_id=draft_id)
    if getattr(args, "require_substack_draft", False) and draft_id is None:
        print("[Substack] ❌ local article exists but remote draft creation is unproven")
        return 5

    # 8) Update metaphor history — best-effort; draft is already pushed, so a
    # housekeeping failure must not flip the run to exit 1.
    try:
        append_metaphor_domain(draft.metaphor_domain_used)
        append_hook_type(getattr(draft, "hook_type", ""))
    except Exception as exc:
        print(f"[PostDraft] ⚠️ append_metaphor_domain/hook failed (continuing): {exc}")

    # 9) Notify Hsin via configured channel (Gmail / macOS / both).
    # Read final article markdown back (footer + cover prompts already appended).
    try:
        final_body_md = article_md.read_text(encoding="utf-8")
    except Exception:
        final_body_md = draft.body_markdown
    pub_url = os.getenv("SUBSTACK_PUBLICATION_URL", "https://hsin73.substack.com")
    draft_url = f"{pub_url}/publish/post/{draft_id}" if draft_id else None
    from substack_radar.composer import SUBSTACK_WORD_FLOOR, SUBSTACK_WORD_CAP, _count_chinese_chars
    notify_substack_success(
        mode=mode,
        draft_title=draft.title,
        draft_subtitle=draft.subtitle,
        draft_url=draft_url,
        body_markdown=final_body_md,
        metadata={
            "chinese_chars": _count_chinese_chars(draft.body_markdown),
            "word_floor": SUBSTACK_WORD_FLOOR,
            "word_cap": SUBSTACK_WORD_CAP,
            "metaphor_domain_used": draft.metaphor_domain_used,
            "hook_type": draft.hook_type,
            "open_ending_form": draft.open_ending_form,
            "reading_time_minutes": draft.reading_time_minutes,
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
        description="Generate a daily Substack draft (morning type-A / evening type-B)."
    )
    p.add_argument("mode", choices=["morning", "evening", "podcast", "company"], help="Which slot to compose")
    p.add_argument("--news-id", default=None, help="(morning) override: specific news_items.id")
    p.add_argument("--ticker", default=None, help="(company) 股票代號，如 NVDA / 2330.TW；不給則讀 .company_next 或 watchlist top")
    p.add_argument(
        "--bundle",
        default=None,
        help=(
            "(any mode) 深度素材包 markdown 路徑（scripts/enrich_youtube_sources.py 產出）。"
            "其精華（重點參考資料＋各源關鍵數據與要角）會疊進素材，觸發 substack_soul.md "
            "§14 巨人之聲·多源綜合法。drain_substack.py 偵測到 YouTube 種子時會自動帶入。"
        ),
    )
    p.add_argument(
        "--no-reports",
        action="store_true",
        help="(podcast) 關掉自動搜書面深度報告（SemiAnalysis/Stratechery 第二瞭望台）。預設開。",
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
